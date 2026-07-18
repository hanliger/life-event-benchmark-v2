#!/usr/bin/env python
"""Export prefix-level gold from trajectories + generated sessions.

Example:
  python scripts/export_prefix_gold.py \
    --trajectories-dir data/generated/trajectories \
    --sessions-dir data/generated/sessions \
    --output data/generated/gold/prefix_gold.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from tqdm import tqdm

from fin_life_benchmark.gold.prefix_gold_exporter import export_prefix_gold
from fin_life_benchmark.io import read_jsonl, write_jsonl
from fin_life_benchmark.trajectory.models import Trajectory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None, help="max trajectories")
    parser.add_argument(
        "--checkpoint-stride",
        type=int,
        default=None,
        help="emit only every N sessions (v3 main evaluation uses 15); default emits every prefix",
    )
    args = parser.parse_args()

    trajectory_files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    if args.limit is not None:
        trajectory_files = trajectory_files[: args.limit]

    records = []
    exported = 0
    for traj_file in tqdm(trajectory_files, desc="prefix-gold"):
        trajectory = Trajectory.model_validate(json.loads(traj_file.read_text(encoding="utf-8")))
        sessions_path = Path(args.sessions_dir) / f"sessions_{trajectory.trajectory_id}.jsonl"
        if not sessions_path.exists():
            continue
        sessions = list(read_jsonl(sessions_path))
        for prefix in export_prefix_gold(
            trajectory,
            sessions,
            checkpoint_stride=args.checkpoint_stride,
        ):
            records.append(prefix.model_dump(mode="json"))
        exported += 1

    if not records:
        raise SystemExit("no prefix gold produced — generate sessions first")

    count = write_jsonl(Path(args.output), records)
    print(f"wrote {count} prefix-gold records from {exported} trajectories -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
