"""Tests for the temporary occurred-event evidence pair pilot.

Covers the strict parser, the occurred-anchor gold projection, the exact
multiset metric (including every error case the protocol pins down), checkpoint
aggregation, and the prompt-leakage audit. No network access.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fin_life_benchmark.benchmark.rq1_builder import (
    build_natural_items,
    taxonomy_hash,
)
from fin_life_benchmark.benchmark.rq1_models import RQ1GoldEventInstance
from fin_life_benchmark.benchmark.rq1_pair_metrics import (
    aggregate_pair_results,
    pair_item_metrics,
)
from fin_life_benchmark.benchmark.rq1_pair_models import (
    PAIR_CHECKPOINT_GRID,
    RQ1_PAIR_PROMPT_FILE,
    RQ1PairPrediction,
    RQ1PredictedPair,
    gold_pairs_from_occurred_trajectory,
    occurred_anchor_session,
)
from fin_life_benchmark.benchmark.rq1_pair_parser import parse_pair_prediction
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

VISIBLE_15 = {f"S{n:03d}": f"D{n:03d}" for n in range(1, 16)}
VISIBLE_30 = {f"S{n:03d}": f"D{n:03d}" for n in range(1, 31)}
PUBLIC_15 = set(VISIBLE_15.values())
PUBLIC_30 = set(VISIBLE_30.values())


def _prediction(*atoms: tuple[str, str], invalid: int = 0) -> RQ1PairPrediction:
    return RQ1PairPrediction(
        valid_pairs=[
            RQ1PredictedPair(event_id=event_id, evidence_session_id=session_id)
            for event_id, session_id in atoms
        ],
        invalid_record_count=invalid,
    )


def _raw(*records: dict) -> str:
    return json.dumps({"pairs": list(records)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# parser


def test_parser_accepts_a_valid_pair():
    prediction = parse_pair_prediction(
        _raw({"event_id": "career_employment", "evidence_session_id": "D012"}),
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.parse_error is None
    assert prediction.invalid_record_count == 0
    assert prediction.atoms() == [("career_employment", "D012")]


def test_parser_accepts_empty_pairs():
    prediction = parse_pair_prediction(
        '{"pairs": []}',
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.parse_error is None
    assert prediction.valid_pairs == []
    assert prediction.invalid_record_count == 0


@pytest.mark.parametrize(
    "record, expected_error",
    [
        ({"event_id": "not_an_event", "evidence_session_id": "D012"}, "unknown_event_id"),
        ({"event_id": "career_employment", "evidence_session_id": "D029"}, "session_id_not_visible"),
        ({"event_id": "career_employment", "evidence_session_id": "12"}, "malformed_evidence_session_id"),
        ({"event_id": "career_employment", "evidence_session_id": "S012"}, "malformed_evidence_session_id"),
        ({"evidence_session_id": "D012"}, "missing_event_id"),
        ({"event_id": "career_employment"}, "missing_evidence_session_id"),
    ],
)
def test_parser_rejects_one_record_per_bad_entry(record, expected_error):
    prediction = parse_pair_prediction(
        _raw(record),
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.valid_pairs == []
    assert prediction.invalid_record_count == 1
    assert any(expected_error in error for error in prediction.validation_errors)


def test_parser_rejects_non_object_entry():
    prediction = parse_pair_prediction(
        _raw("career_employment"),
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.invalid_record_count == 1
    assert any("not_an_object" in e for e in prediction.validation_errors)


def test_parser_charges_one_unit_for_many_field_errors():
    prediction = parse_pair_prediction(
        _raw({"event_id": "nope", "evidence_session_id": "D999"}),
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.invalid_record_count == 1
    assert len(prediction.validation_errors) >= 2


def test_parser_preserves_duplicate_records():
    record = {"event_id": "career_employment", "evidence_session_id": "D012"}
    prediction = parse_pair_prediction(
        _raw(record, record),
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.atoms() == [
        ("career_employment", "D012"),
        ("career_employment", "D012"),
    ]


def test_parser_reports_whole_response_failure():
    prediction = parse_pair_prediction(
        "죄송하지만 답변할 수 없습니다",
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.parse_error == "invalid_json"
    assert prediction.valid_pairs == []
    assert prediction.invalid_record_count == 0


def test_parser_flags_missing_pairs_list():
    prediction = parse_pair_prediction(
        '{"events": []}',
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.parse_error == "missing_pairs_list"


def test_parser_logs_extra_field_without_dropping_the_pair():
    prediction = parse_pair_prediction(
        _raw(
            {
                "event_id": "career_employment",
                "evidence_session_id": "D012",
                "status": "occurred",
            }
        ),
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.invalid_record_count == 0
    assert prediction.atoms() == [("career_employment", "D012")]
    assert any("unexpected_field" in e for e in prediction.validation_errors)


def test_parser_logs_unordered_pairs_without_penalty():
    prediction = parse_pair_prediction(
        _raw(
            {"event_id": "career_employment", "evidence_session_id": "D012"},
            {"event_id": "housing_move", "evidence_session_id": "D005"},
        ),
        visible_public_ids=PUBLIC_15,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert prediction.invalid_record_count == 0
    assert len(prediction.valid_pairs) == 2
    assert "pairs_not_ordered_by_evidence_session" in prediction.validation_errors


# ---------------------------------------------------------------------------
# gold projection


def test_anchor_is_the_establishing_occurred_evidence_session():
    assert (
        occurred_anchor_session(
            f"{TRAJ_ID}_ev001", sessions=BY_ID, visible_session_ids=VISIBLE_15
        )
        == "S012"
    )


def test_anchor_requires_visibility():
    with pytest.raises(ValueError, match="no visible establishing"):
        occurred_anchor_session(
            f"{TRAJ_ID}_ev002", sessions=BY_ID, visible_session_ids=VISIBLE_15
        )


def test_gold_at_15_holds_one_occurred_pair_in_public_ids():
    item = ITEMS[0]
    pairs = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map=item.gold.session_id_map,
        sessions=BY_ID,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert pairs == [("career_employment", "D012")]


def test_gold_excludes_weak_upcoming_and_consequence_sessions():
    """ev003 is only upcoming at 30 and its weak/upcoming sessions never appear."""

    item = ITEMS[1]
    pairs = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map=item.gold.session_id_map,
        sessions=BY_ID,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert pairs == [("career_employment", "D012"), ("career_employment", "D026")]
    sessions_in_gold = {public for _, public in pairs}
    # weak S003/S018/S020, upcoming S007/S028, consequence S014, hard negative S010
    assert sessions_in_gold.isdisjoint(
        {"D003", "D007", "D010", "D014", "D018", "D020", "D028"}
    )
    assert "housing_move" not in {event_id for event_id, _ in pairs}


def test_gold_preserves_repeated_event_id_at_distinct_anchors():
    item = ITEMS[1]
    pairs = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map=item.gold.session_id_map,
        sessions=BY_ID,
        taxonomy_event_ids=TAXONOMY_IDS,
    )
    assert [event_id for event_id, _ in pairs] == [
        "career_employment",
        "career_employment",
    ]
    assert len({public for _, public in pairs}) == 2


def test_gold_rejects_a_non_occurred_instance():
    cancelled = RQ1GoldEventInstance(
        event_instance_id=f"{TRAJ_ID}_ev003",
        event_id="housing_move",
        event_status="cancelled",
        first_evidence_session="S020",
        status_anchor_session="S028",
        core_evidence_sessions=["S020", "S028"],
    )
    with pytest.raises(ValueError, match="non-occurred instance"):
        gold_pairs_from_occurred_trajectory(
            [cancelled],
            session_id_map=VISIBLE_30,
            sessions=BY_ID,
            taxonomy_event_ids=TAXONOMY_IDS,
        )


def test_gold_rejects_an_instance_without_an_establishing_session():
    upcoming_only = RQ1GoldEventInstance(
        event_instance_id=f"{TRAJ_ID}_ev003",
        event_id="housing_move",
        event_status="occurred",  # claimed occurred, but no occurred_evidence exists
        first_evidence_session="S020",
        status_anchor_session="S028",
        core_evidence_sessions=["S020", "S028"],
    )
    with pytest.raises(ValueError, match="no visible establishing"):
        gold_pairs_from_occurred_trajectory(
            [upcoming_only],
            session_id_map=VISIBLE_30,
            sessions=BY_ID,
            taxonomy_event_ids=TAXONOMY_IDS,
        )


def test_gold_rejects_an_inactive_event_id():
    instance = RQ1GoldEventInstance(
        event_instance_id=f"{TRAJ_ID}_ev001",
        event_id="not_in_taxonomy",
        event_status="occurred",
        first_evidence_session="S003",
        status_anchor_session="S012",
        core_evidence_sessions=["S003", "S012"],
    )
    with pytest.raises(ValueError, match="outside the active taxonomy"):
        gold_pairs_from_occurred_trajectory(
            [instance],
            session_id_map=VISIBLE_15,
            sessions=BY_ID,
            taxonomy_event_ids=TAXONOMY_IDS,
        )


# ---------------------------------------------------------------------------
# strict metric


GOLD_A = [("A", "D010")]


def _counts(gold, prediction):
    metrics = pair_item_metrics(gold, prediction)
    return (
        metrics["true_positive_pair_count"],
        metrics["false_positive_pair_count"],
        metrics["false_negative_pair_count"],
    )


def test_metric_perfect_pair():
    prediction = _prediction(("A", "D010"))
    metrics = pair_item_metrics(GOLD_A, prediction)
    assert _counts(GOLD_A, prediction) == (1, 0, 0)
    assert metrics["strict_occurred_event_evidence_precision"] == 1.0
    assert metrics["strict_occurred_event_evidence_recall"] == 1.0
    assert metrics["strict_occurred_event_evidence_f1"] == 1.0
    assert metrics["exact_pair_multiset_match"] == 1.0


def test_metric_wrong_event_at_correct_session():
    assert _counts(GOLD_A, _prediction(("B", "D010"))) == (0, 1, 1)


def test_metric_correct_event_at_wrong_session():
    assert _counts(GOLD_A, _prediction(("A", "D011"))) == (0, 1, 1)


def test_metric_duplicate_prediction_adds_a_false_positive():
    prediction = _prediction(("A", "D010"), ("A", "D010"))
    assert _counts(GOLD_A, prediction) == (1, 1, 0)
    assert pair_item_metrics(GOLD_A, prediction)["duplicate_prediction_count"] == 1


def test_metric_extra_sibling_adds_a_false_positive():
    assert _counts(GOLD_A, _prediction(("A", "D010"), ("B", "D010"))) == (1, 1, 0)


def test_metric_missing_pair_adds_a_false_negative():
    gold = [("A", "D010"), ("A", "D015")]
    assert _counts(gold, _prediction(("A", "D010"))) == (1, 0, 1)


def test_metric_invalid_record_adds_one_false_positive():
    metrics = pair_item_metrics(GOLD_A, _prediction(("A", "D010"), invalid=1))
    assert metrics["true_positive_pair_count"] == 1
    assert metrics["false_positive_pair_count"] == 1
    assert metrics["strict_occurred_event_evidence_precision"] == 0.5
    assert metrics["strict_occurred_event_evidence_recall"] == 1.0
    assert metrics["exact_pair_multiset_match"] == 0.0


@pytest.mark.parametrize(
    "session, why",
    [
        ("D003", "weak_signal_evidence"),
        ("D007", "upcoming_evidence"),
        ("D014", "consequence_session"),
        ("D010", "hard_negative"),
        ("D002", "routine_financial"),
    ],
)
def test_metric_non_occurrence_evidence_is_a_false_positive(session, why):
    """Weak, upcoming, consequence and distractor anchors earn nothing."""

    gold = [("career_employment", "D012")]
    assert _counts(gold, _prediction(("career_employment", session))) == (0, 1, 1)


def test_metric_cancelled_event_and_cancellation_evidence_are_false_positives():
    """A cancelled plan is never gold, so committing to it costs precision."""

    gold = [("career_employment", "D012")]
    prediction = _prediction(
        ("career_employment", "D012"),  # correct
        ("housing_move", "D030"),  # cancelled event at its cancellation session
    )
    metrics = pair_item_metrics(
        gold,
        prediction,
        session_type_by_public_id={
            "D012": "occurred_evidence",
            "D030": "cancellation_evidence",
        },
    )
    assert metrics["true_positive_pair_count"] == 1
    assert metrics["false_positive_pair_count"] == 1
    assert metrics["strict_occurred_event_evidence_precision"] == 0.5
    assert metrics["false_positive_evidence_type_cancellation_evidence"] == 1


def test_metric_counts_false_positive_evidence_types():
    gold = [("career_employment", "D012")]
    prediction = _prediction(
        ("career_employment", "D012"),
        ("career_employment", "D007"),
        ("housing_move", "D010"),
    )
    metrics = pair_item_metrics(
        gold,
        prediction,
        session_type_by_public_id={
            "D007": "upcoming_evidence",
            "D010": "hard_negative",
            "D012": "occurred_evidence",
        },
    )
    assert metrics["false_positive_evidence_type_upcoming_evidence"] == 1
    assert metrics["false_positive_evidence_type_hard_negative"] == 1
    assert metrics["false_positive_pair_count"] == 2


def test_metric_is_order_independent():
    gold = [("A", "D010"), ("B", "D020")]
    forward = pair_item_metrics(gold, _prediction(("A", "D010"), ("B", "D020")))
    reverse = pair_item_metrics(gold, _prediction(("B", "D020"), ("A", "D010")))
    assert forward == reverse
    assert forward["strict_occurred_event_evidence_f1"] == 1.0


def test_metric_empty_gold_and_empty_prediction_is_perfect():
    metrics = pair_item_metrics([], _prediction())
    assert metrics["strict_occurred_event_evidence_precision"] == 1.0
    assert metrics["strict_occurred_event_evidence_recall"] == 1.0
    assert metrics["strict_occurred_event_evidence_f1"] == 1.0


def test_metric_non_empty_gold_and_empty_prediction_is_zero():
    metrics = pair_item_metrics(GOLD_A, _prediction())
    assert metrics["strict_occurred_event_evidence_precision"] == 0.0
    assert metrics["strict_occurred_event_evidence_recall"] == 0.0
    assert metrics["strict_occurred_event_evidence_f1"] == 0.0


def test_metric_empty_gold_and_non_empty_prediction_scores_zero_f1():
    metrics = pair_item_metrics([], _prediction(("A", "D010")))
    assert metrics["strict_occurred_event_evidence_precision"] == 0.0
    assert metrics["strict_occurred_event_evidence_recall"] == 0.0
    assert metrics["strict_occurred_event_evidence_f1"] == 0.0


def test_metric_parse_error_scores_all_gold_as_false_negatives():
    prediction = RQ1PairPrediction(parse_error="invalid_json")
    gold = [("A", "D010"), ("B", "D020")]
    metrics = pair_item_metrics(gold, prediction)
    assert metrics["false_negative_pair_count"] == 2
    assert metrics["false_positive_pair_count"] == 0
    assert metrics["strict_occurred_event_evidence_f1"] == 0.0
    assert metrics["parse_error"] == "invalid_json"


def test_metric_count_bias_is_signed():
    over = pair_item_metrics(GOLD_A, _prediction(("A", "D010"), ("B", "D011")))
    under = pair_item_metrics([("A", "D010"), ("B", "D020")], _prediction())
    assert over["signed_pair_count_bias"] == 1
    assert over["absolute_pair_count_error"] == 1
    assert under["signed_pair_count_bias"] == -2
    assert under["absolute_pair_count_error"] == 2


# ---------------------------------------------------------------------------
# aggregation


def _result(trajectory_id: str, checkpoint: int, gold, prediction):
    return {
        "trajectory_id": trajectory_id,
        "checkpoint_session_count": checkpoint,
        "metrics": pair_item_metrics(gold, prediction),
    }


def test_aggregation_macro_averages_trajectories_within_a_checkpoint():
    results = [
        _result("traj_001", 15, GOLD_A, _prediction(("A", "D010"))),
        _result("traj_002", 15, GOLD_A, _prediction()),
    ]
    summary = aggregate_pair_results(results)
    macro = summary["per_checkpoint"]["15"]["macro_by_trajectory"]
    assert summary["per_checkpoint"]["15"]["n_trajectories"] == 2
    assert macro["strict_occurred_event_evidence_f1"] == 0.5


def test_aggregation_reports_final_at_300_and_equal_weight_auc():
    # perfect at every checkpoint except 300, where recall is halved
    results = [
        _result("traj_001", cp, GOLD_A, _prediction(("A", "D010")))
        for cp in PAIR_CHECKPOINT_GRID[:-1]
    ]
    results.append(
        _result(
            "traj_001",
            300,
            [("A", "D010"), ("A", "D015")],
            _prediction(("A", "D010")),
        )
    )
    summary = aggregate_pair_results(results)
    assert summary["checkpoints"] == list(PAIR_CHECKPOINT_GRID)
    assert summary["n_checkpoints"] == 20
    assert summary["final_checkpoint"] == 300
    final_f1 = summary["final_at_300"]["strict_occurred_event_evidence_f1"]
    assert final_f1 == pytest.approx(2 / 3)
    # equal weighting: 19 checkpoints at 1.0 and one at 2/3
    assert summary["checkpoint_macro_auc"][
        "strict_occurred_event_evidence_f1"
    ] == pytest.approx((19 * 1.0 + 2 / 3) / 20)


def test_aggregation_does_not_pool_atoms_across_checkpoints():
    """A checkpoint with many atoms must not outweigh one with few."""

    results = [
        # 1 gold atom, perfect
        _result("traj_001", 15, GOLD_A, _prediction(("A", "D010"))),
        # 10 gold atoms, all missed
        _result(
            "traj_001",
            30,
            [("A", f"D{n:03d}") for n in range(20, 30)],
            _prediction(),
        ),
    ]
    summary = aggregate_pair_results(results)
    auc = summary["checkpoint_macro_auc"]["strict_occurred_event_evidence_f1"]
    # equal-checkpoint weighting -> 0.5, not the atom-pooled 1/11
    assert auc == pytest.approx(0.5)
    assert summary["per_checkpoint"]["15"]["micro_by_pair_atom"][
        "strict_occurred_event_evidence_f1"
    ] == 1.0


# ---------------------------------------------------------------------------
# prompt audit


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_TEXT = (REPO_ROOT / RQ1_PAIR_PROMPT_FILE).read_text(encoding="utf-8")


def test_prompt_has_no_concrete_session_id_literal():
    from scripts.audit_rq1_pair_protocol import PUBLIC_ID_RE

    assert PUBLIC_ID_RE.findall(PROMPT_TEXT) == []


def test_prompt_has_no_active_event_id():
    assert not [row["event_id"] for row in TAXONOMY if row["event_id"] in PROMPT_TEXT]


def test_prompt_has_no_old_leak_combination():
    from scripts.audit_rq1_pair_protocol import OLD_LEAK_TOKENS

    assert [token for token in OLD_LEAK_TOKENS if token in PROMPT_TEXT] == []


def test_prompt_only_instantiated_example_is_empty():
    from scripts.audit_rq1_pair_protocol import JSON_BLOCK_RE, is_placeholder_block

    instantiated = []
    for block in JSON_BLOCK_RE.findall(PROMPT_TEXT):
        block = block.strip()
        if is_placeholder_block(block):
            continue
        instantiated.append(json.loads(block))
    assert instantiated == [{"pairs": []}]


def test_prompt_states_occurred_only_and_excludes_the_other_statuses():
    from scripts.audit_rq1_pair_protocol import REQUIRED_PROMPT_PHRASES

    missing = [
        name
        for name, phrase in REQUIRED_PROMPT_PHRASES.items()
        if phrase not in PROMPT_TEXT
    ]
    assert missing == []


def test_prompt_carries_no_lifecycle_or_gold_vocabulary():
    from scripts.audit_rq1_pair_protocol import FORBIDDEN_PROMPT_TOKENS

    assert [token for token in FORBIDDEN_PROMPT_TOKENS if token in PROMPT_TEXT] == []


# ---------------------------------------------------------------------------
# offline evaluator + audit end to end


def _write_run(tmp_path: Path) -> tuple[Path, Path, Path]:
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


def test_mock_evaluation_completes_offline(tmp_path, monkeypatch):
    sessions_dir, items_path, taxonomy_path = _write_run(tmp_path)
    out = tmp_path / "predictions.jsonl"
    report = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_rq1_pairs.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--output", str(out),
            "--report", str(report),
        ],
    )
    from scripts import evaluate_rq1_pairs

    evaluate_rq1_pairs.main()

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert all(row["provider"] == "mock" for row in rows)
    assert all(row["predicted_pairs"] == [] for row in rows)
    assert rows[0]["gold_pairs"] == [
        {"event_id": "career_employment", "evidence_session_id": "D012"}
    ]
    # the model-visible prompt is never stored with gold in it, and gold is not
    # rendered: the mock response is the only thing the model "said"
    assert rows[0]["raw_response"] == '{"pairs": []}'

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["stage"] == "stage1_occurred_event_evidence_pairs"
    assert payload["protocol_version"] == "rq1-occurred-event-pairs-temp-v1"
    assert payload["metrics_version"] == "rq1-exact-occurred-pair-metrics-v1"
    assert payload["item_count"] == 2
    assert payload["run_config"]["prompt_sha256"]
    assert payload["checkpoints"] == [15, 30]
    # empty mock predictions against non-empty gold -> zero recall
    assert (
        payload["per_checkpoint"]["30"]["macro_by_trajectory"][
            "strict_occurred_event_evidence_recall"
        ]
        == 0.0
    )
    # the fixture stops at 30, so there is no 300 checkpoint to report
    assert payload["final_at_300"] is None
    assert payload["final_checkpoint"] == 30


def test_checkpoint_filter_selects_one_item(tmp_path, monkeypatch):
    sessions_dir, items_path, taxonomy_path = _write_run(tmp_path)
    out = tmp_path / "cp15.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_rq1_pairs.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--checkpoint", "15",
            "--output", str(out),
            "--report", str(tmp_path / "cp15_report.json"),
        ],
    )
    from scripts import evaluate_rq1_pairs

    evaluate_rq1_pairs.main()
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [row["checkpoint_session_count"] for row in rows] == [15]


def test_checkpoint_filter_rejects_a_missing_checkpoint(tmp_path, monkeypatch):
    sessions_dir, items_path, taxonomy_path = _write_run(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_rq1_pairs.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--checkpoint", "300",
            "--output", str(tmp_path / "none.jsonl"),
            "--report", str(tmp_path / "none.json"),
        ],
    )
    from scripts import evaluate_rq1_pairs

    with pytest.raises(SystemExit, match="no items for checkpoints"):
        evaluate_rq1_pairs.main()


def test_protocol_audit_passes_on_the_fixture_corpus(tmp_path, monkeypatch):
    sessions_dir, items_path, taxonomy_path = _write_run(tmp_path)
    audit_dir = tmp_path / "rq1_pair_temp" / "audit"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_rq1_pair_protocol.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--prompt", RQ1_PAIR_PROMPT_FILE,
            "--output-dir", str(audit_dir),
        ],
    )
    from scripts import audit_rq1_pair_protocol

    audit_rq1_pair_protocol.main()

    decision = json.loads(
        (audit_dir / "pair_protocol_decision.json").read_text(encoding="utf-8")
    )
    assert decision["decision"] == "PASS"
    assert decision["n_violations"] == 0
    report = json.loads(
        (audit_dir / "pair_protocol_audit.json").read_text(encoding="utf-8")
    )
    assert report["stats"]["n_gold_pairs"] == 3  # 1 at cp15 + 2 at cp30
    assert report["stats"]["gold_anchor_session_types"] == {"occurred_evidence": 3}
    manifest = json.loads(
        (tmp_path / "rq1_pair_temp" / "protocol_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["headline_metric"] == "strict_occurred_event_evidence_f1"
    assert manifest["audit_decision"] == "PASS"


def test_protocol_audit_fails_loudly_on_a_leaky_prompt(tmp_path, monkeypatch):
    sessions_dir, items_path, taxonomy_path = _write_run(tmp_path)
    leaky = tmp_path / "leaky_prompt.md"
    leaky.write_text(
        PROMPT_TEXT.replace(
            '{"pairs": []}',
            '{"pairs": [{"event_id": "career_employment", '
            '"evidence_session_id": "D010"}]}',
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_rq1_pair_protocol.py",
            "--items", str(items_path),
            "--sessions-dir", str(sessions_dir),
            "--taxonomy", str(taxonomy_path),
            "--prompt", str(leaky),
            "--output-dir", str(tmp_path / "audit_fail"),
        ],
    )
    from scripts import audit_rq1_pair_protocol

    with pytest.raises(SystemExit) as excinfo:
        audit_rq1_pair_protocol.main()
    assert excinfo.value.code == 1
    report = json.loads(
        (tmp_path / "audit_fail" / "pair_protocol_audit.json").read_text(
            encoding="utf-8"
        )
    )
    codes = {violation["code"] for violation in report["violations"]}
    assert "concrete_session_id_literal" in codes
    assert "active_event_id_in_prompt" in codes
    assert "old_leak_token" in codes
