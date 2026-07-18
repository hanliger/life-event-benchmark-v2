"""Validate generated dialogue sessions against leakage/consistency rules."""

from __future__ import annotations

import re
from typing import Any

from ..fsm.models import LifeEventTemplate
from ..fsm.registry import all_event_labels_ko

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
_EXECUTION_PHRASES = ["바로 실행했습니다", "즉시 변경했습니다", "자동으로 해지했습니다"]
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


class DialogueValidator:
    def __init__(self, templates: dict[str, LifeEventTemplate]):
        self.templates = templates
        self.event_labels = all_event_labels_ko(templates)

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
            if label in visible:
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

        # required cues present / forbidden absent
        for cue in plan.get("must_include_cues") or []:
            if cue and cue not in visible:
                flag("missing_required_cue", f"cue '{cue}' absent")
            if cue and cue in visible and cue not in user_text:
                flag("required_cue_not_in_user_turn", f"cue '{cue}' absent from user turns")
            if cue and cue in visible and not any(cue in text for text in annotated_user_texts):
                flag("required_cue_not_annotated", f"cue '{cue}' not present in any annotated user turn")
        for term in plan.get("must_not_include_terms") or []:
            if term and term in visible:
                flag("forbidden_term", f"term '{term}' present")

        # status consistency
        if session_type in {"hard_negative", "routine_financial"} and status != "no_event":
            flag("status_inconsistent", f"{session_type} with status {status}")
        if status == "occurred" and session_type == "occurred_evidence":
            if not (session.get("cue_annotations") or []):
                flag("occurred_without_consequence_cue", "occurred_evidence session has no cue annotation")
        if status == "cancelled" and "없던 일" not in visible and "취소" not in visible:
            flag("cancelled_without_cancellation_cue", "no cancellation cue in visible text")
        if status == "weak_signal":
            if (
                any(phrase in visible for phrase in _WEAK_SIGNAL_OVERCOMMIT_PHRASES)
                and not any(phrase in visible for phrase in _WEAK_SIGNAL_NEGATION_PHRASES)
            ):
                flag("weak_signal_overcommitted", "weak_signal session implies confirmation")

        # high-risk execution without confirmation
        mapped = session.get("mapped_action")
        if mapped in _HIGH_RISK_FA:
            for phrase in _EXECUTION_PHRASES:
                if phrase in assistant_text and not any(c in assistant_text for c in _CONFIRMATION_PHRASES):
                    flag("high_risk_auto_execution", f"assistant executed without confirmation: '{phrase}'")

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
