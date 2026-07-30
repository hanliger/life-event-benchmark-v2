from __future__ import annotations

import json

import pytest

from financial_memory_experiment.methods.full_context import FullContextMethod
from financial_memory_experiment.methods.letta_adapter import (
    LettaContractDouble,
)
from financial_memory_experiment.methods.mem0_adapter import (
    InMemoryMem0Double,
    Mem0Method,
)
from financial_memory_experiment.methods.retrieval import (
    BM25Method,
    DenseMethod,
    HashEmbedder,
    regex_tokenize,
)
from financial_memory_experiment.prompts import (
    answer_output_tokens,
    expose_rendered_prompt,
)
from financial_memory_experiment.stage1 import (
    STAGE1,
    STAGE1_API3_METHODS,
    STAGE1_EXECUTION_PROFILES,
    STAGE1_MAX_OUTPUT_TOKENS,
    STAGE1_METHOD9_METHODS,
    STAGE1_TOP_K,
    audit_rendered_prompt,
    generation_item,
    rendered_candidate_event_ids,
    stage1_contract,
    visible_prefix_recall,
)
from financial_memory_experiment.paths import ExperimentPaths


S000 = {
    "trajectory_id": "traj_test",
    "session_id": "S000",
    "session_date": "2025-12-31",
    "state": {
        "employment.employer": {"status": "current", "value": "한빛테크"},
    },
}


def _session(number: int, text: str) -> dict[str, object]:
    return {
        "trajectory_id": "traj_test",
        "session_id": f"S{number:03d}",
        "session_date": f"2026-01-{number:02d}",
        "turns": [
            {"speaker": "user", "text": text},
            {"speaker": "assistant", "text": "반영하겠습니다."},
        ],
    }


CANDIDATE_EVENTS = [
    {"event_id": "E001", "label_ko": "이직"},
    {"event_id": "E002", "label_ko": "결혼"},
    {"event_id": "E003", "label_ko": "이사"},
]
STAGE1_ITEM = {
    "item_id": "traj_test_cp003_stage1",
    "stage": STAGE1,
    "trajectory_id": "traj_test",
    "prefix_id": "traj_test_w01",
    "question": (
        "지금까지 실제로 일어난 모든 Life Event와 각 발생을 처음 확정하는 "
        "상담 세션의 pair를 찾으시오."
    ),
    "checkpoint_session_count": 3,
    "visible_sessions": ["S001", "S002", "S003"],
    "gold": {
        "occurred_event_evidence_pairs": [
            {"event_id": "E002", "evidence_session_id": "D002"},
            {"event_id": "E003", "evidence_session_id": "D003"},
        ],
    },
    "metadata": {
        "query_checkpoint": 3,
        "candidate_events": CANDIDATE_EVENTS,
    },
}


class _CapturingReader:
    def __init__(self) -> None:
        self.user = ""
        self.max_tokens: int | None = None

    def generate(self, *, system, user, max_tokens=None):
        self.user = user
        self.max_tokens = max_tokens
        return (
            '{"pairs":[{"event_id":"E002","evidence_session_id":"D002"},'
            '{"event_id":"E003","evidence_session_id":"D003"}]}'
        ), {
            "provider": "capture",
            "model": "capture",
            "paid": False,
        }


def _ingested(method):
    method.ingest_initial(S000)
    method.ingest_session(_session(1, "이직을 고민 중입니다."))
    method.ingest_session(_session(2, "결혼식을 마쳤습니다."))
    method.ingest_session(_session(3, "서울로 이사를 완료했습니다."))
    return method


def _stage1_methods() -> list[tuple[object, _CapturingReader]]:
    result = []
    for factory in (
        lambda reader: FullContextMethod("fc_claude_opus_4_8", reader, "system"),
        lambda reader: BM25Method(
            reader,
            "system",
            k=STAGE1_TOP_K,
            k1=1.5,
            b=0.75,
            tokenizer=regex_tokenize,
            method_id="bm25_claude_opus_4_8",
        ),
        lambda reader: DenseMethod(
            reader,
            "system",
            HashEmbedder(),
            k=STAGE1_TOP_K,
            method_id="dense_ge2_claude_opus_4_8",
        ),
        lambda reader: Mem0Method(
            InMemoryMem0Double,
            reader,
            "system",
            trajectory_id="traj_test",
            k=STAGE1_TOP_K,
            method_id="mem0_claude_opus_4_8",
        ),
    ):
        reader = _CapturingReader()
        result.append((_ingested(factory(reader)), reader))
    return result


def test_stage1_output_budget_and_prompt_exposure_contract():
    assert answer_output_tokens(STAGE1_ITEM) == STAGE1_MAX_OUTPUT_TOKENS
    assert expose_rendered_prompt(STAGE1_ITEM) is True
    # Stage 2 and masking keep the config default so frozen outputs are stable.
    for stage in ("stage2_memory_value", "masking_lifecycle"):
        other = {**STAGE1_ITEM, "stage": stage}
        assert answer_output_tokens(other) is None
        assert expose_rendered_prompt(other) is False


def test_stage1_methods_expose_prompt_and_pass_leakage_audit():
    for method, reader in _stage1_methods():
        answer = method.answer(generation_item(STAGE1_ITEM))
        assert reader.max_tokens == STAGE1_MAX_OUTPUT_TOKENS
        prompt = answer.metadata["rendered_user_prompt"]
        assert prompt == reader.user
        assert answer.metadata["rendered_system_prompt"] == "system"
        check = audit_rendered_prompt(
            {
                "method_id": method.method_id,
                "item": STAGE1_ITEM,
                "prompt": prompt,
                "system_prompt": "system",
                "retrieval_groups": [],
            }
        )
        assert check["passed"], check
        assert check["rendered_candidate_events"] == len(CANDIDATE_EVENTS)
        assert check["gold_fields_in_prompt"] == []
        assert check["future_session_ids"] == []


def test_stage1_generation_item_drops_gold_and_gold_derived_metadata():
    generation = generation_item(STAGE1_ITEM)
    assert "gold" not in generation
    assert "occurred_event_count" not in generation["metadata"]
    assert generation["metadata"]["candidate_events"] == CANDIDATE_EVENTS
    # The original item is untouched so scoring still sees Gold.
    assert len(STAGE1_ITEM["gold"]["occurred_event_evidence_pairs"]) == 2


def test_stage1_audit_rejects_future_sessions_and_narrowed_candidates():
    base = {
        "method_id": "fc_claude_opus_4_8",
        "item": STAGE1_ITEM,
        "system_prompt": "system",
        "retrieval_groups": [],
    }
    good = FullContextMethod(
        "fc_claude_opus_4_8", _CapturingReader(), "system"
    )
    _ingested(good)
    prompt = good.answer(generation_item(STAGE1_ITEM)).metadata[
        "rendered_user_prompt"
    ]
    assert audit_rendered_prompt({**base, "prompt": prompt})["passed"]

    future = prompt + "\n[세션 D004]\n고객: 미래 세션"
    result = audit_rendered_prompt({**base, "prompt": future})
    assert result["future_session_ids"] == [4]
    assert not result["passed"]

    narrowed = prompt.replace("- E001: 이직\n", "")
    result = audit_rendered_prompt({**base, "prompt": narrowed})
    assert result["rendered_candidate_events"] == 2
    assert not result["passed"]

    leaked = prompt + "\nfull_observed_ledger"
    result = audit_rendered_prompt({**base, "prompt": leaked})
    assert result["gold_fields_in_prompt"] == ["full_observed_ledger"]
    assert not result["passed"]

    field_leak = prompt + '\n"gold": {"event_id": "E003"}'
    result = audit_rendered_prompt({**base, "prompt": field_leak})
    assert result["gold_fields_in_prompt"] == ['"gold"']
    assert not result["passed"]


def test_candidate_block_reader_ignores_initial_state_bullets():
    method = _ingested(
        FullContextMethod("fc_claude_opus_4_8", _CapturingReader(), "system")
    )
    prompt = method.answer(generation_item(STAGE1_ITEM)).metadata[
        "rendered_user_prompt"
    ]
    assert "S000" not in prompt
    assert rendered_candidate_event_ids(prompt) == ["E001", "E002", "E003"]


def test_letta_double_renders_stage1_prompt_without_evidence():
    double = _ingested(LettaContractDouble())
    answer = double.answer(generation_item(STAGE1_ITEM))
    prompt = answer.metadata["rendered_user_prompt"]
    assert "## 가능한 Life Event 목록" in prompt
    assert "[S001" not in prompt
    assert answer.metadata["archival_search_limit"] == 1


def test_visible_prefix_recall_is_gold_independent():
    full_context = visible_prefix_recall(
        item=STAGE1_ITEM,
        evidence_session_ids=["S000", "S001", "S002", "S003"],
    )
    assert full_context == {
        "visible_prefix_size": 3,
        "retrieved_evidence_count": 3,
        "visible_prefix_recall": 1.0,
        "visible_prefix_complete": True,
    }
    partial = visible_prefix_recall(
        item=STAGE1_ITEM,
        evidence_session_ids=["S000", "S002"],
    )
    assert partial["visible_prefix_recall"] == pytest.approx(1 / 3)
    assert partial["visible_prefix_complete"] is False
    missed = visible_prefix_recall(
        item=STAGE1_ITEM,
        evidence_session_ids=["S000"],
    )
    assert missed["visible_prefix_recall"] == 0.0
    assert missed["visible_prefix_complete"] is False


def test_stage1_config_contract_matches_frozen_constants():
    contract = stage1_contract(ExperimentPaths.discover())
    assert contract["task_id"] == STAGE1
    assert contract["retrieval_strategy"] == "single_question_query"
    assert contract["retrieval_top_k"] == STAGE1_TOP_K
    assert contract["max_output_tokens"] == STAGE1_MAX_OUTPUT_TOKENS
    assert contract["checkpoints"] == 400
    assert contract["trajectories"] == 20
    assert contract["headline_metric"] == "strict_occurred_event_evidence_f1"
    assert contract["execution_profiles"] == STAGE1_EXECUTION_PROFILES
    assert len(STAGE1_API3_METHODS) == 3
    assert len(STAGE1_METHOD9_METHODS) == 9


def test_stage1_retrieval_query_is_the_question_itself():
    method, reader = _stage1_methods()[1]
    method.answer(generation_item(STAGE1_ITEM))
    ranked = json.loads(
        json.dumps(
            method.answer(generation_item(STAGE1_ITEM)).metadata["retrieval"]
        )
    )
    # Stage 2.2's four-group retrieval must not leak into Stage 1.
    assert "retrieval_groups" not in reader.user
    assert len(ranked) <= STAGE1_TOP_K
    assert all(row["session_id"].startswith("S") for row in ranked)
