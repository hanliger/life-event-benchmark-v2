"""Builder, parser and mock-evaluator tests for RQ1."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fin_life_benchmark.benchmark.rq1_builder import (
    build_gold_ledger,
    build_natural_items,
    build_public_taxonomy,
    build_stage1_pair_items,
    render_sessions_block,
    taxonomy_hash,
    visible_ids_for_condition,
)
from fin_life_benchmark.benchmark.rq1_models import (
    from_public_session_id,
    to_public_session_id,
)
from fin_life_benchmark.benchmark.rq1_parser import parse_prediction
from fin_life_benchmark.fsm.registry import load_life_event_templates
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


def test_exporter_produces_two_checkpoints():
    assert [p["checkpoint_session_count"] for p in PREFIXES] == [15, 30]


def test_gold_ledger_reconstruction_at_15():
    ledger, occurred = build_gold_ledger(PREFIXES[0]["gold_life_events"], BY_ID)
    assert [e.event_instance_id for e in ledger] == [f"{TRAJ_ID}_ev001"]
    ev = ledger[0]
    assert ev.event_status == "occurred"
    assert ev.first_evidence_session == "S003"
    assert ev.status_anchor_session == "S012"
    assert ev.core_evidence_sessions == ["S003", "S007", "S012"]
    assert ev.supporting_sessions == ["S014"]  # consequence, never core
    assert [e.event_instance_id for e in occurred] == [f"{TRAJ_ID}_ev001"]


def test_gold_ledger_at_30_preserves_repeated_event_ids_and_orders():
    ledger, occurred = build_gold_ledger(PREFIXES[1]["gold_life_events"], BY_ID)
    assert [e.event_instance_id for e in ledger] == [
        f"{TRAJ_ID}_ev001",
        f"{TRAJ_ID}_ev002",
        f"{TRAJ_ID}_ev003",
    ]
    assert [e.event_id for e in ledger] == [
        "career_employment",
        "career_employment",
        "housing_move",
    ]
    ev3 = ledger[2]
    assert ev3.event_status == "upcoming"
    # upcoming anchor = latest upcoming evidence
    assert ev3.status_anchor_session == "S028"
    # occurred projection ordered by anchor
    assert [e.status_anchor_session for e in occurred] == ["S012", "S026"]


def test_natural_items_grid_and_public_mapping():
    items = build_natural_items(
        PREFIXES, {TRAJ_ID: BY_ID}, taxonomy_digest="digest"
    )
    assert [i.checkpoint_session_count for i in items] == [15, 30]
    item = items[0]
    assert item.stage == "stage1_event_trajectory"
    assert item.visible_sessions == [f"S{i:03d}" for i in range(1, 16)]
    for sid, pub in item.gold.session_id_map.items():
        assert to_public_session_id(sid) == pub
        assert from_public_session_id(pub) == sid
    assert item.gold.accumulated_hard_negative_count == 1
    assert item.gold.accumulated_event_count == 1
    assert item.gold.input_session_count == 15
    assert item.gold.input_char_count > 0
    fr = item.gold.first_recoverable[f"{TRAJ_ID}_ev001"]
    assert fr["session_id"] is not None and fr["checkpoint"] in (15, 30)


def test_official_stage1_items_are_cumulative_pair_items():
    items = build_stage1_pair_items(
        PREFIXES, {TRAJ_ID: BY_ID}, taxonomy_digest="digest"
    )
    assert [item.stage for item in items] == [
        "stage1_occurred_event_evidence_pairs",
        "stage1_occurred_event_evidence_pairs",
    ]
    assert [len(item.visible_sessions) for item in items] == [15, 30]
    assert items[0].gold.occurred_event_evidence_pairs == [
        {
            "event_id": "career_employment",
            "evidence_session_id": "D012",
        }
    ]
    # The second checkpoint keeps the prior occurrence and adds the newly
    # established one; upcoming housing_move is not Gold.
    assert items[1].gold.occurred_event_evidence_pairs == [
        {
            "event_id": "career_employment",
            "evidence_session_id": "D012",
        },
        {
            "event_id": "career_employment",
            "evidence_session_id": "D026",
        },
    ]
    assert items[1].metadata["task_semantics"] == (
        "all_occurred_event_evidence_pairs_in_prefix"
    )


def test_empty_ledger_gold_is_valid():
    fake_prefix = {
        "trajectory_id": TRAJ_ID,
        "prefix_id": f"{TRAJ_ID}_pfx015",
        "checkpoint_session_count": 15,
        "visible_sessions": [f"S{i:03d}" for i in range(1, 16)],
        "gold_life_events": [],
    }
    items = build_natural_items(
        [fake_prefix], {TRAJ_ID: BY_ID}, taxonomy_digest="digest"
    )
    assert items[0].gold.full_observed_ledger == []
    assert items[0].gold.occurred_trajectory == []


def test_rendering_exposes_only_public_ids_and_turns():
    items = build_natural_items(PREFIXES, {TRAJ_ID: BY_ID}, taxonomy_digest="d")
    item = items[1]
    records = [BY_ID[sid] for sid in item.visible_sessions]
    rendered = render_sessions_block(records, item.gold.session_id_map)
    assert "[세션 D001]" in rendered and "[세션 D030]" in rendered
    for token in (
        "S001",
        TRAJ_ID,
        "session_type",
        "hard_negative",
        "near_miss",
        "cue",
        "persona",
        "month_index",
        "occurred",
    ):
        assert token not in rendered
    # every visible turn is present
    assert rendered.count("고객:") == 30 * 4


def test_conditions_select_expected_sessions():
    items = build_natural_items(PREFIXES, {TRAJ_ID: BY_ID}, taxonomy_digest="d")
    item = items[1]
    assert visible_ids_for_condition(item, "full_prefix") == item.visible_sessions
    assert visible_ids_for_condition(item, "last_15") == item.visible_sessions[-15:]
    oracle = visible_ids_for_condition(item, "oracle_evidence")
    assert oracle == ["S003", "S007", "S012", "S018", "S020", "S026", "S028"]
    with pytest.raises(ValueError):
        visible_ids_for_condition(item, "bogus")


def test_public_taxonomy_exposes_only_id_and_label():
    templates = load_life_event_templates()
    taxonomy = build_public_taxonomy(templates)
    assert len(taxonomy) == 24
    assert all(set(row) == {"event_id", "label_ko"} for row in taxonomy)
    assert taxonomy_hash(taxonomy) == taxonomy_hash(build_public_taxonomy(templates))


# ---------------------------------------------------------------------------
# parser

VISIBLE = {f"D{i:03d}": f"S{i:03d}" for i in range(1, 31)}
TAX_IDS = {row["event_id"] for row in TAXONOMY}


def _payload(events):
    return json.dumps({"events": events}, ensure_ascii=False)


def _event(**overrides):
    event = {
        "prediction_id": "P001",
        "event_id": "career_employment",
        "status": "occurred",
        "first_evidence_session_id": "D003",
        "status_anchor_session_id": "D012",
        "core_evidence_session_ids": ["D003", "D012"],
        "supporting_session_ids": [],
        "confidence": 0.9,
    }
    event.update(overrides)
    return event


def test_parser_accepts_valid_json_and_normalizes_ids():
    pred = parse_prediction(
        _payload([_event()]), visible_public_ids=VISIBLE, taxonomy_event_ids=TAX_IDS
    )
    assert pred.parse_error is None
    assert len(pred.events) == 1
    ev = pred.events[0]
    assert ev.first_evidence_session == "S003"
    assert ev.core_evidence_sessions == ["S003", "S012"]


def test_parser_rejects_unknown_event_id():
    pred = parse_prediction(
        _payload([_event(event_id="nonexistent_event")]),
        visible_public_ids=VISIBLE,
        taxonomy_event_ids=TAX_IDS,
    )
    assert pred.events == []
    assert pred.rejected_events
    assert any("unknown_event_id" in e for e in pred.validation_errors)


def test_parser_rejects_invalid_status_and_invisible_session():
    pred = parse_prediction(
        _payload(
            [
                _event(status="maybe"),
                _event(first_evidence_session_id="D099"),
                _event(status_anchor_session_id="S012"),
            ]
        ),
        visible_public_ids=VISIBLE,
        taxonomy_event_ids=TAX_IDS,
    )
    assert pred.events == []
    joined = "\n".join(pred.validation_errors)
    assert "invalid_status" in joined
    assert "not visible" in joined
    assert "invalid public session id" in joined


def test_parser_dedupes_within_field_and_preserves_duplicates_across_events():
    pred = parse_prediction(
        _payload(
            [
                _event(core_evidence_session_ids=["D003", "D003", "D012"]),
                _event(
                    first_evidence_session_id="D018",
                    status_anchor_session_id="D026",
                    core_evidence_session_ids=["D018", "D026"],
                ),
            ]
        ),
        visible_public_ids=VISIBLE,
        taxonomy_event_ids=TAX_IDS,
    )
    assert len(pred.events) == 2  # same event_id kept as two instances
    assert pred.events[0].core_evidence_sessions == ["S003", "S012"]


def test_parser_flags_malformed_output_without_inventing_predictions():
    pred = parse_prediction(
        "죄송합니다, 판단이 어렵습니다.",
        visible_public_ids=VISIBLE,
        taxonomy_event_ids=TAX_IDS,
    )
    assert pred.parse_error == "invalid_json"
    assert pred.events == []
    pred2 = parse_prediction(
        json.dumps({"other": []}), visible_public_ids=VISIBLE, taxonomy_event_ids=TAX_IDS
    )
    assert pred2.parse_error == "missing_events_list"


def test_parser_keeps_event_with_bad_confidence_but_logs_it():
    pred = parse_prediction(
        _payload([_event(confidence=1.7)]),
        visible_public_ids=VISIBLE,
        taxonomy_event_ids=TAX_IDS,
    )
    assert len(pred.events) == 1
    assert pred.events[0].confidence is None
    assert any("invalid_confidence" in e for e in pred.validation_errors)


def test_parser_flags_out_of_order_events():
    pred = parse_prediction(
        _payload(
            [
                _event(
                    first_evidence_session_id="D018",
                    status_anchor_session_id="D026",
                ),
                _event(),
            ]
        ),
        visible_public_ids=VISIBLE,
        taxonomy_event_ids=TAX_IDS,
    )
    assert len(pred.events) == 2
    assert "events_not_ordered_by_first_evidence" in pred.validation_errors


# ---------------------------------------------------------------------------
# mock end-to-end evaluation (no network)


def test_mock_evaluation_completes_offline(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    write_jsonl(sessions_dir / f"sessions_{TRAJ_ID}.jsonl", SESSIONS)

    rq1_root = tmp_path / "rq1"
    (rq1_root / "natural").mkdir(parents=True)
    items = build_natural_items(
        PREFIXES, {TRAJ_ID: BY_ID}, taxonomy_digest=taxonomy_hash(TAXONOMY)
    )
    write_jsonl(
        rq1_root / "natural" / "progressive_items.jsonl",
        (i.model_dump(mode="json") for i in items),
    )
    (rq1_root / "taxonomy.json").write_text(
        json.dumps(
            {"taxonomy": TAXONOMY, "taxonomy_hash": taxonomy_hash(TAXONOMY)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    out = tmp_path / "predictions.jsonl"
    report = tmp_path / "report.json"
    argv = [
        "evaluate_rq1.py",
        "--items", str(rq1_root / "natural" / "progressive_items.jsonl"),
        "--sessions-dir", str(sessions_dir),
        "--condition", "full_prefix",
        "--output", str(out),
        "--report", str(report),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    from scripts import evaluate_rq1

    evaluate_rq1.main()

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert all(row["response_metadata"]["provider"] == "mock" for row in rows)
    assert all(row["prediction"]["events"] == [] for row in rows)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["items"] == 2
    assert payload["prompt_sha256"]
    assert payload["aggregate"]["checkpoints"] == [15, 30]
    # empty mock predictions -> zero recall everywhere
    assert (
        payload["aggregate"]["per_checkpoint"]["30"]["macro_by_trajectory"][
            "full_ledger_event_recall"
        ]
        == 0.0
    )
    assert "progressive" in payload
