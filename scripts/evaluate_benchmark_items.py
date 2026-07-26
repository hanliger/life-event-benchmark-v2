#!/usr/bin/env python
"""Evaluate benchmark items with an LLM and report accuracy.

Examples:
  python scripts/evaluate_benchmark_items.py \
    --items data/generated/benchmark_items/stage2_single_hop_mcq.jsonl \
    --sessions-dir data/generated/sessions \
    --provider anthropic \
    --model claude-sonnet-5 \
    --execute \
    --output data/generated/eval/stage2_claude_sonnet_5_predictions.jsonl \
    --report data/generated/eval/stage2_claude_sonnet_5_report.json

Stage 2 and Stage 3 MCQ items are scored by exact option match. Stage 1 event-identification
items are scored by exact event_id match.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from collections import Counter
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from dotenv import load_dotenv
from tqdm import tqdm

from fin_life_benchmark.io import RepoPaths, ensure_dialogue_sessions, read_jsonl, write_jsonl
from fin_life_benchmark.llm.client import LLMClient


def _display_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"{parsed.year}년 {parsed.month}월 {parsed.day}일"


def _format_sessions(sessions: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for session in sessions:
        session_date = session.get("session_date")
        if session_date is None or not str(session_date).strip():
            raise ValueError(
                f"session_date is required for evaluation prompt: "
                f"{session.get('trajectory_id')}/{session.get('session_id')}"
            )
        lines = [f"[상담일: {_display_date(str(session_date))}]"]
        for turn in session.get("turns", []):
            speaker = "고객" if turn.get("speaker") == "user" else "상담원"
            lines.append(f"{speaker}: {turn.get('text', '')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _item_date_range(
    item: dict[str, Any], sessions: list[dict[str, Any]]
) -> tuple[str, str]:
    metadata = item.get("metadata") or {}
    start = metadata.get("target_date_start")
    end = metadata.get("target_date_end")
    if not start or not end:
        dates = [str(session.get("session_date") or "") for session in sessions]
        if not dates or not all(dates):
            raise ValueError("date-aware evaluation requires session_date values")
        start, end = dates[-15], dates[-1]
    return str(start), str(end)


def _display_date_range(item: dict[str, Any], sessions: list[dict[str, Any]]) -> str:
    start, end = _item_date_range(item, sessions)
    return f"{_display_date(start)}~{_display_date(end)}"


def _format_initial_memory(memory: dict[str, Any]) -> str:
    if not memory:
        return ""
    lines = ["[초기 금융 메모리]"]
    for path, cell in sorted(memory.items()):
        cell = cell or {}
        line = f"- {path}: 상태={cell.get('status')}, 값={cell.get('value')}"
        pending = cell.get("pending_proposal")
        if isinstance(pending, dict):
            line += f", 변경 예정={pending.get('value')}"
        lines.append(line)
    return "\n".join(lines)


def _load_sessions_by_id(sessions_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    sessions: dict[tuple[str, str], dict[str, Any]] = {}
    files = sorted(sessions_dir.glob("traj_*.jsonl"))
    if not files:
        files = sorted(sessions_dir.glob("sessions_*.jsonl"))
    for path in files:
        for session in read_jsonl(path):
            sessions[(session["trajectory_id"], session["session_id"])] = session
    return sessions



def _load_dialogues_by_id(dialogues_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    dialogues: dict[tuple[str, str], dict[str, Any]] = {}
    paths = sorted(dialogues_dir.glob("traj_*.jsonl"))
    if not paths:
        paths = sorted(dialogues_dir.glob("sessions_*.jsonl"))
    for path in paths:
        for dialogue in read_jsonl(path):
            key = (dialogue["trajectory_id"], dialogue["session_id"])
            if key in dialogues:
                raise ValueError(f"duplicate dialogue key: {key}")
            dialogues[key] = dialogue
    return dialogues
def _visible_sessions(
    item: dict[str, Any],
    sessions_by_id: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    trajectory_id = item["trajectory_id"]
    session_ids = list(item.get("visible_sessions", []))
    missing = [
        session_id
        for session_id in session_ids
        if (trajectory_id, session_id) not in sessions_by_id
    ]
    if missing:
        raise ValueError(
            f"missing visible sessions for {trajectory_id}: {missing[:5]}"
            + (" ..." if len(missing) > 5 else "")
        )
    return [sessions_by_id[(trajectory_id, session_id)] for session_id in session_ids]


def _build_stage3_prompt(
    item: dict[str, Any], sessions: list[dict[str, Any]]
) -> str:
    lines = [
        "다음은 한 고객의 은행 상담 세션 이력입니다.",
        "질문에 지정된 두 상담일의 정보를 각각 찾아 연결하거나 계산하세요.",
        "한 시점의 정보만으로 판단하지 말고, 두 시점의 근거를 모두 사용하세요.",
        "초기 금융 메모리는 전체 이력의 시작 상태를 확인하는 참고 정보로만 사용하세요.",
        "추측하지 말고 보기 중 하나만 고르세요.",
        "",
        _format_sessions(sessions),
        "",
    ]
    initial_memory = (item.get("metadata") or {}).get("initial_memory") or {}
    memory_text = _format_initial_memory(initial_memory)
    if memory_text:
        lines.extend([memory_text, ""])
    lines.extend([item["question"], ""])
    for option in item.get("options", []):
        lines.append(f"{option['option_id']}. {option['text']}")
    lines.extend(
        [
            "",
            '정답 선택지 하나만 JSON으로 답하세요. 예: {"answer": "A"}',
        ]
    )
    return "\n".join(lines)


def _build_stage2_prompt(
    item: dict[str, Any], sessions: list[dict[str, Any]]
) -> str:
    date_range = _display_date_range(item, sessions)
    lines = [
        "다음은 한 고객의 은행 상담 세션 이력입니다.",
        "제공된 전체 상담 이력과 질문에 지정된 날짜 범위를 기준으로 memory 상태를 판단하세요.",
        f"평가 대상 기간: {date_range}",
        "이 기간의 마지막으로 반영된 변화 또는 기간 종료 시점의 현재 상태를 판단하세요.",
        "초기 금융 메모리는 전체 이력의 시작 상태를 확인하는 참고 정보로 사용하세요. 추측하지 말고, 보기 중 하나만 고르세요.",
        "",
        _format_sessions(sessions),
        "",
    ]
    initial_memory = (item.get("metadata") or {}).get("initial_memory") or {}
    memory_text = _format_initial_memory(initial_memory)
    if memory_text:
        lines.extend([memory_text, ""])
    lines.extend([item["question"], ""])
    for option in item.get("options", []):
        lines.append(f"{option['option_id']}. {option['text']}")
    lines.extend(
        [
            "",
            '정답 선택지 하나만 JSON으로 답하세요. 예: {"answer": "A"}',
        ]
    )
    return "\n".join(lines)


def _build_stage1_event_identification_prompt(
    item: dict[str, Any], sessions: list[dict[str, Any]]
) -> str:
    metadata = item.get("metadata") or {}
    candidates = metadata.get("candidate_events") or []
    candidate_lines = [
        f"- {event['event_id']}: {event['label_ko']}" for event in candidates
    ]
    date_range = _display_date_range(item, sessions)
    lines = [
        "다음은 한 고객의 전체 은행 상담 세션 이력입니다.",
        "전체 이력을 참고하되, 질문에 지정된 날짜 범위만 대상으로 판단하세요.",
        "해당 기간에 마지막으로 실제 발생한(occurred) Life Event 하나를 가능한 목록에서 고르세요.",
        "Event 상태나 설명은 답하지 말고 event_id 하나만 답하세요.",
        "",
        _format_sessions(sessions),
        "",
        f"평가 대상 기간: {date_range}",
        item["question"],
        "",
        "가능한 Life Event 목록:",
        *candidate_lines,
        "",
        'JSON만 답하세요. 예: {"event_id": "career_employment"}',
    ]
    return "\n".join(lines)
def _extract_json(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            return None
    return None


def _parse_mcq_answer(raw: str) -> str:
    payload = _extract_json(raw)
    if payload is not None:
        answer = str(payload.get("answer", "")).strip().upper()
        if answer:
            return answer[:1]
    match = re.search(r"\b([A-E])\b", raw.strip().upper())
    return match.group(1) if match else ""


def _parse_stage1_event_identification_answer(raw: str) -> str:
    payload = _extract_json(raw)
    if not payload:
        return ""
    return str(payload.get("event_id", "")).strip()
def _score_item(item: dict[str, Any], raw: str) -> tuple[Any, Any, bool, str | None]:
    stage = item.get("stage")
    if stage in {"stage2_memory_mcq", "stage3_multi_hop_mcq"}:
        pred = _parse_mcq_answer(raw)
        gold = (item.get("gold") or {}).get("correct_option")
        return pred, gold, pred == gold, None if pred else "parse_error"
    if stage == "stage1_event_identification":
        pred = _parse_stage1_event_identification_answer(raw)
        gold = (item.get("gold") or {}).get("event_id")
        return pred, gold, pred == gold, None if pred else "parse_error"
    return None, None, False, f"unsupported_stage:{stage}"


def _build_prompt(item: dict[str, Any], sessions: list[dict[str, Any]]) -> str:
    if item.get("stage") == "stage2_memory_mcq":
        return _build_stage2_prompt(item, sessions)
    if item.get("stage") == "stage3_multi_hop_mcq":
        return _build_stage3_prompt(item, sessions)
    if item.get("stage") == "stage1_event_identification":
        return _build_stage1_event_identification_prompt(item, sessions)
    raise ValueError(f"unsupported stage: {item.get('stage')}")


def _mock_answer(item: dict[str, Any]) -> str:
    if item.get("stage") in {"stage2_memory_mcq", "stage3_multi_hop_mcq"}:
        options = item.get("options") or []
        return json.dumps({"answer": options[0]["option_id"] if options else "A"})
    if item.get("stage") == "stage1_event_identification":
        return json.dumps({"event_id": (item.get("gold") or {}).get("event_id", "")})
    raise ValueError(f"unsupported stage: {item.get('stage')}")


def _summarize(records: list[dict[str, Any]], provider: str, model: str) -> dict[str, Any]:
    by_stage: dict[str, dict[str, Any]] = {}
    for stage in sorted({r["stage"] for r in records}):
        subset = [r for r in records if r["stage"] == stage]
        correct = sum(1 for r in subset if r["correct"])
        by_stage[stage] = {
            "items": len(subset),
            "correct": correct,
            "accuracy": round(correct / len(subset), 4) if subset else None,
            "parse_errors": sum(1 for r in subset if r.get("error") == "parse_error"),
        }

    def grouped_stats(field: str, subset: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        values = sorted(
            {str(row.get(field)) for row in subset if row.get(field) is not None}
        )
        for value in values:
            group = [row for row in subset if str(row.get(field)) == value]
            correct = sum(1 for row in group if row["correct"])
            result[value] = {
                "items": len(group),
                "correct": correct,
                "accuracy": round(correct / len(group), 4) if group else None,
                "parse_errors": sum(
                    1 for row in group if row.get("error") == "parse_error"
                ),
            }
        return result

    memory_mcq_records = [
        row
        for row in records
        if row.get("stage") in {"stage2_memory_mcq", "stage3_multi_hop_mcq"}
    ]
    total = len(records)
    total_correct = sum(1 for r in records if r["correct"])
    return {
        "provider": provider,
        "model": model,
        "items": total,
        "correct": total_correct,
        "accuracy": round(total_correct / total, 4) if total else None,
        "accuracy_percent": round(100 * total_correct / total, 2) if total else None,
        "by_stage": by_stage,
        "by_reasoning_type": grouped_stats(
            "reasoning_type", memory_mcq_records
        ),
        "by_derivation_type": grouped_stats(
            "derivation_type", memory_mcq_records
        ),
        "errors": dict(Counter(r.get("error") for r in records if r.get("error"))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--items", nargs="+", required=True, help="benchmark item jsonl file(s)")
    parser.add_argument("--sessions-dir", default="data/generated/sessions")
    parser.add_argument("--dialogues-dir", default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--execute", action="store_true", help="call real LLM API; otherwise use mock answers")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--output", default="data/generated/eval/predictions.jsonl")
    parser.add_argument("--report", default="data/generated/eval/report.json")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()
    ensure_dialogue_sessions(args.sessions_dir)

    load_dotenv()
    if args.execute:
        provider = args.provider or os.environ.get("DEFAULT_LLM_PROVIDER")
        model = args.model or os.environ.get("DEFAULT_GENERATION_MODEL")
    else:
        provider = args.provider or "mock"
        model = args.model or "mock"
    if args.execute and (
        not provider or not model or provider == "mock" or model == "mock"
    ):
        raise SystemExit(
            "--execute requires --provider/--model or non-mock "
            "DEFAULT_LLM_PROVIDER/DEFAULT_GENERATION_MODEL values"
        )

    items: list[dict[str, Any]] = []
    for item_path in args.items:
        items.extend(read_jsonl(Path(item_path)))
    if args.max_items is not None:
        items = items[: args.max_items]
    if not items:
        raise SystemExit("no benchmark items loaded")

    has_stage1 = any(item.get("stage") == "stage1_event_identification" for item in items)
    has_stage2 = any(item.get("stage") == "stage2_memory_mcq" for item in items)
    has_stage3 = any(
        item.get("stage") == "stage3_multi_hop_mcq" for item in items
    )
    dialogue_input_dir = Path(args.dialogues_dir or args.sessions_dir)
    sessions_by_id = (
        _load_sessions_by_id(dialogue_input_dir)
        if has_stage2 or has_stage3
        else {}
    )
    dialogues_by_id = _load_dialogues_by_id(dialogue_input_dir) if has_stage1 else {}
    if has_stage1 and not dialogues_by_id:
        raise SystemExit(f"no dialogue records under {dialogue_input_dir}")
    if (has_stage2 or has_stage3) and not sessions_by_id:
        raise SystemExit(f"no dialogue records under {dialogue_input_dir}")
    client = None
    if args.execute:
        client = LLMClient(provider=provider, model=model, temperature=args.temperature, max_tokens=args.max_tokens)

    output = Path(args.output)
    report = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("", encoding="utf-8")

    records: list[dict[str, Any]] = []
    system = (
        RepoPaths.default().prompts / "system" / "benchmark_evaluator_ko.txt"
    ).read_text(encoding="utf-8").strip()
    for item in tqdm(items, desc="evaluate"):
        if item.get("stage") == "stage1_event_identification":
            visible = _visible_sessions(item, dialogues_by_id)
        else:
            visible = _visible_sessions(item, sessions_by_id)
        prompt = _build_prompt(item, visible)
        if args.execute:
            assert client is not None
            raw = client.generate(system=system, user=prompt)
            metadata = client.last_response_metadata
        else:
            raw = _mock_answer(item)
            metadata = {"provider": "mock", "model": "mock"}
        pred, gold, correct, error = _score_item(item, raw)
        record = {
            "item_id": item.get("item_id"),
            "stage": item.get("stage"),
            "reasoning_type": (
                item.get("reasoning_type")
                or (
                    "single_hop"
                    if item.get("stage") == "stage2_memory_mcq"
                    else "multi_hop"
                    if item.get("stage") == "stage3_multi_hop_mcq"
                    else None
                )
            ),
            "derivation_type": (item.get("metadata") or {}).get("derivation_type"),
            "trajectory_id": item.get("trajectory_id"),
            "prefix_id": item.get("prefix_id"),
            "n_visible_sessions": len(item.get("visible_sessions", [])),
            "prediction": pred,
            "gold": gold,
            "correct": correct,
            "error": error,
            "raw_response": raw,
            "response_metadata": metadata,
        }
        records.append(record)
        with output.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = _summarize(records, provider or "mock", model or "mock")
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"evaluated {summary['items']} items: {summary['correct']} correct -> acc {summary['accuracy_percent']}%")
    for stage, stats in summary["by_stage"].items():
        print(f"  {stage}: {stats['correct']}/{stats['items']} = {stats['accuracy'] * 100:.2f}%")
    print(f"predictions -> {output}")
    print(f"report -> {report}")
    if not args.execute:
        print("NOTE: mock answers only. Add --execute with --provider/--model for real LLM evaluation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
