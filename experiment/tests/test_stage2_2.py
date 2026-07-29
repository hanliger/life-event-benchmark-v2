from __future__ import annotations

import copy
import json

import pytest

from financial_memory_experiment.prompts import build_query
from financial_memory_experiment.stage2_2 import (
    ALLOWED_STATUSES,
    STAGE2_2,
    VALUE_KINDS,
    initial_copy_score,
    parse_stage2_2_prediction,
    project_state,
    score_stage2_2,
)


def _state() -> dict[str, dict]:
    return {
        path: {
            "value": None,
            "status": "unknown",
            "evidence_session_ids": [],
        }
        for path in VALUE_KINDS
    }


def test_projection_removes_internal_property_ids():
    raw = {
        path: {"value": None, "status": "unknown"}
        for path in VALUE_KINDS
    }
    raw["housing.properties"] = {
        "value": [
            {
                "property_id": "property_secret",
                "address": "서울 중구",
                "role": "primary_residence",
                "mortgage_status": "active",
                "ownership_status": "owned",
                "acquisition_event_instance_id": "event_secret",
            }
        ],
        "status": "current",
    }
    raw["housing.primary_residence_property_id"] = {
        "value": "property_secret",
        "status": "current",
    }

    projected = project_state(raw)

    assert projected["housing.properties"]["value"] == [
        {
            "address": "서울 중구",
            "role": "primary_residence",
            "mortgage_status": "active",
            "ownership_status": "owned",
        }
    ]
    assert (
        projected["housing.primary_residence_property_id"]["value"]
        == projected["housing.properties"]["value"][0]
    )
    assert "property_secret" not in json.dumps(projected, ensure_ascii=False)
    assert "event_secret" not in json.dumps(projected, ensure_ascii=False)


def test_parser_requires_all_cells_but_preserves_valid_partial_output():
    payload = {
        "schema_version": "stage2_2_reconstruct-v1",
        "state": {
            path: {
                "value": None,
                "status": "unknown",
                "evidence_session_ids": [],
            }
            for path in VALUE_KINDS
        },
    }
    payload["state"]["employment.employer"]["evidence_session_ids"] = ["D046"]
    parsed = parse_stage2_2_prediction(
        json.dumps(payload, ensure_ascii=False), checkpoint=45
    )

    assert parsed["parse_error"] is None
    assert "employment.employer" not in parsed["state"]
    assert any("invalid_or_future_evidence" in error for error in parsed["validation_errors"])
    assert len(parsed["state"]) == len(VALUE_KINDS) - 1


def test_state_metrics_separate_changed_and_unchanged_behavior():
    initial = _state()
    gold = copy.deepcopy(initial)
    gold["employment.employer"] = {
        "value": "미래정보시스템",
        "status": "current",
        "evidence_session_ids": ["D015"],
    }
    prediction = {"state": copy.deepcopy(gold)}

    metrics = score_stage2_2(
        prediction=prediction,
        initial_state=initial,
        gold_state=gold,
    )

    assert metrics["final_state_accuracy"] == 1.0
    assert metrics["changed_state_accuracy"] == 1.0
    assert metrics["unchanged_state_accuracy"] == 1.0
    assert metrics["change_confusion"]["tp_correct"] == 1
    assert metrics["change_confusion"]["tn"] == len(VALUE_KINDS) - 1

    item = {"gold": {"initial_state": initial, "state": gold}}
    baseline = initial_copy_score(item)
    assert baseline["changed_state_accuracy"] == 0.0
    assert baseline["unchanged_state_accuracy"] == 1.0
    assert baseline["change_confusion"]["fn"] == 1


def test_stage2_2_prompt_exposes_schema_not_gold_values():
    item = {
        "stage": STAGE2_2,
        "question": "현재 상태를 복원하세요.",
    }
    evidence = [
        {
            "session_id": "S001",
            "session_date": "2026-01-01",
            "turns": [
                {"speaker": "user", "text": "회사 이름은 비공개예요."},
                {"speaker": "assistant", "text": "알겠습니다."},
            ],
        }
    ]

    query = build_query(item, evidence)

    assert "[D001 |" in query
    assert "employment.employer" in query
    assert ", ".join(ALLOWED_STATUSES) in query
    assert "미래정보시스템" not in query
    assert "JSON 객체 하나만" in query
