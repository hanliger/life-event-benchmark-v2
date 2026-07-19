#!/usr/bin/env python
"""Generate banking dialogue sessions from trajectories.

Examples:
  # mock (no API, default)
  python scripts/generate_dialogue_sessions.py \
    --trajectories-dir data/runs/<RUN_ID>/trajectories \
    --locale ko_KR --output-dir data/runs/<RUN_ID>/dialogues/sessions \
    --raw-output-dir data/runs/<RUN_ID>/dialogues/raw_outputs \
    --max-trajectories 3 --mock

  # dry-run: write prompts only
  python scripts/generate_dialogue_sessions.py ... --dry-run

  # real LLM calls (requires .env keys)
  python scripts/generate_dialogue_sessions.py ... --execute
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from tqdm import tqdm

from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.generator import DialogueGenerator
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, read_jsonl
from fin_life_benchmark.llm.client import LLMClient
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.trajectory.models import Trajectory
from fin_life_benchmark.validation.dialogue_plan_validator import DialoguePlanValidator


def _write_jsonl_line(handle, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument(
        "--plans-dir",
        default=None,
        help="load validated plans_*.jsonl from this directory instead of rebuilding plans",
    )
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--raw-output-dir",
        default=None,
        help="where dry-run prompts and LLM raw outputs are written; default: data/raw_model_outputs/dialogue",
    )
    parser.add_argument("--max-trajectories", type=int, default=None)
    parser.add_argument("--max-sessions", type=int, default=None, help="cap sessions per trajectory")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="keep successfully generated sessions and log failed sessions to errors_<trajectory_id>.jsonl",
    )
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
    plan_validator = DialoguePlanValidator(templates, paths)

    if args.execute:
        client = LLMClient.from_env(provider=args.provider, model=args.model)
        if client.provider == "mock":
            raise SystemExit("--execute requires DEFAULT_LLM_PROVIDER=openai|anthropic in .env")
        raw_output_dir = Path(args.raw_output_dir) if args.raw_output_dir else None
        generator = DialogueGenerator(mode="llm", client=client, paths=paths, raw_output_dir=raw_output_dir)
    elif args.dry_run:
        raw_output_dir = Path(args.raw_output_dir) if args.raw_output_dir else None
        generator = DialogueGenerator(mode="dry_run", paths=paths, raw_output_dir=raw_output_dir)
    else:
        raw_output_dir = Path(args.raw_output_dir) if args.raw_output_dir else None
        generator = DialogueGenerator(mode="mock", paths=paths, raw_output_dir=raw_output_dir)

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
        if args.plans_dir:
            plan_path = Path(args.plans_dir) / f"plans_{trajectory.trajectory_id}.jsonl"
            if not plan_path.exists():
                raise SystemExit(f"missing saved plan file: {plan_path}")
            plans = [
                DialogueGenerationPlan.model_validate(record)
                for record in read_jsonl(plan_path)
            ]
            violations = plan_validator.validate_plans(plans, trajectory)
            if violations:
                summary = ", ".join(sorted({item.code for item in violations}))
                raise SystemExit(f"saved plans failed validation for {trajectory.trajectory_id}: {summary}")
        else:
            plans = planner.build_plans(trajectory, seed=args.seed)
        if args.max_sessions is not None:
            plans = plans[: args.max_sessions]
        if args.dry_run:
            for plan in plans:
                generator.generate_session(plan, trajectory.persona)
            continue

        error_path = output_dir / f"errors_{trajectory.trajectory_id}.jsonl"
        with out_path.open("w", encoding="utf-8") as out_handle:
            error_handle = error_path.open("w", encoding="utf-8") if args.continue_on_error else None
            try:
                for plan in plans:
                    try:
                        session = generator.generate_session(plan, trajectory.persona)
                    except Exception as exc:
                        if not args.continue_on_error:
                            raise
                        assert error_handle is not None
                        error_record = {
                            "trajectory_id": trajectory.trajectory_id,
                            "session_id": plan.session_id,
                            "month_index": plan.month_index,
                            "session_type": plan.session_type,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                        metadata = getattr(getattr(generator, "client", None), "last_response_metadata", None)
                        if metadata:
                            error_record["llm_metadata"] = metadata
                        _write_jsonl_line(error_handle, error_record)
                        continue
                    if session is not None:
                        _write_jsonl_line(out_handle, session.model_dump(mode="json"))
                        total_sessions += 1
            finally:
                if error_handle is not None:
                    error_handle.close()

    if args.dry_run:
        prompt_dir = Path(args.raw_output_dir) if args.raw_output_dir else paths.raw_model_outputs / "dialogue"
        print(f"dry-run: prompts written to {prompt_dir}")
    else:
        print(f"wrote {total_sessions} sessions -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
