#!/usr/bin/env python
"""Fetch the frozen v1 counterfactual filler bank from Hugging Face."""

from __future__ import annotations

import argparse

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.io import (
    DEFAULT_COUNTERFACTUAL_FILLERS_SUBDIR,
    DEFAULT_DIALOGUE_REPO,
    ensure_counterfactual_fillers,
)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        required=True,
        help="local counterfactual_fillers root; sessions/, plans/, audit/ are restored below it",
    )
    parser.add_argument(
        "--repo-id",
        help=f"HF dataset id (default env/{DEFAULT_DIALOGUE_REPO})",
    )
    parser.add_argument("--revision", help="HF commit, tag, or branch")
    parser.add_argument("--token", help="HF token (default environment)")
    parser.add_argument("--trajectory-id", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = ensure_counterfactual_fillers(
        args.output_root,
        repo_id=args.repo_id,
        revision=args.revision,
        token=args.token,
        trajectory_ids=args.trajectory_id or None,
        force=args.force,
    )
    print(
        f"ready: {root} "
        f"(remote subdir: {DEFAULT_COUNTERFACTUAL_FILLERS_SUBDIR})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
