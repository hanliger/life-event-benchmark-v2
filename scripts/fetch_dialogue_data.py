"""Fetch dialogue session data from the private HuggingFace dataset.

This repo is code-only; the dialogue corpus lives in a HF dataset. Use this to
populate a run's sessions directory before validation/gold/benchmark steps:

    python scripts/fetch_dialogue_data.py \
        --sessions-dir data/runs/<RUN_ID>/dialogues/sessions

Configuration (repo id, revision, subdir, token) is read from the environment
(HF_DIALOGUE_REPO, HF_DIALOGUE_REVISION, HF_DIALOGUE_SUBDIR, HF_TOKEN); flags
below override it. Consumers also fetch automatically when a sessions directory
is empty, so this script is mainly for pre-fetching or forcing a refresh.
"""

from __future__ import annotations

import argparse

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.fetch_dialogue_data in tests
    from scripts import _bootstrap  # type: ignore # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.io import DEFAULT_DIALOGUE_REPO, ensure_dialogue_sessions


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions-dir",
        required=True,
        help="local directory to populate with sessions_*.jsonl",
    )
    parser.add_argument("--repo-id", default=None, help=f"HF dataset id (default env/{DEFAULT_DIALOGUE_REPO})")
    parser.add_argument("--revision", default=None, help="git revision/branch/tag")
    parser.add_argument("--token", default=None, help="HF access token (default env)")
    parser.add_argument("--trajectory-id", action="append", default=[], help="restore only these trajectory ids (repeatable)")
    parser.add_argument("--dialogues-only", action="store_true", help="answer-free dialogue turns only (do not join gold labels)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even if the directory already has session files",
    )
    args = parser.parse_args()

    sessions_dir = ensure_dialogue_sessions(
        args.sessions_dir,
        repo_id=args.repo_id,
        revision=args.revision,
        token=args.token,
        include_gold=not args.dialogues_only,
        trajectory_ids=args.trajectory_id or None,
        force=args.force,
    )
    n = len(sorted(sessions_dir.glob("sessions_*.jsonl")))
    print(f"{sessions_dir}: {n} sessions_*.jsonl file(s) ready")


if __name__ == "__main__":
    main()
