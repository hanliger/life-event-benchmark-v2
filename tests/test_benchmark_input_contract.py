"""The evaluated model only receives answer-free dialogue and initial memory."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fin_life_benchmark.benchmark.stage2_memory import (
    Stage2QuestionPolicy,
    normalize_stage2_answer,
)

from scripts.evaluate_benchmark_items import (
    _build_stage2_prompt,
    _score_item,
    _visible_sessions,
)
from scripts.export_public_benchmark import public_item, public_session


def test_stage2_prompt_uses_dates_and_excludes_internal_session_metadata():
    session = {
        "trajectory_id": "traj_001",
        "session_id": "S001",
        "session_date": "2025-03-15",
        "turns": [
            {"speaker": "user", "text": "급여일은 25일이에요."},
            {"speaker": "assistant", "text": "확인했습니다."},
        ],
        "plan": {"secret_gold": "DO_NOT_LEAK"},
        "structured_context": {"future_answer": "DO_NOT_LEAK_EITHER"},
        "cue_annotations": [{"linked_memory_value": 25}],
    }
    item = {
        "question": "2025년 3월 15일 기준, 등록된 급여일은 며칠인가?",
        "options": [{"option_id": "A", "text": "25일"}],
        "metadata": {
            "answer_type": "mcq",
            "initial_memory": {
                "employment.salary_day": {
                    "value": 10,
                    "status": "current",
                    "historical_values": [],
                }
            },
        },
    }

    prompt = _build_stage2_prompt(item, [session])

    assert "급여일은 25일이에요" in prompt
    assert "[상담일 2025-03-15]" in prompt
    assert "[세션 S001]" not in prompt
    assert "employment.salary_day" in prompt
    assert "DO_NOT_LEAK" not in prompt
    assert "structured_context" not in prompt
    assert "cue_annotations" not in prompt


def test_stage2_free_response_normalizes_krw_surface_forms():
    item = {
        "stage": "stage2_memory_value",
        "metadata": {"answer_type": "free_response", "normalizer": "krw"},
        "gold": {
            "answer_type": "free_response",
            "answer_value": 3_000_000,
            "normalized_answer": "3000000",
        },
    }

    prediction, gold, correct, error = _score_item(
        item, '{"answer": "300만원"}'
    )

    assert prediction == "300만원"
    assert gold == 3_000_000
    assert correct is True
    assert error is None


def test_stage2_free_response_normalizes_composite_korean_krw_surfaces():
    cases = {
        "1억 2천만원": 120_000_000,
        "1억2천만원": 120_000_000,
        "3백만원": 3_000_000,
        "삼백만원": 3_000_000,
        "1.5억": 150_000_000,
        "2,500,000원": 2_500_000,
    }

    for surface, expected in cases.items():
        assert normalize_stage2_answer(surface, "krw") == str(expected)


def test_stage2_free_response_accepts_explicit_null_as_absent_value():
    item = {
        "stage": "stage2_memory_value",
        "metadata": {"answer_type": "free_response", "normalizer": "text"},
        "gold": {
            "answer_type": "free_response",
            "answer_value": None,
            "normalized_answer": "__none__",
        },
    }

    prediction, gold, correct, error = _score_item(item, '{"answer": null}')

    assert prediction is None
    assert gold is None
    assert correct is True
    assert error is None


def test_stage2_free_response_maps_korean_surface_to_internal_list_value():
    item = {
        "stage": "stage2_memory_value",
        "metadata": {
            "answer_type": "free_response",
            "normalizer": "string_list",
            "answer_aliases": {"주택담보대출": "mortgage"},
        },
        "gold": {
            "answer_type": "free_response",
            "answer_value": ["mortgage"],
            "normalized_answer": '["mortgage"]',
        },
    }

    prediction, gold, correct, error = _score_item(
        item, '{"answer": "주택담보대출"}'
    )

    assert prediction == "주택담보대출"
    assert gold == ["mortgage"]
    assert correct is True
    assert error is None


def test_session_lookup_is_trajectory_scoped():
    sessions = {
        ("traj_001", "S001"): {
            "trajectory_id": "traj_001",
            "session_id": "S001",
        },
        ("traj_002", "S001"): {
            "trajectory_id": "traj_002",
            "session_id": "S001",
        },
    }
    item = {"trajectory_id": "traj_001", "visible_sessions": ["S001"]}

    assert _visible_sessions(item, sessions)[0]["trajectory_id"] == "traj_001"


def test_public_exports_strip_private_annotations_and_gold_but_keep_date():
    session = {
        "session_id": "S001",
        "trajectory_id": "traj_001",
        "session_date": "2025-03-15",
        "turns": [{"speaker": "user", "text": "안녕하세요"}],
        "plan": {"structured_context": {"answer": 25}},
        "cue_annotations": [{"linked_memory_value": 25}],
    }
    item = {
        "item_id": "item_1",
        "stage": "stage2_memory_value",
        "trajectory_id": "traj_001",
        "prefix_id": "pfx1",
        "visible_sessions": ["S001"],
        "question": "질문",
        "options": [
            {
                "option_id": "A",
                "text": "보기",
                "correct": True,
                "error_type": None,
            }
        ],
        "gold": {"correct_option": "A"},
        "metadata": {
            "answer_type": "mcq",
            "checkpoint_date": "2025-03-15",
            "initial_memory": {},
            "initial_memory_source": {"secret": "DO_NOT_LEAK"},
        },
    }

    safe_session = public_session(session)
    safe_item = public_item(item)

    assert set(safe_session) == {
        "session_id",
        "trajectory_id",
        "session_date",
        "turns",
    }
    assert "gold" not in safe_item
    assert "initial_memory_source" not in safe_item["metadata"]
    assert safe_item["metadata"]["answer_type"] == "mcq"
    assert set(safe_item["options"][0]) == {"option_id", "text"}


def test_stage2_policy_excludes_disabled_paths_and_selectors():
    policy = Stage2QuestionPolicy()
    instance = SimpleNamespace(params={"property_id": "property_001"})

    assert policy.selector_specs("employment.salary_account", instance) == []
    assert policy.selector_specs("financial_products.loans", instance) == []
    assert [
        selector
        for selector, _, _ in policy.selector_specs("housing.properties", instance)
    ] == ["owned_count", "address", "ownership_status"]
    assert policy.selector_specs("financial_products.pension_or_irp", instance) == []


def test_stage2_child_education_question_is_valid_for_dated_reuse():
    policy = Stage2QuestionPolicy()

    question = policy.path_policy("education.child_education_stage")["question_ko"]

    assert "새로 반영된" not in question
    assert "기록된" in question
