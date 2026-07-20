#!/usr/bin/env python
"""Audit generated dialogue sessions against their exact saved plans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, read_jsonl, load_yaml
from fin_life_benchmark.validation.dialogue_generation_audit import audit_dialogue_generation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--raw-output-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trajectory-id")
    args = parser.parse_args()
    plan_files = (
        [Path(args.plans_dir) / f"plans_{args.trajectory_id}.jsonl"]
        if args.trajectory_id else sorted(Path(args.plans_dir).glob("plans_traj_*.jsonl"))
    )
    plans = [record for path in plan_files if path.exists() for record in read_jsonl(path)]
    selected_ids = {record["trajectory_id"] for record in plans}
    sessions = [
        record for path in sorted(Path(args.sessions_dir).glob("sessions_*.jsonl"))
        for record in read_jsonl(path) if record.get("trajectory_id") in selected_ids
    ]
    errors = [
        record for path in sorted(Path(args.sessions_dir).glob("errors_*.jsonl"))
        for record in read_jsonl(path) if record.get("trajectory_id") in selected_ids
    ]
    paths = RepoPaths.default()
    cfg = load_yaml(paths.generation / "dialogue.yaml")
    report = audit_dialogue_generation(
        plans, sessions, errors, load_life_event_templates(paths),
        turns_min=int(cfg.get("turns_min", 1)), turns_max=int(cfg.get("turns_max", 10000)),
        user_turns_min=int(cfg.get("user_turns_min", 1)),
        user_turns_max=int(cfg.get("user_turns_max", 10000)),
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dialogue_generation_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary = report["summary"]
    lines = ["# Dialogue generation audit", "", *[f"- {key}: {value}" for key, value in summary.items()], "", "## Violations", ""]
    lines.extend(f"- {key}: {value}" for key, value in report["violation_counts"].items())
    if not report["violation_counts"]:
        lines.append("- none")
    (output_dir / "dialogue_generation_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"audited {len(sessions)}/{len(plans)} generated sessions -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
