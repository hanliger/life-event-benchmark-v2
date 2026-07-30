#!/usr/bin/env python
"""Audit Stage 3 Multi-hop item structure and dialogue/PrefixGold provenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.benchmark.multihop import (
    audit_stage3_multihop_items,
    build_stage3_multihop_targets,
    load_multihop_session_records,
    load_stage3_multihop_policy,
    load_stage3_multihop_representative_policy,
)
from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.gold.prefix_gold_exporter import serialize_memory_state
from fin_life_benchmark.io import ensure_dialogue_sessions, read_jsonl
from fin_life_benchmark.trajectory.models import Trajectory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--policy",
        default="configs/registries/stage3_multihop_policy.yaml",
    )
    args = parser.parse_args()

    ensure_dialogue_sessions(args.sessions_dir)
    items = list(read_jsonl(Path(args.items)))
    if not items:
        raise SystemExit("no Multi-hop items to audit")
    trajectory_ids = {str(item.get("trajectory_id") or "") for item in items}
    prefixes = [
        row
        for row in read_prefix_gold(Path(args.prefix_gold))
        if str(row.get("trajectory_id") or "") in trajectory_ids
    ]
    sessions = {
        trajectory_id: records
        for trajectory_id, records in load_multihop_session_records(
            args.sessions_dir
        ).items()
        if trajectory_id in trajectory_ids
    }
    missing_sessions = sorted(trajectory_ids - set(sessions))
    if missing_sessions:
        raise SystemExit(f"missing canonical sessions for: {missing_sessions}")

    initial_memory_by_traj: dict[str, dict] = {}
    for trajectory_id in sorted(trajectory_ids):
        trajectory_path = Path(args.trajectories_dir) / f"{trajectory_id}.json"
        if not trajectory_path.exists():
            raise SystemExit(f"missing trajectory file: {trajectory_path}")
        trajectory = Trajectory.model_validate_json(
            trajectory_path.read_text(encoding="utf-8")
        )
        initial_memory_by_traj[trajectory_id] = serialize_memory_state(
            trajectory.initial_financial_memory_state
        )

    policy = load_stage3_multihop_policy(args.policy)
    expected = build_stage3_multihop_targets(
        prefixes,
        sessions,
        policy,
        initial_memory_by_traj=initial_memory_by_traj,
        representative_policy=load_stage3_multihop_representative_policy(
            args.policy
        ),
    )
    expected_representatives = expected.report["representative_selection"]

    report = audit_stage3_multihop_items(
        items,
        prefixes,
        sessions,
        policy=policy,
        expected_representatives=expected_representatives,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"multi-hop audit: {report['passed_items']}/{report['items']} passed "
        f"-> {output}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
