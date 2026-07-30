from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from fin_life_benchmark.benchmark.stage2_memory import normalize_stage2_answer

from .stage1 import STAGE1, STAGE1_MAX_OUTPUT_TOKENS
from .stage2_2 import (
    ALLOWED_STATUSES,
    SCHEMA_VERSION,
    STAGE2_2,
    VALUE_KINDS,
    value_schema_description,
)


# Stages whose reported comparison needs the rendered prompt preserved as an
# artifact and a reasoning-aware output budget. Stage 2 and masking keep the
# config default so their existing frozen outputs stay byte-identical.
_BUDGETED_STAGES = (STAGE1, STAGE2_2)


def answer_output_tokens(item: dict[str, Any]) -> int | None:
    if item.get("stage") not in _BUDGETED_STAGES:
        return None
    default = (
        STAGE1_MAX_OUTPUT_TOKENS if item.get("stage") == STAGE1 else 20_000
    )
    return int((item.get("metadata") or {}).get("max_output_tokens", default))


def expose_rendered_prompt(item: dict[str, Any]) -> bool:
    return item.get("stage") in _BUDGETED_STAGES


def _date(value: Any) -> str:
    try:
        parsed = date.fromisoformat(str(value))
        return f"{parsed.year}년 {parsed.month}월 {parsed.day}일"
    except ValueError:
        return str(value)


def format_session(session: dict[str, Any]) -> str:
    lines = [f"[{session['session_id']} | 상담일: {_date(session['session_date'])}]"]
    for turn in session.get("turns") or []:
        speaker = "고객" if turn.get("speaker") == "user" else "상담원"
        lines.append(f"{speaker}: {turn.get('text', '')}")
    return "\n".join(lines)


def format_s000(record: dict[str, Any]) -> str:
    lines = [f"[S000 | 초기 금융 상태 | 기준일: {_date(record['session_date'])}]"]
    for path, cell in sorted((record.get("state") or {}).items()):
        lines.append(
            f"- {path}: 상태={cell.get('status')}, 값="
            f"{json.dumps(cell.get('value'), ensure_ascii=False, sort_keys=True)}"
        )
    return "\n".join(lines)


def s000_as_session(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "trajectory_id": record["trajectory_id"],
        "session_id": "S000",
        "session_date": record["session_date"],
        "turns": [{"speaker": "user", "text": format_s000(record)}],
    }


def answer_contract(item: dict[str, Any]) -> str:
    if item["stage"] == STAGE2_2:
        return f'{{"schema_version":"{SCHEMA_VERSION}","state":{{...}}}}'
    if item["stage"] == STAGE1:
        return (
            '{"pairs":[{"event_id":"<EVENT_ID>",'
            '"evidence_session_id":"<D###>"}]}'
        )
    if (
        item["stage"] == "stage2_memory_value"
        and (item.get("metadata") or {}).get("answer_type") == "free_response"
    ):
        return "<answer>값</answer>"
    return "<answer>A</answer>"


def build_query(item: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    if item["stage"] == STAGE2_2:
        return _build_stage2_2_query(item, evidence)
    if item["stage"] == STAGE1:
        return _build_stage1_query(item, evidence)
    lines = [
        "아래 제공된 상담 이력 또는 검색 근거와 질문만 사용하세요.",
        "질문의 대상 기간과 현재 질의 checkpoint를 혼동하지 마세요.",
        "",
        "[제공된 이력/근거]",
        *(format_session(row) for row in evidence),
        "",
        f"[질문]\n{item['question']}",
    ]
    if item.get("options"):
        lines.extend(
            ["", "[선택지]", *(f"{o['option_id']}. {o['text']}" for o in item["options"])]
        )
    lines.extend(["", f"설명 없이 {answer_contract(item)} 형식으로만 답하세요."])
    return "\n".join(lines)


def _build_stage1_query(
    item: dict[str, Any], evidence: list[dict[str, Any]]
) -> str:
    candidates = (item.get("metadata") or {}).get("candidate_events") or []
    return "\n".join(
        [
            "다음은 한 고객의 은행 상담 세션 이력입니다.",
            "지금까지 실제로 일어난 모든 Life Event와, 각 발생을 처음 "
            "확정하는 상담 세션을 짝지어 보고하세요.",
            "",
            "판단 규칙:",
            "- 약한 암시, 앞으로의 계획, 예정, 취소된 사건은 보고하지 마세요.",
            "- 후속 처리나 뒤늦은 과거 회상은 최초 발생 확정 세션이 아닙니다.",
            "- 같은 event_id가 여러 번 발생했다면 각각 별도 pair로 쓰세요.",
            "- 사건 수는 알려져 있지 않으며, 발생 사건이 없을 수 있습니다.",
            "",
            "## 가능한 Life Event 목록",
            *(f"- {c['event_id']}: {c['label_ko']}" for c in candidates),
            "",
            "## 상담 세션 이력",
            *(
                _public_stage1_session(row)
                for row in evidence
                if str(row.get("session_id")) != "S000"
            ),
            "",
            "설명이나 Markdown 없이 JSON 객체 하나만 출력하세요.",
            '형식: {"pairs":[{"event_id":"<목록의 ID>",'
            '"evidence_session_id":"<보이는 D###>"}]}',
            "각 레코드에는 위 두 필드만 쓰고, 발생 사건이 없으면 "
            '{"pairs":[]}를 출력하세요.',
        ]
    )


def _public_session(session: dict[str, Any]) -> str:
    canonical = str(session["session_id"])
    public = (
        f"D{int(canonical[1:]):03d}"
        if canonical.startswith("S") and canonical[1:].isdigit()
        else canonical
    )
    rendered = {**session, "session_id": public}
    return format_session(rendered)


def _public_stage1_session(session: dict[str, Any]) -> str:
    """Render Stage 1 evidence without leaking IDs embedded by memory stores."""

    canonical = str(session["session_id"])
    public = (
        f"D{int(canonical[1:]):03d}"
        if canonical.startswith("S") and canonical[1:].isdigit()
        else canonical
    )
    lines = [f"[세션 {public}]"]
    for turn in session.get("turns") or []:
        speaker = "고객" if turn.get("speaker") == "user" else "상담원"
        text = re.sub(
            r"\bS(\d{3,})\b",
            lambda match: f"D{int(match.group(1)):03d}",
            str(turn.get("text", "")),
        )
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _build_stage2_2_query(
    item: dict[str, Any], evidence: list[dict[str, Any]]
) -> str:
    schema_lines = [
        f"- {path}: {value_schema_description(path)}"
        for path in VALUE_KINDS
    ]
    example = {
        "schema_version": SCHEMA_VERSION,
        "state": {
            "<각 required path>": {
                "value": "<현재 값 또는 null>",
                "status": "<허용 status>",
                "evidence_session_ids": ["D015"],
            }
        },
    }
    return "\n".join(
        [
            "초기 금융 memory와 checkpoint까지의 상담만 사용하여 현재 상태를 복원하세요.",
            "미래 계획이나 가능성은 현재 사실로 반영하지 마세요.",
            "일반 조회와 과거 회상은 명시적인 현재 상태 변경이 아니면 초기/최신 상태를 유지하세요.",
            "각 path의 value와 status는 checkpoint 시점의 최종 상태여야 합니다.",
            "초기 상태와 달라진 path에는 이를 뒷받침하는 D### 상담 ID를 하나 이상 쓰세요.",
            "초기 상태와 같은 path의 evidence_session_ids는 빈 배열로 쓰세요.",
            "설명, Markdown, 코드 펜스 없이 JSON 객체 하나만 출력하세요.",
            "",
            "[허용 status]",
            ", ".join(ALLOWED_STATUSES),
            "",
            "[필수 state schema: 아래 34개 path를 정확히 한 번씩 모두 출력]",
            *schema_lines,
            "",
            "[출력 구조 예시 — 값은 예시가 아니라 placeholder]",
            json.dumps(example, ensure_ascii=False, indent=2),
            "",
            "[초기 상태와 상담 이력]",
            *(_public_session(row) for row in evidence),
            "",
            f"[질문]\n{item['question']}",
        ]
    )


_ANSWER = re.compile(r"<answer>\s*([^<]+?)\s*</answer>", re.IGNORECASE)


def parse_answer(item: dict[str, Any], raw: str) -> str:
    match = _ANSWER.search(raw)
    value = match.group(1).strip() if match else ""
    if item["stage"] == STAGE1:
        return raw.strip()
    if item["stage"] == "stage2_memory_value":
        metadata = item.get("metadata") or {}
        if metadata.get("answer_type") == "free_response":
            return normalize_stage2_answer(
                value,
                metadata.get("normalizer"),
                metadata.get("answer_aliases") or {},
            )
    return value.upper()[:1] if value.upper()[:1] in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" else ""


def gold_answer(item: dict[str, Any]) -> str:
    if item["stage"] == STAGE1:
        return ""
    if item["stage"] == "stage2_memory_value":
        gold = item.get("gold") or {}
        if (item.get("metadata") or {}).get("answer_type") == "free_response":
            return str(gold.get("normalized_answer") or "")
    return str((item.get("gold") or {}).get("correct_option") or "")
