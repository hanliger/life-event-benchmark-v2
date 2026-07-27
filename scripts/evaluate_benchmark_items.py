#!/usr/bin/env python
"""Evaluate Stage 1 and Stage 2 benchmark items with an LLM.

Stage 2 supports both closed-domain MCQ and normalized free-response items.
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

from fin_life_benchmark.benchmark.stage2_memory import normalize_stage2_answer
from fin_life_benchmark.io import RepoPaths, ensure_dialogue_sessions, read_jsonl
from fin_life_benchmark.llm.client import LLMClient


def _format_sessions(
    sessions: list[dict[str, Any]],
    *,
    use_dates: bool = False,
) -> str:
    blocks: list[str] = []
    for session in sessions:
        if use_dates:
            heading = f"[상담일 {session.get('session_date', '날짜 미상')}]"
        else:
            heading = f"[세션 {session['session_id']}]"
        lines = [heading]
        for turn in session.get("turns", []):
            speaker = "고객" if turn.get("speaker") == "user" else "상담원"
            lines.append(f"{speaker}: {turn.get('text', '')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _format_initial_memory(memory: dict[str, Any]) -> str:
    if not memory:
        return ""
    lines = ["[초기 금융 메모리]"]
    for path, raw_cell in sorted(memory.items()):
        cell = raw_cell or {}
        lines.append(
            f"- {path}: 상태={cell.get('status')}, 값={cell.get('value')}"
        )
    return "\n".join(lines)


def _load_sessions_by_id(
    sessions_dir: Path,
) -> dict[tuple[str, str], dict[str, Any]]:
    sessions: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(sessions_dir.glob("sessions_*.jsonl")):
        for session in read_jsonl(path):
            sessions[(session["trajectory_id"], session["session_id"])] = session
    return sessions


def _visible_sessions(
    item: dict[str, Any],
    sessions_by_id: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    trajectory_id = item["trajectory_id"]
    return [
        sessions_by_id[(trajectory_id, session_id)]
        for session_id in item.get("visible_sessions", [])
        if (trajectory_id, session_id) in sessions_by_id
    ]


def _build_stage2_prompt(
    item: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> str:
    metadata = item.get("metadata") or {}
    answer_type = metadata.get("answer_type") or (item.get("gold") or {}).get(
        "answer_type"
    )
    lines = [
        "다음은 한 고객의 날짜순 은행 상담 이력입니다.",
        "보이는 상담 발화와 초기 금융 메모리만 근거로 답하세요.",
        "질문에 적힌 기준일 시점의 값을 답하고, Life Event를 추측해 설명하지 마세요.",
        "",
        _format_sessions(sessions, use_dates=True),
        "",
    ]
    memory_text = _format_initial_memory(metadata.get("initial_memory") or {})
    if memory_text:
        lines.extend([memory_text, ""])
    lines.extend([item["question"], ""])

    if answer_type == "mcq":
        for option in item.get("options", []):
            lines.append(f"{option['option_id']}. {option['text']}")
        lines.extend(
            ["", '정답 선택지 하나만 JSON으로 답하세요. 예: {"answer": "A"}']
        )
    else:
        lines.append(
            '정답 값만 JSON으로 답하세요. 예: {"answer": "300만원"}'
        )
    return "\n".join(lines)


def _build_stage1_prompt(
    item: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> str:
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
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(raw[start : end + 1])
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None
    return None


def _parse_mcq_answer(raw: str) -> str:
    payload = _extract_json(raw)
    if payload is not None:
        answer = str(payload.get("answer", "")).strip().upper()
        if answer:
            return answer[:1]
    match = re.search(r"\b([A-Z])\b", raw.strip().upper())
    return match.group(1) if match else ""


def _parse_free_response(raw: str) -> tuple[Any, bool]:
    payload = _extract_json(raw)
    if payload is not None and "answer" in payload:
        # An explicit JSON null is a valid answer for a memory value that is absent.
        return payload["answer"], True
    text = raw.strip()
    return (text, True) if text else (None, False)


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
    return [
        {
            "life_event_label": event.get("life_event_label"),
            "event_status": event.get("event_status"),
        }
        for event in events
        if isinstance(event, dict)
    ]


def _score_item(
    item: dict[str, Any],
    raw: str,
) -> tuple[Any, Any, bool, str | None]:
    stage = item.get("stage")
    if stage == "stage2_memory_value":
        gold_payload = item.get("gold") or {}
        metadata = item.get("metadata") or {}
        answer_type = metadata.get("answer_type") or gold_payload.get("answer_type")
        if answer_type == "mcq":
            pred = _parse_mcq_answer(raw)
            gold = gold_payload.get("correct_option")
            return pred, gold, pred == gold, None if pred else "parse_error"
        if answer_type == "free_response":
            pred, parsed = _parse_free_response(raw)
            normalizer = metadata.get("normalizer")
            normalized_pred = (
                normalize_stage2_answer(
                    pred,
                    normalizer,
                    metadata.get("answer_aliases") or {},
                )
                if parsed
                else None
            )
            normalized_gold = gold_payload.get("normalized_answer")
            return (
                pred,
                gold_payload.get("answer_value"),
                normalized_pred == normalized_gold,
                None if parsed else "parse_error",
            )
        return None, None, False, f"unsupported_answer_type:{answer_type}"
    if stage == "stage1_event_status":
        pred_events = _parse_stage1_answer(raw)
        gold_events = (item.get("gold") or {}).get("life_events") or []
        pred = sorted({_event_key(event) for event in pred_events})
        gold = sorted({_event_key(event) for event in gold_events})
        return (
            pred_events,
            gold_events,
            pred == gold,
            None if pred_events else "parse_error",
        )
    return None, None, False, f"unsupported_stage:{stage}"


def _build_prompt(
    item: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> str:
    if item.get("stage") == "stage2_memory_value":
        return _build_stage2_prompt(item, sessions)
    if item.get("stage") == "stage1_event_status":
        return _build_stage1_prompt(item, sessions)
    raise ValueError(f"unsupported stage: {item.get('stage')}")


def _mock_answer(item: dict[str, Any]) -> str:
    if item.get("stage") == "stage2_memory_value":
        gold = item.get("gold") or {}
        if gold.get("answer_type") == "mcq":
            return json.dumps({"answer": gold.get("correct_option")})
        return json.dumps(
            {"answer": gold.get("answer_value")}, ensure_ascii=False
        )
    return json.dumps(
        {
            "life_events": [
                {"life_event_label": None, "event_status": "no_event"}
            ]
        },
        ensure_ascii=False,
    )


def _dimension_summary(
    records: list[dict[str, Any]],
    key: str,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    values = sorted({str(record.get(key) or "unknown") for record in records})
    for value in values:
        subset = [record for record in records if str(record.get(key) or "unknown") == value]
        correct = sum(1 for record in subset if record["correct"])
        output[value] = {
            "items": len(subset),
            "correct": correct,
            "accuracy": round(correct / len(subset), 4) if subset else None,
        }
    return output


def _summarize(
    records: list[dict[str, Any]],
    provider: str,
    model: str,
) -> dict[str, Any]:
    total = len(records)
    total_correct = sum(1 for record in records if record["correct"])
    return {
        "provider": provider,
        "model": model,
        "items": total,
        "correct": total_correct,
        "accuracy": round(total_correct / total, 4) if total else None,
        "accuracy_percent": (
            round(100 * total_correct / total, 2) if total else None
        ),
        "by_stage": _dimension_summary(records, "stage"),
        "by_answer_type": _dimension_summary(records, "answer_type"),
        "by_memory_path": _dimension_summary(records, "memory_path"),
        "by_checkpoint_session_count": _dimension_summary(
            records, "checkpoint_session_count"
        ),
        "by_checkpoint_change_type": _dimension_summary(
            records, "checkpoint_change_type"
        ),
        "errors": dict(
            Counter(record.get("error") for record in records if record.get("error"))
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--items", nargs="+", required=True, help="benchmark item jsonl file(s)"
    )
    parser.add_argument("--sessions-dir", default="data/generated/sessions")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--execute", action="store_true", help="call real LLM API"
    )
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--output", default="data/generated/eval/predictions.jsonl")
    parser.add_argument("--report", default="data/generated/eval/report.json")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()
    ensure_dialogue_sessions(args.sessions_dir)

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
        client = LLMClient(
            provider=provider,
            model=model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )

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
        visible = _visible_sessions(item, sessions_by_id)
        prompt = _build_prompt(item, visible)
        if args.execute:
            assert client is not None
            raw = client.generate(system=system, user=prompt)
            response_metadata = client.last_response_metadata
        else:
            raw = _mock_answer(item)
            response_metadata = {"provider": "mock", "model": "mock"}
        pred, gold, correct, error = _score_item(item, raw)
        item_metadata = item.get("metadata") or {}
        item_gold = item.get("gold") or {}
        record = {
            "item_id": item.get("item_id"),
            "stage": item.get("stage"),
            "trajectory_id": item.get("trajectory_id"),
            "prefix_id": item.get("prefix_id"),
            "n_visible_sessions": len(item.get("visible_sessions", [])),
            "answer_type": item_metadata.get("answer_type"),
            "memory_path": item_gold.get("memory_path"),
            "value_selector": item_gold.get("value_selector"),
            "checkpoint_session_count": item_metadata.get(
                "checkpoint_session_count"
            ),
            "checkpoint_date": item_metadata.get("checkpoint_date"),
            "checkpoint_change_type": item_gold.get("checkpoint_change_type"),
            "prediction": pred,
            "gold": gold,
            "correct": correct,
            "error": error,
            "raw_response": raw,
            "response_metadata": response_metadata,
        }
        records.append(record)
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = _summarize(records, provider or "mock", model or "mock")
    report.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"evaluated {summary['items']} items: {summary['correct']} correct "
        f"-> acc {summary['accuracy_percent']}%"
    )
    for stage, stats in summary["by_stage"].items():
        print(
            f"  {stage}: {stats['correct']}/{stats['items']} "
            f"= {stats['accuracy'] * 100:.2f}%"
        )
    print(f"predictions -> {output}")
    print(f"report -> {report}")
    if not args.execute:
        print("NOTE: mock gold answers only; add --execute for a real LLM run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
