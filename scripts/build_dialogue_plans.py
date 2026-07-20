#!/usr/bin/env python
"""Build and audit dialogue plans without generating any dialogue."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, write_jsonl
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.trajectory.models import Trajectory
from fin_life_benchmark.validation.dialogue_plan_validator import DialoguePlanValidator


def _write_report(report_dir: Path, report: dict) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "dialogue_plan_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Dialogue plan audit",
        "",
        f"- trajectories: {report['trajectory_count']}",
        f"- plans: {report['plan_count']}",
        f"- violations: {report['violation_count']}",
        f"- uncovered event/status combinations: {len(report['uncovered_event_status'])}",
        "",
        "## Session types",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in report["counts"]["session_type"].items())
    lines.extend(["", "## Violation codes", ""])
    lines.extend(f"- {key}: {value}" for key, value in report["violation_codes"].items())
    if not report["violation_codes"]:
        lines.append("- none")
    lines.extend(["", "## Uncovered event/status", ""])
    lines.extend(f"- {item}" for item in report["uncovered_event_status"] or ["none"])
    (report_dir / "dialogue_plan_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    paths = RepoPaths.default()
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, load_locale(args.locale, paths), paths)
    validator = DialoguePlanValidator(templates, paths)
    trajectory_files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    if not trajectory_files:
        raise SystemExit(f"no traj_*.json under {args.trajectories_dir}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    all_plans = []
    all_violations = []
    active_pairs = set()
    for trajectory_file in trajectory_files:
        trajectory = Trajectory.model_validate(json.loads(trajectory_file.read_text(encoding="utf-8")))
        plans = planner.build_plans(trajectory, seed=args.seed)
        write_jsonl(
            output_dir / f"plans_{trajectory.trajectory_id}.jsonl",
            (plan.model_dump(mode="json") for plan in plans),
        )
        all_plans.extend(plans)
        all_violations.extend(validator.validate_plans(plans, trajectory))
        for instance in trajectory.life_event_instances:
            for item in instance.status_history:
                active_pairs.add((instance.event_id, item.status.value))

    evidence_coverage_gaps = set(validator.registry_coverage_gaps(active_pairs))
    uncovered = sorted(
        f"{event_id}+{status}"
        for event_id, status in active_pairs
        if status in {"weak_signal", "upcoming", "occurred", "cancelled"}
        and (
            not (planner.task_registry.get(event_id) or {}).get(status)
            or (event_id, status)
            in evidence_coverage_gaps
        )
    )
    counts = validator.audit_counts(all_plans)
    violation_codes = dict(sorted(Counter(item.code for item in all_violations).items()))
    report = {
        "trajectory_count": len(trajectory_files),
        "plan_count": len(all_plans),
        "violation_count": len(all_violations),
        "violation_codes": violation_codes,
        "violations": [item.model_dump(mode="json") for item in all_violations],
        "uncovered_event_status": uncovered,
        "counts": counts,
    }
    _write_report(Path(args.report_dir), report)
    print(
        f"built {len(all_plans)} plans across {len(trajectory_files)} trajectories; "
        f"violations={len(all_violations)}"
    )
    return 1 if all_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
