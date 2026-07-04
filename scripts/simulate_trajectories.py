#!/usr/bin/env python
"""Simulate life-state trajectories for normalized personas.

Example:
  python scripts/simulate_trajectories.py \
    --personas data/personas/normalized/personas_ko_KR.jsonl \
    --locale ko_KR \
    --num-trajectories 10 \
    --start-age-policy persona_age \
    --horizon-years 10 \
    --output-dir data/generated/trajectories \
    --seed 42
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from tqdm import tqdm

from fin_life_benchmark.actions.initial_actions_generator import build_initial_actions
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, load_yaml, read_jsonl
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.memory.initial_state_generator import build_initial_memory
from fin_life_benchmark.persona.models import NormalizedPersona
from fin_life_benchmark.trajectory.episode_bridge import episode_scripted_events
from fin_life_benchmark.trajectory.simulator import TrajectorySimulator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personas", required=True)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--num-trajectories", type=int, default=10)
    parser.add_argument("--start-age-policy", choices=["persona_age"], default="persona_age")
    parser.add_argument("--horizon-years", type=int, default=10)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--mode",
        choices=["hazard", "episode"],
        default="hazard",
        help="hazard: probabilistic sampling only; episode: also force events "
        "from a life_generator episode (guarantees occurred coverage)",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="in episode mode, bias episode selection toward impact-producing "
        "events (grows the rare post_occurred class)",
    )
    parser.add_argument("--episode-count", type=int, default=6)
    args = parser.parse_args()

    paths = RepoPaths.default()
    locale = load_locale(args.locale, paths)
    templates = load_life_event_templates(paths)
    sim_config = load_yaml(paths.generation / "simulation.yaml")
    simulator = TrajectorySimulator(templates, locale, sim_config)

    personas = [NormalizedPersona.model_validate(row) for row in read_jsonl(Path(args.personas))]
    if not personas:
        raise SystemExit("no personas found — run normalize_personas.py first")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for i in tqdm(range(args.num_trajectories), desc="simulate"):
        persona = personas[i % len(personas)]
        seed = args.seed + i
        trajectory_id = f"traj_{seed:05d}"
        memory = build_initial_memory(persona, locale, seed=seed)
        actions = build_initial_actions(persona, memory, locale, seed=seed)
        forced_events = None
        if args.mode == "episode":
            forced_events = episode_scripted_events(
                seed=seed,
                horizon_months=args.horizon_years * 12,
                start_age=persona.age,
                coverage=args.coverage,
                episode_count=args.episode_count,
                paths=paths,
            )
        trajectory = simulator.simulate(
            persona=persona,
            initial_memory=memory,
            initial_actions=actions,
            horizon_years=args.horizon_years,
            seed=seed,
            trajectory_id=trajectory_id,
            forced_events=forced_events,
        )
        out_path = output_dir / f"{trajectory_id}.json"
        out_path.write_text(
            json.dumps(trajectory.model_dump(mode="json"), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        written += 1

    print(f"wrote {written} trajectories -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
