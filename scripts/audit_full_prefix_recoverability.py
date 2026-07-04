#!/usr/bin/env python
"""Audit: cumulative (full-prefix) recoverability of gold events.

Example:
  python scripts/audit_full_prefix_recoverability.py \
    --prefix-gold data/generated/gold/prefix_gold.jsonl \
    --output-dir data/generated/quality_reports
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import read_jsonl
from fin_life_benchmark.validation.audits import audit_full_prefix_recoverability, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--output-dir", default="data/generated/quality_reports")
    args = parser.parse_args()

    prefixes = list(read_jsonl(Path(args.prefix_gold)))
    if not prefixes:
        raise SystemExit("empty prefix gold")

    report = audit_full_prefix_recoverability(prefixes)
    out = Path(args.output_dir)
    write_report(report, out / "full_prefix_recoverability.json", "Full-Prefix Recoverability Audit",
                 out / "full_prefix_recoverability.md")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
