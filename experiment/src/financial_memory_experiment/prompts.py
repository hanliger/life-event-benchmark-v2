from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from fin_life_benchmark.benchmark.stage2_memory import normalize_stage2_answer

from .stage2_2 import (
    ALLOWED_STATUSES,
    SCHEMA_VERSION,
    STAGE2_2,
    VALUE_KINDS,
    value_schema_description,
)


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
    if item["stage"] == "stage1_event_identification":
        return "<answer>event_id</answer>"
    if (
        item["stage"] == "stage2_memory_value"
        and (item.get("metadata") or {}).get("answer_type") == "free_response"
    ):
        return "<answer>값</answer>"
    return "<answer>A</answer>"


def build_query(item: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    if item["stage"] == STAGE2_2:
        return _build_stage2_2_query(item, evidence)
    lines = [
        "아래 제공된 상담 이력 또는 검색 근거와 질문만 사용하세요.",
        "질문의 대상 기간과 현재 질의 checkpoint를 혼동하지 마세요.",
        *(
            [
                "이 질문은 서로 다른 두 상담일의 근거를 모두 사용해야 합니다.",
                "한 시점만 보고 답하지 말고, 질문에 따라 두 값을 순서대로 연결하거나 합산하세요.",
            ]
            if item["stage"] == "stage3_multi_hop_mcq"
            else []
        ),
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


def _public_session(session: dict[str, Any]) -> str:
    canonical = str(session["session_id"])
    public = (
        f"D{int(canonical[1:]):03d}"
        if canonical.startswith("S") and canonical[1:].isdigit()
        else canonical
    )
    rendered = {**session, "session_id": public}
    return format_session(rendered)


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
