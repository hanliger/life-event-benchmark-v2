#!/usr/bin/env python
"""Normalize raw Nemotron personas into NormalizedPersona JSONL.

Example:
  python scripts/normalize_personas.py \
    --input-dir Nemotron-Personas-Korea \
    --locale ko_KR \
    --output data/personas/normalized/personas_ko_KR.jsonl \
    --limit 20 --sample-random --seed 42
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from tqdm import tqdm

from fin_life_benchmark.io import write_jsonl
from fin_life_benchmark.persona.nemotron_adapter import iter_raw_personas, normalize_persona


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", required=True, help="directory holding raw persona files")
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None, help="max personas (smoke runs)")
    parser.add_argument(
        "--sample-random",
        action="store_true",
        help="sample --limit personas reproducibly instead of taking the first rows",
    )
    parser.add_argument("--seed", type=int, default=42, help="random seed for --sample-random")
    args = parser.parse_args()
    if args.sample_random and args.limit is None:
        parser.error("--sample-random requires --limit")

    records = []
    raw_iter = iter_raw_personas(
        Path(args.input_dir),
        limit=args.limit,
        random_sample=args.sample_random,
        seed=args.seed,
    )
    for raw in tqdm(raw_iter, desc="normalize", total=args.limit):
        persona = normalize_persona(raw, locale=args.locale)
        records.append(persona.model_dump(mode="json"))

    if not records:
        raise SystemExit("no personas normalized — check --input-dir")

    count = write_jsonl(Path(args.output), records)
    print(f"wrote {count} normalized personas -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
