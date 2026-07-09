#!/usr/bin/env python
"""Retry failed dialogue sessions and merge them with existing successes.

This script reads errors_<trajectory_id>.jsonl, regenerates only those session
IDs, and writes a merged sessions_<trajectory_id>.jsonl sorted by session_id.
Existing successful sessions are preserved unless a retried session succeeds,
in which case the retried version fills that missing session_id.
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
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, read_jsonl, write_jsonl
from fin_life_benchmark.llm.client import LLMClient
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.trajectory.models import Trajectory


def _session_sort_key(session: dict[str, Any]) -> tuple[int, str]:
    session_id = str(session.get("session_id", ""))
    if session_id.startswith("S") and session_id[1:].isdigit():
        return int(session_id[1:]), session_id
    return 10**9, session_id


def _write_jsonl_line(handle, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


def _load_existing_sessions(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    sessions: dict[str, dict[str, Any]] = {}
    for session in read_jsonl(path):
        session_id = session.get("session_id")
        if session_id:
            sessions[str(session_id)] = session
    return sessions


def _load_failed_session_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    seen: set[str] = set()
    failed: list[str] = []
    for error in read_jsonl(path):
        session_id = error.get("session_id")
        if not session_id or session_id in seen:
            continue
        seen.add(str(session_id))
        failed.append(str(session_id))
    return failed


def _clean_retry_label(label: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in label.strip())
    return cleaned.strip("_") or "retry"


def _next_retry_suffix(raw_dir: Path, trajectory_id: str, session_ids: list[str], label: str) -> str:
    base = _clean_retry_label(label)
    index = 1
    while True:
        candidate = base if index == 1 else f"{base}{index}"
        suffix = f"_{candidate}"
        would_collide = any(
            any(raw_dir.glob(f"{trajectory_id}_{session_id}{suffix}*.txt"))
            for session_id in session_ids
        )
        if not would_collide:
            return suffix
        index += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--trajectory-id", default=None, help="retry a single trajectory id, e.g. traj_00078")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-trajectories", type=int, default=None)
    parser.add_argument("--max-retry-sessions", type=int, default=None)
    parser.add_argument(
        "--raw-output-dir",
        default=None,
        help="where retry LLM raw outputs are written; default: data/raw_model_outputs/dialogue",
    )
    parser.add_argument(
        "--retry-label",
        default="retry",
        help="filename label appended before .txt; default creates names like traj_00078_S030_retry.txt",
    )
    parser.add_argument(
        "--replace-errors",
        action="store_true",
        help="replace errors_<trajectory_id>.jsonl with only retry failures; otherwise write retry_errors_<trajectory_id>.jsonl",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="call the LLM API")
    mode.add_argument("--mock", action="store_true", help="deterministic template dialogues")
    parser.add_argument("--provider", default=None, help="override DEFAULT_LLM_PROVIDER")
    parser.add_argument("--model", default=None, help="override DEFAULT_GENERATION_MODEL")
    args = parser.parse_args()

    paths = RepoPaths.default()
    locale = load_locale(args.locale, paths)
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, locale, paths)

    raw_output_dir = Path(args.raw_output_dir) if args.raw_output_dir else paths.raw_model_outputs / "dialogue"
    generator: DialogueGenerator | None = None

    if args.execute:
        client = LLMClient.from_env(provider=args.provider, model=args.model)
        if client.provider == "mock":
            raise SystemExit("--execute requires DEFAULT_LLM_PROVIDER=openai|anthropic in .env")
        generator_client = client
    else:
        generator_client = None

    trajectory_files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    if args.trajectory_id:
        trajectory_files = [p for p in trajectory_files if p.stem == args.trajectory_id]
    if args.max_trajectories is not None:
        trajectory_files = trajectory_files[: args.max_trajectories]
    if not trajectory_files:
        raise SystemExit(f"no matching traj_*.json under {args.trajectories_dir}")

    sessions_dir = Path(args.sessions_dir)
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    total_attempted = 0
    total_recovered = 0
    total_failed = 0

    for traj_file in tqdm(trajectory_files, desc="retry-dialogue"):
        trajectory = Trajectory.model_validate(json.loads(traj_file.read_text(encoding="utf-8")))
        trajectory_id = trajectory.trajectory_id
        sessions_path = sessions_dir / f"sessions_{trajectory_id}.jsonl"
        errors_path = sessions_dir / f"errors_{trajectory_id}.jsonl"
        retry_errors_path = errors_path if args.replace_errors else sessions_dir / f"retry_errors_{trajectory_id}.jsonl"

        failed_session_ids = _load_failed_session_ids(errors_path)
        if args.max_retry_sessions is not None:
            failed_session_ids = failed_session_ids[: args.max_retry_sessions]
        if not failed_session_ids:
            print(f"skip {trajectory_id}: no failed sessions in {errors_path}")
            continue

        raw_filename_suffix = _next_retry_suffix(raw_output_dir, trajectory_id, failed_session_ids, args.retry_label)
        if args.execute:
            generator = DialogueGenerator(
                mode="llm",
                client=generator_client,
                paths=paths,
                raw_output_dir=raw_output_dir,
                raw_filename_suffix=raw_filename_suffix,
            )
        else:
            generator = DialogueGenerator(
                mode="mock",
                paths=paths,
                raw_output_dir=raw_output_dir,
                raw_filename_suffix=raw_filename_suffix,
            )
        print(f"{trajectory_id}: retry raw outputs -> {raw_output_dir}/*{raw_filename_suffix}.txt")

        existing_sessions = _load_existing_sessions(sessions_path)
        plans = {plan.session_id: plan for plan in planner.build_plans(trajectory, seed=args.seed)}
        missing_plans = [sid for sid in failed_session_ids if sid not in plans]
        if missing_plans:
            print(f"warning {trajectory_id}: no plans for {missing_plans}")

        retry_failures: list[dict[str, Any]] = []
        recovered = 0
        for session_id in tqdm(failed_session_ids, desc=trajectory_id, leave=False):
            plan = plans.get(session_id)
            if plan is None:
                retry_failures.append(
                    {
                        "trajectory_id": trajectory_id,
                        "session_id": session_id,
                        "error_type": "MissingPlanError",
                        "error": f"no plan found for {session_id}",
                    }
                )
                continue
            total_attempted += 1
            try:
                session = generator.generate_session(plan, trajectory.persona)
            except Exception as exc:  # keep retrying remaining sessions
                error_record = {
                    "trajectory_id": trajectory_id,
                    "session_id": plan.session_id,
                    "month_index": plan.month_index,
                    "session_type": plan.session_type,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                metadata = getattr(getattr(generator, "client", None), "last_response_metadata", None)
                if metadata:
                    error_record["llm_metadata"] = metadata
                retry_failures.append(error_record)
                total_failed += 1
                continue
            if session is not None:
                existing_sessions[session.session_id] = session.model_dump(mode="json")
                recovered += 1
                total_recovered += 1

        merged = [existing_sessions[key] for key in sorted(existing_sessions, key=lambda sid: _session_sort_key(existing_sessions[sid]))]
        write_jsonl(sessions_path, merged)
        write_jsonl(retry_errors_path, retry_failures)
        print(
            f"{trajectory_id}: attempted {len(failed_session_ids)}, recovered {recovered}, "
            f"remaining failures {len(retry_failures)} -> {sessions_path}"
        )
        if retry_failures:
            print(f"retry errors -> {retry_errors_path}")

    print(f"total attempted {total_attempted}, recovered {total_recovered}, failed {total_failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
