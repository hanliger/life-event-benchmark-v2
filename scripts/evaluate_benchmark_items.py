#!/usr/bin/env python
"""Evaluate benchmark items with an LLM and report accuracy.

Examples:
  python scripts/evaluate_benchmark_items.py \
    --items data/generated/benchmark_items/stage2_memory_mcq.jsonl \
    --sessions-dir data/generated/sessions \
    --provider anthropic \
    --model claude-sonnet-5 \
    --execute \
    --output data/generated/eval/stage2_claude_sonnet_5_predictions.jsonl \
    --report data/generated/eval/stage2_claude_sonnet_5_report.json

Stage 2 MCQ is scored by exact option match. Stage 1 event-status items are
also supported with exact set matching over (life_event_label, event_status),
but Stage 2 is the cleaner first-pass accuracy benchmark.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from dotenv import load_dotenv
from tqdm import tqdm

from fin_life_benchmark.io import read_jsonl, write_jsonl
from fin_life_benchmark.llm.client import LLMClient


def _format_sessions(sessions: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for session in sessions:
        lines = [f"[세션 {session['session_id']}]"]
        for turn in session.get("turns", []):
            speaker = "고객" if turn.get("speaker") == "user" else "상담원"
            lines.append(f"{speaker}: {turn.get('text', '')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _format_initial_memory(memory: dict[str, Any]) -> str:
    if not memory:
        return ""
    lines = ["[초기 금융 메모리]"]
    for path, cell in sorted(memory.items()):
        cell = cell or {}
        lines.append(f"- {path}: 상태={cell.get('status')}, 값={cell.get('value')}")
    return "\n".join(lines)


def _load_sessions_by_id(sessions_dir: Path) -> dict[str, dict[str, Any]]:
    sessions: dict[str, dict[str, Any]] = {}
    for path in sorted(sessions_dir.glob("sessions_*.jsonl")):
        for session in read_jsonl(path):
            sessions[session["session_id"]] = session
    return sessions


def _visible_sessions(item: dict[str, Any], sessions_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [sessions_by_id[sid] for sid in item.get("visible_sessions", []) if sid in sessions_by_id]


def _build_stage2_prompt(item: dict[str, Any], sessions: list[dict[str, Any]]) -> str:
    lines = [
        "다음은 한 고객의 은행 상담 세션 이력입니다.",
        "현재 보이는 상담 이력과 초기 금융 메모리만 근거로 문제를 푸세요.",
        "추측하지 말고, 보기 중 가장 적절한 선택지 하나만 고르세요.",
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
    lines.extend([
        "",
        '정답 선택지 하나만 JSON으로 답하세요. 예: {"answer": "A"}',
    ])
    return "\n".join(lines)


def _build_stage1_prompt(item: dict[str, Any], sessions: list[dict[str, Any]]) -> str:
    lines = [
        "다음은 한 고객의 은행 상담 세션 이력입니다.",
        "현재 보이는 상담 이력만 근거로 감지되는 Life Event와 상태를 답하세요.",
        "상태는 weak_signal, upcoming, occurred, cancelled 중 하나입니다.",
        "확인되는 이벤트가 없으면 no_event로 답하세요.",
        "",
        _format_sessions(sessions),
        "",
        item["question"],
        "",
        "JSON만 답하세요.",
        '이벤트가 있으면: {"life_events": [{"life_event_label": "이사", "event_status": "weak_signal"}]}',
        '이벤트가 없으면: {"life_events": [{"life_event_label": null, "event_status": "no_event"}]}',
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


def _event_key(event: dict[str, Any]) -> tuple[str | None, str]:
    label = event.get("life_event_label")
    status = event.get("event_status")
    if status == "no_event":
        label = None
    return label, str(status)


def _parse_stage1_answer(raw: str) -> list[dict[str, Any]]:
    payload = _extract_json(raw)
    if not payload:
        return []
    events = payload.get("life_events")
    if isinstance(events, dict):
        events = [events]
    if not isinstance(events, list):
        return []
    parsed = []
    for event in events:
        if not isinstance(event, dict):
            continue
        parsed.append(
            {
                "life_event_label": event.get("life_event_label"),
                "event_status": event.get("event_status"),
            }
        )
    return parsed


def _score_item(item: dict[str, Any], raw: str) -> tuple[Any, Any, bool, str | None]:
    stage = item.get("stage")
    if stage == "stage2_memory_mcq":
        pred = _parse_mcq_answer(raw)
        gold = (item.get("gold") or {}).get("correct_option")
        return pred, gold, pred == gold, None if pred else "parse_error"
    if stage == "stage1_event_status":
        pred_events = _parse_stage1_answer(raw)
        gold_events = (item.get("gold") or {}).get("life_events") or []
        pred = sorted({_event_key(event) for event in pred_events})
        gold = sorted({_event_key(event) for event in gold_events})
        return pred_events, gold_events, pred == gold, None if pred_events else "parse_error"
    return None, None, False, f"unsupported_stage:{stage}"


def _build_prompt(item: dict[str, Any], sessions: list[dict[str, Any]]) -> str:
    if item.get("stage") == "stage2_memory_mcq":
        return _build_stage2_prompt(item, sessions)
    if item.get("stage") == "stage1_event_status":
        return _build_stage1_prompt(item, sessions)
    raise ValueError(f"unsupported stage: {item.get('stage')}")


def _mock_answer(item: dict[str, Any]) -> str:
    if item.get("stage") == "stage2_memory_mcq":
        options = item.get("options") or []
        return json.dumps({"answer": options[0]["option_id"] if options else "A"})
    return json.dumps({"life_events": [{"life_event_label": None, "event_status": "no_event"}]}, ensure_ascii=False)


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
        "errors": dict(Counter(r.get("error") for r in records if r.get("error"))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--items", nargs="+", required=True, help="benchmark item jsonl file(s)")
    parser.add_argument("--sessions-dir", default="data/generated/sessions")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--execute", action="store_true", help="call real LLM API; otherwise use mock answers")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--output", default="data/generated/eval/predictions.jsonl")
    parser.add_argument("--report", default="data/generated/eval/report.json")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()

    load_dotenv()
    provider = args.provider or ("mock" if not args.execute else None)
    model = args.model or ("mock" if not args.execute else None)
    if args.execute and (not provider or not model):
        raise SystemExit("--execute requires --provider and --model")

    items: list[dict[str, Any]] = []
    for item_path in args.items:
        items.extend(read_jsonl(Path(item_path)))
    if args.max_items is not None:
        items = items[: args.max_items]
    if not items:
        raise SystemExit("no benchmark items loaded")

    sessions_by_id = _load_sessions_by_id(Path(args.sessions_dir))
    client = None
    if args.execute:
        client = LLMClient(provider=provider, model=model, temperature=args.temperature, max_tokens=args.max_tokens)

    output = Path(args.output)
    report = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("", encoding="utf-8")

    records: list[dict[str, Any]] = []
    system = "당신은 금융 상담 이력 기반 벤치마크를 푸는 평가 대상 모델입니다. 반드시 요청된 JSON만 출력하세요."
    for item in tqdm(items, desc="evaluate"):
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
