#!/usr/bin/env python
"""Generate dialogue sessions from frozen plans with canary safety controls."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import threading
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.generate_dialogue_sessions in tests
    from scripts import _bootstrap  # type: ignore # noqa: F401
from tqdm import tqdm

from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.generation_control import (
    build_generation_manifest,
    raw_dialogue_json_schema,
    require_canary_pass,
    require_human_review_pass,
    require_regression_pass,
    resolve_model_profile,
    select_trajectory_files,
    verify_canary_manifest,
    write_immutable_manifest,
)
from fin_life_benchmark.dialogue.generator import DialogueGenerator
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, read_jsonl
from fin_life_benchmark.llm.client import LLMClient
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.trajectory.models import Trajectory
from fin_life_benchmark.validation.dialogue_plan_validator import DialoguePlanValidator


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
    temporary.replace(path)


def _load_by_session_id(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {
        str(record["session_id"]): record
        for record in read_jsonl(path)
        if record.get("session_id")
    }


def _write_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--plans-dir", default=None)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--raw-output-dir", default=None)
    parser.add_argument("--trajectory-id")
    parser.add_argument("--exclude-trajectory-id", action="append", default=[])
    parser.add_argument("--trajectory-ids-file")
    parser.add_argument("--max-trajectories", type=int)
    parser.add_argument("--max-sessions", type=int)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="number of independent session-generation workers (default: 1)",
    )
    parser.add_argument("--allow-partial-plans", action="store_true", help="bake-off only: allow a validated Pydantic subset instead of a 300-plan trajectory")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--confirm-multi-trajectory-generation", action="store_true")
    parser.add_argument("--model-profile")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--canary-manifest")
    parser.add_argument("--require-canary-pass")
    parser.add_argument("--require-human-review-pass")
    parser.add_argument("--require-regression-pass")
    parser.add_argument("--regression-manifest")
    parser.add_argument("--allow-canary-config-mismatch", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--overwrite-session-id", action="append", default=[])
    parser.add_argument(
        "--only-session-id",
        action="append",
        default=[],
        help="generate only these session IDs after validating the full saved plan",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--mock", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")
    paths = RepoPaths.default()
    try:
        trajectory_files = select_trajectory_files(
            args.trajectories_dir,
            trajectory_id=args.trajectory_id,
            exclude_trajectory_ids=args.exclude_trajectory_id,
            trajectory_ids_file=args.trajectory_ids_file,
            max_trajectories=args.max_trajectories,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    selected_ids = [path.stem for path in trajectory_files]
    print("selected trajectory IDs: " + (", ".join(selected_ids) if selected_ids else "(none)"))
    if args.execute:
        if not args.plans_dir:
            raise SystemExit("--execute requires --plans-dir; real generation must use frozen saved plans")
        if not selected_ids:
            raise SystemExit("--execute selected zero trajectories")
        if len(selected_ids) > 1 and not args.confirm_multi_trajectory_generation:
            raise SystemExit(
                "--execute with multiple trajectories requires "
                "--confirm-multi-trajectory-generation"
            )

    production_mode = bool(
        args.canary_manifest
        or args.require_canary_pass
        or args.require_human_review_pass
    )
    if production_mode:
        if len(selected_ids) != 19:
            raise SystemExit(f"production continuation must select exactly 19 trajectories, got {len(selected_ids)}")
        if not all(
            (
                args.canary_manifest,
                args.require_canary_pass,
                args.require_human_review_pass,
            )
        ):
            raise SystemExit(
                "production continuation requires --canary-manifest, "
                "--require-canary-pass, and --require-human-review-pass"
            )
        if args.overwrite:
            raise SystemExit("broad --overwrite is prohibited in production continuation mode")
        try:
            require_canary_pass(args.require_canary_pass)
            require_human_review_pass(args.require_human_review_pass)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if bool(args.require_regression_pass) != bool(args.regression_manifest):
        raise SystemExit(
            "full canary v2 requires both --require-regression-pass and "
            "--regression-manifest"
        )
    if args.require_regression_pass:
        try:
            require_regression_pass(args.require_regression_pass)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    effective = resolve_model_profile(args.model_profile, args.provider, args.model, paths)
    mode_name = "llm" if args.execute else "dry_run" if args.dry_run else "mock"
    plans_dir = Path(args.plans_dir) if args.plans_dir else paths.trajectories
    manifest = build_generation_manifest(
        run_id=Path(args.trajectories_dir).resolve().parent.name,
        trajectory_files=trajectory_files,
        plans_dir=plans_dir,
        effective_model=effective,
        mode=mode_name,
        seed=args.seed,
        overwrite_policy={
            "overwrite": args.overwrite,
            "resume": args.resume,
            "retry_errors": args.retry_errors,
            "workers": args.workers,
            "overwrite_session_ids": sorted(set(args.overwrite_session_id)),
            "only_session_ids": sorted(set(args.only_session_id)),
        },
        paths=paths,
    )
    if production_mode:
        try:
            verify_canary_manifest(
                manifest, args.canary_manifest, args.allow_canary_config_mismatch
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if args.regression_manifest:
        try:
            verify_canary_manifest(manifest, args.regression_manifest, False)
            manifest["regression_manifest"] = manifest.pop("canary_manifest")
            manifest["regression_config_mismatch_fields"] = manifest.pop(
                "canary_config_mismatch_fields"
            )
            manifest["regression_config_mismatch_override"] = manifest.pop(
                "canary_config_mismatch_override"
            )
        except ValueError as exc:
            raise SystemExit(f"regression {exc}") from exc
    output_dir = Path(args.output_dir)
    raw_output_dir = Path(args.raw_output_dir) if args.raw_output_dir else None
    manifest_path = output_dir.parent / "generation_manifest.json"
    try:
        write_immutable_manifest(manifest_path, manifest)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    locale = load_locale(args.locale, paths)
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, locale, paths)
    plan_validator = DialoguePlanValidator(templates, paths)
    def build_generator() -> DialogueGenerator:
        if args.execute:
            client = LLMClient.from_env(
                provider=effective["provider"],
                model=effective["model"],
                reasoning_effort=effective.get("reasoning_effort"),
                response_format=effective.get("response_format", "prompt_json"),
                response_schema=(
                    raw_dialogue_json_schema()
                    if effective.get("response_format") == "json_schema" else None
                ),
                max_tokens=int(effective.get("max_tokens", 8192)),
            )
            return DialogueGenerator("llm", client, paths, raw_output_dir)
        if args.dry_run:
            return DialogueGenerator("dry_run", paths=paths, raw_output_dir=raw_output_dir)
        return DialogueGenerator("mock", paths=paths, raw_output_dir=raw_output_dir)

    if args.execute and args.workers > 1:
        generator = None
    else:
        generator = build_generator()

    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir.parent / "production_progress.json"
    overwrite_ids = set(args.overwrite_session_id)
    total_sessions = 0
    completed_trajectories: list[str] = []
    for trajectory_file in tqdm(trajectory_files, desc="dialogue"):
        trajectory = Trajectory.model_validate(json.loads(trajectory_file.read_text(encoding="utf-8")))
        if args.plans_dir:
            plan_path = plans_dir / f"plans_{trajectory.trajectory_id}.jsonl"
            plans = [DialogueGenerationPlan.model_validate(record) for record in read_jsonl(plan_path)]
            violations = [] if args.allow_partial_plans else plan_validator.validate_plans(plans, trajectory)
            if violations:
                codes = ", ".join(sorted({item.code for item in violations}))
                raise SystemExit(f"saved plans failed validation for {trajectory.trajectory_id}: {codes}")
        else:
            plans = planner.build_plans(trajectory, seed=args.seed)
        if args.only_session_id:
            requested_ids = set(args.only_session_id)
            available_ids = {plan.session_id for plan in plans}
            missing_ids = sorted(requested_ids - available_ids)
            if missing_ids:
                raise SystemExit(
                    "requested --only-session-id values are absent from saved plan: "
                    + ", ".join(missing_ids)
                )
            plans = [plan for plan in plans if plan.session_id in requested_ids]
        if args.max_sessions is not None:
            plans = plans[: args.max_sessions]
        if args.dry_run:
            assert generator is not None
            for plan in plans:
                generator.generate_session(plan, trajectory.persona)
            continue

        out_path = output_dir / f"sessions_{trajectory.trajectory_id}.jsonl"
        error_path = output_dir / f"errors_{trajectory.trajectory_id}.jsonl"
        successful = _load_by_session_id(out_path)
        errors = _load_by_session_id(error_path)
        errors = {session_id: record for session_id, record in errors.items() if session_id not in successful}
        if out_path.exists() and not any((args.overwrite, args.resume, args.retry_errors, overwrite_ids)):
            print(f"skip existing {out_path} (use --resume; --overwrite only outside production)")
            completed_trajectories.append(trajectory.trajectory_id)
            continue
        if args.overwrite and not production_mode:
            successful = {}
            errors = {}

        pending_plans: list[DialogueGenerationPlan] = []
        for plan in plans:
            force = plan.session_id in overwrite_ids
            if plan.session_id in successful and not force:
                continue
            if args.retry_errors and plan.session_id not in errors and plan.session_id in successful:
                continue
            pending_plans.append(plan)

        def persist_result(
            plan: DialogueGenerationPlan,
            session: Any | None,
            error_info: tuple[str, str, dict[str, Any]] | None,
        ) -> None:
            nonlocal total_sessions
            if error_info is not None:
                error_type, error_message, metadata = error_info
                error_record = {
                    "trajectory_id": trajectory.trajectory_id,
                    "session_id": plan.session_id,
                    "month_index": plan.month_index,
                    "session_type": plan.session_type,
                    "error_type": error_type,
                    "error": error_message,
                    "llm_metadata": metadata,
                }
                errors[plan.session_id] = error_record
                _atomic_jsonl(error_path, [errors[key] for key in sorted(errors)])
                _write_progress(progress_path, {
                    "manifest": str(manifest_path),
                    "trajectory_id": trajectory.trajectory_id,
                    "last_session_id": plan.session_id,
                    "successful_session_count": len(successful),
                    "error_session_count": len(errors),
                    "completed_trajectories": completed_trajectories,
                })
                return
            if session is not None:
                successful[plan.session_id] = session.model_dump(mode="json")
                errors.pop(plan.session_id, None)
                _atomic_jsonl(out_path, [successful[key] for key in sorted(successful)])
                _atomic_jsonl(error_path, [errors[key] for key in sorted(errors)])
                total_sessions += 1
                _write_progress(progress_path, {
                    "manifest": str(manifest_path),
                    "trajectory_id": trajectory.trajectory_id,
                    "last_session_id": plan.session_id,
                    "successful_session_count": len(successful),
                    "error_session_count": len(errors),
                    "completed_trajectories": completed_trajectories,
                })

        if args.execute and args.workers > 1:
            worker_state = threading.local()

            def generate_one(
                plan: DialogueGenerationPlan,
            ) -> tuple[DialogueGenerationPlan, Any | None, tuple[str, str, dict[str, Any]] | None]:
                worker_generator: DialogueGenerator | None = None
                try:
                    worker_generator = getattr(worker_state, "generator", None)
                    if worker_generator is None:
                        worker_generator = build_generator()
                        worker_state.generator = worker_generator
                    session = worker_generator.generate_session(plan, trajectory.persona)
                    return plan, session, None
                except Exception as exc:
                    client = getattr(worker_generator, "client", None)
                    metadata = dict(getattr(client, "last_response_metadata", {}) or {})
                    if client is not None and hasattr(client, "_provider_attempts_since_success"):
                        client._provider_attempts_since_success = 0
                        client._request_started_at = None
                    return plan, None, (type(exc).__name__, str(exc), metadata)

            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [executor.submit(generate_one, plan) for plan in pending_plans]
                for future in tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=f"sessions:{trajectory.trajectory_id}",
                    leave=False,
                ):
                    plan, session, error_info = future.result()
                    persist_result(plan, session, error_info)
                    if error_info is not None and not args.continue_on_error:
                        raise RuntimeError(error_info[1])
        else:
            assert generator is not None
            for plan in pending_plans:
                try:
                    session = generator.generate_session(plan, trajectory.persona)
                except Exception as exc:
                    client = getattr(generator, "client", None)
                    metadata = dict(getattr(client, "last_response_metadata", {}) or {})
                    if client is not None and hasattr(client, "_provider_attempts_since_success"):
                        client._provider_attempts_since_success = 0
                        client._request_started_at = None
                    persist_result(
                        plan,
                        None,
                        (type(exc).__name__, str(exc), metadata),
                    )
                    if not args.continue_on_error:
                        raise
                    continue
                persist_result(plan, session, None)
        if len(successful) == len(plans) and not errors:
            completed_trajectories.append(trajectory.trajectory_id)

    if args.dry_run:
        prompt_dir = raw_output_dir or paths.raw_model_outputs / "dialogue"
        print(f"dry-run: prompts written to {prompt_dir}")
    else:
        print(f"wrote {total_sessions} new sessions -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
