from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from fin_life_benchmark.benchmark.stage2_memory import normalize_stage2_answer


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
    if item["stage"] == "stage1_event_identification":
        return "<answer>event_id</answer>"
    if (
        item["stage"] == "stage2_memory_value"
        and (item.get("metadata") or {}).get("answer_type") == "free_response"
    ):
        return "<answer>값</answer>"
    return "<answer>A</answer>"


def build_query(item: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    lines = [
        "아래 제공된 상담 이력 또는 검색 근거와 질문만 사용하세요.",
        "질문의 대상 기간과 현재 질의 checkpoint를 혼동하지 마세요.",
        "",
        "[제공된 이력/근거]",
        *(format_session(row) for row in evidence),
        "",
        f"[질문]\n{item['question']}",
    ]
    if item["stage"] == "stage1_event_identification":
        candidates = (item.get("metadata") or {}).get("candidate_events") or []
        lines.extend(
            ["", "[가능한 event_id]", *(f"- {c['event_id']}: {c['label_ko']}" for c in candidates)]
        )
    elif item.get("options"):
        lines.extend(
            ["", "[선택지]", *(f"{o['option_id']}. {o['text']}" for o in item["options"])]
        )
    lines.extend(["", f"설명 없이 {answer_contract(item)} 형식으로만 답하세요."])
    return "\n".join(lines)


_ANSWER = re.compile(r"<answer>\s*([^<]+?)\s*</answer>", re.IGNORECASE)


def parse_answer(item: dict[str, Any], raw: str) -> str:
    match = _ANSWER.search(raw)
    value = match.group(1).strip() if match else ""
    if item["stage"] == "stage1_event_identification":
        return value
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
    if item["stage"] == "stage1_event_identification":
        return str((item.get("gold") or {}).get("event_id") or "")
    if item["stage"] == "stage2_memory_value":
        gold = item.get("gold") or {}
        if (item.get("metadata") or {}).get("answer_type") == "free_response":
            return str(gold.get("normalized_answer") or "")
    return str((item.get("gold") or {}).get("correct_option") or "")
