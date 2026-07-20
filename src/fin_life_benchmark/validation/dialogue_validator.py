"""Validate generated dialogue sessions against leakage/consistency rules."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from ..fsm.models import LifeEventTemplate
from ..fsm.registry import all_event_labels_ko
from ..io import RepoPaths, load_yaml

_ASSISTANT_LEAK_PATTERNS = [
    "이사하셨군요",
    "결혼하셨군요",
    "출산하셨군요",
    "이직하셨군요",
    "퇴사하셨군요",
    "장례 치르셨군요",
    "신혼이시군요",
]

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F02F\U0001F900-\U0001F9FF❤️]"
)
# 초성체: 2+ consecutive bare jamo (ㅋㅋ, ㅇㅇ, ㄷㄷ ...)
_CHOSEONG_RE = re.compile(r"[ㄱ-ㅎㅏ-ㅣ]{2,}")
_FA_CODE_RE = re.compile(r"FA-\d{2}")

_HIGH_RISK_FA = {"FA-07", "FA-08", "FA-09", "FA-10"}
_COMPLETION_RE = re.compile(
    r"(?:완료(?:되었|됐|되었습니다|됐습니다|했|했습니다)|"
    r"처리(?:했|했습니다|되었|됐|되었습니다|됐습니다)|"
    r"접수(?:되었|됐|되었습니다|됐습니다|했|했습니다)|"
    r"적용(?:되었|됐|되었습니다|됐습니다|됩니다|했|했습니다)|"
    r"등록(?:되었|됐|되었습니다|됐습니다|했|했습니다)|"
    r"변경해\s*두었|변경해\s*뒀|해지(?:했|했습니다|되었|됐|되었습니다|됐습니다)|"
    r"실행(?:했|했습니다|되었|됐|되었습니다|됐습니다)|요청을\s*받았습니다|"
    r"다음\s*이체일부터\s*반영)"
)
_CONFIRMATION_PHRASES = ["확인 후", "확인 후에", "동의", "본인 확인"]
_WEAK_SIGNAL_OVERCOMMIT_PHRASES = [
    "이미 확정",
    "확정됐",
    "확정되었",
    "확정되었습니다",
    "확정된 상태",
    "확정된 일정",
    "확정된 건입니다",
    "완전히 확정",
]
_WEAK_SIGNAL_NEGATION_PHRASES = [
    "확정된 건 아닌",
    "확정은 아니",
    "확정된 상태는 아니",
    "확정된 일정은 아니",
    "확정된 것은 아니",
]
_OFFLINE_BANKING_TERMS = [
    "창구",
    "영업점",
    "방문",
    "모시겠습니다",
    "신분증 지참",
    "실물 신분증",
    "신청서",
    "서명",
    "출력",
    "우편 발송",
    "우편 배송",
    "배송",
    "방문 수령",
    "창구 수령",
    "실물 수령",
]

_CONCRETE_NUMBER_RE = re.compile(
    r"(?<![0-9A-Za-z_])(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<unit>천만|백만|십만|억|만|천|백)?(?P<currency>원)?"
)
_KOREAN_NUMBER_MULTIPLIERS = {
    "억": Decimal("100000000"),
    "천만": Decimal("10000000"),
    "백만": Decimal("1000000"),
    "십만": Decimal("100000"),
    "만": Decimal("10000"),
    "천": Decimal("1000"),
    "백": Decimal("100"),
}

_GENERIC_SLOT_VALUES = {
    "해당 금액",
    "정해둔 금액",
    "설정한 금액",
    "선택한 날짜",
    "해당 날짜",
    "매달 초",
    "부모님 계좌",
    "지정 계좌",
}

_VISIBLE_SLOT_ALIASES = {
    "main_checking": ("주거래계좌", "주거래 계좌", "입출금계좌", "입출금 계좌"),
    "spouse": ("배우자", "가족"),
    "KRW": ("원화", "원"),
    "USD": ("미국 달러", "달러", "USD"),
    "JPY": ("일본 엔", "엔화", "JPY"),
    "EUR": ("유로", "EUR"),
}

_LABEL_SUFFIX_RE = r"(?:하|해|했|할|한|하는|하려|를|을|가|이|는|은|로|으로|와|과|에|도|부터|까지|계획|예정)"


def contains_contextual_event_label(text: str, label: str) -> bool:
    """Match a Korean event label as a token/stem, never inside another word."""
    if not text or not label:
        return False
    pattern = re.compile(
        rf"(?<![0-9A-Za-z가-힣]){re.escape(label)}(?=$|[^0-9A-Za-z가-힣]|{_LABEL_SUFFIX_RE})"
    )
    return bool(pattern.search(text))


def policy_claims(text: str) -> set[str]:
    """Normalize the small set of policy claims used by trajectory audits."""
    claims: set[str] = set()
    if re.search(r"(?:공동명의|공동\s*명의).{0,18}(?:가능|등록할 수|만들 수 있)", text):
        claims.add("joint_account:supported")
    if re.search(r"(?:공동명의|공동\s*명의).{0,18}(?:불가능|지원하지 않|만들 수 없)", text):
        claims.add("joint_account:unsupported")
    if re.search(r"(?:대표\s*명의|한\s*명).{0,12}(?:필수|반드시|해야)", text):
        claims.add("joint_account:unsupported")
    return claims


def completion_turn_indices(turns: list[dict[str, Any]]) -> list[int]:
    indices: list[int] = []
    for index, turn in enumerate(turns):
        if turn.get("speaker") != "assistant":
            continue
        text = str(turn.get("text", ""))
        if not _COMPLETION_RE.search(text):
            continue
        if any(
            marker in text
            for marker in (
                "완료되지",
                "처리하지",
                "실행하지",
                "적용되지",
                "반영되지",
                "완료 전",
                "본인인증",
                "인증이 완료",
                "조회가 완료",
                "확인이 완료",
                "안내가 완료",
                "확인 후",
                "승인 후",
                "승인해야",
                "확인 후 진행",
                "승인 후에만",
            )
        ):
            continue
        indices.append(index)
    return indices


def _slot_value_visible(slot: str, value: Any, user_text: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float, Decimal)):
        try:
            canonical = _canonical_decimal(Decimal(str(value)))
        except InvalidOperation:
            return False
        return canonical in _numbers_in_text(user_text)
    if isinstance(value, str):
        if value in user_text:
            return True
        if any(alias in user_text for alias in _VISIBLE_SLOT_ALIASES.get(value, ())):
            return True
        if slot == "product_or_goal":
            tokens = [
                token
                for token in re.findall(r"[가-힣]{2,}", value)
                if token not in {"설정", "확인", "준비", "변경"}
            ]
            return bool(tokens) and any(token in user_text for token in tokens)
        return False
    if isinstance(value, list):
        return bool(value) and all(_slot_value_visible(slot, item, user_text) for item in value)
    if isinstance(value, dict):
        leaves = [item for item in value.values() if item is not None]
        return bool(leaves) and any(
            _slot_value_visible(slot, item, user_text) for item in leaves
        )
    return False


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _numbers_in_text(text: str) -> list[str]:
    values: list[str] = []
    for match in _CONCRETE_NUMBER_RE.finditer(text):
        try:
            value = Decimal(match.group("number").replace(",", ""))
        except InvalidOperation:
            continue
        unit = match.group("unit")
        if unit:
            value *= _KOREAN_NUMBER_MULTIPLIERS[unit]
        values.append(_canonical_decimal(value))
    return values


def _collect_grounded_numbers(value: Any, result: set[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float, Decimal)):
        try:
            result.add(_canonical_decimal(Decimal(str(value))))
        except InvalidOperation:
            return
        return
    if isinstance(value, str):
        result.update(_numbers_in_text(value))
        return
    if isinstance(value, list):
        for item in value:
            _collect_grounded_numbers(item, result)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_grounded_numbers(item, result)


def grounded_concrete_values(plan: dict[str, Any]) -> set[str]:
    """Return concrete numeric values explicitly available to dialogue.

    Runtime metadata such as month_index and session_id is intentionally
    excluded: it must not accidentally authorize an unrelated amount, date,
    rate, or account suffix in visible dialogue.
    """
    context = plan.get("structured_context") or {}
    event = context.get("event") or {}
    sources = {
        "financial_task": plan.get("financial_task"),
        "must_include_cues": plan.get("must_include_cues") or [],
        "planned_cues": [
            {
                "required_value": cue.get("required_value"),
                "surface_hint": cue.get("surface_hint"),
            }
            for cue in (plan.get("planned_cues") or [])
        ],
        "event_params": event.get("params") or {},
        "current_life_state": (context.get("current_state") or {}).get("life_state") or {},
        "current_financial_memory": context.get("current_financial_memory") or context.get("current_memory") or {},
        "session_memory_updates": context.get("session_memory_updates") or [],
        "event_memory_updates": context.get("event_memory_updates") or [],
        "stale_memory_pairs": plan.get("stale_memory_pairs") or [],
    }
    result: set[str] = set()
    _collect_grounded_numbers(sources, result)
    return result


def ungrounded_concrete_values(
    visible: str, plan: dict[str, Any]
) -> list[str]:
    grounded = grounded_concrete_values(plan)
    ungrounded: list[str] = []
    for value in _numbers_in_text(visible):
        if value not in grounded and value not in ungrounded:
            ungrounded.append(value)
    return ungrounded


class DialogueValidator:
    def __init__(
        self,
        templates: dict[str, LifeEventTemplate],
        paths: RepoPaths | None = None,
    ):
        self.paths = paths or RepoPaths.default()
        self.templates = templates
        self.event_labels = all_event_labels_ko(templates)
        self.disclosure_registry = load_yaml(
            self.paths.registries / "dialogue_event_disclosure_patterns.yaml"
        )
        self.policy_registry = load_yaml(
            self.paths.registries / "bank_policy_profile.yaml"
        )
        self.cfg = load_yaml(self.paths.generation / "dialogue.yaml")

    def validate_session(self, session: dict[str, Any]) -> list[dict[str, str]]:
        """Return a list of violations: [{code, detail}]."""
        violations: list[dict[str, str]] = []

        def flag(code: str, detail: str) -> None:
            violations.append({"code": code, "detail": detail})

        turns = session.get("turns") or []
        if not turns:
            flag("no_turns", "session has no turns")
            return violations
        if turns[0].get("speaker") != "user":
            flag("first_turn_not_user", "first turn is not user")
        if turns[-1].get("speaker") != "assistant":
            flag("last_turn_not_assistant", "last turn is not assistant")

        # speaker alternation
        for i in range(1, len(turns)):
            if turns[i]["speaker"] == turns[i - 1]["speaker"]:
                flag("speaker_alternation", f"turns {i-1}/{i} same speaker")
                break

        visible = " ".join(t.get("text", "") for t in turns)
        user_text = " ".join(t.get("text", "") for t in turns if t.get("speaker") == "user")
        assistant_text = " ".join(t.get("text", "") for t in turns if t.get("speaker") == "assistant")

        # leakage: skip labels that are substrings of this session's own
        # required cues (financial-consequence cues may share words with a
        # composite label, e.g. '수술비 수납' vs '수술')
        plan_cues = (session.get("plan") or {}).get("must_include_cues") or []
        for label in self.event_labels:
            if any(label in cue for cue in plan_cues):
                continue
            if contains_contextual_event_label(visible, label):
                flag("event_label_leakage", f"label '{label}' in visible text")
        if _FA_CODE_RE.search(visible):
            flag("fa_code_leakage", "FA-XX code in visible text")
        for phrase in _ASSISTANT_LEAK_PATTERNS:
            if phrase in assistant_text:
                flag("assistant_event_summary", f"assistant says '{phrase}'")
        if _EMOJI_RE.search(visible):
            flag("emoji", "emoji in visible text")
        if _CHOSEONG_RE.search(visible):
            flag("choseongche", "초성체 in visible text")
        for term in _OFFLINE_BANKING_TERMS:
            if term in visible:
                flag("offline_banking_context", f"offline/branch term '{term}' in visible text")
                break

        # cue annotations must point at user turns
        plan = session.get("plan") or {}
        target_memory_paths = set(plan.get("target_memory_paths") or [])
        cue_annotations = session.get("cue_annotations") or []
        annotated_user_texts: list[str] = []
        for cue in cue_annotations:
            idx = cue.get("turn_index", -1)
            if not (0 <= idx < len(turns)):
                flag("cue_index_out_of_range", f"cue turn_index {idx}")
            elif turns[idx]["speaker"] != "user":
                flag("cue_not_user_turn", f"cue at turn {idx} is not a user turn")
            else:
                annotated_user_texts.append(turns[idx].get("text", ""))
                cue_text = cue.get("evidence_text") or cue.get("cue_text")
                if cue_text and cue_text not in turns[idx].get("text", ""):
                    flag("cue_text_not_in_annotated_turn", f"evidence text '{cue_text}' absent from turn {idx}")
            linked = cue.get("linked_memory_path")
            if linked is not None and linked not in target_memory_paths:
                flag("cue_linked_path_not_target", f"linked_memory_path '{linked}' not in target_memory_paths")
        if plan.get("must_include_cues") and not cue_annotations:
            flag("missing_cue_annotation", "must_include_cues present but cue_annotations is empty")

        expected_memory_facts = (
            (plan.get("structured_context") or {}).get("session_memory_updates") or []
        )
        for expected in expected_memory_facts:
            matching = [
                cue for cue in cue_annotations
                if cue.get("cue_type") == "memory_fact"
                and cue.get("linked_memory_path") == expected.get("path")
                and cue.get("linked_memory_operation") == expected.get("operation")
                and cue.get("linked_memory_value") == expected.get("new_value")
            ]
            if not matching:
                flag(
                    "missing_memory_fact_grounding",
                    f"{expected.get('path')} {expected.get('operation')} lacks exact annotation",
                )

        status = session.get("event_status_after_session", "no_event")
        session_type = session.get("session_type", "")
        evidence_types = {
            "weak_signal_evidence",
            "upcoming_evidence",
            "occurred_evidence",
            "cancellation_evidence",
        }

        if session_type in evidence_types:
            opening = str(turns[0].get("text", ""))
            task_terms = [
                token
                for token in re.findall(r"[가-힣]{2,}", str(plan.get("financial_task") or session.get("financial_task") or ""))
                if token not in {"확인", "설정", "관리", "내역", "관련"}
            ]
            task_annotated = any(
                cue.get("turn_index") == 0 and cue.get("cue_type") == "task_intent"
                for cue in cue_annotations
            )
            if not task_annotated and not any(term in opening for term in task_terms):
                flag(
                    "task_not_introduced_in_opening",
                    "first user turn does not introduce the planned banking task",
                )

            dimensions = plan.get("evidence_dimensions") or []
            dimension_by_id = {
                str(item.get("dimension_id")): item for item in dimensions
            }
            realized: dict[str, list[int]] = {}
            for cue in cue_annotations:
                dimension_id = cue.get("evidence_dimension_id")
                if not dimension_id:
                    continue
                if dimension_id not in dimension_by_id:
                    flag(
                        "evidence_dimension_annotation_mismatch",
                        f"unknown evidence dimension '{dimension_id}'",
                    )
                    continue
                expected_role = str(dimension_by_id[dimension_id].get("role"))
                if cue.get("cue_type") not in {expected_role, "memory_fact"}:
                    flag(
                        "evidence_dimension_annotation_mismatch",
                        f"{dimension_id} annotated as {cue.get('cue_type')}, expected {expected_role}",
                    )
                index = int(cue.get("turn_index", -1))
                if 0 <= index < len(turns) and turns[index].get("speaker") == "user":
                    realized.setdefault(str(dimension_id), []).append(index)
            required_dimensions = [
                item for item in dimensions if item.get("required", True)
            ]
            missing_dimensions = [
                item
                for item in required_dimensions
                if str(item.get("dimension_id")) not in realized
            ]
            if missing_dimensions:
                missing_ids = [str(item.get("dimension_id")) for item in missing_dimensions]
                flag("required_evidence_not_realized", ", ".join(missing_ids))
                flag("insufficient_event_evidence", ", ".join(missing_ids))
            required_roles = {
                str(item.get("role")) for item in required_dimensions
            }
            realized_roles = {
                str(dimension_by_id[dimension_id].get("role"))
                for dimension_id in realized
                if dimension_id in dimension_by_id
            }
            for role in sorted(required_roles - realized_roles):
                flag("missing_required_evidence_role", role)
            if "subtype_disambiguation" in required_roles - realized_roles:
                flag("subtype_not_disambiguated", "subtype-disambiguation evidence missing")
            if dimensions and not realized_roles.intersection(
                {"state_change", "entity_change", "subtype_disambiguation", "uncertainty", "future_timing", "cancellation", "prior_current_contrast"}
            ):
                flag("generic_financial_task_only", "only generic financial activity is evidenced")

            realized_turns = sorted({turn for values in realized.values() for turn in values})
            planned_slots = set(plan.get("evidence_placement_slots") or [])
            if realized_turns and planned_slots and not set(realized_turns).issubset(planned_slots):
                flag(
                    "evidence_placement_strategy_mismatch",
                    f"realized at {realized_turns}, planned {sorted(planned_slots)}",
                )
            dialogue_contract = (plan.get("structured_context") or {}).get("dialogue_contract") or {}
            final_reveal = bool(dialogue_contract.get("explicit_final_reveal"))
            deadline = int(
                dialogue_contract.get(
                    "evidence_deadline_user_turn",
                    self.cfg.get("semantic_validity", {}).get("evidence_deadline_user_turn", 3),
                )
            )
            user_indices = [index for index, turn in enumerate(turns) if turn.get("speaker") == "user"]
            deadline_index = user_indices[min(max(deadline, 1), len(user_indices)) - 1]
            if realized_turns and not final_reveal and max(realized_turns) > deadline_index:
                flag(
                    "evidence_revealed_too_late",
                    f"evidence after user turn {deadline}",
                )

        event_id = str(((plan.get("structured_context") or {}).get("event") or {}).get("event_id") or "")
        disclosure = self.disclosure_registry.get(event_id) or {}
        direct_patterns = list(plan.get("forbidden_direct_event_patterns") or [])
        direct_patterns.extend(disclosure.get("disallowed") or [])
        status_exceptions = disclosure.get("status_exceptions") or {}
        exception_spec = status_exceptions.get(status) or {}
        exception_patterns = set(
            exception_spec.get("allowed_direct_patterns") or []
            if isinstance(exception_spec, dict)
            else exception_spec or []
        )
        for pattern in dict.fromkeys(direct_patterns):
            if pattern and pattern not in exception_patterns and pattern in visible:
                flag("direct_event_disclosure", f"direct pattern '{pattern}'")
                flag("forbidden_event_paraphrase", f"forbidden paraphrase '{pattern}'")
        for pattern in disclosure.get("review_only") or []:
            if pattern and pattern in visible:
                flag("near_direct_event_disclosure", f"borderline pattern '{pattern}'")

        if session_type == "hard_negative" or plan.get("expected_memory_operation") == "no_update":
            unexpected_facts = [
                cue for cue in cue_annotations
                if cue.get("cue_type") == "memory_fact"
                or cue.get("linked_memory_operation") not in {None, "no_update"}
            ]
            if unexpected_facts:
                flag(
                    "hard_negative_unintended_update",
                    "no-update session contains a memory fact or mutation annotation",
                )

        invented_values = ungrounded_concrete_values(visible, plan)
        if invented_values:
            flag(
                "concrete_value_hallucination",
                ", ".join(invented_values[:8]),
            )

        # required cues present / forbidden absent
        for cue in plan.get("must_include_cues") or []:
            if cue and cue not in visible:
                flag("missing_required_cue", f"cue '{cue}' absent")
            if cue and cue in visible and cue not in user_text:
                flag("required_cue_not_in_user_turn", f"cue '{cue}' absent from user turns")
            if cue and cue in visible and not any(cue in text for text in annotated_user_texts):
                flag("required_cue_not_annotated", f"cue '{cue}' not present in any annotated user turn")
        for term in plan.get("must_not_include_terms") or []:
            prohibited = (
                contains_contextual_event_label(visible, term)
                if term in self.event_labels
                else bool(term and term in visible)
            )
            if prohibited:
                flag("forbidden_term", f"term '{term}' present")

        # status consistency
        if session_type in {"hard_negative", "routine_financial"} and status != "no_event":
            flag("status_inconsistent", f"{session_type} with status {status}")
        if status == "occurred" and session_type == "occurred_evidence":
            if not (session.get("cue_annotations") or []):
                flag("occurred_without_consequence_cue", "occurred_evidence session has no cue annotation")
        if status == "weak_signal":
            if (
                any(phrase in visible for phrase in _WEAK_SIGNAL_OVERCOMMIT_PHRASES)
                and not any(phrase in visible for phrase in _WEAK_SIGNAL_NEGATION_PHRASES)
            ):
                flag("weak_signal_overcommitted", "weak_signal session implies confirmation")

        # High-risk completion is legal only when the planner supplied every
        # concrete slot and the user explicitly confirmed in a visible turn.
        mapped = session.get("mapped_action")
        if mapped in _HIGH_RISK_FA:
            contract = plan.get("action_execution_contract") or {}
            resolution = session.get("action_resolution") or {}
            completion_indices = completion_turn_indices(turns)
            attempted_completion = bool(completion_indices) or resolution.get("mode") == "executed_after_confirmation"
            confirmation_index = resolution.get("explicit_confirmation_turn_index")
            visible_confirmation = (
                isinstance(confirmation_index, int)
                and 0 <= confirmation_index < len(turns)
                and turns[confirmation_index].get("speaker") == "user"
                and any(
                    phrase in str(turns[confirmation_index].get("text", ""))
                    for phrase in ("확인", "동의", "승인", "진행해", "실행해", "해주세요", "해 주세요")
                )
            )
            provided = resolution.get("provided_slots") or {}
            grounded = contract.get("grounded_slots") or {}
            for slot, value in provided.items():
                if (
                    (isinstance(value, str) and value in _GENERIC_SLOT_VALUES)
                    or slot not in grounded
                    or grounded.get(slot) != value
                ):
                    flag("high_risk_unplanned_slot_value", f"{slot}={value!r}")
            missing = list(contract.get("missing_slots") or [])
            runtime_missing = [
                slot
                for slot in contract.get("required_slots") or []
                if slot != "explicit_confirmation"
                and (
                    slot not in grounded
                    or slot not in provided
                    or provided.get(slot) != grounded.get(slot)
                    or not _slot_value_visible(slot, grounded.get(slot), user_text)
                )
            ]
            if attempted_completion and not contract.get("required_slots") and contract.get("action_mode") != "information_only":
                flag("high_risk_missing_required_slot", "execution contract has no required slots")
            if attempted_completion and (missing or runtime_missing):
                flag(
                    "high_risk_missing_required_slot",
                    ", ".join(sorted(set(missing + runtime_missing))),
                )
            if attempted_completion and contract.get("confirmation_required") and not visible_confirmation:
                flag("high_risk_missing_confirmation", "completion lacks visible explicit user confirmation")
            if (
                completion_indices
                and visible_confirmation
                and int(confirmation_index) >= min(completion_indices)
            ):
                flag("high_risk_missing_confirmation", "confirmation does not precede completion")
            if attempted_completion and (
                not contract.get("completion_allowed")
                or missing
                or runtime_missing
                or (contract.get("confirmation_required") and not visible_confirmation)
            ):
                flag("high_risk_false_completion", f"completion at turns {completion_indices}")
                flag("high_risk_auto_execution", f"unsafe completion at turns {completion_indices}")
            recorded_completion = resolution.get("completion_turn_index")
            if (recorded_completion is None) != (not completion_indices) or (
                completion_indices and recorded_completion not in completion_indices
            ):
                flag("high_risk_action_resolution_mismatch", "completion index disagrees with visible dialogue")
            expected_mode = (
                "executed_after_confirmation"
                if completion_indices
                else contract.get("action_mode", "information_only")
            )
            if resolution.get("mode", "information_only") != expected_mode:
                flag(
                    "high_risk_action_resolution_mismatch",
                    f"mode {resolution.get('mode')!r}, expected {expected_mode!r}",
                )
            expected_missing = sorted(missing)
            if sorted(resolution.get("missing_slots") or []) != expected_missing:
                flag("high_risk_action_resolution_mismatch", "missing slots disagree with plan contract")

        policy_rules = (self.policy_registry.get("rules") or {})
        for policy_key, rule in policy_rules.items():
            if not isinstance(rule, dict):
                continue
            for pattern in rule.get("prohibited_patterns") or []:
                if pattern and pattern in assistant_text:
                    flag("unsupported_bank_policy_claim", f"{policy_key}: '{pattern}'")
        normalized_claims = policy_claims(assistant_text)
        if {
            "joint_account:supported",
            "joint_account:unsupported",
        }.issubset(normalized_claims):
            flag("bank_policy_contradiction", "contradictory joint-account claims")

        return violations


def summarize_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    failed = [r for r in results if r["violations"]]
    by_code: dict[str, int] = {}
    for r in failed:
        for v in r["violations"]:
            by_code[v["code"]] = by_code.get(v["code"], 0) + 1
    return {
        "total_sessions": total,
        "sessions_with_violations": len(failed),
        "pass_rate": round(1 - len(failed) / total, 4) if total else None,
        "violations_by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
    }
