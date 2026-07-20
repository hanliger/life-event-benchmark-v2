#!/usr/bin/env python
"""Score a completed dialogue human-review packet and enforce review gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore # noqa: F401

from fin_life_benchmark.io import read_jsonl


REQUIRED_FIELDS = (
    "natural_korean_dialogue",
    "event_task_alignment",
    "lifecycle_calibration",
    "memory_grounding",
    "assistant_semantic_leakage",
    "high_risk_safety",
    "event_implicit_but_recoverable",
    "comments",
)

RATE_THRESHOLDS = {
    "event_implicit_but_recoverable": 0.95,
    "event_task_alignment": 0.95,
    "lifecycle_calibration": 0.95,
    "natural_korean_dialogue": 0.90,
}

CRITICAL_FIELDS = (
    "memory_grounding",
    "high_risk_safety",
    "assistant_semantic_leakage",
)


def _as_pass(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"pass", "passed", "true", "yes", "1"}:
            return True
        if normalized in {"fail", "failed", "false", "no", "0"}:
            return False
    raise ValueError(f"review value must be pass/fail or boolean, got {value!r}")


def score_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    incomplete: list[dict[str, str]] = []
    scores: dict[str, list[bool]] = {
        field: [] for field in REQUIRED_FIELDS if field != "comments"
    }
    for index, record in enumerate(records):
        reviewer = record.get("reviewer") or {}
        session_id = str(
            (record.get("evaluator_only") or {}).get("session_id") or index
        )
        for field in REQUIRED_FIELDS:
            if field not in reviewer or reviewer.get(field) is None:
                incomplete.append({"session_id": session_id, "field": field})
        if any(item["session_id"] == session_id for item in incomplete):
            continue
        for field in scores:
            try:
                scores[field].append(_as_pass(reviewer[field]))
            except ValueError:
                incomplete.append({"session_id": session_id, "field": field})

    rates = {
        field: round(sum(values) / len(values), 6) if values else 0.0
        for field, values in scores.items()
    }
    critical_failures = {
        field: len(values) - sum(values)
        for field, values in scores.items()
        if field in CRITICAL_FIELDS
    }
    failures: list[dict[str, Any]] = []
    if incomplete:
        failures.append(
            {
                "gate": "complete_reviewer_fields",
                "actual": len(incomplete),
                "threshold": 0,
            }
        )
    for field, count in critical_failures.items():
        if count:
            failures.append(
                {"gate": f"{field}_failures", "actual": count, "threshold": 0}
            )
    for field, threshold in RATE_THRESHOLDS.items():
        if rates[field] < threshold:
            failures.append(
                {"gate": f"{field}_pass_rate", "actual": rates[field], "threshold": threshold}
            )
    return {
        "decision": "FAIL" if failures else "PASS",
        "reviewed_session_count": len(records),
        "incomplete_fields": incomplete,
        "pass_rates": rates,
        "critical_failure_counts": critical_failures,
        "hard_gate_failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    records = list(read_jsonl(args.input))
    result = score_records(records)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "human_review_decision.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Dialogue human-review decision",
        "",
        f"- decision: **{result['decision']}**",
        f"- reviewed sessions: {result['reviewed_session_count']}",
        "",
        "## Pass rates",
        "",
    ]
    lines.extend(
        f"- {field}: {rate:.3f}"
        for field, rate in result["pass_rates"].items()
    )
    lines.extend(["", "## Hard gate failures", ""])
    lines.extend(
        f"- {item}" for item in result["hard_gate_failures"] or ["none"]
    )
    (output_dir / "human_review_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(f"human review decision: {result['decision']}")
    return 0 if result["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
