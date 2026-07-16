#!/usr/bin/env python
"""Sample and normalize personas with explicit age-band quotas.

Example:
  python scripts/sample_stratified_personas.py \
    --input-dir Nemotron-Personas-Korea \
    --locale ko_KR \
    --output data/runs/ko_KR_age20s4_30s6_40s6_50s4_seed42/inputs/personas_ko_KR_age20s4_30s6_40s6_50s4_seed42.jsonl \
    --quota 20-29:4 --quota 30-39:6 --quota 40-49:6 --quota 50-59:4 \
    --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import write_jsonl
from fin_life_benchmark.persona.nemotron_adapter import iter_raw_personas, normalize_persona


def parse_quota(value: str) -> tuple[tuple[int, int], int]:
    try:
        band, count = value.split(":", 1)
        lo, hi = band.split("-", 1)
        lo_i, hi_i, count_i = int(lo), int(hi), int(count)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("quota must look like 20-29:4") from exc
    if lo_i > hi_i:
        raise argparse.ArgumentTypeError(f"invalid age band: {band}")
    if count_i < 0:
        raise argparse.ArgumentTypeError(f"quota count must be non-negative: {value}")
    return (lo_i, hi_i), count_i


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", required=True, help="directory holding raw persona files")
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--output", required=True)
    parser.add_argument("--quota", action="append", type=parse_quota, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--summary-output", default=None, help="optional JSON summary path")
    args = parser.parse_args()

    quotas: list[tuple[tuple[int, int], int]] = args.quota
    min_age = min(lo for (lo, _), _ in quotas)
    max_age = max(hi for (_, hi), _ in quotas)
    buckets = {band: [] for band, _ in quotas}

    for raw in iter_raw_personas(
        Path(args.input_dir),
        limit=None,
        random_sample=False,
        min_age=min_age,
        max_age=max_age,
    ):
        try:
            age = int(raw.get("age"))
        except (TypeError, ValueError, OverflowError):
            continue
        for band, _ in quotas:
            lo, hi = band
            if lo <= age <= hi:
                buckets[band].append(raw)
                break

    rng = random.Random(args.seed)
    selected = []
    summary: dict[str, object] = {
        "strategy": "age_stratified",
        "seed": args.seed,
        "locale": args.locale,
        "input_dir": str(Path(args.input_dir)),
        "output": str(Path(args.output)),
        "quotas": {},
        "available_counts": {},
    }
    for band, count in quotas:
        pool = buckets[band]
        label = f"{band[0]}-{band[1]}"
        summary["quotas"][label] = count
        summary["available_counts"][label] = len(pool)
        if len(pool) < count:
            raise SystemExit(f"not enough personas for age band {label}: need {count}, have {len(pool)}")
        selected.extend(rng.sample(pool, count))

    records = [normalize_persona(raw, locale=args.locale).model_dump(mode="json") for raw in selected]
    count = write_jsonl(Path(args.output), records)
    summary["sampled_count"] = count
    summary["persona_ids"] = [record["persona_id"] for record in records]
    summary["age_counts"] = {
        f"{band[0]}-{band[1]}": sum(1 for record in records if band[0] <= int(record["age"]) <= band[1])
        for band, _ in quotas
    }
    summary["sex_counts_by_age_band"] = {
        f"{band[0]}-{band[1]}": {
            sex: sum(
                1
                for record in records
                if band[0] <= int(record["age"]) <= band[1] and (record.get("sex") or "unknown") == sex
            )
            for sex in sorted({record.get("sex") or "unknown" for record in records})
        }
        for band, _ in quotas
    }
    if args.summary_output:
        path = Path(args.summary_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {count} stratified personas -> {args.output}")
    for band, _ in quotas:
        ages = [record["age"] for record in records if band[0] <= int(record["age"]) <= band[1]]
        print(f"{band[0]}-{band[1]}: {ages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
