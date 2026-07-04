#!/usr/bin/env python
"""Normalize raw Nemotron personas into NormalizedPersona JSONL.

Example:
  python scripts/normalize_personas.py \
    --input-dir nemotron-personas-korea \
    --locale ko_KR \
    --output data/personas/normalized/personas_ko_KR.jsonl \
    --limit 20
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
    args = parser.parse_args()

    records = []
    for raw in tqdm(iter_raw_personas(Path(args.input_dir), limit=args.limit), desc="normalize", total=args.limit):
        persona = normalize_persona(raw, locale=args.locale)
        records.append(persona.model_dump(mode="json"))

    if not records:
        raise SystemExit("no personas normalized — check --input-dir")

    count = write_jsonl(Path(args.output), records)
    print(f"wrote {count} normalized personas -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
