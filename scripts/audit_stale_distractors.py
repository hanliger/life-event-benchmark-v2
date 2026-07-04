#!/usr/bin/env python
"""Audit: availability of stale-memory / stale-action distractors.

Example:
  python scripts/audit_stale_distractors.py \
    --items data/generated/benchmark_items/stage3_action_mcq.jsonl \
    --prefix-gold data/generated/gold/prefix_gold.jsonl \
    --output-dir data/generated/quality_reports
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.io import read_jsonl
from fin_life_benchmark.validation.audits import audit_stale_distractors, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--items", required=True)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--output-dir", default="data/generated/quality_reports")
    args = parser.parse_args()

    items = list(read_jsonl(Path(args.items)))
    prefixes = list(read_prefix_gold(Path(args.prefix_gold)))

    report = audit_stale_distractors(items, prefixes)
    out = Path(args.output_dir)
    write_report(report, out / "stale_distractors.json", "Stale Distractor Audit", out / "stale_distractors.md")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
