#!/usr/bin/env python
"""Audit saved dialogue plans against their source trajectories."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from build_dialogue_plans import _write_report
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, read_jsonl
from fin_life_benchmark.trajectory.models import Trajectory
from fin_life_benchmark.validation.dialogue_plan_validator import DialoguePlanValidator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    paths = RepoPaths.default()
    validator = DialoguePlanValidator(load_life_event_templates(paths), paths)
    trajectories = {
        path.stem: Trajectory.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in Path(args.trajectories_dir).glob("traj_*.json")
    }
    all_plans = []
    all_violations = []
    plan_files = sorted(Path(args.plans_dir).glob("plans_traj_*.jsonl"))
    if not plan_files:
        raise SystemExit(f"no plans_traj_*.jsonl under {args.plans_dir}")
    for plan_file in plan_files:
        plans = [DialogueGenerationPlan.model_validate(record) for record in read_jsonl(plan_file)]
        trajectory_id = plans[0].trajectory_id if plans else plan_file.stem.removeprefix("plans_")
        trajectory = trajectories.get(trajectory_id)
        if trajectory is None:
            raise SystemExit(f"missing source trajectory for {trajectory_id}")
        all_plans.extend(plans)
        all_violations.extend(validator.validate_plans(plans, trajectory))
    report = {
        "trajectory_count": len(plan_files),
        "plan_count": len(all_plans),
        "violation_count": len(all_violations),
        "violation_codes": dict(sorted(Counter(item.code for item in all_violations).items())),
        "violations": [item.model_dump(mode="json") for item in all_violations],
        "uncovered_event_status": [],
        "counts": validator.audit_counts(all_plans),
    }
    _write_report(Path(args.output_dir), report)
    print(f"audited {len(all_plans)} plans; violations={len(all_violations)}")
    return 1 if all_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
