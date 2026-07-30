from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import load_experiment_config
from .corpus import corpus_root
from .paths import ExperimentPaths
from .util import read_jsonl


STAGE1 = "stage1_event_identification"
# Anthropic counts adaptive thinking against max_tokens, so the one-line
# <answer>E###</answer> contract still needs the Stage 2.2 reasoning budget.
STAGE1_MAX_OUTPUT_TOKENS = 20_000
# Frozen Stage 1 retrieval budget from docs/protocol.md (`top_k_main`).
STAGE1_TOP_K = 10

_SESSION_IN_PROMPT = re.compile(r"\[S(\d{3,})\s*\|")
CANDIDATE_HEADER = "[가능한 event_id]"
# Gold-only field names that must never reach a generation prompt.
_GOLD_FIELD_NAMES = (
    '"gold"',
    "event_instance_id",
    "target_event_id",
    "target_event_label",
    "target_event_status",
)


def stage1_contract(paths: ExperimentPaths | None = None) -> dict[str, Any]:
    """Fail closed if the config drifts from the frozen Stage 1 contract."""

    cfg = load_experiment_config(paths)
    section = cfg[STAGE1]
    contract = {
        "task_id": str(section["task_id"]),
        "retrieval_strategy": str(section["retrieval"]["strategy"]),
        "retrieval_top_k": int(section["retrieval"]["top_k"]),
        "max_output_tokens": int(section["max_output_tokens"]),
        "trajectories": int(section["expected"]["trajectories"]),
        "checkpoints": int(section["expected"]["checkpoints"]),
    }
    if contract["task_id"] != STAGE1:
        raise ValueError(f"Stage 1 task_id must be {STAGE1}")
    if contract["retrieval_strategy"] != "single_question_query":
        raise ValueError(
            "Stage 1 retrieval contract is a single question query; per-group "
            "retrieval belongs to Stage 2.2"
        )
    if contract["retrieval_top_k"] != STAGE1_TOP_K:
        raise ValueError(f"Stage 1 retrieval top_k must be {STAGE1_TOP_K}")
    if contract["max_output_tokens"] != STAGE1_MAX_OUTPUT_TOKENS:
        raise ValueError(
            f"Stage 1 max_output_tokens must be {STAGE1_MAX_OUTPUT_TOKENS}"
        )
    if (
        contract["trajectories"] <= 0
        or contract["checkpoints"] % contract["trajectories"]
    ):
        raise ValueError(
            "Stage 1 expects the same number of window checkpoints in every "
            "trajectory"
        )
    if contract["checkpoints"] != int(
        cfg["dataset"]["expected"]["stage1_items"]
    ):
        raise ValueError(
            "Stage 1 expected checkpoints must equal the frozen stage1_items "
            "count"
        )
    return contract


def stage1_item_path(paths: ExperimentPaths) -> Path:
    return corpus_root(paths) / "canonical_items" / f"{STAGE1}.jsonl"


def stage1_items(paths: ExperimentPaths) -> list[dict[str, Any]]:
    return list(read_jsonl(stage1_item_path(paths)))


def build_stage1_items(paths: ExperimentPaths) -> dict[str, Any]:
    """Materialize Stage 1 items from the no_prospective corpus."""

    from .items import build_stage1_rows
    from .util import sha256_file, write_jsonl

    contract = stage1_contract(paths)
    cfg = load_experiment_config(paths)
    rows = build_stage1_rows(
        paths,
        corpus_root(paths),
        stride=int(cfg["benchmark"]["checkpoint_stride"]),
    )
    if len(rows) != contract["checkpoints"]:
        raise ValueError(
            f"expected {contract['checkpoints']} Stage 1 items, got {len(rows)}"
        )
    trajectories = {str(row["trajectory_id"]) for row in rows}
    if len(trajectories) != contract["trajectories"]:
        raise ValueError(
            f"expected {contract['trajectories']} trajectories, "
            f"got {len(trajectories)}"
        )
    path = stage1_item_path(paths)
    write_jsonl(path, rows)
    return {
        "path": str(path),
        "items": len(rows),
        "trajectories": len(trajectories),
        "sha256": sha256_file(path),
        "corpus": "dialogues_no_prospective + gold_no_prospective",
    }


def query_checkpoint(item: dict[str, Any]) -> int:
    return int((item.get("metadata") or {})["query_checkpoint"])


def generation_item(item: dict[str, Any]) -> dict[str, Any]:
    """Strip Gold and Gold-derived annotations before rendering a prompt.

    `build_query` never renders item metadata, so this is defence in depth that
    also keeps `show-prompt` and the paid path byte-identical.
    """

    result = {key: value for key, value in item.items() if key != "gold"}
    result["metadata"] = {
        key: value
        for key, value in (item.get("metadata") or {}).items()
        if key not in {"target_event_status"}
    }
    return result


def target_window_session_ids(item: dict[str, Any]) -> list[str]:
    """Return the target window's session IDs, e.g. S016…S030."""

    metadata = item.get("metadata") or item
    start = int(str(metadata["target_session_start"]).removeprefix("S"))
    end = int(str(metadata["target_session_end"]).removeprefix("S"))
    return [f"S{number:03d}" for number in range(start, end + 1)]


def target_window_recall(
    *, item_metadata: dict[str, Any], evidence_session_ids: list[str]
) -> dict[str, Any]:
    """Gold-independent retrieval quality: did evidence cover the target window?

    Full Context necessarily scores 1.0 because it receives every session; the
    measure separates the retrieval arms from each other, not from Full Context.
    """

    window = set(target_window_session_ids(item_metadata))
    retrieved = {str(value) for value in evidence_session_ids}
    hit = window & retrieved
    return {
        "target_window_size": len(window),
        "retrieved_evidence_count": len(retrieved - {"S000"}),
        "target_window_recall": len(hit) / len(window) if window else None,
        "target_window_hit": bool(hit),
    }


def rendered_candidate_event_ids(prompt: str) -> list[str]:
    """Read back the candidate block so a narrowed list cannot pass unnoticed.

    Only the bullet lines that directly follow the candidate header count; the
    S000 initial-state block uses the same bullet shape elsewhere in the prompt.
    """

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
    """Fail closed on future sessions, Gold fields, or a narrowed candidate set."""

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
    gold_instance_id = str((item.get("gold") or {}).get("event_instance_id") or "")
    return {
        "method_id": rendered["method_id"],
        "trajectory_id": item["trajectory_id"],
        "checkpoint": checkpoint,
        "max_visible_session_id": max(session_ids, default=0),
        "future_session_ids": future_session_ids,
        "gold_fields_in_prompt": gold_fields_in_prompt,
        "gold_fields_in_retrieval_query": gold_fields_in_retrieval_query,
        "gold_event_instance_id_in_prompt": bool(
            gold_instance_id and gold_instance_id in prompt
        ),
        "expected_candidate_events": expected_candidates,
        "rendered_candidate_events": rendered_candidates,
        "candidate_events_exposed_by_task_prompt": True,
        "passed": (
            not future_session_ids
            and not gold_fields_in_prompt
            and not gold_fields_in_retrieval_query
            and not (gold_instance_id and gold_instance_id in prompt)
            and rendered_candidates == expected_candidates
        ),
    }
