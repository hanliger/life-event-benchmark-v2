#!/usr/bin/env python
"""Generate initial financial memory states + standing actions per persona.

Example:
  python scripts/generate_initial_states.py \
    --personas data/personas/normalized/personas_ko_KR.jsonl \
    --locale ko_KR \
    --output data/generated/trajectories/initial_states.jsonl \
    --limit 20
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.actions.initial_actions_generator import build_initial_actions
from fin_life_benchmark.io import read_jsonl, write_jsonl
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.memory.initial_state_generator import build_initial_memory
from fin_life_benchmark.persona.models import NormalizedPersona


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personas", required=True)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    locale = load_locale(args.locale)
    records = []
    for i, row in enumerate(read_jsonl(Path(args.personas))):
        if args.limit is not None and i >= args.limit:
            break
        persona = NormalizedPersona.model_validate(row)
        memory = build_initial_memory(persona, locale, seed=args.seed)
        actions = build_initial_actions(persona, memory, locale, seed=args.seed)
        records.append(
            {
                "persona_id": persona.persona_id,
                "locale": args.locale,
                "initial_financial_memory_state": memory.model_dump(mode="json"),
                "initial_standing_actions": [a.model_dump(mode="json") for a in actions],
            }
        )

    if not records:
        raise SystemExit("no personas found — run normalize_personas.py first")

    count = write_jsonl(Path(args.output), records)
    print(f"wrote {count} initial states -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
