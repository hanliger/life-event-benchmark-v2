#!/usr/bin/env python
"""Deterministically audit counterfactual filler plans and generated banks."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.dialogue.counterfactual_fillers import (
    CounterfactualFiller,
    CounterfactualFillerPlan,
    audit_filler_bank,
)
from fin_life_benchmark.io import read_jsonl


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Counterfactual filler audit",
        "",
        f"- Decision: **{report['decision']}**",
        f"- Personas: {report['persona_count']}",
        f"- Expected fillers: {report['expected_fillers']}",
        f"- Actual fillers: {report['actual_fillers']}",
        f"- Violations: {report['violation_count']}",
        "",
        "## Per-persona",
        "",
        "| trajectory | decision | expected | actual | violations |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for item in report["per_persona"]:
        lines.append(
            f"| {item['trajectory_id']} | {item['decision']} | "
            f"{item['expected_fillers']} | {item['actual_fillers']} | "
            f"{len(item['violations'])} |"
        )
    lines.extend(["", "## Violations", ""])
    if not report["violations"]:
        lines.append("None.")
    else:
        for item in report["violations"]:
            lines.append(
                f"- `{item['trajectory_id']}/{item.get('filler_id')}` "
                f"**{item['code']}**: {item['detail']}"
            )
    lines.extend(["", "## Task distribution", ""])
    for task, count in sorted(report["task_distribution"].items()):
        lines.append(f"- `{task}`: {count}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--fillers-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--trajectory-id", action="append", default=[])
    args = parser.parse_args()

    requested = set(args.trajectory_id)
    plan_files = sorted(Path(args.plans_dir).glob("plans_traj_*.jsonl"))
    if requested:
        plan_files = [
            path for path in plan_files
            if path.stem.removeprefix("plans_") in requested
        ]
    if not plan_files:
        raise SystemExit("no filler plans selected")

    per_persona = []
    all_violations = []
    task_distribution: Counter[str] = Counter()
    for plan_file in plan_files:
        trajectory_id = plan_file.stem.removeprefix("plans_")
        filler_file = Path(args.fillers_dir) / f"fillers_{trajectory_id}.jsonl"
        plans = [
            CounterfactualFillerPlan.model_validate(item)
            for item in read_jsonl(plan_file)
        ]
        fillers = (
            [
                CounterfactualFiller.model_validate(item)
                for item in read_jsonl(filler_file)
            ]
            if filler_file.exists()
            else []
        )
        result = audit_filler_bank(plans, fillers)
        result["trajectory_id"] = trajectory_id
        per_persona.append(result)
        for task, count in result["task_distribution"].items():
            task_distribution[task] += count
        all_violations.extend(
            {"trajectory_id": trajectory_id, **violation}
            for violation in result["violations"]
        )

    report = {
        "decision": (
            "PASS"
            if all(item["decision"] == "PASS" for item in per_persona)
            else "FAIL"
        ),
        "persona_count": len(per_persona),
        "expected_fillers": sum(item["expected_fillers"] for item in per_persona),
        "actual_fillers": sum(item["actual_fillers"] for item in per_persona),
        "violation_count": len(all_violations),
        "task_distribution": dict(sorted(task_distribution.items())),
        "per_persona": per_persona,
        "violations": all_violations,
    }
    decision = {
        "decision": report["decision"],
        "persona_count": report["persona_count"],
        "expected_fillers": report["expected_fillers"],
        "actual_fillers": report["actual_fillers"],
        "violation_count": report["violation_count"],
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "filler_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "filler_audit.md").write_text(_markdown(report), encoding="utf-8")
    (out_dir / "filler_decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, ensure_ascii=False))
    print(f"audit -> {out_dir}")
    return 0 if report["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
