#!/usr/bin/env python
"""Audit: how many evidence sessions are solvable from a single session.

Example:
  python scripts/audit_single_session_recoverability.py \
    --sessions-dir data/generated/sessions \
    --output-dir data/generated/quality_reports
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import read_jsonl
from fin_life_benchmark.validation.audits import audit_single_session_recoverability, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--output-dir", default="data/generated/quality_reports")
    args = parser.parse_args()

    sessions = []
    for path in sorted(Path(args.sessions_dir).glob("sessions_*.jsonl")):
        sessions.extend(read_jsonl(path))
    if not sessions:
        raise SystemExit(f"no sessions under {args.sessions_dir}")

    report = audit_single_session_recoverability(sessions)
    out = Path(args.output_dir)
    write_report(report, out / "single_session_recoverability.json", "Single-Session Recoverability Audit",
                 out / "single_session_recoverability.md")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
