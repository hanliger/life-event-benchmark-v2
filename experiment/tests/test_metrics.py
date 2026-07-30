from __future__ import annotations

import pytest

from financial_memory_experiment.metrics import (
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
