#!/usr/bin/env python
"""Validate and atomically publish the frozen v1 filler bank to Hugging Face."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.dialogue.counterfactual_fillers import (
    CounterfactualFiller,
    CounterfactualFillerPlan,
    FILLER_CONTRACT_VERSION,
    audit_filler_bank,
)
from fin_life_benchmark.io import (
    DEFAULT_COUNTERFACTUAL_FILLERS_SUBDIR,
    DEFAULT_DIALOGUE_REPO,
    read_jsonl,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fillers-root",
        required=True,
        help="local root containing sessions/, plans/, audit/full/",
    )
    parser.add_argument(
        "--repo-id",
        default=os.getenv("HF_DIALOGUE_REPO") or DEFAULT_DIALOGUE_REPO,
    )
    parser.add_argument("--revision", default="main")
    parser.add_argument("--token", default=None)
    parser.add_argument(
        "--dataset-card",
        default="docs/huggingface_dataset_card.md",
    )
    parser.add_argument(
        "--remote-subdir",
        default=DEFAULT_COUNTERFACTUAL_FILLERS_SUBDIR,
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    root = Path(args.fillers_root)
    sessions_dir = root / "sessions"
    plans_dir = root / "plans"
    filler_audit_dir = root / "audit" / "full"
    masking_audit_dir = root / "audit" / "masking_full"
    session_files = sorted(sessions_dir.glob("fillers_traj_*.jsonl"))
    plan_files = sorted(plans_dir.glob("plans_traj_*.jsonl"))
    if len(session_files) != 20 or len(plan_files) != 20:
        raise SystemExit(
            f"expected 20 session + 20 plan files, got "
            f"{len(session_files)} + {len(plan_files)}"
        )

    session_ids = {
        path.stem.removeprefix("fillers_") for path in session_files
    }
    plan_ids = {path.stem.removeprefix("plans_") for path in plan_files}
    if session_ids != plan_ids:
        raise SystemExit("filler session/plan trajectory sets differ")

    total_rows = 0
    for trajectory_id in sorted(session_ids):
        plans = [
            CounterfactualFillerPlan.model_validate(item)
            for item in read_jsonl(plans_dir / f"plans_{trajectory_id}.jsonl")
        ]
        fillers = [
            CounterfactualFiller.model_validate(item)
            for item in read_jsonl(sessions_dir / f"fillers_{trajectory_id}.jsonl")
        ]
        result = audit_filler_bank(plans, fillers)
        if result["decision"] != "PASS":
            raise SystemExit(
                f"local filler audit failed for {trajectory_id}: "
                f"{result['violations'][:3]}"
            )
        total_rows += len(fillers)
    if total_rows != 400:
        raise SystemExit(f"expected 400 filler rows, got {total_rows}")

    required_files = [
        sessions_dir / "filler_generation_manifest.json",
        filler_audit_dir / "filler_audit.json",
        filler_audit_dir / "filler_audit.md",
        filler_audit_dir / "filler_decision.json",
        masking_audit_dir / "masking_audit.json",
        masking_audit_dir / "masking_audit.md",
        masking_audit_dir / "masking_decision.json",
        Path(args.dataset_card),
    ]
    missing = [str(path) for path in required_files if not path.exists()]
    if missing:
        raise SystemExit("required publish files missing: " + ", ".join(missing))

    remote_subdir = args.remote_subdir.strip("/")
    publish_files: list[tuple[Path, str]] = []
    publish_files.extend(
        (path, f"{remote_subdir}/sessions/{path.name}")
        for path in session_files
    )
    publish_files.extend(
        (path, f"{remote_subdir}/plans/{path.name}")
        for path in plan_files
    )
    publish_files.append(
        (
            sessions_dir / "filler_generation_manifest.json",
            f"{remote_subdir}/filler_generation_manifest.json",
        )
    )
    for path in required_files[1:4]:
        publish_files.append((path, f"{remote_subdir}/audit/{path.name}"))
    for path in required_files[4:7]:
        publish_files.append((path, f"{remote_subdir}/audit/{path.name}"))
    publish_files.append((Path(args.dataset_card), "README.md"))

    repo_root = Path(__file__).resolve().parents[1]
    source_files = [
        repo_root / "src/fin_life_benchmark/dialogue/counterfactual_fillers.py",
        repo_root / "src/fin_life_benchmark/io/hf_data.py",
        repo_root / "scripts/generate_counterfactual_fillers.py",
        repo_root / "scripts/mask_lifecycle_experiment.py",
    ]
    artifact_manifest: dict[str, Any] = {
        "layout_version": "counterfactual-fillers-hf-v1",
        "contract_version": FILLER_CONTRACT_VERSION,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "repo_id": args.repo_id,
        "remote_subdir": remote_subdir,
        "persona_files": len(session_files),
        "filler_rows": total_rows,
        "source_git_commit": _git(repo_root, "rev-parse", "HEAD"),
        "source_git_branch": _git(repo_root, "branch", "--show-current"),
        "source_worktree_dirty": bool(_git(repo_root, "status", "--porcelain")),
        "source_file_sha256": {
            str(path.relative_to(repo_root)): _sha256(path)
            for path in source_files
        },
        "artifact_file_sha256": {
            remote_path: _sha256(local_path)
            for local_path, remote_path in publish_files
            if remote_path != "README.md"
        },
    }
    print(
        json.dumps(
            {
                "repo_id": args.repo_id,
                "revision": args.revision,
                "remote_subdir": remote_subdir,
                "persona_files": len(session_files),
                "filler_rows": total_rows,
                "files_to_commit": len(publish_files) + 1,
                "source_worktree_dirty": artifact_manifest["source_worktree_dirty"],
                "execute": args.execute,
            },
            ensure_ascii=False,
        )
    )
    if not args.execute:
        print("dry-run only; pass --execute to publish")
        return 0

    try:
        from huggingface_hub import CommitOperationAdd, HfApi
    except ModuleNotFoundError as exc:
        raise SystemExit("huggingface_hub is required for publishing") from exc

    token = (
        args.token
        or os.getenv("HF_TOKEN")
        or os.getenv("HUGGINGFACE_TOKEN")
        or None
    )
    api = HfApi(token=token)
    repo_info = api.repo_info(
        repo_id=args.repo_id,
        repo_type="dataset",
        revision=args.revision,
    )
    artifact_manifest["parent_commit"] = repo_info.sha
    with tempfile.TemporaryDirectory(prefix="cf-filler-publish-") as temporary:
        manifest_path = Path(temporary) / "artifact_manifest.json"
        manifest_path.write_text(
            json.dumps(artifact_manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        operations = [
            CommitOperationAdd(
                path_in_repo=remote_path,
                path_or_fileobj=str(local_path),
            )
            for local_path, remote_path in publish_files
        ]
        operations.append(
            CommitOperationAdd(
                path_in_repo=f"{remote_subdir}/artifact_manifest.json",
                path_or_fileobj=str(manifest_path),
            )
        )
        commit = api.create_commit(
            repo_id=args.repo_id,
            repo_type="dataset",
            revision=args.revision,
            parent_commit=repo_info.sha,
            operations=operations,
            commit_message=(
                "Add v1 persona counterfactual filler bank "
                "(400 Sonnet 5 dialogues)"
            ),
        )
    print(
        json.dumps(
            {
                "commit_oid": commit.oid,
                "commit_url": commit.commit_url,
                "parent_commit": repo_info.sha,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
