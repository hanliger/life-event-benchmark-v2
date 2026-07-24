#!/usr/bin/env python
"""Restore frozen data (HF when absent) and build audited masking PrefixGold."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.io import (
    RepoPaths,
    ensure_counterfactual_fillers,
    ensure_dialogue_sessions,
)


def _run(command: list[str], root: Path) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=root, check=True)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="counterfactual_hf")
    parser.add_argument("--repo-id", help="HF dataset override")
    parser.add_argument(
        "--revision",
        help="pin an HF commit/tag; defaults to HF_DIALOGUE_REVISION or repo main",
    )
    parser.add_argument("--token", help="HF token (default environment)")
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--max-events", type=int, default=10000)
    parser.add_argument("--expected-events", type=int, default=451)
    args = parser.parse_args()

    paths = RepoPaths.default()
    run_dir = paths.root / "data" / "runs" / args.run_id
    trajectories_dir = run_dir / "trajectories"
    fixed_dir = run_dir / "trajectories_fixed"
    sessions_dir = run_dir / "dialogues" / "sessions"
    fillers_root = run_dir / "counterfactual_fillers"
    fillers_dir = fillers_root / "sessions"
    audit_dir = fillers_root / "audit" / "masking_full"
    logs_dir = fillers_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    fixture_dir = paths.root / "tests" / "fixtures" / "trajectories"
    fixture_files = sorted(fixture_dir.glob("traj_*.json"))
    if not fixture_files:
        raise SystemExit(f"frozen trajectory fixtures missing: {fixture_dir}")
    trajectories_dir.mkdir(parents=True, exist_ok=True)
    for source in fixture_files:
        destination = trajectories_dir / source.name
        if not destination.exists():
            shutil.copy2(source, destination)
    trajectory_ids = [path.stem for path in fixture_files]
    print(f"trajectories: {len(trajectory_ids)} ready -> {trajectories_dir}")

    missing_sessions = [
        trajectory_id
        for trajectory_id in trajectory_ids
        if not (sessions_dir / f"sessions_{trajectory_id}.jsonl").exists()
    ]
    ensure_dialogue_sessions(
        sessions_dir,
        repo_id=args.repo_id,
        revision=args.revision,
        token=args.token,
        trajectory_ids=trajectory_ids,
        force=args.force_fetch or bool(missing_sessions),
    )

    missing_fillers = [
        trajectory_id
        for trajectory_id in trajectory_ids
        if not (fillers_dir / f"fillers_{trajectory_id}.jsonl").exists()
    ]
    ensure_counterfactual_fillers(
        fillers_root,
        repo_id=args.repo_id,
        revision=args.revision,
        token=args.token,
        trajectory_ids=trajectory_ids,
        force=args.force_fetch or bool(missing_fillers),
    )

    _run(
        [
            sys.executable,
            "scripts/fix_education_stage_trajectory.py",
            "--in-dir",
            str(trajectories_dir),
            "--out-dir",
            str(fixed_dir),
        ],
        paths.root,
    )

    ladder_path = run_dir / "masking_ladder.json"
    prefix_gold_path = run_dir / "masking_ladder_prefix_gold.jsonl"
    _run(
        [
            sys.executable,
            "scripts/mask_lifecycle_experiment.py",
            "--trajectories-dir",
            str(fixed_dir),
            "--sessions-dir",
            str(sessions_dir),
            "--fillers-dir",
            str(fillers_dir),
            "--out",
            str(ladder_path),
            "--prefix-gold-out",
            str(prefix_gold_path),
            "--max-events",
            str(args.max_events),
            "--quiet",
        ],
        paths.root,
    )

    audit_command = [
        sys.executable,
        "scripts/audit_lifecycle_masking.py",
        "--ladder",
        str(ladder_path),
        "--prefix-gold",
        str(prefix_gold_path),
        "--exclusions",
        str(ladder_path.with_suffix(".exclusions.json")),
        "--out-dir",
        str(audit_dir),
    ]
    if args.expected_events is not None:
        audit_command.extend(["--expected-events", str(args.expected_events)])
    _run(audit_command, paths.root)

    print(f"\ncounterfactual ablation artifacts ready -> {run_dir}")
    print(f"masking decision -> {audit_dir / 'masking_decision.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
