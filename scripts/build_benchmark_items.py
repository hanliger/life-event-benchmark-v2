#!/usr/bin/env python
"""Build official benchmark items.

Example:
  python scripts/build_benchmark_items.py \
    --prefix-gold data/generated/gold/prefix_gold.jsonl \
    --sessions-dir data/generated/sessions \
    --output-dir data/generated/benchmark_items
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.io import ensure_dialogue_sessions, read_jsonl, write_jsonl
from fin_life_benchmark.trajectory.models import Trajectory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-items", type=int, default=None, help="cap per stage")
    args = parser.parse_args()
    ensure_dialogue_sessions(args.sessions_dir)

    prefixes = list(read_prefix_gold(Path(args.prefix_gold)))
    if not prefixes:
        raise SystemExit("empty prefix gold — run export_prefix_gold.py first")

    sessions_by_traj: dict[str, list[dict]] = {}
    for path in sorted(Path(args.sessions_dir).glob("sessions_*.jsonl")):
        for session in read_jsonl(path):
            sessions_by_traj.setdefault(session["trajectory_id"], []).append(session)

    trajectories_by_traj: dict[str, Trajectory] = {}
    for path in sorted(Path(args.trajectories_dir).glob("traj_*.json")):
        trajectory = Trajectory.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        trajectories_by_traj[trajectory.trajectory_id] = trajectory

    builder = ItemBuilder(seed=args.seed)
    outputs = {
        "stage2_memory_value.jsonl": builder.build_stage2(
            prefixes, sessions_by_traj, trajectories_by_traj
        ),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, items in outputs.items():
        if args.max_items is not None:
            items = items[: args.max_items]
        count = write_jsonl(output_dir / filename, (i.model_dump(mode="json") for i in items))
        print(f"{filename}: {count} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
