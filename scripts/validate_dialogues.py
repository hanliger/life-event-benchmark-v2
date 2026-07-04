#!/usr/bin/env python
"""Validate generated dialogue sessions and write quality reports.

Example:
  python scripts/validate_dialogues.py \
    --sessions-dir data/generated/sessions \
    --output-dir data/generated/quality_reports
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import read_jsonl
from fin_life_benchmark.validation.dialogue_validator import DialogueValidator, summarize_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--output-dir", default="data/generated/quality_reports")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    templates = load_life_event_templates()
    validator = DialogueValidator(templates)

    results = []
    files = sorted(Path(args.sessions_dir).glob("sessions_*.jsonl"))
    if not files:
        raise SystemExit(f"no sessions_*.jsonl under {args.sessions_dir}")
    for path in files:
        for session in read_jsonl(path):
            if args.limit is not None and len(results) >= args.limit:
                break
            results.append(
                {
                    "trajectory_id": session.get("trajectory_id"),
                    "session_id": session.get("session_id"),
                    "session_type": session.get("session_type"),
                    "violations": validator.validate_session(session),
                }
            )

    summary = summarize_report(results)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dialogue_quality_report.json").write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Dialogue Quality Report",
        "",
        f"- total sessions: {summary['total_sessions']}",
        f"- sessions with violations: {summary['sessions_with_violations']}",
        f"- pass rate: {summary['pass_rate']}",
        "",
        "## Violations by code",
        "",
    ]
    for code, count in summary["violations_by_code"].items():
        lines.append(f"- `{code}`: {count}")
    if not summary["violations_by_code"]:
        lines.append("- (none)")
    (output_dir / "dialogue_quality_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"pass rate {summary['pass_rate']} ({summary['sessions_with_violations']}/{summary['total_sessions']} failed)")
    print(f"reports -> {output_dir}/dialogue_quality_report.{{json,md}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
