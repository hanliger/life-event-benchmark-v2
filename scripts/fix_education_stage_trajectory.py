#!/usr/bin/env python
"""Apply the education-stage transition correction to Trajectory JSON objects.

Mirrors scripts/fix_education_stage_gold.py at the trajectory level so a
trajectory's event records stay consistent with the corrected gold: for every
education_child_stage_entry event, the recorded transition is set to
predecessor(new_stage) -> new_stage (from the ordered progression), fixing the
same-stage/backward records caused by the shared education memory cell.

Only the transition RECORDS are touched (life_event_instances[].params and
timeline_steps[].memory_updates for education.child_education_stage). State
values (the stage a child is currently in) are left as-is -- they were correct;
only the recorded transition was degenerate.

    python scripts/fix_education_stage_trajectory.py \
        --in-dir tests/fixtures/trajectories \
        --out-dir data/runs/hf_full/trajectories_fixed
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

STAGES = ["pre_school", "primary", "middle", "high"]
_NORMALIZE = {"preschool": "pre_school"}


def _norm(stage):
    return _NORMALIZE.get(stage, stage)


def _predecessor(new_stage):
    new = _norm(new_stage)
    i = STAGES.index(new) if new in STAGES else 0
    return STAGES[i - 1] if i > 0 else STAGES[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-dir", default="tests/fixtures/trajectories")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(in_dir.glob("traj_*.json"))
    if not files:
        raise SystemExit(f"no traj_*.json under {in_dir}")

    per_traj: Counter = Counter()
    for tf in files:
        traj = json.loads(tf.read_text(encoding="utf-8"))
        changed = 0
        edu_instances = set()
        for inst in traj.get("life_event_instances") or []:
            if inst.get("event_id") != "education_child_stage_entry":
                continue
            edu_instances.add(inst.get("event_instance_id"))
            params = inst.get("params") or {}
            new = _norm(params.get("new_stage"))
            prev = _predecessor(new)
            if _norm(params.get("previous_stage")) != prev:
                params["previous_stage"] = prev
                changed += 1
        for step in traj.get("timeline_steps") or []:
            for u in step.get("memory_updates") or []:
                if "child_education_stage" not in (u.get("path") or ""):
                    continue
                # Only education stage-entry transitions (skip clears with null value).
                new = _norm(u.get("new_value"))
                if new not in STAGES:
                    continue
                prev = _predecessor(new)
                if _norm(u.get("old_value")) != prev:
                    u["old_value"] = prev
                    changed += 1
        (out_dir / tf.name).write_text(
            json.dumps(traj, ensure_ascii=False), encoding="utf-8"
        )
        if changed:
            per_traj[traj["trajectory_id"]] = changed

    print(f"corrected education transition records in {sum(per_traj.values())} spots")
    print("  by trajectory:", dict(sorted(per_traj.items())))
    print(f"output -> {out_dir} ({len(files)} trajectories)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
