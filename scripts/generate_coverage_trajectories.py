#!/usr/bin/env python
"""Coverage-driven trajectory generation for rare financial life events.

The hazard sampler can underproduce rare event/memory-update combinations. This
driver pairs financially salient events with matching personas, then forces a
life_generator episode containing the event. The downstream pipeline still
derives sessions, prefix gold, and Stage 2 memory MCQs normally.

Example:
  python scripts/generate_coverage_trajectories.py \
    --personas data/personas/normalized/personas_ko_KR.jsonl \
    --locale ko_KR --horizon-years 12 \
    --output-dir data/generated/trajectories --seed 500
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from tqdm import tqdm

from fin_life_benchmark.actions.initial_actions_generator import build_initial_actions
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, load_yaml, read_jsonl
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.memory.initial_state_generator import build_initial_memory
from fin_life_benchmark.persona.models import NormalizedPersona
from fin_life_benchmark.trajectory.episode_bridge import (
    episode_scripted_events,
    impact_pairs,
    templates_for_event,
)
from fin_life_benchmark.trajectory.simulator import TrajectorySimulator
import json


def _action_matches(selector: dict, action) -> bool:
    if not selector:
        return False
    if "label" in selector and action.type != selector["label"]:
        return False
    if "linked_memory_path" in selector and selector["linked_memory_path"] not in action.linked_memory_paths:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personas", required=True)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--horizon-years", type=int, default=12)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=500)
    parser.add_argument("--max-per-pair", type=int, default=2, help="trajectories per (event,action) pair")
    args = parser.parse_args()

    paths = RepoPaths.default()
    locale = load_locale(args.locale, paths)
    templates = load_life_event_templates(paths)
    sim_config = load_yaml(paths.generation / "simulation.yaml")
    simulator = TrajectorySimulator(templates, locale, sim_config)

    personas = [NormalizedPersona.model_validate(row) for row in read_jsonl(Path(args.personas))]
    if not personas:
        raise SystemExit("no personas found — run normalize_personas.py first")

    # index personas by the standing-action types their initial actions contain
    actions_by_persona: dict[str, list] = {}
    for persona in personas:
        memory = build_initial_memory(persona, locale, seed=args.seed)
        actions_by_persona[persona.persona_id] = build_initial_actions(persona, memory, locale, seed=args.seed)

    event_templates = templates_for_event(paths)
    pairs = impact_pairs(paths)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    covered_pairs = 0
    uncovered: list[str] = []
    horizon_months = args.horizon_years * 12

    for pair_idx, (event_id, selector) in enumerate(tqdm(pairs, desc="coverage")):
        if event_id not in event_templates:
            uncovered.append(f"{event_id} (no life_generator template)")
            continue
        # personas whose initial actions match this impact selector
        matching = [
            p for p in personas
            if any(_action_matches(selector, a) for a in actions_by_persona[p.persona_id])
        ]
        if not matching:
            uncovered.append(f"{event_id} :: {selector}")
            continue
        covered_pairs += 1
        for k in range(min(args.max_per_pair, len(matching))):
            persona = matching[k]
            seed = args.seed + pair_idx * 100 + k
            trajectory_id = f"traj_cov_{pair_idx:03d}_{k}_{seed:05d}"
            memory = build_initial_memory(persona, locale, seed=seed)
            actions = build_initial_actions(persona, memory, locale, seed=seed)
            # force an episode containing this event so its guards/prerequisites
            # (e.g. purchase-before-sale) are satisfied by the episode ordering
            template_ids = event_templates[event_id]
            forced = episode_scripted_events(
                seed=seed,
                horizon_months=horizon_months,
                start_age=persona.age,
                template_ids=template_ids[:2],
                paths=paths,
            )
            trajectory = simulator.simulate(
                persona=persona,
                initial_memory=memory,
                initial_actions=actions,
                horizon_years=args.horizon_years,
                seed=seed,
                trajectory_id=trajectory_id,
                forced_events=forced,
            )
            out_path = output_dir / f"{trajectory_id}.json"
            out_path.write_text(
                json.dumps(trajectory.model_dump(mode="json"), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            written += 1

    print(f"wrote {written} coverage trajectories -> {output_dir}")
    print(f"covered {covered_pairs}/{len(pairs)} impact pairs")
    if uncovered:
        print("uncovered impact pairs (no matching persona/template in this pool):")
        for u in uncovered:
            print(f"  - {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
