#!/usr/bin/env python
"""Generate banking dialogue sessions from trajectories.

Examples:
  # mock (no API, default)
  python scripts/generate_dialogue_sessions.py \
    --trajectories-dir data/generated/trajectories \
    --locale ko_KR --output-dir data/generated/sessions --max-trajectories 3 --mock

  # dry-run: write prompts only
  python scripts/generate_dialogue_sessions.py ... --dry-run

  # real LLM calls (requires .env keys)
  python scripts/generate_dialogue_sessions.py ... --execute
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from tqdm import tqdm

from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.generator import DialogueGenerator
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, write_jsonl
from fin_life_benchmark.llm.client import LLMClient
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.trajectory.models import Trajectory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-trajectories", type=int, default=None)
    parser.add_argument("--max-sessions", type=int, default=None, help="cap sessions per trajectory")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="call the LLM API")
    mode.add_argument("--dry-run", action="store_true", help="write prompts only")
    mode.add_argument("--mock", action="store_true", help="deterministic template dialogues (default)")
    parser.add_argument("--provider", default=None, help="override DEFAULT_LLM_PROVIDER")
    parser.add_argument("--model", default=None, help="override DEFAULT_GENERATION_MODEL")
    args = parser.parse_args()

    paths = RepoPaths.default()
    locale = load_locale(args.locale, paths)
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, locale, paths)

    if args.execute:
        client = LLMClient.from_env(provider=args.provider, model=args.model)
        if client.provider == "mock":
            raise SystemExit("--execute requires DEFAULT_LLM_PROVIDER=openai|anthropic in .env")
        generator = DialogueGenerator(mode="llm", client=client, paths=paths)
    elif args.dry_run:
        generator = DialogueGenerator(mode="dry_run", paths=paths)
    else:
        generator = DialogueGenerator(mode="mock", paths=paths)

    trajectory_files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    if args.max_trajectories is not None:
        trajectory_files = trajectory_files[: args.max_trajectories]
    if not trajectory_files:
        raise SystemExit(f"no traj_*.json under {args.trajectories_dir} — run simulate_trajectories.py first")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    total_sessions = 0
    for traj_file in tqdm(trajectory_files, desc="dialogue"):
        trajectory = Trajectory.model_validate(json.loads(traj_file.read_text(encoding="utf-8")))
        out_path = output_dir / f"sessions_{trajectory.trajectory_id}.jsonl"
        if out_path.exists() and not args.overwrite and not args.dry_run:
            print(f"skip existing {out_path} (use --overwrite)")
            continue
        plans = planner.build_plans(trajectory, seed=args.seed)
        if args.max_sessions is not None:
            plans = plans[: args.max_sessions]
        sessions = []
        for plan in plans:
            session = generator.generate_session(plan, trajectory.persona)
            if session is not None:
                sessions.append(session.model_dump(mode="json"))
        if sessions:
            write_jsonl(out_path, sessions)
            total_sessions += len(sessions)

    if args.dry_run:
        print(f"dry-run: prompts written to {paths.raw_model_outputs / 'dialogue'}")
    else:
        print(f"wrote {total_sessions} sessions -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
