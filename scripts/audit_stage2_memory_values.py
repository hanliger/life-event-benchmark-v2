#!/usr/bin/env python
"""Audit Stage 2 memory-value item contracts."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from fin_life_benchmark.benchmark.stage2_memory import normalize_stage2_answer
from fin_life_benchmark.io import read_jsonl

_ALLOWED_SOURCE_OPERATIONS = {"create", "update", "no_change"}
# A longer evaluation prefix reuses the original item; it is not a new
# carry-forward memory change.
_ALLOWED_CHECKPOINT_CHANGES = {"update", "no_change"}


def _question_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.year}년 {parsed.month}월 {parsed.day}일"


def audit(items: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    answer_types: Counter[str] = Counter()
    change_types: Counter[str] = Counter()
    paths: Counter[str] = Counter()
    option_sets: dict[tuple[str, str, str, str], set[str]] = {}
    checkpoint_counts: defaultdict[str, set[int]] = defaultdict(set)

    def fail(item: dict[str, Any], code: str, detail: str) -> None:
        issues.append(
            {
                "item_id": item.get("item_id"),
                "code": code,
                "detail": detail,
            }
        )

    for item in items:
        gold = item.get("gold") or {}
        metadata = item.get("metadata") or {}
        if item.get("stage") != "stage2_memory_value":
            fail(item, "wrong_stage", str(item.get("stage")))
            continue

        answer_type = metadata.get("answer_type") or gold.get("answer_type")
        answer_types[str(answer_type)] += 1
        path = str(gold.get("memory_path") or "")
        selector = str(gold.get("value_selector") or "")
        entity = str(gold.get("entity_id") or "")
        paths[path] += 1

        checkpoint_date = str(metadata.get("checkpoint_date") or "")
        try:
            surface = _question_date(checkpoint_date)
        except ValueError:
            fail(item, "invalid_checkpoint_date", checkpoint_date)
        else:
            if surface not in str(item.get("question") or ""):
                fail(item, "date_missing_from_question", surface)

        count = int(metadata.get("checkpoint_session_count") or 0)
        if count <= 0 or count % 15:
            fail(item, "invalid_checkpoint_stride", str(count))
        target_count = int(
            metadata.get("target_checkpoint_session_count") or count
        )
        evaluation_count = int(
            metadata.get("evaluation_checkpoint_session_count") or count
        )
        if target_count <= 0 or target_count % 15:
            fail(item, "invalid_target_checkpoint_stride", str(target_count))
        if evaluation_count <= 0 or evaluation_count % 15:
            fail(
                item,
                "invalid_evaluation_checkpoint_stride",
                str(evaluation_count),
            )
        if evaluation_count < target_count:
            fail(
                item,
                "evaluation_prefix_before_target",
                f"{evaluation_count} < {target_count}",
            )
        checkpoint_counts[str(item.get("trajectory_id"))].add(count)

        source_operation = gold.get("source_operation")
        if source_operation not in _ALLOWED_SOURCE_OPERATIONS:
            fail(item, "status_only_or_unknown_operation", str(source_operation))
        change_type = str(gold.get("checkpoint_change_type") or "")
        change_types[change_type] += 1
        if change_type not in _ALLOWED_CHECKPOINT_CHANGES:
            fail(item, "invalid_checkpoint_change_type", change_type)
        if not gold.get("target_event_instance_id"):
            fail(item, "missing_target_event_instance", "")

        options = item.get("options") or []
        if answer_type == "mcq":
            if len(options) < 2:
                fail(item, "too_few_options", str(len(options)))
            option_ids = [str(option.get("option_id")) for option in options]
            option_texts = [str(option.get("text")) for option in options]
            if len(option_ids) != len(set(option_ids)):
                fail(item, "duplicate_option_id", str(option_ids))
            if len(option_texts) != len(set(option_texts)):
                fail(item, "duplicate_option_text", str(option_texts))
            correct = [option for option in options if option.get("correct")]
            if len(correct) != 1:
                fail(item, "incorrect_correct_option_count", str(len(correct)))
            elif gold.get("correct_option") != correct[0].get("option_id"):
                fail(item, "correct_option_mismatch", str(gold.get("correct_option")))
            key = (str(item.get("trajectory_id")), path, selector, entity)
            candidate_set = set(option_texts)
            if key in option_sets and option_sets[key] != candidate_set:
                fail(item, "candidate_pool_changed", str(sorted(candidate_set)))
            option_sets[key] = candidate_set
        elif answer_type == "free_response":
            if options:
                fail(item, "free_response_has_options", str(len(options)))
            normalized = gold.get("normalized_answer")
            if normalized in {None, ""}:
                fail(item, "empty_normalized_answer", "")
            expected = normalize_stage2_answer(
                gold.get("answer_value"),
                metadata.get("normalizer"),
                metadata.get("answer_aliases") or {},
            )
            if normalized != expected:
                fail(item, "normalized_answer_mismatch", f"{normalized!r} != {expected!r}")
        else:
            fail(item, "unsupported_answer_type", str(answer_type))

    return {
        "passed": not issues,
        "items": len(items),
        "issue_count": len(issues),
        "answer_types": dict(sorted(answer_types.items())),
        "checkpoint_change_types": dict(sorted(change_types.items())),
        "memory_paths": dict(sorted(paths.items())),
        "checkpoints_by_trajectory": {
            trajectory_id: sorted(values)
            for trajectory_id, values in sorted(checkpoint_counts.items())
        },
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True)
    parser.add_argument(
        "--output",
        default="data/generated/quality_reports/stage2_memory_value_audit.json",
    )
    args = parser.parse_args()

    report = audit(list(read_jsonl(Path(args.items))))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Stage 2 audit: {report['items']} items, "
        f"{report['issue_count']} issues -> {output}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
