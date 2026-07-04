#!/usr/bin/env python
"""Build stage1/2/3 benchmark items + stage3 counterfactual MCQ items.

Example:
  python scripts/build_benchmark_items.py \
    --prefix-gold data/generated/gold/prefix_gold.jsonl \
    --sessions-dir data/generated/sessions \
    --output-dir data/generated/benchmark_items
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.io import RepoPaths, load_yaml, read_jsonl, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-items", type=int, default=None, help="cap per stage")
    args = parser.parse_args()

    prefixes = list(read_prefix_gold(Path(args.prefix_gold)))
    if not prefixes:
        raise SystemExit("empty prefix gold — run export_prefix_gold.py first")

    sessions_by_traj: dict[str, list[dict]] = {}
    for path in sorted(Path(args.sessions_dir).glob("sessions_*.jsonl")):
        for session in read_jsonl(path):
            sessions_by_traj.setdefault(session["trajectory_id"], []).append(session)

    builder = ItemBuilder(seed=args.seed)
    paths = RepoPaths.default()
    life_events = load_yaml(paths.registries / "life_events.yaml")
    impact_registry = load_yaml(paths.registries / "event_to_action_impact.yaml")
    label_to_event_id = {
        spec["label_ko"]: event_id
        for event_id, spec in life_events.items()
        if isinstance(spec, dict) and spec.get("label_ko")
    }
    outputs = {
        "stage1_event_status.jsonl": builder.build_stage1(prefixes, sessions_by_traj),
        "stage2_memory_update.jsonl": builder.build_stage2(prefixes, sessions_by_traj),
        "stage3_action_decision.jsonl": builder.build_stage3(prefixes, sessions_by_traj),
        "stage3_action_mcq.jsonl": builder.build_stage3_mcq(
            prefixes,
            sessions_by_traj,
            impact_registry=impact_registry,
            label_to_event_id=label_to_event_id,
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
