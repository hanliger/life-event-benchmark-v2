"""Materialize the frozen benchmark run into a local run directory.

The 20 frozen trajectories and dialogue sessions are the current generation
result and are NOT regenerated. Their sources:

- trajectories: tracked, byte-frozen, under ``tests/fixtures/trajectories/``
- dialogue sessions: the HuggingFace dataset (``dialogues`` + ``gold`` configs),
  reconstructed into ``sessions_traj_XXX.jsonl`` (see ``io/hf_data.py``)

This copies the frozen trajectories and fetches the frozen sessions into
``data/runs/<RUN_ID>/`` so downstream steps (plan/gold/items/eval) can run on
the existing corpus. Downstream artifacts are deterministic and may be rebuilt;
the trajectories and sessions themselves are never regenerated here.

    python scripts/restore_frozen_run.py --run-id frozen
    make restore-frozen-run RUN_ID=frozen
"""

from __future__ import annotations

import argparse
import shutil

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.restore_frozen_run in tests
    from scripts import _bootstrap  # type: ignore # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.io import RepoPaths, ensure_dialogue_sessions


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="frozen", help="run id under data/runs/ (default: frozen)")
    parser.add_argument(
        "--trajectory-id",
        action="append",
        default=[],
        help="restore only these trajectory ids (repeatable); default: all frozen trajectories",
    )
    parser.add_argument(
        "--skip-sessions",
        action="store_true",
        help="only restore trajectories; do not fetch dialogue sessions from HF",
    )
    parser.add_argument(
        "--dialogues-only",
        action="store_true",
        help="fetch answer-free dialogue turns only (no gold labels joined in)",
    )
    args = parser.parse_args()

    paths = RepoPaths.default()
    frozen_traj_dir = paths.root / "tests" / "fixtures" / "trajectories"
    if not frozen_traj_dir.is_dir():
        raise SystemExit(f"frozen trajectories not found: {frozen_traj_dir}")

    run_dir = paths.root / "data" / "runs" / args.run_id
    traj_out = run_dir / "trajectories"
    sess_out = run_dir / "dialogues" / "sessions"
    traj_out.mkdir(parents=True, exist_ok=True)

    wanted = set(args.trajectory_id) or None
    available = sorted(frozen_traj_dir.glob("*.json"))
    selected = [p for p in available if wanted is None or p.stem in wanted]
    if not selected:
        raise SystemExit(
            f"no frozen trajectories matched {sorted(wanted)}; "
            f"available: {[p.stem for p in available]}"
        )
    for src in selected:
        shutil.copy2(src, traj_out / src.name)
    print(f"trajectories: copied {len(selected)} -> {traj_out}")

    if args.skip_sessions:
        print("sessions: skipped (--skip-sessions)")
        return

    ensure_dialogue_sessions(
        sess_out,
        include_gold=not args.dialogues_only,
        trajectory_ids=[p.stem for p in selected],
        force=True,
    )
    n = len(sorted(sess_out.glob("sessions_*.jsonl")))
    kind = "answer-free dialogues" if args.dialogues_only else "gold-joined sessions"
    print(f"sessions: {n} {kind} file(s) -> {sess_out}")
    print(f"\nfrozen run ready at {run_dir}")


if __name__ == "__main__":
    main()
