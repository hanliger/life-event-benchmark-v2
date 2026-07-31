from __future__ import annotations

import pytest

from financial_memory_experiment.metrics import (
    _gca_components,
    _stage2_2_gca15,
    _stage2_2_path_macro,
    hierarchical_stage2,
)


def _row(trajectory: str, target: str, correct: bool):
    return {
        "trajectory_id": trajectory,
        "item_id": f"{trajectory}-{target}",
        "correct": correct,
        "item_metadata": {"canonical_target_id": target},
    }


def test_stage2_uses_trajectory_target_checkpoint_hierarchy():
    rows = [
        _row("t1", "a", True),
        _row("t1", "a", False),
        _row("t1", "b", True),
        _row("t2", "c", False),
    ]
    score, trajectories = hierarchical_stage2(rows)
    assert trajectories == pytest.approx({"t1": 0.75, "t2": 0.0})
    assert score == pytest.approx(0.375)


def test_stage2_2_path_metrics_macro_trajectories_after_checkpoints():
    rows = []
    for trajectory, correct, classification in (
        ("t1", True, "tp_correct"),
        ("t2", False, "tp_wrong_value"),
    ):
        for checkpoint in (15, 30):
            rows.append(
                {
                    "trajectory_id": trajectory,
                    "query_checkpoint": checkpoint,
                    "metrics": {
                        "path_outcomes": {
                            "employment.employer": {
                                "classification": classification,
                                "gold_changed": True,
                                "cell_correct": correct,
                                "gold_event_session_ids": ["D015"],
                            }
                        }
                    },
                }
            )
    result = _stage2_2_path_macro(rows)
    path = result["path_metrics"]["employment.employer"]
    assert path["reported_trajectories"] == 2
    assert path["eligible_trajectories"] == 2
    assert path["final_state_accuracy"] == pytest.approx(0.5)
    assert path["correct_change_f1"] == pytest.approx(0.5)
    assert path["event_macro_update_accuracy"] == pytest.approx(0.5)
    assert path["retention_after_update"] == pytest.approx(0.5)
    assert len(result["path_trajectory_metrics"]) == 2


def _cell(value):
    return {"value": value, "status": "current", "evidence_session_ids": []}


def test_gca15_uses_checkpoint_deltas_and_official_harmonic_formula():
    age = "profile.age"
    dependents = "household.dependents"
    initial = {age: _cell(30), dependents: _cell(0)}
    rows = [
        {
            "trajectory_id": "t1",
            "query_checkpoint": 15,
            "gold": {age: _cell(31), dependents: _cell(0)},
            "prediction": {age: _cell(31), dependents: _cell(1)},
        },
        {
            "trajectory_id": "t1",
            "query_checkpoint": 30,
            "gold": {age: _cell(31), dependents: _cell(2)},
            "prediction": {age: _cell(31), dependents: _cell(2)},
        },
    ]
    result = _stage2_2_gca15(rows, {"t1": initial})
    assert result["counts"] == {
        "correct": 2,
        "wrong": 1,
        "overshot": 0,
        "missed": 0,
    }
    assert result["value_precision"] == pytest.approx(2 / 3)
    assert result["value_recall"] == pytest.approx(2 / 3)
    assert result["label_precision"] == pytest.approx(1.0)
    assert result["label_recall"] == pytest.approx(1.0)
    assert result["score"] == pytest.approx(0.6875)


def test_gca15_formula_weights_value_and_label_support_like_reference():
    result = _gca_components(
        {"correct": 2, "wrong": 1, "overshot": 3, "missed": 4}
    )
    prediction_support = 6
    gold_support = 7
    values = (2 / 6, 2 / 7, 3 / 6, 3 / 7)
    weights = (
        10 / 11 * prediction_support,
        10 / 11 * gold_support,
        1 / 11 * prediction_support,
        1 / 11 * gold_support,
    )
    expected = sum(weights) / sum(
        weight / value for weight, value in zip(weights, values)
    )
    assert result["score"] == pytest.approx(expected)


def test_gca15_does_not_recount_a_persistent_wrong_value():
    age = "profile.age"
    initial = {age: _cell(30)}
    rows = [
        {
            "trajectory_id": "t1",
            "query_checkpoint": 15,
            "gold": {age: _cell(31)},
            "prediction": {age: _cell(30)},
        },
        {
            "trajectory_id": "t1",
            "query_checkpoint": 30,
            "gold": {age: _cell(31)},
            "prediction": {age: _cell(30)},
        },
    ]
    result = _stage2_2_gca15(rows, {"t1": initial})
    assert result["counts"] == {
        "correct": 0,
        "wrong": 1,
        "overshot": 0,
        "missed": 0,
    }
    assert result["score"] == 0.0


def test_gca15_treats_a_missing_required_path_as_missed():
    age = "profile.age"
    initial = {age: _cell(30)}
    rows = [
        {
            "trajectory_id": "t1",
            "query_checkpoint": 15,
            "gold": {age: _cell(31)},
            "prediction": {},
        }
    ]
    result = _stage2_2_gca15(rows, {"t1": initial})
    assert result["counts"] == {
        "correct": 0,
        "wrong": 0,
        "overshot": 0,
        "missed": 1,
    }


def test_gca15_rewards_a_correction_without_recounting_gold():
    age = "profile.age"
    initial = {age: _cell(30)}
    rows = [
        {
            "trajectory_id": "t1",
            "query_checkpoint": 15,
            "gold": {age: _cell(31)},
            "prediction": {age: _cell(32)},
        },
        {
            "trajectory_id": "t1",
            "query_checkpoint": 30,
            "gold": {age: _cell(31)},
            "prediction": {age: _cell(31)},
        },
    ]
    result = _stage2_2_gca15(rows, {"t1": initial})
    assert result["counts"] == {
        "correct": 1,
        "wrong": 1,
        "overshot": 0,
        "missed": 0,
    }
