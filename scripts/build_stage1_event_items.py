#!/usr/bin/env python
"""Build official Stage 1 cumulative occurred-event/evidence pair items."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.benchmark.rq1_builder import (
    build_public_taxonomy,
    build_stage1_pair_items,
    load_session_records,
    taxonomy_hash,
)
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.io import RepoPaths, ensure_dialogue_sessions, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--taxonomy-output", required=True)
    parser.add_argument("--trajectory-id", action="append", default=[])
    parser.add_argument("--checkpoint-stride", type=int, default=15)
    args = parser.parse_args()

    if args.checkpoint_stride != 15:
        parser.error("official Stage 1 uses a fixed 15-session checkpoint stride")
    sessions_dir = Path(ensure_dialogue_sessions(args.sessions_dir))
    prefixes = list(read_prefix_gold(Path(args.prefix_gold)))
    if args.trajectory_id:
        wanted = set(args.trajectory_id)
        prefixes = [
            row for row in prefixes if str(row["trajectory_id"]) in wanted
        ]
    if not prefixes:
        raise SystemExit("empty prefix gold — export prefix gold first")

    trajectory_ids = sorted({str(row["trajectory_id"]) for row in prefixes})
    sessions = load_session_records(sessions_dir, trajectory_ids)
    templates = load_life_event_templates(RepoPaths.default())
    taxonomy = build_public_taxonomy(templates)
    digest = taxonomy_hash(taxonomy)
    items = build_stage1_pair_items(
        prefixes,
        sessions,
        taxonomy_digest=digest,
        taxonomy_event_ids={row["event_id"] for row in taxonomy},
        checkpoint_stride=args.checkpoint_stride,
    )
    if not items:
        raise SystemExit("no Stage 1 checkpoints produced")

    output = Path(args.output)
    count = write_jsonl(output, (item.model_dump(mode="json") for item in items))
    taxonomy_output = Path(args.taxonomy_output)
    taxonomy_output.parent.mkdir(parents=True, exist_ok=True)
    taxonomy_output.write_text(
        json.dumps(
            {"taxonomy": taxonomy, "taxonomy_hash": digest},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"stage1 occurred-event/evidence pairs: {count} items -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
