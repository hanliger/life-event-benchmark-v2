"""Tests for the cp300 terminal-evidence-only RQ1 diagnostic.

Covers session-type filtering, the invariant that gold is the unchanged
full-prefix gold, the false-positive decomposition, the stored-baseline
comparison, and the offline evaluator + audit end to end. No network access.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fin_life_benchmark.benchmark.lifecycle_masking import TERMINAL_TYPES
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
from fin_life_benchmark.benchmark.rq1_pair_terminal_only import (
    TERMINAL_EVIDENCE_SESSION_TYPES,
    classify_pair_errors,
    compare_with_baseline,
    find_baseline_row,
    session_type_counts,
    terminal_only_visible_ids,
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


def test_terminal_set_is_the_shared_canonical_definition():
    assert TERMINAL_EVIDENCE_SESSION_TYPES == set(TERMINAL_TYPES)
    assert TERMINAL_EVIDENCE_SESSION_TYPES == {
        "occurred_evidence",
        "cancellation_evidence",
    }


def test_filter_retains_occurred_evidence():
    assert terminal_only_visible_ids(PREFIX_30, BY_ID) == ["S012", "S026"]


def test_filter_retains_cancellation_evidence():
    sessions = _with_cancellation()
    assert terminal_only_visible_ids(PREFIX_30, sessions) == ["S012", "S026", "S029"]


@pytest.mark.parametrize(
    "session_id, session_type",
    [
        ("S003", "weak_signal_evidence"),
        ("S007", "upcoming_evidence"),
        ("S014", "consequence_session"),
        ("S010", "hard_negative"),
        ("S002", "routine_financial"),
    ],
)
def test_filter_removes_every_non_terminal_type(session_id, session_type):
    assert BY_ID[session_id]["session_type"] == session_type
    assert session_id not in terminal_only_visible_ids(PREFIX_30, BY_ID)


def test_filter_removes_stale_recall_sessions():
    sessions = {sid: dict(record) for sid, record in BY_ID.items()}
    sessions["S016"] = {**sessions["S016"], "session_type": "stale_recall_session"}
    assert "S016" not in terminal_only_visible_ids(PREFIX_30, sessions)


def test_filter_preserves_original_public_ids_without_renumbering():
    item = ITEMS[1]
    id_map = dict(item.gold.session_id_map)
    retained = terminal_only_visible_ids(list(item.visible_sessions), BY_ID)
    # D### values are the ones the full prefix already used, not 1..n
    assert [id_map[sid] for sid in retained] == ["D012", "D026"]


def test_filter_is_chronological_even_from_unordered_input():
    shuffled = list(reversed(PREFIX_30))
    assert terminal_only_visible_ids(shuffled, BY_ID) == ["S012", "S026"]


def test_session_type_counts_reports_the_visible_histogram():
    sessions = _with_cancellation()
    retained = terminal_only_visible_ids(PREFIX_30, sessions)
    assert session_type_counts(retained, sessions) == {
        "cancellation_evidence": 1,
        "occurred_evidence": 2,
    }


def test_terminal_only_is_an_evaluator_condition():
    assert "terminal_only" in RQ1_PAIR_CONDITIONS
    assert "full_prefix" in RQ1_PAIR_CONDITIONS


# ---------------------------------------------------------------------------
# gold


def test_terminal_only_gold_equals_full_prefix_gold():
    item = ITEMS[1]
    id_map = dict(item.gold.session_id_map)
    prefix_ids = list(item.visible_sessions)
    retained = terminal_only_visible_ids(prefix_ids, BY_ID)

    full = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map={sid: id_map[sid] for sid in prefix_ids},
        sessions=BY_ID,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    terminal = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map={sid: id_map[sid] for sid in retained},
        sessions=BY_ID,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert terminal == full
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
        id_map[sid] for sid in terminal_only_visible_ids(prefix_ids, BY_ID)
    }
    assert {public for _, public in full} <= retained_public


def test_cancellation_sessions_create_no_gold_pair():
    sessions = _with_cancellation()
    item = ITEMS[1]
    id_map = dict(item.gold.session_id_map)
    retained = terminal_only_visible_ids(list(item.visible_sessions), sessions)
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


def _comparison(baseline_row, terminal_prediction):
    metrics = pair_item_metrics(
        GOLD, terminal_prediction, session_type_by_public_id=TYPES
    )
    return compare_with_baseline(
        gold_pairs=GOLD,
        prediction=terminal_prediction,
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


def test_comparison_deltas_are_terminal_minus_full():
    baseline = _baseline_row(("career_employment", "D012"), ("housing_move", "D026"))
    comparison = _comparison(baseline, _prediction(("career_employment", "D012")))
    # terminal: 1 TP of 1 predicted, 2 gold -> P 1.0, R 0.5, F1 2/3
    assert comparison["terminal_only"]["precision"] == 1.0
    assert comparison["terminal_only"]["recall"] == 0.5
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


def test_comparison_reports_a_new_terminal_only_true_positive():
    baseline = _baseline_row(("career_employment", "D012"))
    comparison = _comparison(
        baseline, _prediction(("career_employment", "D012"), ("housing_move", "D026"))
    )
    assert comparison["new_terminal_only_true_positives"] == [
        {"event_id": "housing_move", "evidence_session_id": "D026"}
    ]
    assert comparison["delta"]["recall"] == 0.5


def test_comparison_reports_a_new_false_positive():
    baseline = _baseline_row(("career_employment", "D012"))
    comparison = _comparison(
        baseline,
        _prediction(("career_employment", "D012"), ("career_employment", "D026")),
    )
    assert comparison["new_terminal_only_false_positives"] == [
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

# The fixture corpus tops out at cp30, so the end-to-end runs pin the diagnostic
# to that checkpoint via the module constant rather than shipping a 300-session
# fixture.
import fin_life_benchmark.benchmark.rq1_pair_terminal_only as terminal_only_module


@pytest.fixture
def fixture_run(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal_only_module, "TERMINAL_ONLY_CHECKPOINT", 30)
    from scripts import evaluate_rq1_pairs

    monkeypatch.setattr(evaluate_rq1_pairs, "TERMINAL_ONLY_CHECKPOINT", 30)

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


def test_terminal_only_mock_evaluation_renders_only_terminal_sessions(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    out = tmp_path / "terminal_only.jsonl"
    report = tmp_path / "terminal_only_report.json"
    _run_evaluator(
        monkeypatch,
        [
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--condition", "terminal_only",
            "--output", str(out),
            "--report", str(report),
        ],
    )

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["condition"] == "terminal_only"
    # still a cp30 item, with a shorter visible context
    assert row["checkpoint_session_count"] == 30
    assert row["visible_session_count"] == 2
    assert row["visible_session_type_counts"] == {"occurred_evidence": 2}
    # gold is the unchanged full-prefix gold
    assert row["gold_pairs"] == [
        {"event_id": "career_employment", "evidence_session_id": "D012"},
        {"event_id": "career_employment", "evidence_session_id": "D026"},
    ]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["terminal_only"]["visible_session_count"] == 2
    assert payload["terminal_only"]["metrics"]["gold_pair_count"] == 2


def test_terminal_only_gold_matches_the_full_prefix_run(
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
    terminal = tmp_path / "terminal.jsonl"
    _run_evaluator(
        monkeypatch,
        [
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--condition", "terminal_only",
            "--baseline-predictions", str(full),
            "--output", str(terminal),
            "--report", str(tmp_path / "terminal_report.json"),
        ],
    )

    full_row = json.loads(full.read_text(encoding="utf-8").splitlines()[0])
    terminal_row = json.loads(terminal.read_text(encoding="utf-8").splitlines()[0])
    assert terminal_row["gold_pairs"] == full_row["gold_pairs"]
    assert full_row["visible_session_count"] == 30
    assert terminal_row["visible_session_count"] == 2
    comparison = terminal_row["baseline_comparison"]
    assert comparison["gold_identical_to_full"] is True
    # both sides are the empty mock prediction
    assert comparison["delta"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0}


def test_terminal_only_requires_the_diagnostic_checkpoint(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    with pytest.raises(SystemExit, match="requires exactly --checkpoint"):
        _run_evaluator(
            monkeypatch,
            [
                "--items", str(items_path),
                "--sessions-dir", str(sessions_dir),
                "--taxonomy", str(taxonomy_path),
                "--condition", "terminal_only",
                "--output", str(tmp_path / "x.jsonl"),
                "--report", str(tmp_path / "x.json"),
            ],
        )


def test_terminal_only_refuses_more_than_one_item(fixture_run, tmp_path, monkeypatch):
    sessions_dir, items_path, taxonomy_path = fixture_run
    write_jsonl(
        items_path,
        (
            {**item.model_dump(mode="json"), "trajectory_id": TRAJ_ID}
            for item in list(ITEMS) + list(ITEMS)
        ),
    )
    with pytest.raises(SystemExit, match="evaluates exactly one item"):
        _run_evaluator(
            monkeypatch,
            [
                "--items", str(items_path),
                "--sessions-dir", str(sessions_dir),
                "--taxonomy", str(taxonomy_path),
                "--checkpoint", "30",
                "--condition", "terminal_only",
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


def test_terminal_only_audit_passes_on_the_fixture_corpus(
    fixture_run, tmp_path, monkeypatch
):
    sessions_dir, items_path, taxonomy_path = fixture_run
    pair_root = tmp_path / "rq1_pair_temp"

    # the natural protocol audit first: it writes the manifest whose prompt and
    # taxonomy hashes the terminal-only audit must match
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

    audit_dir = pair_root / "terminal_only" / "audit"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_rq1_pair_terminal_only.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--prompt", RQ1_PAIR_PROMPT_FILE,
            "--trajectory-id", TRAJ_ID,
            "--checkpoint", "30",
            "--output-dir", str(audit_dir),
        ],
    )
    from scripts import audit_rq1_pair_terminal_only

    audit_rq1_pair_terminal_only.main()

    decision = json.loads(
        (audit_dir / "terminal_only_decision.json").read_text(encoding="utf-8")
    )
    assert decision["decision"] == "PASS"
    assert decision["n_violations"] == 0
    assert decision["visible_session_count"] == 2
    assert decision["gold_pair_count"] == 2

    report = json.loads(
        (audit_dir / "terminal_only_audit.json").read_text(encoding="utf-8")
    )
    assert report["stats"]["visible_session_type_counts"] == {"occurred_evidence": 2}
    assert report["stats"]["removed_session_count"] == 28
    assert "weak_signal_evidence" in report["stats"]["removed_session_type_counts"]
    assert (audit_dir / "terminal_only_audit.md").read_text(encoding="utf-8")


def test_terminal_only_audit_fails_on_a_hash_mismatch(
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
            "audit_rq1_pair_terminal_only.py",
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
    from scripts import audit_rq1_pair_terminal_only

    with pytest.raises(SystemExit) as excinfo:
        audit_rq1_pair_terminal_only.main()
    assert excinfo.value.code == 1
    report = json.loads(
        (audit_dir / "terminal_only_audit.json").read_text(encoding="utf-8")
    )
    codes = {violation["code"] for violation in report["violations"]}
    assert "prompt_hash_differs_from_protocol" in codes
    assert "taxonomy_hash_differs_from_protocol" in codes


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


def _failures(usage, metadata=None, prediction=None):
    from scripts.evaluate_rq1_pairs import inference_contract_failures

    return inference_contract_failures(
        metadata if metadata is not None else dict(ADAPTIVE_META),
        usage,
        prediction if prediction is not None else _prediction(),
        provider="anthropic",
        thinking_mode="adaptive",
        reasoning_effort="xhigh",
    )


def test_gate_accepts_a_positive_thinking_count():
    assert _failures(_usage(4321)) == []


def test_gate_rejects_a_null_thinking_count():
    failures = _failures(_usage(None, source="unavailable"))
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
                "--condition", "terminal_only",
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
