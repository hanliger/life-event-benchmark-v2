#!/usr/bin/env python
"""Run identical sampled plans through one or more model profiles."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--trajectory-id", required=True)
    parser.add_argument("--model-profile", action="append", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--continue-on-error", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    output_root = Path(args.output_root)
    for profile in args.model_profile:
        profile_root = output_root / profile
        command = [
            sys.executable, "scripts/generate_dialogue_sessions.py",
            "--trajectories-dir", args.trajectories_dir,
            "--plans-dir", args.plans_dir,
            "--trajectory-id", args.trajectory_id,
            "--model-profile", profile,
            "--output-dir", str(profile_root / "sessions"),
            "--raw-output-dir", str(profile_root / "raw_outputs"),
            "--allow-partial-plans", "--overwrite",
            "--execute" if args.execute else "--dry-run",
        ]
        if args.continue_on_error:
            command.append("--continue-on-error")
        subprocess.run(command, check=True)
        if args.execute:
            subprocess.run([
                sys.executable, "scripts/audit_dialogue_generation.py",
                "--trajectories-dir", args.trajectories_dir,
                "--plans-dir", args.plans_dir,
                "--sessions-dir", str(profile_root / "sessions"),
                "--raw-output-dir", str(profile_root / "raw_outputs"),
                "--output-dir", str(profile_root / "audit"),
                "--trajectory-id", args.trajectory_id,
            ], check=True)
    if args.execute:
        subprocess.run([
            sys.executable, "scripts/compare_dialogue_models.py",
            "--result-root", str(output_root),
            "--output-dir", str(output_root.parent / "comparison"),
        ], check=True)
    else:
        comparison = output_root.parent / "comparison"; comparison.mkdir(parents=True, exist_ok=True)
        (comparison / "model_comparison.json").write_text(json.dumps({"status": "dry_run", "profiles": args.model_profile}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__": raise SystemExit(main())
