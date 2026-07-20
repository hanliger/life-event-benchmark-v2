"""Objective reliability, fidelity, quality, and efficiency audit for dialogues."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from statistics import median
from typing import Any

from ..fsm.models import LifeEventTemplate
from ..io import RepoPaths, load_yaml
from .dialogue_validator import DialogueValidator, policy_claims

_INTERNAL_RE = re.compile(r"(?:FA-\d{2}|[a-z_]+\.[a-z_]+|task_template_id|planned_cues|session_type)")
_GENERIC_CONFIRM = ("확인했습니다", "네, 가능합니다", "처리해 드렸습니다")
_UPCOMING_CURRENT_PHRASES = ("이미 적용", "현재 적용", "지금 바뀌었", "완료됐")
_LIFECYCLE_PHRASE_FAMILIES = {
    "weak_signal": {
        "canonical_uncertainty": ("아직 확정은 아니어서", "조건만 미리", "확정된 건 아닌데"),
    },
    "upcoming": {
        "canonical_future": ("다음 달부터", "다음 달쯤", "미리 준비", "예정이라"),
    },
    "occurred": {
        "canonical_actuality": ("이번에 실제로 반영", "실제로 반영돼서", "실제로 반영"),
    },
    "cancelled": {
        "canonical_cancel": ("취소하려고요", "없던 일이 됐"),
    },
}


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
    cfg = load_yaml(RepoPaths.default().generation / "dialogue.yaml")
    diversity_cfg = cfg.get("surface_diversity") or {}
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
    repair_reason_counts: Counter = Counter()
    first_user_by_id: dict[str, str] = {}
    user_turns_by_id: dict[str, list[str]] = {}
    policy_by_trajectory: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )

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
                path = pair.get("path")
                old_grounded = any(
                    cue.get("cue_type") == "stale_value"
                    and cue.get("linked_memory_path") == path
                    and cue.get("linked_memory_value") == old_value
                    and cue.get("evidence_text")
                    for cue in annotations
                )
                current_grounded = any(
                    cue.get("cue_type") == "current_value"
                    and cue.get("linked_memory_path") == path
                    and cue.get("linked_memory_value") == current_value
                    and cue.get("evidence_text")
                    for cue in annotations
                )
                if not (old_grounded and current_grounded):
                    violations.append({"session_id": session_id, "code": "stale_old_current_confusion", "detail": str(path)})
        if _INTERNAL_RE.search(visible):
            violations.append({"session_id": session_id, "code": "internal_metadata_leakage", "detail": "internal token visible"})

        texts = [turn.get("text", "") for turn in session.get("turns") or []]
        user_texts = [
            turn.get("text", "")
            for turn in session.get("turns") or []
            if turn.get("speaker") == "user"
        ]
        if user_texts:
            first_user_by_id[session_id] = user_texts[0]
            user_turns_by_id[session_id] = user_texts
        for claim in policy_claims(" ".join(
            turn.get("text", "")
            for turn in session.get("turns") or []
            if turn.get("speaker") == "assistant"
        )):
            policy_by_trajectory[str(session.get("trajectory_id"))][claim].append(
                session_id
            )
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
        repair_reason_counts.update(metadata.get("repair_reason_counts") or {})
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

    diversity: dict[str, Any] = {}
    exact_opening_groups: dict[str, list[str]] = defaultdict(list)
    exact_user_turn_groups: dict[str, list[str]] = defaultdict(list)
    for session_id, opening in first_user_by_id.items():
        exact_opening_groups[_normalized(opening)].append(session_id)
        for text in user_turns_by_id.get(session_id, []):
            exact_user_turn_groups[_normalized(text)].append(session_id)
    opening_limit = int(diversity_cfg.get("exact_opening_max_count", 2))
    duplicate_openings = []
    for normalized, group in exact_opening_groups.items():
        if normalized and len(group) > opening_limit:
            detail = {
                "normalized": normalized,
                "session_ids": sorted(group),
                "example": first_user_by_id[group[0]],
                "count": len(group),
            }
            duplicate_openings.append(detail)
            violations.append(
                {
                    "session_id": group[0],
                    "session_ids": sorted(group),
                    "code": "duplicate_opening_over_limit",
                    "detail": f"normalized opening occurs {len(group)} times",
                    "example": first_user_by_id[group[0]],
                }
            )

    evidence_plans = [
        plan
        for plan in plans
        if plan.get("session_type")
        in {
            "weak_signal_evidence",
            "upcoming_evidence",
            "occurred_evidence",
            "cancellation_evidence",
        }
    ]
    by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for plan in evidence_plans:
        by_status[str(plan.get("event_status_after_session"))].append(plan)
    phrase_concentrations: list[dict[str, Any]] = []
    family_concentrations: list[dict[str, Any]] = []
    placement_distribution: dict[str, dict[str, int]] = {}
    exact_ratio_limit = float(
        diversity_cfg.get("exact_lifecycle_phrase_max_ratio", 0.15)
    )
    family_ratio_limit = float(
        diversity_cfg.get("lifecycle_phrase_family_max_ratio", 0.40)
    )
    placement_ratio_limit = float(
        diversity_cfg.get("evidence_placement_max_ratio", 0.60)
    )
    placement_threshold = int(
        diversity_cfg.get("placement_strategy_status_count_threshold", 10)
    )
    placement_min = int(
        diversity_cfg.get("min_placement_strategies_when_status_count_at_least", 3)
    )
    for status, status_plans in sorted(by_status.items()):
        status_ids = [str(plan.get("session_id")) for plan in status_plans]
        for family, phrases in (_LIFECYCLE_PHRASE_FAMILIES.get(status) or {}).items():
            family_ids: set[str] = set()
            for phrase in phrases:
                ids_for_phrase = [
                    session_id
                    for session_id in status_ids
                    if phrase in _visible(session_by_id.get(session_id) or {})
                ]
                ratio = len(ids_for_phrase) / len(status_ids) if status_ids else 0
                if ratio > exact_ratio_limit:
                    item = {
                        "status": status,
                        "phrase": phrase,
                        "ratio": round(ratio, 6),
                        "session_ids": ids_for_phrase,
                        "examples": [first_user_by_id.get(item, "") for item in ids_for_phrase[:3]],
                    }
                    phrase_concentrations.append(item)
                    violations.append({"session_id": ids_for_phrase[0], "code": "lifecycle_exact_phrase_overconcentration", "detail": str(item), "session_ids": ids_for_phrase})
                family_ids.update(ids_for_phrase)
            family_ratio = len(family_ids) / len(status_ids) if status_ids else 0
            if family_ratio > family_ratio_limit:
                item = {"status": status, "family": family, "ratio": round(family_ratio, 6), "session_ids": sorted(family_ids)}
                family_concentrations.append(item)
                violations.append({"session_id": sorted(family_ids)[0], "code": "lifecycle_phrase_family_overconcentration", "detail": str(item), "session_ids": sorted(family_ids)})
        placement_counts = Counter(
            str(plan.get("evidence_placement_strategy")) for plan in status_plans
        )
        placement_distribution[status] = dict(sorted(placement_counts.items()))
        if len(status_plans) >= placement_threshold:
            largest = max(placement_counts.values(), default=0)
            if len(placement_counts) < placement_min or largest / len(status_plans) > placement_ratio_limit:
                offending_strategy, count = placement_counts.most_common(1)[0]
                offending_ids = [
                    str(plan.get("session_id"))
                    for plan in status_plans
                    if str(plan.get("evidence_placement_strategy")) == offending_strategy
                ]
                violations.append({"session_id": offending_ids[0], "code": "evidence_placement_overconcentration", "detail": f"{status}: {dict(placement_counts)}", "session_ids": offending_ids})

        surface_groups: dict[str, list[str]] = defaultdict(list)
        for plan in status_plans:
            family = plan.get("lifecycle_surface_family")
            if family:
                surface_groups[str(family)].append(str(plan.get("session_id")))
        for family, group in surface_groups.items():
            ratio = len(group) / len(status_plans) if status_plans else 0
            if len(status_plans) >= placement_threshold and ratio > family_ratio_limit:
                item = {
                    "status": status,
                    "family": family,
                    "source": "planned_lifecycle_surface_family",
                    "ratio": round(ratio, 6),
                    "session_ids": group,
                }
                family_concentrations.append(item)
                violations.append(
                    {
                        "session_id": group[0],
                        "code": "lifecycle_phrase_family_overconcentration",
                        "detail": str(item),
                        "session_ids": group,
                    }
                )

        strategy_groups: dict[str, list[str]] = defaultdict(list)
        for plan in status_plans:
            event_id = str(((plan.get("structured_context") or {}).get("event") or {}).get("event_id"))
            key = f"{event_id}:{plan.get('lifecycle_surface_family')}"
            strategy_groups[key].append(str(plan.get("session_id")))
        for key, group in strategy_groups.items():
            if len(status_plans) >= placement_threshold and len(group) / len(status_plans) > family_ratio_limit:
                violations.append({"session_id": group[0], "code": "event_strategy_overconcentration", "detail": f"{status}:{key}={len(group)}/{len(status_plans)}", "session_ids": group})

    hard_negative_plans = [plan for plan in plans if plan.get("session_type") == "hard_negative"]
    hard_variant_groups: dict[str, list[str]] = defaultdict(list)
    for plan in hard_negative_plans:
        hard_variant_groups[str(plan.get("hard_negative_surface_variant_id"))].append(
            str(plan.get("session_id"))
        )
    hard_limit = float(diversity_cfg.get("hard_negative_template_max_ratio", 0.15))
    hard_concentrations = []
    for variant_id, group in sorted(hard_variant_groups.items()):
        ratio = len(group) / len(hard_negative_plans) if hard_negative_plans else 0
        if ratio > hard_limit:
            item = {"variant_id": variant_id, "ratio": round(ratio, 6), "session_ids": group}
            hard_concentrations.append(item)
            violations.append({"session_id": group[0], "code": "hard_negative_template_overconcentration", "detail": str(item), "session_ids": group})

    opening_trigram_pairs: list[dict[str, Any]] = []
    opening_ids = sorted(first_user_by_id)
    opening_trigrams = {key: _trigrams(first_user_by_id[key]) for key in opening_ids}
    for left_index, left in enumerate(opening_ids):
        for right in opening_ids[left_index + 1:]:
            a, b = opening_trigrams[left], opening_trigrams[right]
            if not a or not b:
                continue
            similarity = len(a & b) / len(a | b)
            if similarity >= 0.85:
                opening_trigram_pairs.append({"session_ids": [left, right], "similarity": round(similarity, 6)})

    for trajectory_id, claims in policy_by_trajectory.items():
        supported = claims.get("joint_account:supported") or []
        unsupported = claims.get("joint_account:unsupported") or []
        if supported and unsupported:
            ids_for_claim = sorted(set(supported + unsupported))
            violations.append({"session_id": ids_for_claim[0], "session_ids": ids_for_claim, "code": "bank_policy_contradiction", "detail": f"trajectory {trajectory_id} contains supported and unsupported joint-account claims"})

    diversity = {
        "exact_normalized_first_user_turn_frequency": dict(
            sorted((key, len(value)) for key, value in exact_opening_groups.items())
        ),
        "exact_normalized_user_turn_frequency": dict(
            sorted((key, len(value)) for key, value in exact_user_turn_groups.items())
        ),
        "duplicate_opening_groups": duplicate_openings,
        "lifecycle_exact_phrase_concentrations": phrase_concentrations,
        "lifecycle_phrase_family_concentrations": family_concentrations,
        "lifecycle_surface_family_frequency": dict(
            sorted(Counter(str(plan.get("lifecycle_surface_family")) for plan in evidence_plans).items())
        ),
        "evidence_placement_distribution": placement_distribution,
        "event_status_strategy_frequency": dict(
            sorted(Counter(
                f"{((plan.get('structured_context') or {}).get('event') or {}).get('event_id')}|{plan.get('event_status_after_session')}|{plan.get('lifecycle_surface_variant_id')}"
                for plan in evidence_plans
            ).items())
        ),
        "task_template_opening_strategy_frequency": dict(
            sorted(Counter(
                f"{plan.get('task_template_id')}|{plan.get('evidence_placement_strategy')}"
                for plan in plans
            ).items())
        ),
        "hard_negative_surface_variant_frequency": dict(
            sorted((key, len(value)) for key, value in hard_variant_groups.items())
        ),
        "hard_negative_concentrations": hard_concentrations,
        "cancellation_realization_frequency": dict(
            sorted(Counter(
                str(plan.get("lifecycle_surface_family"))
                for plan in evidence_plans
                if plan.get("event_status_after_session") == "cancelled"
            ).items())
        ),
        "first_user_turn_trigram_similar_pairs": opening_trigram_pairs,
    }

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
            "repair_reason_counts": dict(sorted(repair_reason_counts.items())),
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
        "surface_diversity": diversity,
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
