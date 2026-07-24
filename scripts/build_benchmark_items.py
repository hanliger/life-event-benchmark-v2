#!/usr/bin/env python
"""Build Stage 2 memory-transition benchmark items from prefix gold.

Example:
  python scripts/build_benchmark_items.py \
    --prefix-gold data/runs/v4/gold/prefix_gold_checkpoints_15.jsonl \
    --sessions-dir data/runs/v4/mcq_work/dialogues \
    --gold-dir data/runs/v4/mcq_work/gold \
    --trajectories-dir data/runs/v4/trajectories \
    --output-dir data/runs/v4/mcq_work/benchmark_items
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.benchmark.mcq_input import (
    build_stage2_checkpoints,
    load_stage2_question_policy,
)
from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.gold.prefix_gold_exporter import serialize_memory_state
from fin_life_benchmark.io import ensure_dialogue_sessions, read_jsonl, write_jsonl
from fin_life_benchmark.trajectory.models import Trajectory


def _jsonl_files(directory: Path) -> list[Path]:
    trajectory_files = sorted(directory.glob("traj_*.jsonl"))
    if trajectory_files:
        return trajectory_files
    return sorted(directory.glob("sessions_*.jsonl"))


def _load_session_records(directory: Path) -> dict[str, list[dict]]:
    records_by_traj: dict[str, list[dict]] = {}
    for path in _jsonl_files(directory):
        for session in read_jsonl(path):
            trajectory_id = session.get("trajectory_id")
            if not trajectory_id:
                raise ValueError(f"session record missing trajectory_id: {path}")
            records_by_traj.setdefault(str(trajectory_id), []).append(session)
    for records in records_by_traj.values():
        records.sort(key=lambda row: str(row["session_id"]))
    return records_by_traj


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument(
        "--gold-dir",
        default=None,
        help="session-level gold directory; defaults to the sibling gold directory",
    )
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--window-size", type=int, default=15)
    parser.add_argument(
        "--allow-missing-event-targets",
        action="store_true",
        help="debug only: permit occurred events without a transition target",
    )
    parser.add_argument(
        "--stage2-policy",
        default="configs/registries/stage2_question_policy.yaml",
        help="event-to-memory-path and four-option policy for Stage 2",
    )
    parser.add_argument("--max-items", type=int, default=None, help="cap final items")
    args = parser.parse_args()
    ensure_dialogue_sessions(args.sessions_dir)

    prefixes = list(read_prefix_gold(Path(args.prefix_gold)))
    if not prefixes:
        raise SystemExit("empty prefix gold — run export_prefix_gold.py first")

    sessions_dir = Path(args.sessions_dir)
    gold_dir = Path(args.gold_dir) if args.gold_dir else sessions_dir.parent / "gold"
    sessions_by_traj = _load_session_records(gold_dir)
    if not sessions_by_traj:
        raise SystemExit(f"no session-level gold JSONL files under {gold_dir}")

    initial_memory_by_traj: dict[str, dict] = {}
    for path in sorted(Path(args.trajectories_dir).glob("traj_*.json")):
        trajectory = Trajectory.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        initial_memory_by_traj[trajectory.trajectory_id] = serialize_memory_state(
            trajectory.initial_financial_memory_state
        )

    question_policy = load_stage2_question_policy(args.stage2_policy)
    checkpoints = build_stage2_checkpoints(
        prefixes,
        sessions_by_traj=sessions_by_traj,
        initial_memory_by_traj=initial_memory_by_traj,
        question_policy=question_policy,
        strict_event_targets=not args.allow_missing_event_targets,
        window_size=args.window_size,
    )
    builder = ItemBuilder(seed=args.seed)
    items = builder.build_stage2(
        checkpoints,
        initial_memory_by_traj=initial_memory_by_traj,
    )
    if args.max_items is not None:
        items = items[: args.max_items]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    count = write_jsonl(
        output_dir / "stage2_memory_mcq.jsonl",
        (item.model_dump(mode="json") for item in items),
    )
    n_checkpoints = len(checkpoints)
    n_with_targets = sum(bool(checkpoint.targets) for checkpoint in checkpoints)
    n_targets = len({
        target.canonical_target_id
        for checkpoint in checkpoints
        for target in checkpoint.targets
    })
    print(
        f"stage2_memory_mcq.jsonl: {count} items from "
        f"{n_with_targets}/{n_checkpoints} checkpoints; "
        f"{n_targets} canonical event targets"
    )
    print(f"output -> {output_dir / 'stage2_memory_mcq.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
