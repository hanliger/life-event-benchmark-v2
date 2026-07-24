#!/usr/bin/env python
"""Build Stage 1 occurred-event items directly from dialogue/gold JSONL files."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.benchmark.mcq_input import load_mcq_windows
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dialogues-dir", required=True)
    parser.add_argument("--gold-dir", required=True)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--trajectory-id", default=None)
    parser.add_argument("--window-size", type=int, default=15)
    args = parser.parse_args()

    paths = RepoPaths.default()
    windows = load_mcq_windows(
        args.dialogues_dir,
        args.gold_dir,
        args.trajectories_dir,
        trajectory_id=args.trajectory_id,
        window_size=args.window_size,
    )
    templates = load_life_event_templates(paths)
    items = ItemBuilder().build_stage1_event_identification(windows, templates)
    count = write_jsonl(Path(args.output), (item.model_dump(mode="json") for item in items))
    print(f"stage1-event-identification: {count} items -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
