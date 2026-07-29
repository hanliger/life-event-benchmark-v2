#!/usr/bin/env python
"""Run the history-necessity filter over Stage 2 MCQ items.

Example:
  python scripts/run_history_filter.py \
    --items data/generated/benchmark_items/stage2_memory_value.jsonl \
    --sessions-dir data/generated/sessions \
    --mode single_session \
    --validators openai:gpt-4o-mini,anthropic:claude-haiku-4-5 \
    --max-items 20 --execute

Without --execute (or without API keys) a mock validator is used and the run
is clearly marked as placeholder.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import _bootstrap  # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.io import ensure_dialogue_sessions, read_jsonl, write_jsonl
from fin_life_benchmark.validation.history_filter import MODES, parse_validators, run_filter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--items", required=True)
    parser.add_argument("--sessions-dir", default="data/generated/sessions")
    parser.add_argument("--mode", choices=MODES, default="single_session")
    parser.add_argument("--validators", default=None, help="provider:model[,provider:model...]")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--execute", action="store_true", help="allow real API validators")
    parser.add_argument("--output", default=None, help="default: <items>.filtered.jsonl")
    parser.add_argument("--report", default="data/generated/quality_reports/history_filter_report.json")
    args = parser.parse_args()
    ensure_dialogue_sessions(args.sessions_dir)

    load_dotenv()
    spec = args.validators or os.environ.get("HISTORY_FILTER_VALIDATORS", "mock:mock-validator")
    if not args.execute:
        if not spec.startswith("mock"):
            print("no --execute: falling back to mock validator (placeholder verdicts, no API calls)")
        spec = "mock:mock-validator"
    else:
        missing = []
        if "openai:" in spec and not os.environ.get("OPENAI_API_KEY"):
            missing.append("OPENAI_API_KEY")
        if "anthropic:" in spec and not os.environ.get("ANTHROPIC_API_KEY"):
            missing.append("ANTHROPIC_API_KEY")
        if ("gemini:" in spec or "google:" in spec) and not (
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        ):
            missing.append("GEMINI_API_KEY or GOOGLE_API_KEY")
        if missing:
            print(f"API keys missing ({', '.join(missing)}): falling back to mock validator")
            spec = "mock:mock-validator"

    validators = parse_validators(spec)

    items_path = Path(args.items)
    all_items = list(read_jsonl(items_path)) if items_path.exists() else []
    if args.max_items is not None:
        all_items = all_items[: args.max_items]
    items = [
        item for item in all_items
        if (item.get("metadata") or {}).get("answer_type") == "mcq"
    ]
    if not all_items:
        # Small smoke runs can legitimately produce zero Stage 2 items.
        output = Path(args.output) if args.output else items_path.with_suffix("").with_suffix(".filtered.jsonl")
        write_jsonl(output, [])
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"items": 0, "mode": args.mode, "by_status": {}}, indent=2), encoding="utf-8")
        print("no items to filter (0 MCQ items) — wrote empty filtered file and report")
        return 0

    sessions_by_id: dict[tuple[str, str], dict] = {}
    for path in sorted(Path(args.sessions_dir).glob("sessions_*.jsonl")):
        for session in read_jsonl(path):
            sessions_by_id[(session["trajectory_id"], session["session_id"])] = session

    filtered_mcq = run_filter(items, sessions_by_id, validators, args.mode)
    filtered_by_id = {item["item_id"]: item for item in filtered_mcq}
    results = []
    for item in all_items:
        if item["item_id"] in filtered_by_id:
            results.append(filtered_by_id[item["item_id"]])
        else:
            preserved = dict(item)
            preserved["filter_status"] = "keep"
            preserved["filter_meta"] = {
                "mode": args.mode,
                "reason": "history filter currently applies to MCQ only",
            }
            results.append(preserved)

    output = Path(args.output) if args.output else Path(args.items).with_suffix("").with_suffix(".filtered.jsonl")
    write_jsonl(output, results)

    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["filter_status"]] = by_status.get(r["filter_status"], 0) + 1

    # Aggregate signal: a history-free validator's overall accuracy vs the
    # majority-answer baseline. Per-item leakage flags can over-report when a
    # prior happens to match one item; only aggregate-above-baseline indicates
    # the set is broadly solvable without the full conversation history.
    n = len(items)
    total_votes = sum(len(r.get("filter_votes", [])) for r in filtered_mcq)
    correct_votes = sum(
        1 for r in filtered_mcq for v in r.get("filter_votes", []) if v.get("correct")
    )
    overall_acc = round(correct_votes / total_votes, 4) if total_votes else None
    answer_counts: dict[str, int] = {}
    for r in filtered_mcq:
        answer = (r.get("gold") or {}).get("correct_option")
        if answer:
            answer_counts[answer] = answer_counts.get(answer, 0) + 1
    majority_baseline = round(max(answer_counts.values()) / n, 4) if answer_counts and n else None
    beats_baseline = (
        overall_acc is not None and majority_baseline is not None and overall_acc > majority_baseline + 0.05
    )

    report = {
        "items": len(results),
        "mcq_items_evaluated": len(items),
        "free_response_items_skipped": len(all_items) - len(items),
        "mode": args.mode,
        "validators": [getattr(v, "name", "?") for v in validators],
        "mock_only": all(getattr(v, "provider", "") == "mock" for v in validators),
        "by_status": by_status,
        "overall_history_free_accuracy": overall_acc,
        "majority_answer_baseline": majority_baseline,
        "beats_baseline_without_history": beats_baseline,
        "verdict": (
            "LEAKAGE: solvable without history"
            if beats_baseline
            else "OK: history-free accuracy at or below majority baseline"
        ),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"filtered {len(items)} MCQ items; preserved "
        f"{len(results) - len(items)} free-response items -> {output}"
    )
    if overall_acc is not None:
        print(f"history-free accuracy {overall_acc:.1%} vs majority baseline {majority_baseline:.1%} -> {report['verdict']}")
    if report["mock_only"]:
        print("NOTE: mock validators only — filter_status values are placeholders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
