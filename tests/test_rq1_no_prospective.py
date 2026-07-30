"""Tests for the cp300 no-prospective-evidence RQ1 diagnostic.

Covers session-type filtering -- weak-signal and upcoming sessions out, every
other type in -- the invariant that gold is the unchanged full-prefix gold, the
false-positive decomposition, the stored-baseline comparison, and the offline
evaluator + audit end to end. No network access.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fin_life_benchmark.benchmark.lifecycle_masking import (
    UPCOMING_TYPES,
    WEAK_TYPES,
)
from fin_life_benchmark.benchmark.rq1_builder import (
    build_natural_items,
    taxonomy_hash,
)
from fin_life_benchmark.benchmark.rq1_pair_metrics import pair_item_metrics
from fin_life_benchmark.benchmark.rq1_pair_models import (
    RQ1_PAIR_CONDITIONS,
    RQ1_PAIR_PROMPT_FILE,
    RQ1PairPrediction,
    RQ1PredictedPair,
    gold_pairs_from_occurred_trajectory,
)
from fin_life_benchmark.benchmark.rq1_pair_no_prospective import (
    PROSPECTIVE_EVIDENCE_SESSION_TYPES,
    classify_pair_errors,
    compare_with_baseline,
    find_baseline_row,
    session_type_counts,
    no_prospective_visible_ids,
    surviving_prospective_sessions,
)
from fin_life_benchmark.gold.prefix_gold_exporter import export_prefix_gold
from fin_life_benchmark.io.jsonl import write_jsonl

from rq1_fixtures import TAXONOMY, TRAJ_ID, build_sessions, build_trajectory

SESSIONS = build_sessions()
BY_ID = {s["session_id"]: s for s in SESSIONS}
TRAJECTORY = build_trajectory()
PREFIXES = [
    p.model_dump(mode="json")
    for p in export_prefix_gold(TRAJECTORY, SESSIONS, checkpoint_stride=15)
]
ITEMS = build_natural_items(
    PREFIXES, {TRAJ_ID: BY_ID}, taxonomy_digest=taxonomy_hash(TAXONOMY)
)
TAXONOMY_IDS = {row["event_id"] for row in TAXONOMY}

# The fixture corpus has no cancellation session, so these tests add one on the
# occurred-only trajectory whenever cancellation behavior is under test.
PREFIX_30 = [f"S{n:03d}" for n in range(1, 31)]


def _prediction(*atoms: tuple[str, str], invalid: int = 0) -> RQ1PairPrediction:
    return RQ1PairPrediction(
        valid_pairs=[
            RQ1PredictedPair(event_id=event_id, evidence_session_id=session_id)
            for event_id, session_id in atoms
        ],
        invalid_record_count=invalid,
    )


def _with_cancellation(session_id: str = "S029") -> dict[str, dict]:
    sessions = {sid: dict(record) for sid, record in BY_ID.items()}
    sessions[session_id] = {
        **sessions[session_id],
        "session_type": "cancellation_evidence",
        "linked_event_instance_id": f"{TRAJ_ID}_ev003",
        "event_status_after_session": "cancelled",
    }
    return sessions


# ---------------------------------------------------------------------------
# session filtering


# The fixture prefix S001..S030 holds weak signals at S003/S018/S020 and
# upcoming plans at S007/S028 -- the five sessions this condition removes.
PROSPECTIVE_IDS = ["S003", "S007", "S018", "S020", "S028"]
RETAINED_30 = [sid for sid in PREFIX_30 if sid not in PROSPECTIVE_IDS]


def test_prospective_set_is_the_shared_canonical_definition():
    assert PROSPECTIVE_EVIDENCE_SESSION_TYPES == set(WEAK_TYPES | UPCOMING_TYPES)
    assert PROSPECTIVE_EVIDENCE_SESSION_TYPES == {
        "weak_signal_evidence",
        "upcoming_evidence",
    }


@pytest.mark.parametrize(
    "session_id, session_type",
    [
        ("S003", "weak_signal_evidence"),
        ("S007", "upcoming_evidence"),
        ("S018", "weak_signal_evidence"),
        ("S028", "upcoming_evidence"),
    ],
)
def test_filter_removes_prospective_evidence(session_id, session_type):
    assert BY_ID[session_id]["session_type"] == session_type
    assert session_id not in no_prospective_visible_ids(PREFIX_30, BY_ID)


@pytest.mark.parametrize(
    "session_id, session_type",
    [
        ("S012", "occurred_evidence"),
        ("S014", "consequence_session"),
        ("S010", "hard_negative"),
        ("S002", "routine_financial"),
    ],
)
def test_filter_retains_every_other_type(session_id, session_type):
    """The distractors and the downstream sessions stay -- that is the point."""

    assert BY_ID[session_id]["session_type"] == session_type
    assert session_id in no_prospective_visible_ids(PREFIX_30, BY_ID)


def test_filter_retains_cancellation_evidence():
    sessions = _with_cancellation()
    assert "S029" in no_prospective_visible_ids(PREFIX_30, sessions)


def test_filter_retains_stale_recall_sessions():
    sessions = {sid: dict(record) for sid, record in BY_ID.items()}
    sessions["S016"] = {**sessions["S016"], "session_type": "stale_recall_session"}
    assert "S016" in no_prospective_visible_ids(PREFIX_30, sessions)


def test_retained_set_is_exactly_the_prefix_minus_prospective_evidence():
    assert no_prospective_visible_ids(PREFIX_30, BY_ID) == RETAINED_30
    assert len(RETAINED_30) == 25


def test_filter_preserves_original_public_ids_without_renumbering():
    item = ITEMS[1]
    id_map = dict(item.gold.session_id_map)
    retained = no_prospective_visible_ids(list(item.visible_sessions), BY_ID)
    # D### values are the ones the full prefix already used, not 1..n, so the
    # removed sessions leave gaps rather than shifting everything down
    public = [id_map[sid] for sid in retained]
    assert public[:4] == ["D001", "D002", "D004", "D005"]
    assert "D003" not in public and "D028" not in public
    assert public[-1] == "D030"


def test_filter_is_chronological_even_from_unordered_input():
    shuffled = list(reversed(PREFIX_30))
    assert no_prospective_visible_ids(shuffled, BY_ID) == RETAINED_30


def test_session_type_counts_reports_the_visible_histogram():
    sessions = _with_cancellation()
    retained = no_prospective_visible_ids(PREFIX_30, sessions)
    assert session_type_counts(retained, sessions) == {
        "cancellation_evidence": 1,
        "consequence_session": 1,
        "hard_negative": 1,
        "occurred_evidence": 2,
        "routine_financial": 20,
    }


def test_no_prospective_is_an_evaluator_condition():
    assert "no_prospective" in RQ1_PAIR_CONDITIONS
    assert "full_prefix" in RQ1_PAIR_CONDITIONS


# ---------------------------------------------------------------------------
# gold


def test_no_prospective_gold_equals_full_prefix_gold():
    item = ITEMS[1]
    id_map = dict(item.gold.session_id_map)
    prefix_ids = list(item.visible_sessions)
    retained = no_prospective_visible_ids(prefix_ids, BY_ID)

    full = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map={sid: id_map[sid] for sid in prefix_ids},
        sessions=BY_ID,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    ablated = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map={sid: id_map[sid] for sid in retained},
        sessions=BY_ID,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert ablated == full
    assert full == [("career_employment", "D012"), ("career_employment", "D026")]


def test_every_occurrence_anchor_remains_visible():
    item = ITEMS[1]
    id_map = dict(item.gold.session_id_map)
    prefix_ids = list(item.visible_sessions)
    full = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map={sid: id_map[sid] for sid in prefix_ids},
        sessions=BY_ID,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    retained_public = {
        id_map[sid] for sid in no_prospective_visible_ids(prefix_ids, BY_ID)
    }
    assert {public for _, public in full} <= retained_public


def test_cancellation_sessions_create_no_gold_pair():
    sessions = _with_cancellation()
    item = ITEMS[1]
    id_map = dict(item.gold.session_id_map)
    retained = no_prospective_visible_ids(list(item.visible_sessions), sessions)
    pairs = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map={sid: id_map[sid] for sid in retained},
        sessions=sessions,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert "S029" in retained  # visible on purpose
    assert id_map["S029"] not in {public for _, public in pairs}


# ---------------------------------------------------------------------------
# error decomposition


GOLD = [("career_employment", "D012"), ("housing_move", "D026")]
TYPES = {
    "D012": "occurred_evidence",
    "D026": "occurred_evidence",
    "D029": "cancellation_evidence",
    # distractors stay visible in this condition, so both are anchorable
    "D010": "hard_negative",
    "D002": "routine_financial",
    # visible occurred evidence that is not an anchor: a later occurred session
    # of an instance whose earliest one already carries the gold pair
    "D030": "occurred_evidence",
}


def _categories(prediction):
    return classify_pair_errors(
        GOLD, prediction, session_type_by_public_id=TYPES
    )["false_positive_categories"]


def test_wrong_event_at_a_gold_occurred_session_is_classified():
    assert _categories(_prediction(("housing_move", "D012")))[
        "wrong_event_at_gold_occurred_session"
    ] == 1


def test_correct_event_at_a_wrong_occurred_session_is_classified():
    assert _categories(_prediction(("career_employment", "D030")))[
        "correct_event_at_wrong_occurred_session"
    ] == 1


def test_a_gold_session_with_the_wrong_label_outranks_the_wrong_session_bucket():
    """D026 is housing_move's anchor, so a career label there is the wrong event."""

    categories = _categories(_prediction(("career_employment", "D026")))
    assert categories["wrong_event_at_gold_occurred_session"] == 1
    assert categories["correct_event_at_wrong_occurred_session"] == 0


def test_an_unrelated_label_at_a_non_anchor_session_falls_through_to_other():
    assert _categories(_prediction(("relationship_marriage", "D030")))["other"] == 1


def test_prediction_at_a_cancellation_session_is_classified():
    assert _categories(_prediction(("housing_move", "D029")))[
        "prediction_at_cancellation_session"
    ] == 1


def test_prediction_at_a_hard_negative_is_its_own_bucket():
    """The distractor mass stays visible here, so anchoring on it is the

    negative control this condition exists to measure -- not an "other".
    """

    categories = _categories(_prediction(("housing_move", "D010")))
    assert categories["prediction_at_hard_negative_session"] == 1
    assert categories["other"] == 0


def test_a_gold_label_on_a_hard_negative_still_reports_the_distractor_anchor():
    categories = _categories(_prediction(("career_employment", "D010")))
    assert categories["prediction_at_hard_negative_session"] == 1
    assert categories["correct_event_at_wrong_occurred_session"] == 0


def test_prediction_at_a_routine_session_falls_through_to_other():
    assert _categories(_prediction(("housing_move", "D002")))["other"] == 1


def test_every_false_positive_is_histogrammed_by_anchor_session_type():
    decomposition = classify_pair_errors(
        GOLD,
        _prediction(
            ("housing_move", "D010"),
            ("housing_move", "D029"),
            ("housing_move", "D002"),
            ("career_employment", "D026"),
        ),
        session_type_by_public_id=TYPES,
    )
    assert decomposition["false_positive_anchor_session_types"] == {
        "cancellation_evidence": 1,
        "hard_negative": 1,
        "occurred_evidence": 1,
        "routine_financial": 1,
    }


def test_duplicate_pair_is_classified_separately_from_content_errors():
    categories = _categories(
        _prediction(("career_employment", "D012"), ("career_employment", "D012"))
    )
    assert categories["duplicate_pair"] == 1
    assert categories["wrong_event_at_gold_occurred_session"] == 0
    assert categories["other"] == 0


def test_invalid_records_are_their_own_category():
    assert _categories(_prediction(invalid=2))["invalid_record"] == 2


def test_a_true_positive_enters_no_category():
    categories = _categories(
        _prediction(("career_employment", "D012"), ("housing_move", "D026"))
    )
    assert sum(categories.values()) == 0


def test_false_negatives_report_event_and_gold_session():
    decomposition = classify_pair_errors(
        GOLD, _prediction(("career_employment", "D012")), session_type_by_public_id=TYPES
    )
    assert decomposition["false_negatives"] == [
        {"event_id": "housing_move", "evidence_session_id": "D026"}
    ]


def test_no_partial_credit_for_a_sibling_label():
    """A sibling at the right session is a false positive, not a half point."""

    prediction = _prediction(("relationship_marriage", "D012"))
    metrics = pair_item_metrics(GOLD, prediction, session_type_by_public_id=TYPES)
    assert metrics["true_positive_pair_count"] == 0
    assert _categories(prediction)["wrong_event_at_gold_occurred_session"] == 1


# ---------------------------------------------------------------------------
# baseline comparison


def _baseline_row(*atoms: tuple[str, str], gold=GOLD, invalid: int = 0) -> dict:
    prediction = _prediction(*atoms, invalid=invalid)
    return {
        "item_id": f"{TRAJ_ID}_cp300_rq1",
        "trajectory_id": TRAJ_ID,
        "checkpoint_session_count": 300,
        "condition": "full_prefix",
        "provider": "openai",
        "model": "gpt-5.5",
        "n_visible_sessions": 300,
        "predicted_pairs": [
            {"event_id": e, "evidence_session_id": s} for e, s in atoms
        ],
        "gold_pairs": [{"event_id": e, "evidence_session_id": s} for e, s in gold],
        "metrics": pair_item_metrics(gold, prediction),
    }


def _comparison(baseline_row, ablated_prediction):
    metrics = pair_item_metrics(
        GOLD, ablated_prediction, session_type_by_public_id=TYPES
    )
    return compare_with_baseline(
        gold_pairs=GOLD,
        prediction=ablated_prediction,
        metrics=metrics,
        baseline_row=baseline_row,
        session_type_by_public_id=TYPES,
    )


def test_comparison_reuses_the_stored_full_prediction():
    baseline = _baseline_row(("career_employment", "D012"), ("housing_move", "D026"))
    comparison = _comparison(baseline, _prediction(("career_employment", "D012")))
    assert comparison["gold_identical_to_full"] is True
    assert comparison["baseline_file_row"]["model"] == "gpt-5.5"
    assert comparison["full_prefix"]["f1"] == 1.0


def test_comparison_deltas_are_ablated_minus_full():
    baseline = _baseline_row(("career_employment", "D012"), ("housing_move", "D026"))
    comparison = _comparison(baseline, _prediction(("career_employment", "D012")))
    # no_prospective: 1 TP of 1 predicted, 2 gold -> P 1.0, R 0.5, F1 2/3
    assert comparison["no_prospective"]["precision"] == 1.0
    assert comparison["no_prospective"]["recall"] == 0.5
    assert comparison["delta"]["precision"] == 0.0
    assert comparison["delta"]["recall"] == -0.5
    assert comparison["delta"]["f1"] == pytest.approx(2 / 3 - 1.0, abs=1e-6)


def test_comparison_reports_a_full_correct_pair_lost():
    baseline = _baseline_row(("career_employment", "D012"), ("housing_move", "D026"))
    comparison = _comparison(baseline, _prediction(("career_employment", "D012")))
    assert comparison["full_correct_pairs_lost"] == [
        {"event_id": "housing_move", "evidence_session_id": "D026"}
    ]
    assert comparison["pairs_retained_from_full"] == [
        {"event_id": "career_employment", "evidence_session_id": "D012"}
    ]
    assert comparison["prediction_count_change"] == -1


def test_comparison_reports_a_new_no_prospective_true_positive():
    baseline = _baseline_row(("career_employment", "D012"))
    comparison = _comparison(
        baseline, _prediction(("career_employment", "D012"), ("housing_move", "D026"))
    )
    assert comparison["new_no_prospective_true_positives"] == [
        {"event_id": "housing_move", "evidence_session_id": "D026"}
    ]
    assert comparison["delta"]["recall"] == 0.5


def test_comparison_reports_a_new_false_positive():
    baseline = _baseline_row(("career_employment", "D012"))
    comparison = _comparison(
        baseline,
        _prediction(("career_employment", "D012"), ("career_employment", "D026")),
    )
    assert comparison["new_no_prospective_false_positives"] == [
        {"event_id": "career_employment", "evidence_session_id": "D026"}
    ]


def test_comparison_classifies_a_cancellation_session_false_positive():
    baseline = _baseline_row(("career_employment", "D012"))
    comparison = _comparison(
        baseline, _prediction(("career_employment", "D012"), ("housing_move", "D029"))
    )
    assert comparison["cancelled_event_false_positives"] == [
        {"event_id": "housing_move", "evidence_session_id": "D029"}
    ]


def test_comparison_classifies_a_hard_negative_false_positive():
    baseline = _baseline_row(("career_employment", "D012"))
    comparison = _comparison(
        baseline, _prediction(("career_employment", "D012"), ("housing_move", "D010"))
    )
    assert comparison["hard_negative_false_positives"] == [
        {"event_id": "housing_move", "evidence_session_id": "D010"}
    ]
    assert comparison["cancelled_event_false_positives"] == []


def test_comparison_flags_a_baseline_with_different_gold():
    baseline = _baseline_row(
        ("career_employment", "D012"), gold=[("career_employment", "D012")]
    )
    comparison = _comparison(baseline, _prediction(("career_employment", "D012")))
    assert comparison["gold_identical_to_full"] is False
    assert comparison["gold_symmetric_difference"] == [
        {"event_id": "housing_move", "evidence_session_id": "D026"}
    ]


def test_find_baseline_row_rejects_an_ambiguous_file():
    rows = [_baseline_row(("career_employment", "D012")) for _ in range(2)]
    with pytest.raises(ValueError, match="expected exactly one"):
        find_baseline_row(rows, trajectory_id=TRAJ_ID, checkpoint=300)


def test_find_baseline_row_rejects_a_missing_checkpoint():
    rows = [_baseline_row(("career_employment", "D012"))]
    with pytest.raises(ValueError, match="no full_prefix baseline row"):
        find_baseline_row(rows, trajectory_id=TRAJ_ID, checkpoint=15)


# ---------------------------------------------------------------------------
# offline evaluator + audit end to end

# The fixture corpus tops out at cp30, which the diagnostic accepts directly:
# the condition runs at whatever checkpoint is named, so no 300-session fixture
# and no constant patching is needed.


@pytest.fixture
def fixture_run(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    write_jsonl(sessions_dir / f"sessions_{TRAJ_ID}.jsonl", SESSIONS)
    rq1_root = tmp_path / "rq1"
    items_path = rq1_root / "natural" / "progressive_items.jsonl"
    write_jsonl(items_path, (item.model_dump(mode="json") for item in ITEMS))
    taxonomy_path = rq1_root / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {"taxonomy": TAXONOMY, "taxonomy_hash": taxonomy_hash(TAXONOMY)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return sessions_dir, items_path, taxonomy_path


def _run_evaluator(monkeypatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["evaluate_rq1_pairs.py", *argv])
    from scripts import evaluate_rq1_pairs

    evaluate_rq1_pairs.main()


def test_no_prospective_mock_evaluation_drops_only_prospective_sessions(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    out = tmp_path / "no_prospective.jsonl"
    report = tmp_path / "no_prospective_report.json"
    _run_evaluator(
        monkeypatch,
        [
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--condition", "no_prospective",
            "--output", str(out),
            "--report", str(report),
        ],
    )

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["condition"] == "no_prospective"
    # still a cp30 item, five sessions shorter
    assert row["checkpoint_session_count"] == 30
    assert row["visible_session_count"] == 25
    assert row["visible_session_type_counts"] == {
        "consequence_session": 1,
        "hard_negative": 1,
        "occurred_evidence": 2,
        "routine_financial": 21,
    }
    # gold is the unchanged full-prefix gold
    assert row["gold_pairs"] == [
        {"event_id": "career_employment", "evidence_session_id": "D012"},
        {"event_id": "career_employment", "evidence_session_id": "D026"},
    ]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert len(payload["no_prospective"]) == 1
    rung = payload["no_prospective"][0]
    assert rung["visible_session_count"] == 25
    assert rung["removed_session_count"] == 5
    assert rung["metrics"]["gold_pair_count"] == 2


def test_no_prospective_gold_matches_the_full_prefix_run(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    full = tmp_path / "full.jsonl"
    _run_evaluator(
        monkeypatch,
        [
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--condition", "full_prefix",
            "--output", str(full),
            "--report", str(tmp_path / "full_report.json"),
        ],
    )
    ablated = tmp_path / "ablated.jsonl"
    _run_evaluator(
        monkeypatch,
        [
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--condition", "no_prospective",
            "--baseline-predictions", str(full),
            "--output", str(ablated),
            "--report", str(tmp_path / "ablated_report.json"),
        ],
    )

    full_row = json.loads(full.read_text(encoding="utf-8").splitlines()[0])
    ablated_row = json.loads(ablated.read_text(encoding="utf-8").splitlines()[0])
    assert ablated_row["gold_pairs"] == full_row["gold_pairs"]
    assert full_row["visible_session_count"] == 30
    assert ablated_row["visible_session_count"] == 25
    comparison = ablated_row["baseline_comparison"]
    assert comparison["gold_identical_to_full"] is True
    # both sides are the empty mock prediction
    assert comparison["delta"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0}


def test_no_prospective_requires_an_explicit_checkpoint(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    with pytest.raises(SystemExit, match="requires at least one"):
        _run_evaluator(
            monkeypatch,
            [
                "--items", str(items_path),
                "--sessions-dir", str(sessions_dir),
                "--taxonomy", str(taxonomy_path),
                "--condition", "no_prospective",
                "--output", str(tmp_path / "x.jsonl"),
                "--report", str(tmp_path / "x.json"),
            ],
        )


def test_no_prospective_runs_a_checkpoint_ladder(fixture_run, tmp_path, monkeypatch):
    """Several checkpoints in one run, one report rung each, in ladder order."""

    sessions_dir, items_path, taxonomy_path = fixture_run
    out = tmp_path / "ladder.jsonl"
    report = tmp_path / "ladder_report.json"
    _run_evaluator(
        monkeypatch,
        [
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "15",
            "--checkpoint", "30",
            "--condition", "no_prospective",
            "--output", str(out),
            "--report", str(report),
        ],
    )

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [row["checkpoint_session_count"] for row in rows] == [15, 30]
    # each rung is shorter than its own prefix, and the ladder still grows
    assert [row["visible_session_count"] for row in rows] == [13, 25]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert [r["checkpoint_session_count"] for r in payload["no_prospective"]] == [15, 30]
    # the standard per-checkpoint aggregate covers the ladder, so the trend is
    # readable from the report without re-deriving it from the prediction rows
    assert sorted(payload["per_checkpoint"]) == ["15", "30"]


def _substituted_sessions() -> list[dict]:
    """The fixture corpus with each prospective session neutralized in place.

    Mirrors what build_no_prospective_corpus.py produces: same ids, same
    positions, same count -- only the type and the content change.
    """

    return [
        {**record, "session_type": "routine_financial", "cue_annotations": []}
        if record["session_id"] in PROSPECTIVE_IDS
        else record
        for record in SESSIONS
    ]


def test_substituted_arm_renders_every_session_in_the_prefix(
    fixture_run, tmp_path, monkeypatch
):
    """The point of this arm: cp30 shows 30 sessions, not 25."""

    _, items_path, taxonomy_path = fixture_run
    substituted_dir = tmp_path / "substituted"
    write_jsonl(substituted_dir / f"sessions_{TRAJ_ID}.jsonl", _substituted_sessions())
    out = tmp_path / "subst.jsonl"
    report = tmp_path / "subst_report.json"
    _run_evaluator(
        monkeypatch,
        [
            "--items", str(items_path),
            "--sessions-dir", str(substituted_dir),
            "--taxonomy", str(taxonomy_path),
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--condition", "no_prospective_substituted",
            "--output", str(out),
            "--report", str(report),
        ],
    )

    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["visible_session_count"] == 30
    assert row["checkpoint_session_count"] == 30
    # nothing dropped -- the five prospective slots are now routine fillers
    assert row["visible_session_type_counts"]["routine_financial"] == 26
    assert not any(
        stype in row["visible_session_type_counts"]
        for stype in PROSPECTIVE_EVIDENCE_SESSION_TYPES
    )
    # and the ablation did not move what counts as correct
    assert row["gold_pairs"] == [
        {"event_id": "career_employment", "evidence_session_id": "D012"},
        {"event_id": "career_employment", "evidence_session_id": "D026"},
    ]
    rung = json.loads(report.read_text(encoding="utf-8"))["no_prospective"][0]
    assert rung["condition"] == "no_prospective_substituted"
    assert rung["removed_session_count"] == 0


def test_substituted_arm_refuses_an_unsubstituted_corpus(
    fixture_run, tmp_path, monkeypatch
):
    """Nothing in the render path would reveal the wrong --sessions-dir."""

    sessions_dir, items_path, taxonomy_path = fixture_run
    with pytest.raises(SystemExit, match="every prospective session substituted"):
        _run_evaluator(
            monkeypatch,
            [
                "--items", str(items_path),
                "--sessions-dir", str(sessions_dir),
                "--taxonomy", str(taxonomy_path),
                "--trajectory-id", TRAJ_ID,
                "--checkpoint", "30",
                "--condition", "no_prospective_substituted",
                "--output", str(tmp_path / "x.jsonl"),
                "--report", str(tmp_path / "x.json"),
            ],
        )


def test_surviving_prospective_sessions_finds_exactly_the_prospective_slots():
    assert surviving_prospective_sessions(PREFIX_30, BY_ID) == PROSPECTIVE_IDS
    substituted = {s["session_id"]: s for s in _substituted_sessions()}
    assert surviving_prospective_sessions(PREFIX_30, substituted) == []


def test_no_prospective_refuses_duplicate_items_for_one_checkpoint(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    write_jsonl(
        items_path,
        (
            {**item.model_dump(mode="json"), "trajectory_id": TRAJ_ID}
            for item in list(ITEMS) + list(ITEMS)
        ),
    )
    with pytest.raises(SystemExit, match="duplicates at"):
        _run_evaluator(
            monkeypatch,
            [
                "--items", str(items_path),
                "--sessions-dir", str(sessions_dir),
                "--taxonomy", str(taxonomy_path),
                "--checkpoint", "30",
                "--condition", "no_prospective",
                "--output", str(tmp_path / "x.jsonl"),
                "--report", str(tmp_path / "x.json"),
            ],
        )


def test_full_prefix_condition_is_unaffected(fixture_run, tmp_path, monkeypatch):
    """Gold moving to the full-prefix projection must not change full_prefix."""

    sessions_dir, items_path, taxonomy_path = fixture_run
    out = tmp_path / "full.jsonl"
    _run_evaluator(
        monkeypatch,
        [
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--output", str(out),
            "--report", str(tmp_path / "full_report.json"),
        ],
    )
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert [row["n_visible_sessions"] for row in rows] == [15, 30]
    assert rows[0]["gold_pairs"] == [
        {"event_id": "career_employment", "evidence_session_id": "D012"}
    ]


def test_no_prospective_audit_passes_on_the_fixture_corpus(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    pair_root = tmp_path / "rq1_pair_temp"

    # the natural protocol audit first: it writes the manifest whose prompt and
    # taxonomy hashes the no-prospective audit must match
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_rq1_pair_protocol.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--prompt", RQ1_PAIR_PROMPT_FILE,
            "--output-dir", str(pair_root / "audit"),
        ],
    )
    from scripts import audit_rq1_pair_protocol

    audit_rq1_pair_protocol.main()

    audit_dir = pair_root / "no_prospective" / "audit"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_rq1_pair_no_prospective.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--prompt", RQ1_PAIR_PROMPT_FILE,
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--output-dir", str(audit_dir),
        ],
    )
    from scripts import audit_rq1_pair_no_prospective

    audit_rq1_pair_no_prospective.main()

    decision = json.loads(
        (audit_dir / "no_prospective_decision.json").read_text(encoding="utf-8")
    )
    assert decision["decision"] == "PASS"
    assert decision["n_violations"] == 0
    assert decision["visible_session_count"] == 25
    assert decision["removed_session_count"] == 5
    assert decision["gold_pair_count"] == 2

    report = json.loads(
        (audit_dir / "no_prospective_audit.json").read_text(encoding="utf-8")
    )
    assert report["stats"]["visible_session_type_counts"] == {
        "consequence_session": 1,
        "hard_negative": 1,
        "occurred_evidence": 2,
        "routine_financial": 21,
    }
    # only the two prospective types are removed, and all of them are
    assert report["stats"]["removed_session_type_counts"] == {
        "upcoming_evidence": 2,
        "weak_signal_evidence": 3,
    }
    assert (audit_dir / "no_prospective_audit.md").read_text(encoding="utf-8")


def test_no_prospective_audit_fails_on_a_hash_mismatch(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    manifest = tmp_path / "protocol_manifest.json"
    manifest.write_text(
        json.dumps({"prompt_sha256": "deadbeef", "taxonomy_hash": "deadbeef"}),
        encoding="utf-8",
    )
    audit_dir = tmp_path / "mismatch"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_rq1_pair_no_prospective.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--prompt", RQ1_PAIR_PROMPT_FILE,
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--protocol-manifest", str(manifest),
            "--output-dir", str(audit_dir),
        ],
    )
    from scripts import audit_rq1_pair_no_prospective

    with pytest.raises(SystemExit) as excinfo:
        audit_rq1_pair_no_prospective.main()
    assert excinfo.value.code == 1
    report = json.loads(
        (audit_dir / "no_prospective_audit.json").read_text(encoding="utf-8")
    )
    codes = {violation["code"] for violation in report["violations"]}
    assert "prompt_hash_differs_from_protocol" in codes
    assert "taxonomy_hash_differs_from_protocol" in codes


def test_no_prospective_audit_fails_when_a_distractor_is_also_removed(
    fixture_run, tmp_path, monkeypatch
):
    """The distinguishing invariant: subtract prospective evidence and nothing else.

    Over-removal is exactly the defect this condition was redesigned to avoid,
    so the audit must catch it rather than report a smaller context as a pass.
    """

    sessions_dir, items_path, taxonomy_path = fixture_run
    from scripts import audit_rq1_pair_no_prospective as audit_module

    real_filter = audit_module.no_prospective_visible_ids

    def _over_remove(prefix_ids, sessions):
        return [
            sid
            for sid in real_filter(prefix_ids, sessions)
            if sessions[sid].get("session_type") != "hard_negative"
        ]

    monkeypatch.setattr(audit_module, "no_prospective_visible_ids", _over_remove)

    audit_dir = tmp_path / "over_removed"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_rq1_pair_no_prospective.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--prompt", RQ1_PAIR_PROMPT_FILE,
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--output-dir", str(audit_dir),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        audit_module.main()
    assert excinfo.value.code == 1
    report = json.loads(
        (audit_dir / "no_prospective_audit.json").read_text(encoding="utf-8")
    )
    codes = {violation["code"] for violation in report["violations"]}
    assert "hard_negative_not_preserved" in codes
    assert "retained_set_is_not_the_complement" in codes


# ---------------------------------------------------------------------------
# require-thinking-tokens gate


def _usage(thinking_tokens, source="output_tokens_details", truncated=False):
    return {
        "input_tokens": 100,
        "output_tokens": 20,
        "reasoning_tokens": None,
        "thinking_tokens": thinking_tokens,
        "thinking_tokens_source": source,
        "finish_reason": "max_tokens" if truncated else "end_turn",
        "truncated": truncated,
        "request_duration_ms": 10,
    }


ADAPTIVE_META = {
    "thinking_mode_applied": "adaptive",
    "reasoning_effort_applied": "xhigh",
    "streaming_used": True,
}


def _gate(usage, metadata=None, prediction=None):
    """Returns (fatal, metadata-gap) reason codes."""

    from scripts.evaluate_rq1_pairs import inference_contract_failures

    return inference_contract_failures(
        metadata if metadata is not None else dict(ADAPTIVE_META),
        usage,
        prediction if prediction is not None else _prediction(),
        provider="anthropic",
        thinking_mode="adaptive",
        reasoning_effort="xhigh",
    )


def _failures(usage, metadata=None, prediction=None):
    return _gate(usage, metadata, prediction)[0]


def test_gate_accepts_a_positive_thinking_count():
    assert _failures(_usage(4321)) == []


def test_a_missing_count_with_the_config_confirmed_is_a_gap_not_a_failure():
    """The provider applied everything asked; it just did not report the number.

    Charging this as a configuration failure would discard a valid measurement
    over a reporting gap, so the item stays scored and the run stays green.
    """

    failures, gaps = _gate(_usage(None, source="unavailable"))
    assert failures == []
    assert any(g.startswith("thinking_tokens_unavailable") for g in gaps)


def test_a_missing_count_is_fatal_when_the_config_is_not_confirmed():
    """No positive confirmation means an absent count proves nothing."""

    failures, gaps = _gate(
        _usage(None, source="unavailable"),
        metadata={**ADAPTIVE_META, "streaming_used": False},
    )
    assert gaps == []
    assert "streaming_not_used" in failures
    assert any(f.startswith("thinking_tokens_unavailable") for f in failures)


def test_a_missing_count_from_a_non_anthropic_provider_stays_fatal():
    from scripts.evaluate_rq1_pairs import inference_contract_failures

    failures, gaps = inference_contract_failures(
        {},
        _usage(None, source="unavailable"),
        _prediction(),
        provider="openai",
        thinking_mode=None,
        reasoning_effort=None,
    )
    assert gaps == []
    assert any(f.startswith("thinking_tokens_unavailable") for f in failures)


def test_gate_rejects_a_zero_thinking_count():
    failures = _failures(_usage(0))
    assert any(f.startswith("thinking_tokens_not_positive") for f in failures)


def test_gate_rejects_a_silent_fallback_to_the_non_thinking_shape():
    failures = _failures(
        _usage(None, source="unavailable"),
        metadata={
            "thinking_mode_applied": None,
            "reasoning_effort_applied": None,
            "streaming_used": False,
        },
    )
    assert "adaptive_thinking_not_applied" in failures
    assert "streaming_not_used" in failures
    assert any(f.startswith("reasoning_effort_not_applied") for f in failures)
    # a fallback to the non-thinking shape never earns the gap exception
    assert any(f.startswith("thinking_tokens_unavailable") for f in failures)


def test_gate_rejects_a_truncated_response():
    failures = _failures(_usage(4321, truncated=True))
    assert any(f.startswith("response_truncated") for f in failures)


def test_gate_rejects_invalid_records():
    failures = _failures(_usage(4321), prediction=_prediction(invalid=1))
    assert any(f.startswith("invalid_records") for f in failures)


def test_gate_rejects_a_parse_error():
    failures = _failures(
        _usage(4321), prediction=RQ1PairPrediction(parse_error="invalid_json")
    )
    assert any(f.startswith("parse_error") for f in failures)


def test_failed_gate_excludes_the_item_and_exits_non_zero(
    fixture_run, tmp_path, monkeypatch
):
    """The mock provider reports no thinking tokens, so the gate must fire."""

    sessions_dir, items_path, taxonomy_path = fixture_run
    out = tmp_path / "gated.jsonl"
    report = tmp_path / "gated_report.json"
    with pytest.raises(SystemExit, match="inference configuration error"):
        _run_evaluator(
            monkeypatch,
            [
                "--items", str(items_path),
                "--sessions-dir", str(sessions_dir),
                "--taxonomy", str(taxonomy_path),
                "--trajectory-id", TRAJ_ID,
                "--checkpoint", "30",
                "--condition", "no_prospective",
                "--require-thinking-tokens",
                "--output", str(out),
                "--report", str(report),
            ],
        )
    # artifacts are still written, and the failed item is not scored
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["inference_configuration_error"]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["item_count"] == 0
    assert payload["inference_configuration_errors"]


def test_provider_usage_never_reports_absent_thinking_as_zero():
    from scripts.evaluate_rq1_pairs import _provider_usage

    usage = _provider_usage(
        {
            "thinking_tokens": None,
            "thinking_tokens_source": "unavailable",
            "usage": {"input_tokens": 10, "output_tokens": 3},
        }
    )
    assert usage["thinking_tokens"] is None
    assert usage["thinking_tokens_source"] == "unavailable"


def test_provider_usage_reads_gemini_thoughts_as_thinking_tokens():
    from scripts.evaluate_rq1_pairs import _provider_usage

    usage = _provider_usage({"usage": {"thoughts_token_count": 199331}})
    assert usage["thinking_tokens"] == 199331
    assert usage["thinking_tokens_source"] == "provider_usage"


def test_provider_usage_marks_a_length_capped_openai_response_truncated():
    from scripts.evaluate_rq1_pairs import _provider_usage

    assert _provider_usage({"finish_reason": "length"})["truncated"] is True
    assert _provider_usage({"finish_reason": "stop"})["truncated"] is False


def test_no_test_in_this_module_touches_a_provider(monkeypatch):
    """Guard: the diagnostic must never reach a network client offline."""

    import fin_life_benchmark.llm.client as client_module

    def _boom(*args, **kwargs):
        raise AssertionError("no live provider call is allowed in tests")

    monkeypatch.setattr(client_module.LLMClient, "generate", _boom)
    assert Path(__file__).exists()


# ---------------------------------------------------------------------------
# sampling provenance
#
# This pilot runs one call per (model, condition, checkpoint), so whether the
# requested temperature actually reached the provider decides whether a number
# is reproducible or a single unmeasured draw. Two of the three models this
# protocol uses reject the parameter, so the fact has to survive into the
# artifacts instead of living only in prose.


def test_frontier_models_do_not_receive_the_requested_temperature():
    from fin_life_benchmark.llm.client import (
        _anthropic_supports_temperature,
        _openai_supports_temperature,
    )

    assert _anthropic_supports_temperature("claude-opus-5") is False
    assert _openai_supports_temperature("gpt-5.6-sol") is False


def test_provider_usage_reports_a_dropped_temperature_as_non_deterministic():
    from scripts.evaluate_rq1_pairs import _provider_usage

    usage = _provider_usage(
        {
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "temperature_requested": 0.0,
            "temperature_applied": None,
            "temperature_omission_reason": "model_rejects_temperature",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    assert usage["deterministic_sampling"] is False
    assert usage["temperature_omission_reason"] == "model_rejects_temperature"


def test_provider_usage_reports_an_honored_temperature_as_deterministic():
    from scripts.evaluate_rq1_pairs import _provider_usage

    usage = _provider_usage(
        {
            "provider": "gemini",
            "model": "gemini-3.1-pro-preview",
            "temperature_requested": 0.0,
            "temperature_applied": 0.0,
            "temperature_omission_reason": None,
            "usage": {"prompt_token_count": 10, "candidates_token_count": 5},
        }
    )
    assert usage["deterministic_sampling"] is True


def test_report_records_the_single_replicate_design(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    report = tmp_path / "sampling_report.json"
    _run_evaluator(
        monkeypatch,
        [
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--condition", "no_prospective",
            "--output", str(tmp_path / "s.jsonl"),
            "--report", str(report),
        ],
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["sampling"]["replicates_per_cell"] == 1
    assert payload["run_config"]["replicates_per_cell"] == 1
