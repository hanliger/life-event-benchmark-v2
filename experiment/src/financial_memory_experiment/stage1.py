"""Official Stage 1 contract for the experiment harness.

Stage 1 is cumulative occurred-event/evidence-session pair reconstruction.
Every 15 sessions the model receives the prefix visible so far and returns all
``(event_id, evidence_session_id)`` pairs established in that prefix.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fin_life_benchmark.benchmark.rq1_pair_models import RQ1_PAIR_STAGE

from .config import load_experiment_config
from .paths import ExperimentPaths
from .stage1_pairs import (
    HEADLINE_METRIC,
    MAX_OUTPUT_TOKENS,
    build_items,
    item_path_for,
)
from .util import read_jsonl


STAGE1 = RQ1_PAIR_STAGE
STAGE1_MAX_OUTPUT_TOKENS = MAX_OUTPUT_TOKENS
STAGE1_TOP_K = 10
STAGE1_API3_METHODS = (
    "fc_gpt_5_6_sol",
    "fc_claude_opus_4_8",
    "fc_gemini_3_1_pro",
)
STAGE1_SMALL4_METHODS = (
    "fc_gpt_5_6_terra",
    "fc_gpt_5_6_luna",
    "fc_claude_sonnet_4_6",
    "fc_gemini_3_5_flash",
)
STAGE1_METHOD9_METHODS = (
    "fc_claude_opus_4_8",
    "bm25_claude_opus_4_8",
    "dense_ge2_claude_opus_4_8",
    "mem0_claude_opus_4_8",
    "letta_claude_opus_4_8",
    "fc_openrouter_llama_4_maverick",
    "fc_openrouter_gpt_oss_120b",
    "fc_openrouter_qwen_3_5_122b_a10b",
    "fc_openrouter_qwen_3_6_35b_a3b_fp8",
)
STAGE1_EXECUTION_PROFILES = {
    "api3": {
        "methods": STAGE1_API3_METHODS,
        "request_timeout_seconds": 600,
        "parse_retries": 0,
    },
    "small4": {
        "methods": STAGE1_SMALL4_METHODS,
        "request_timeout_seconds": 600,
        "parse_retries": 0,
    },
    "method9": {
        "methods": STAGE1_METHOD9_METHODS,
        "request_timeout_seconds": 300,
        "parse_retries": 1,
    },
}

_SESSION_IN_PROMPT = re.compile(r"\[세션 D(\d{3,})\]")
CANDIDATE_HEADER = "## 가능한 Life Event 목록"
_GOLD_FIELD_NAMES = (
    '"gold"',
    "event_instance_id",
    "full_observed_ledger",
    "occurred_trajectory",
    "session_id_map",
    "status_anchor_session",
)


def stage1_contract(paths: ExperimentPaths | None = None) -> dict[str, Any]:
    """Validate the configured task, retrieval, and item-grid contract."""

    cfg = load_experiment_config(paths)
    section = cfg[STAGE1]
    contract = {
        "task_id": str(section["task_id"]),
        "retrieval_strategy": str(section["retrieval"]["strategy"]),
        "retrieval_top_k": int(section["retrieval"]["top_k"]),
        "max_output_tokens": int(section["max_output_tokens"]),
        "trajectories": int(section["expected"]["trajectories"]),
        "checkpoints": int(section["expected"]["checkpoints"]),
        "checkpoint_stride": int(cfg["benchmark"]["checkpoint_stride"]),
        "headline_metric": str(section["headline_metric"]),
        "execution_profiles": {
            str(profile_id): {
                "methods": tuple(map(str, profile["methods"])),
                "request_timeout_seconds": int(
                    profile["request_timeout_seconds"]
                ),
                "parse_retries": int(profile["parse_retries"]),
            }
            for profile_id, profile in section["execution_profiles"].items()
        },
    }
    if contract["task_id"] != STAGE1:
        raise ValueError(f"Stage 1 task_id must be {STAGE1}")
    if contract["retrieval_strategy"] != "single_question_query":
        raise ValueError("Stage 1 retrieval must use one task-level query")
    if contract["retrieval_top_k"] != STAGE1_TOP_K:
        raise ValueError(f"Stage 1 retrieval top_k must be {STAGE1_TOP_K}")
    if contract["max_output_tokens"] != STAGE1_MAX_OUTPUT_TOKENS:
        raise ValueError(
            f"Stage 1 max_output_tokens must be {STAGE1_MAX_OUTPUT_TOKENS}"
        )
    if contract["checkpoint_stride"] != 15:
        raise ValueError("official Stage 1 checkpoint stride must be 15")
    if contract["headline_metric"] != HEADLINE_METRIC:
        raise ValueError(f"Stage 1 headline metric must be {HEADLINE_METRIC}")
    if contract["execution_profiles"] != STAGE1_EXECUTION_PROFILES:
        raise ValueError(
            "official Stage 1 execution profiles must preserve the direct-API "
            "three-model run and the independent nine-method grid"
        )
    if (
        contract["trajectories"] <= 0
        or contract["checkpoints"] % contract["trajectories"]
    ):
        raise ValueError(
            "Stage 1 expects the same checkpoint grid in every trajectory"
        )
    if contract["checkpoints"] != int(
        cfg["dataset"]["expected"]["stage1_items"]
    ):
        raise ValueError(
            "Stage 1 expected checkpoints must equal dataset.stage1_items"
        )
    return contract


def stage1_item_path(paths: ExperimentPaths) -> Path:
    return item_path_for(paths)


def stage1_items(paths: ExperimentPaths) -> list[dict[str, Any]]:
    return list(read_jsonl(stage1_item_path(paths)))


def build_stage1_items(paths: ExperimentPaths) -> dict[str, Any]:
    contract = stage1_contract(paths)
    result = build_items(paths)
    if result["items"] != contract["checkpoints"]:
        raise ValueError(
            f"expected {contract['checkpoints']} Stage 1 items, "
            f"got {result['items']}"
        )
    if result["trajectories"] != contract["trajectories"]:
        raise ValueError(
            f"expected {contract['trajectories']} trajectories, "
            f"got {result['trajectories']}"
        )
    return result


def query_checkpoint(item: dict[str, Any]) -> int:
    metadata = item.get("metadata") or {}
    return int(
        metadata.get("query_checkpoint")
        or item.get("checkpoint_session_count")
        or len(item.get("visible_sessions") or [])
    )


def generation_item(item: dict[str, Any]) -> dict[str, Any]:
    """Remove evaluator-only Gold before prompt construction."""

    result = {key: value for key, value in item.items() if key != "gold"}
    result["metadata"] = {
        key: value
        for key, value in (item.get("metadata") or {}).items()
        if key not in {"occurred_event_count", "session_type_counts"}
    }
    return result


def visible_prefix_recall(
    *, item: dict[str, Any], evidence_session_ids: list[str]
) -> dict[str, Any]:
    """Gold-independent coverage of the visible dialogue prefix."""

    visible = set(map(str, item.get("visible_sessions") or []))
    retrieved = set(map(str, evidence_session_ids)) - {"S000"}
    hit = visible & retrieved
    return {
        "visible_prefix_size": len(visible),
        "retrieved_evidence_count": len(retrieved),
        "visible_prefix_recall": len(hit) / len(visible) if visible else None,
        "visible_prefix_complete": visible <= retrieved,
    }


def rendered_candidate_event_ids(prompt: str) -> list[str]:
    _, _, tail = prompt.partition(CANDIDATE_HEADER)
    if not tail:
        return []
    candidates: list[str] = []
    for line in tail.splitlines():
        if not line.startswith("- "):
            if candidates:
                break
            continue
        event_id, _, _ = line.removeprefix("- ").partition(":")
        candidates.append(event_id.strip())
    return candidates


def audit_rendered_prompt(rendered: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on canonical IDs, future sessions, Gold, or taxonomy drift."""

    item = rendered["item"]
    checkpoint = query_checkpoint(item)
    prompt = str(rendered["prompt"])
    groups_json = json.dumps(
        rendered.get("retrieval_groups") or [],
        ensure_ascii=False,
        sort_keys=True,
    )
    session_ids = [int(value) for value in _SESSION_IN_PROMPT.findall(prompt)]
    future_session_ids = [value for value in session_ids if value > checkpoint]
    expected_candidates = len(
        (item.get("metadata") or {}).get("candidate_events") or []
    )
    rendered_candidates = len(rendered_candidate_event_ids(prompt))
    gold_fields_in_prompt = sorted(
        name for name in _GOLD_FIELD_NAMES if name in prompt
    )
    gold_fields_in_retrieval_query = sorted(
        name for name in _GOLD_FIELD_NAMES if name in groups_json
    )
    canonical_ids = sorted(set(re.findall(r"\bS\d{3,}\b", prompt)))
    passed = (
        not future_session_ids
        and not canonical_ids
        and not gold_fields_in_prompt
        and not gold_fields_in_retrieval_query
        and rendered_candidates == expected_candidates
    )
    return {
        "method_id": rendered["method_id"],
        "trajectory_id": item["trajectory_id"],
        "checkpoint": checkpoint,
        "max_visible_session_id": max(session_ids, default=0),
        "future_session_ids": future_session_ids,
        "canonical_session_ids_in_prompt": canonical_ids,
        "gold_fields_in_prompt": gold_fields_in_prompt,
        "gold_fields_in_retrieval_query": gold_fields_in_retrieval_query,
        "expected_candidate_events": expected_candidates,
        "rendered_candidate_events": rendered_candidates,
        "candidate_events_exposed_by_task_prompt": True,
        "passed": passed,
    }
