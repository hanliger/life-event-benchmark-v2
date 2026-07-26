#!/usr/bin/env python
"""Build Stage 2 Single-hop memory-transition items from PrefixGold.

Example:
  export RUN_ID=exp1
  python scripts/build_benchmark_items.py \
    --prefix-gold data/runs/$RUN_ID/gold/prefix_gold_checkpoints_15.jsonl \
    --sessions-dir data/runs/$RUN_ID/dialogues/sessions \
    --trajectories-dir data/runs/$RUN_ID/trajectories \
    --output-dir data/runs/$RUN_ID/benchmark_items
"""

from __future__ import annotations

import argparse
import json
import re
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
    # Prefer the joined files when a directory contains both a raw HF export
    # and the local canonical sessions produced from it.
    session_files = sorted(directory.glob("sessions_*.jsonl"))
    if session_files:
        return session_files
    return sorted(directory.glob("traj_*.jsonl"))


def _load_session_records(directory: Path) -> dict[str, list[dict]]:
    records_by_traj: dict[str, list[dict]] = {}
    for path in _jsonl_files(directory):
        for session in read_jsonl(path):
            trajectory_id = session.get("trajectory_id")
            if not trajectory_id:
                raise ValueError(f"session record missing trajectory_id: {path}")
            session_id = str(session.get("session_id") or "")
            if not re.fullmatch(r"S\d+", session_id):
                raise ValueError(
                    f"session record has invalid session_id={session_id!r}: {path}"
                )
            records = records_by_traj.setdefault(str(trajectory_id), [])
            if any(str(row.get("session_id")) == session_id for row in records):
                raise ValueError(
                    f"duplicate session_id {trajectory_id}/{session_id}: {path}"
                )
            records.append(session)
    for records in records_by_traj.values():
        records.sort(key=lambda row: int(str(row["session_id"])[1:]))
    return records_by_traj


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument(
        "--trajectory-id",
        default=None,
        help="build items for one trajectory only",
    )
    parser.add_argument(
        "--sessions-dir",
        default=None,
        help=(
            "canonical run directory with sessions_traj_*.jsonl; "
            "when omitted, provide --gold-dir for split dialogue/gold input"
        ),
    )
    parser.add_argument(
        "--gold-dir",
        default=None,
        help=(
            "optional split session-level gold directory; "
            "defaults to --sessions-dir for merged run sessions"
        ),
    )
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--window-size", type=int, default=15)
    parser.add_argument(
        "--shuffle-options",
        action="store_true",
        help="deterministically shuffle Stage 2 A-D options per canonical target",
    )
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
    if args.trajectory_id:
        prefixes = [
            prefix
            for prefix in prefixes
            if str(prefix.get("trajectory_id")) == args.trajectory_id
        ]
    if not prefixes:
        scope = f" for {args.trajectory_id}" if args.trajectory_id else ""
        raise SystemExit(
            f"empty prefix gold{scope} — run export_prefix_gold.py first"
        )

    sessions_dir = Path(args.sessions_dir) if args.sessions_dir else None
    if args.gold_dir:
        gold_dir = Path(args.gold_dir)
    elif sessions_dir:
        # ensure_dialogue_sessions materializes the HF dialogue+gold join in
        # this directory, so it is also the canonical session-level gold input.
        gold_dir = sessions_dir
    else:
        raise SystemExit("provide --gold-dir (or --sessions-dir)")
    sessions_by_traj = _load_session_records(gold_dir)
    if not sessions_by_traj:
        raise SystemExit(f"no session-level gold JSONL files under {gold_dir}")
    prefix_trajectory_ids = {str(prefix["trajectory_id"]) for prefix in prefixes}
    missing_session_gold = sorted(prefix_trajectory_ids - set(sessions_by_traj))
    if missing_session_gold:
        raise SystemExit(
            "session-level gold is missing for prefix trajectories: "
            f"{missing_session_gold}; expected files under {gold_dir}"
        )
    missing_session_ids: dict[str, list[str]] = {}
    for trajectory_id in sorted(prefix_trajectory_ids):
        expected = {
            str(session_id)
            for prefix in prefixes
            if str(prefix["trajectory_id"]) == trajectory_id
            for session_id in prefix.get("visible_sessions") or []
        }
        actual = {
            str(session.get("session_id"))
            for session in sessions_by_traj[trajectory_id]
        }
        missing = sorted(
            expected - actual,
            key=lambda session_id: int(session_id[1:]),
        )
        if missing:
            missing_session_ids[trajectory_id] = missing
    if missing_session_ids:
        details = "; ".join(
            f"{trajectory_id}: {missing[:5]}"
            + (" ..." if len(missing) > 5 else "")
            for trajectory_id, missing in missing_session_ids.items()
        )
        raise SystemExit(
            "session-level gold is incomplete for prefix sessions: "
            f"{details}; expected files under {gold_dir}"
        )

    initial_memory_by_traj: dict[str, dict] = {}
    for path in sorted(Path(args.trajectories_dir).glob("traj_*.json")):
        trajectory = Trajectory.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        initial_memory_by_traj[trajectory.trajectory_id] = serialize_memory_state(
            trajectory.initial_financial_memory_state
        )
    missing_initial_memory = sorted(
        prefix_trajectory_ids - set(initial_memory_by_traj)
    )
    if missing_initial_memory:
        raise SystemExit(
            "initial trajectory state is missing for prefix trajectories: "
            f"{missing_initial_memory}; expected files under {args.trajectories_dir}"
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
    builder = ItemBuilder(
        seed=args.seed,
        shuffle_options=args.shuffle_options,
    )
    items = builder.build_stage2(
        checkpoints,
        initial_memory_by_traj=initial_memory_by_traj,
        window_size=args.window_size,
    )
    if args.max_items is not None:
        items = items[: args.max_items]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    count = write_jsonl(
        output_dir / "stage2_single_hop_mcq.jsonl",
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
        f"stage2_single_hop_mcq.jsonl: {count} items from "
        f"{n_with_targets}/{n_checkpoints} checkpoints; "
        f"{n_targets} canonical event targets"
    )
    print(f"output -> {output_dir / 'stage2_single_hop_mcq.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
