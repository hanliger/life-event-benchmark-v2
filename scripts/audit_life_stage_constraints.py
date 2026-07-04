#!/usr/bin/env python
"""Audit: replay trajectories against life-stage guards; violations must be 0.

Example:
  python scripts/audit_life_stage_constraints.py \
    --trajectories-dir data/generated/trajectories \
    --output-dir data/generated/quality_reports
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.validation.audits import audit_life_stage_constraints, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output-dir", default="data/generated/quality_reports")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    if args.limit is not None:
        files = files[: args.limit]
    if not files:
        raise SystemExit("no trajectories")
    trajectories = [json.loads(f.read_text(encoding="utf-8")) for f in files]

    report = audit_life_stage_constraints(trajectories)
    out = Path(args.output_dir)
    write_report(report, out / "life_stage_constraints.json", "Life-Stage Constraint Audit",
                 out / "life_stage_constraints.md")
    summary = {k: v for k, v in report.items() if k != "violations"}
    print(summary)
    if report["invalid_life_stage_transitions"]:
        print(f"WARNING: {report['invalid_life_stage_transitions']} violations — see JSON report")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
