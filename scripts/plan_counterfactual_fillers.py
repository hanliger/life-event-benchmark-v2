#!/usr/bin/env python
"""Create frozen 20-session/persona counterfactual filler plans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.dialogue.counterfactual_fillers import build_filler_plans
from fin_life_benchmark.trajectory.models import Trajectory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--trajectory-id", action="append", default=[])
    parser.add_argument("--exclude-trajectory-id", action="append", default=[])
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    requested = set(args.trajectory_id)
    excluded = set(args.exclude_trajectory_id)
    files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    if requested:
        missing = requested - {path.stem for path in files}
        if missing:
            raise SystemExit(f"unknown trajectory IDs: {', '.join(sorted(missing))}")
        files = [path for path in files if path.stem in requested]
    files = [path for path in files if path.stem not in excluded]
    if not files:
        raise SystemExit("no trajectories selected")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for path in files:
        trajectory = Trajectory.model_validate_json(path.read_text(encoding="utf-8"))
        out_path = out_dir / f"plans_{trajectory.trajectory_id}.jsonl"
        if out_path.exists() and not args.overwrite:
            print(f"skip existing {out_path}")
            continue
        plans = build_filler_plans(trajectory)
        with out_path.open("w", encoding="utf-8") as handle:
            for plan in plans:
                handle.write(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False) + "\n")
        total += len(plans)
        print(f"{trajectory.trajectory_id}: {len(plans)} plans -> {out_path}")
    print(f"wrote {total} plans across {len(files)} persona(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
