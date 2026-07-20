"""Objective reliability, fidelity, quality, and efficiency audit for dialogues."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from statistics import median
from typing import Any

from ..fsm.models import LifeEventTemplate
from .dialogue_validator import DialogueValidator

_INTERNAL_RE = re.compile(r"(?:FA-\d{2}|[a-z_]+\.[a-z_]+|task_template_id|planned_cues|session_type)")
_GENERIC_CONFIRM = ("확인했습니다", "네, 가능합니다", "처리해 드렸습니다")
_UPCOMING_CURRENT_PHRASES = ("이미 적용", "현재 적용", "지금 바뀌었", "완료됐")


def _visible(session: dict[str, Any]) -> str:
    return " ".join(turn.get("text", "") for turn in session.get("turns") or [])


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-zA-Z가-힣 ]", " ", text.lower())).strip()


def _trigrams(text: str) -> set[str]:
    tokens = _normalized(text).split()
    return {" ".join(tokens[index:index + 3]) for index in range(max(0, len(tokens) - 2))}


def _is_substantive_utterance(text: str) -> bool:
    normalized = _normalized(text)
    compact = normalized.replace(" ", "")
    return len(compact) >= 12 or len(normalized.split()) >= 5


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def audit_dialogue_generation(
    plans: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    templates: dict[str, LifeEventTemplate],
    turns_min: int = 1,
    turns_max: int = 10_000,
    user_turns_min: int = 1,
    user_turns_max: int = 10_000,
) -> dict[str, Any]:
    plan_by_id = {item["session_id"]: item for item in plans}
    session_by_id = {item["session_id"]: item for item in sessions}
    validator = DialogueValidator(templates)
    violations: list[dict[str, Any]] = []
    quality_flags: Counter = Counter()
    repair_sessions = 0
    repair_attempts = 0
    latencies: list[float] = []
    input_tokens = output_tokens = cached_tokens = total_requests = 0
    tokens_by_type: dict[str, Counter] = defaultdict(Counter)
    tokens_by_status: dict[str, Counter] = defaultdict(Counter)
    dialogue_hashes: dict[str, list[str]] = defaultdict(list)
    trigram_sets: dict[str, set[str]] = {}
    repeated_utterance_session_ids: list[str] = []

    for session_id, session in session_by_id.items():
        plan = plan_by_id.get(session_id) or session.get("plan") or {}
        visible = _visible(session)
        session_validation = validator.validate_session(session)
        for item in session_validation:
            violations.append({"session_id": session_id, **item})
        if session.get("financial_task") != plan.get("financial_task"):
            violations.append({"session_id": session_id, "code": "event_status_task_mismatch", "detail": "generated task differs from plan"})
        expected_updates = (plan.get("structured_context") or {}).get("session_memory_updates") or []
        annotations = session.get("cue_annotations") or []
        for update in expected_updates:
            path_matches = [cue for cue in annotations if cue.get("cue_type") == "memory_fact" and cue.get("linked_memory_path") == update.get("path")]
            if not path_matches:
                violations.append({"session_id": session_id, "code": "memory_fact_path_mismatch", "detail": str(update.get("path"))})
                continue
            if not any(cue.get("linked_memory_operation") == update.get("operation") for cue in path_matches):
                violations.append({"session_id": session_id, "code": "memory_fact_operation_mismatch", "detail": str(update.get("path"))})
            if not any(cue.get("linked_memory_value") == update.get("new_value") for cue in path_matches):
                violations.append({"session_id": session_id, "code": "memory_fact_value_mismatch", "detail": str(update.get("path"))})
        if plan.get("event_status_after_session") == "cancelled":
            if any(cue.get("linked_memory_operation") not in {None, "clear_pending", "no_update"} for cue in annotations):
                violations.append({"session_id": session_id, "code": "cancelled_value_committed", "detail": "non-clear operation annotated"})
        if plan.get("event_status_after_session") == "upcoming":
            pending_evidence_turns = []
            for cue in annotations:
                if cue.get("linked_memory_operation") != "set_pending":
                    continue
                turn_index = cue.get("turn_index", -1)
                if 0 <= turn_index < len(session.get("turns") or []):
                    pending_evidence_turns.append(
                        (session.get("turns") or [])[turn_index].get("text", "")
                    )
            if any(
                phrase in text
                for text in pending_evidence_turns
                for phrase in _UPCOMING_CURRENT_PHRASES
            ):
                violations.append({"session_id": session_id, "code": "upcoming_value_treated_current", "detail": "pending memory evidence describes the future value as current"})
        if plan.get("session_type") == "stale_recall_session":
            for pair in plan.get("stale_memory_pairs") or []:
                old_value, current_value = pair.get("old_value"), pair.get("current_value")
                if isinstance(old_value, (str, int)) and isinstance(current_value, (str, int)):
                    if str(old_value) not in visible or str(current_value) not in visible:
                        violations.append({"session_id": session_id, "code": "stale_old_current_confusion", "detail": str(pair.get("path"))})
        if _INTERNAL_RE.search(visible):
            violations.append({"session_id": session_id, "code": "internal_metadata_leakage", "detail": "internal token visible"})

        texts = [turn.get("text", "") for turn in session.get("turns") or []]
        user_turn_count = sum(
            turn.get("speaker") == "user" for turn in session.get("turns") or []
        )
        if not turns_min <= len(texts) <= turns_max:
            violations.append(
                {
                    "session_id": session_id,
                    "code": "turn_contract_violation",
                    "detail": f"expected {turns_min}..{turns_max} total turns, got {len(texts)}",
                }
            )
        if not user_turns_min <= user_turn_count <= user_turns_max:
            violations.append(
                {
                    "session_id": session_id,
                    "code": "user_turn_contract_violation",
                    "detail": (
                        f"expected {user_turns_min}..{user_turns_max} user turns, "
                        f"got {user_turn_count}"
                    ),
                }
            )
        substantive = [
            _normalized(text) for text in texts if _is_substantive_utterance(text)
        ]
        if len(substantive) != len(set(substantive)):
            quality_flags["repeated_utterance_session"] += 1
            repeated_utterance_session_ids.append(session_id)
        assistant = [turn.get("text", "") for turn in session.get("turns") or [] if turn.get("speaker") == "assistant"]
        repeated_assistant = len(assistant) - len(set(assistant))
        if assistant and repeated_assistant / len(assistant) > 0.2:
            quality_flags["repeated_assistant_utterance"] += 1
        generic = sum(any(phrase in text for phrase in _GENERIC_CONFIRM) for text in assistant)
        if assistant and generic / len(assistant) > 0.5:
            quality_flags["consecutive_generic_confirmation"] += 1
        if "{" in visible or "}" in visible:
            quality_flags["visible_json_fragment"] += 1
        if _INTERNAL_RE.search(visible):
            quality_flags["unnatural_schema_path"] += 1
        if len(texts) > turns_max:
            quality_flags["excessive_turn_count"] += 1
        if len(texts) < turns_min:
            quality_flags["too_short_session"] += 1
        if not turns_min <= len(texts) <= turns_max or not (
            user_turns_min <= user_turn_count <= user_turns_max
        ):
            quality_flags["turn_contract_violation_session"] += 1
        if any(
            item.get("code") == "concrete_value_hallucination"
            for item in session_validation
        ):
            quality_flags["concrete_value_hallucination"] += 1

        normalized = _normalized(visible)
        dialogue_hashes[hashlib.sha256(normalized.encode()).hexdigest()].append(session_id)
        trigram_sets[session_id] = _trigrams(visible)
        metadata = session.get("generation_metadata") or {}
        repairs = int(metadata.get("repair_count") or 0)
        repair_attempts += repairs
        repair_sessions += int(repairs > 0)
        total_requests += int(metadata.get("provider_request_count") or 0)
        response_usage = metadata.get("usage") or {}
        in_tokens = int(metadata.get("prompt_tokens") or metadata.get("input_tokens") or response_usage.get("prompt_tokens") or response_usage.get("input_tokens") or 0)
        out_tokens = int(metadata.get("completion_tokens") or metadata.get("output_tokens") or response_usage.get("completion_tokens") or response_usage.get("output_tokens") or 0)
        cached = int(metadata.get("cached_tokens") or response_usage.get("cached_tokens") or response_usage.get("cache_read_input_tokens") or 0)
        input_tokens += in_tokens
        output_tokens += out_tokens
        cached_tokens += cached
        latency = metadata.get("request_duration_ms")
        if latency is not None:
            latencies.append(float(latency))
        tokens_by_type[str(plan.get("session_type"))].update(input=in_tokens, output=out_tokens, cached=cached)
        tokens_by_status[str(plan.get("event_status_after_session"))].update(input=in_tokens, output=out_tokens, cached=cached)

    identical_groups = [ids for ids in dialogue_hashes.values() if len(ids) > 1]
    quality_flags["identical_dialogue_groups"] = len(identical_groups)
    near_duplicate_pairs: list[list[str]] = []
    ids = sorted(trigram_sets)
    for left_index, left in enumerate(ids):
        for right in ids[left_index + 1:]:
            a, b = trigram_sets[left], trigram_sets[right]
            if not a or not b:
                continue
            similarity = len(a & b) / len(a | b)
            if similarity >= 0.85:
                near_duplicate_pairs.append([left, right])
    quality_flags["near_duplicate_pairs"] = len(near_duplicate_pairs)

    violation_counts = Counter(item["code"] for item in violations)
    missing = sorted(set(plan_by_id) - set(session_by_id))
    planned = len(plans)
    successful = len(session_by_id)
    return {
        "summary": {
            "planned_session_count": planned,
            "successful_session_count": successful,
            "missing_session_count": len(missing),
            "generation_failure_count": len(errors),
            "provider_request_failures": sum(
                error.get("error_type")
                not in {"LLMOutputValidationError", "ValueError", "ValidationError"}
                for error in errors
            ),
            "empty_responses": sum(error.get("error_type") == "EmptyLLMResponseError" for error in errors),
            "initial_json_parse_failures": sum(
                any("JSON" in text or "no JSON" in text for text in ((session.get("generation_metadata") or {}).get("validation_errors") or [])[:1])
                for session in sessions
            ),
            "schema_failures": sum("schema" in str(error.get("error", "")).lower() for error in errors),
            "repair_attempts": repair_attempts,
            "sessions_requiring_repair": repair_sessions,
            "sessions_failing_after_repairs": len(errors),
            "success_rate": round(successful / planned, 6) if planned else 0,
            "repair_session_rate": round(repair_sessions / planned, 6) if planned else 0,
        },
        "violation_counts": dict(sorted(violation_counts.items())),
        "violations": violations,
        "quality": {
            **dict(sorted(quality_flags.items())),
            "repeated_utterance_session_ids": repeated_utterance_session_ids,
            "identical_dialogue_groups": identical_groups,
            "near_duplicate_pairs": near_duplicate_pairs,
            "near_duplicate_session_rate": round(len({item for pair in near_duplicate_pairs for item in pair}) / planned, 6) if planned else 0,
            "repeated_utterance_session_rate": round(quality_flags["repeated_utterance_session"] / planned, 6) if planned else 0,
        },
        "efficiency": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "total_requests": total_requests,
            "total_repair_requests": repair_attempts,
            "p50_latency_ms": round(median(latencies), 3) if latencies else None,
            "p95_latency_ms": _percentile(latencies, 0.95),
            "tokens_by_session_type": {key: dict(value) for key, value in sorted(tokens_by_type.items())},
            "tokens_by_lifecycle_status": {key: dict(value) for key, value in sorted(tokens_by_status.items())},
        },
        "missing_session_ids": missing,
        "error_records": errors,
    }
