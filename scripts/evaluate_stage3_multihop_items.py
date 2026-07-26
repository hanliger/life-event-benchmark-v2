#!/usr/bin/env python
"""Evaluate Stage 3 Multi-hop MCQ items with an LLM."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from dotenv import load_dotenv
from tqdm import tqdm

from evaluate_benchmark_items import (
    _format_initial_memory,
    _format_sessions,
    _load_sessions_by_id,
    _parse_mcq_answer,
    _visible_sessions,
)
from fin_life_benchmark.io import RepoPaths, ensure_dialogue_sessions, read_jsonl
from fin_life_benchmark.llm.client import LLMClient


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


def _grouped_stats(
    records: list[dict[str, Any]], field: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in sorted({str(row.get(field)) for row in records}):
        rows = [row for row in records if str(row.get(field)) == value]
        correct = sum(1 for row in rows if row["correct"])
        result[value] = {
            "items": len(rows),
            "correct": correct,
            "accuracy": round(correct / len(rows), 4) if rows else None,
            "parse_errors": sum(
                1 for row in rows if row.get("error") == "parse_error"
            ),
        }
    return result


def _summarize(
    records: list[dict[str, Any]], provider: str, model: str
) -> dict[str, Any]:
    total = len(records)
    correct = sum(1 for row in records if row["correct"])
    return {
        "provider": provider,
        "model": model,
        "stage": "stage3_multi_hop_mcq",
        "items": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else None,
        "accuracy_percent": round(100 * correct / total, 2) if total else None,
        "by_derivation_type": _grouped_stats(records, "derivation_type"),
        "by_trajectory": _grouped_stats(records, "trajectory_id"),
        "errors": dict(
            Counter(row.get("error") for row in records if row.get("error"))
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument(
        "--output",
        default="data/generated/eval/stage3_predictions.jsonl",
    )
    parser.add_argument(
        "--report",
        default="data/generated/eval/stage3_report.json",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()

    ensure_dialogue_sessions(args.sessions_dir)
    items = list(read_jsonl(Path(args.items)))
    if args.max_items is not None:
        items = items[: args.max_items]
    if not items:
        raise SystemExit("no Stage 3 items loaded")
    invalid_stages = sorted(
        {
            str(item.get("stage"))
            for item in items
            if item.get("stage") != "stage3_multi_hop_mcq"
        }
    )
    if invalid_stages:
        raise SystemExit(f"non-Stage 3 items supplied: {invalid_stages}")

    sessions_by_id = _load_sessions_by_id(Path(args.sessions_dir))
    if not sessions_by_id:
        raise SystemExit(f"no dialogue records under {args.sessions_dir}")

    load_dotenv()
    if args.execute:
        provider = args.provider or os.environ.get("DEFAULT_LLM_PROVIDER")
        model = args.model or os.environ.get("DEFAULT_GENERATION_MODEL")
        if not provider or not model or provider == "mock" or model == "mock":
            raise SystemExit(
                "--execute requires --provider/--model or non-mock "
                "DEFAULT_LLM_PROVIDER/DEFAULT_GENERATION_MODEL values"
            )
        client = LLMClient(
            provider=provider,
            model=model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    else:
        provider = "mock"
        model = "mock"
        client = None

    output = Path(args.output)
    report = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("", encoding="utf-8")

    system = (
        RepoPaths.default().prompts / "system" / "benchmark_evaluator_ko.txt"
    ).read_text(encoding="utf-8").strip()
    records: list[dict[str, Any]] = []
    for item in tqdm(items, desc="evaluate-stage3"):
        visible = _visible_sessions(item, sessions_by_id)
        prompt = _build_stage3_prompt(item, visible)
        if client is not None:
            raw = client.generate(system=system, user=prompt)
            response_metadata = client.last_response_metadata
        else:
            options = item.get("options") or []
            raw = json.dumps(
                {"answer": options[0]["option_id"] if options else "A"}
            )
            response_metadata = {"provider": "mock", "model": "mock"}
        prediction = _parse_mcq_answer(raw)
        gold = (item.get("gold") or {}).get("correct_option")
        record = {
            "item_id": item.get("item_id"),
            "stage": "stage3_multi_hop_mcq",
            "reasoning_type": "multi_hop",
            "derivation_type": (item.get("metadata") or {}).get(
                "derivation_type"
            ),
            "trajectory_id": item.get("trajectory_id"),
            "prefix_id": item.get("prefix_id"),
            "n_visible_sessions": len(item.get("visible_sessions") or []),
            "prediction": prediction,
            "gold": gold,
            "correct": prediction == gold,
            "error": None if prediction else "parse_error",
            "raw_response": raw,
            "response_metadata": response_metadata,
        }
        records.append(record)
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = _summarize(records, provider, model)
    report.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"evaluated {summary['items']} Stage 3 items: "
        f"{summary['correct']} correct -> acc {summary['accuracy_percent']}%"
    )
    print(f"predictions -> {output}")
    print(f"report -> {report}")
    if client is None:
        print("NOTE: mock answers only. Add --execute for real LLM evaluation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
