from __future__ import annotations

import csv

from financial_memory_experiment.metrics import write_tables
from financial_memory_experiment.prompts import build_query, parse_answer
from financial_memory_experiment.stage3 import _normalize_stage3_item


def _stage3_item():
    return {
        "item_id": "traj_test_stage3",
        "stage": "stage3_multi_hop_mcq",
        "trajectory_id": "traj_test",
        "prefix_id": "traj_test_s030",
        "visible_sessions": [f"S{index:03d}" for index in range(1, 31)],
        "question": "두 날짜의 직장 순서는?",
        "options": [
            {"option_id": option, "text": option, "correct": option == "B"}
            for option in "ABCD"
        ],
        "gold": {
            "correct_option": "B",
            "hop_count": 2,
            "hops": [
                {
                    "checkpoint_session_count": 15,
                    "evidence_sessions": ["S014", "S015"],
                },
                {
                    "checkpoint_session_count": 30,
                    "evidence_sessions": ["S030"],
                },
            ],
        },
        "metadata": {
            "reasoning_type": "multi_hop",
            "derivation_type": "state_sequence",
            "first_visible_checkpoint": 30,
            "initial_memory": {"employment.employer": {"value": "old"}},
        },
    }


def test_stage3_item_uses_s000_once_and_exposes_both_hop_evidence():
    item = _normalize_stage3_item(_stage3_item())
    metadata = item["metadata"]
    assert "initial_memory" not in metadata
    assert metadata["initial_state_protocol"] == "S000_ingest_once"
    assert metadata["query_checkpoint"] == 30
    assert metadata["hop_checkpoints"] == [15, 30]
    assert metadata["evidence_sessions"] == ["S014", "S015", "S030"]


def test_stage3_prompt_and_parser_use_the_mcq_contract():
    item = _normalize_stage3_item(_stage3_item())
    prompt = build_query(item, [])
    assert "서로 다른 두 상담일의 근거를 모두 사용" in prompt
    assert "순서대로 연결하거나 합산" in prompt
    assert parse_answer(item, "<answer>B</answer>") == "B"


def test_stage3_derivation_table_is_written(tmp_path):
    report = {
        "completeness": {"reporting_ready": False},
        "methods": {
            "bm25_gemini_3_6": {
                "stage3_multi_hop_mcq": {
                    "items": 2,
                    "score": 0.5,
                    "ci95": [0.0, 1.0],
                    "parse_errors": 0,
                    "aggregation": "trajectory_macro",
                    "accuracy_by_derivation_type": {
                        "expense_aggregation": 1.0,
                        "state_sequence": 0.0,
                    },
                    "question_micro_accuracy_by_derivation_type": {
                        "expense_aggregation": 1.0,
                        "state_sequence": 0.0,
                    },
                    "accuracy_by_retention_lag_windows": {},
                }
            }
        },
        "paired_method_deltas": {},
    }
    write_tables(report, tmp_path)
    with (tmp_path / "stage3_by_derivation.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert {row["derivation_type"] for row in rows} == {
        "expense_aggregation",
        "state_sequence",
    }
    assert set(rows[0]) >= {
        "trajectory_macro_accuracy",
        "question_micro_accuracy",
    }
