#!/usr/bin/env python
"""Build dialogue-grounded Stage 3 Multi-hop MCQ items."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.benchmark.stage3_item_builder import Stage3ItemBuilder
from fin_life_benchmark.benchmark.multihop import (
    build_stage3_multihop_targets,
    load_multihop_session_records,
    load_stage3_multihop_policy,
    load_stage3_multihop_representative_policy,
)
from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.gold.prefix_gold_exporter import serialize_memory_state
from fin_life_benchmark.io import ensure_dialogue_sessions, write_jsonl
from fin_life_benchmark.trajectory.models import Trajectory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trajectory-id", default=None)
    parser.add_argument(
        "--policy",
        default="configs/registries/stage3_multihop_policy.yaml",
    )
    parser.add_argument("--window-size", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--shuffle-options", action="store_true")
    parser.add_argument("--max-items", type=int, default=None)
    args = parser.parse_args()

    ensure_dialogue_sessions(args.sessions_dir)
    prefixes = list(read_prefix_gold(Path(args.prefix_gold)))
    if args.trajectory_id:
        prefixes = [
            row
            for row in prefixes
            if str(row.get("trajectory_id")) == args.trajectory_id
        ]
    if not prefixes:
        raise SystemExit("no PrefixGold records matched the requested scope")

    trajectory_ids = {str(row["trajectory_id"]) for row in prefixes}
    sessions_by_traj = load_multihop_session_records(args.sessions_dir)
    missing_sessions = sorted(trajectory_ids - set(sessions_by_traj))
    if missing_sessions:
        raise SystemExit(f"missing canonical sessions for: {missing_sessions}")
    sessions_by_traj = {
        trajectory_id: sessions_by_traj[trajectory_id]
        for trajectory_id in sorted(trajectory_ids)
    }

    initial_memory_by_traj: dict[str, dict] = {}
    for trajectory_id in sorted(trajectory_ids):
        path = Path(args.trajectories_dir) / f"{trajectory_id}.json"
        if not path.exists():
            raise SystemExit(f"missing trajectory file: {path}")
        trajectory = Trajectory.model_validate_json(path.read_text(encoding="utf-8"))
        initial_memory_by_traj[trajectory_id] = serialize_memory_state(
            trajectory.initial_financial_memory_state
        )

    representative_policy = load_stage3_multihop_representative_policy(
        args.policy
    )

    result = build_stage3_multihop_targets(
        prefixes,
        sessions_by_traj,
        load_stage3_multihop_policy(args.policy),
        initial_memory_by_traj=initial_memory_by_traj,
        representative_policy=representative_policy,
        window_size=args.window_size,
    )
    items = Stage3ItemBuilder(
        shuffle_options=args.shuffle_options,
    ).build_stage3_multihop(
        result.targets,
        initial_memory_by_traj=initial_memory_by_traj,
    )
    if args.max_items is not None:
        items = items[: args.max_items]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    items_path = output_dir / "stage3_multi_hop_mcq.jsonl"
    report_path = output_dir / "stage3_multi_hop_build_report.json"
    count = write_jsonl(
        items_path,
        (item.model_dump(mode="json") for item in items),
    )
    report = {
        **result.report,
        "written_item_count": count,
        "trajectory_scope": sorted(trajectory_ids),
        "output": str(items_path),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"stage3_multi_hop_mcq.jsonl: {count} items; "
        f"{len(trajectory_ids)} trajectories"
    )
    print(f"items -> {items_path}")
    print(f"build report -> {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
