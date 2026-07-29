#!/usr/bin/env python
"""Build RQ1 natural progressive items (stage1_event_trajectory).

One item per trajectory/checkpoint pair at a fixed 15-session stride
(15, 30, ..., 300). Gold is derived from checkpoint PrefixGold joined with
canonical session records; the model-visible surface is materialized later
by the evaluator from session references (public ``D###`` ids + turns).

Outputs under ``--output-dir`` (conventionally data/runs/<RUN_ID>/rq1/):

    manifest.json
    taxonomy.json
    natural/progressive_items.jsonl
    natural/final_items.jsonl

Usage:
    python scripts/build_rq1_items.py \
        --prefix-gold data/runs/$RUN_ID/gold/prefix_gold_checkpoints_15.jsonl \
        --sessions-dir data/runs/$RUN_ID/dialogues/sessions \
        --trajectories-dir data/runs/$RUN_ID/trajectories \
        --output-dir data/runs/$RUN_ID/rq1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.build_rq1_items in tests
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.benchmark.rq1_builder import (
    CHECKPOINT_STRIDE,
    build_natural_items,
    build_public_taxonomy,
    load_session_records,
    taxonomy_hash,
)
from fin_life_benchmark.benchmark.rq1_models import (
    RQ1_BUILDER_VERSION,
    RQ1_METRICS_VERSION,
)
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.io import ensure_dialogue_sessions
from fin_life_benchmark.io.jsonl import write_jsonl
from fin_life_benchmark.io.paths import RepoPaths

PROMPT_RELPATH = Path("prompts/benchmark/rq1_event_trajectory_ko.md")

DEFAULT_DEV_TRAJECTORIES = ("traj_001",)

MODEL_VISIBLE_FIELDS = ("public_session_id", "turns[].speaker", "turns[].text")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(args: list[str], root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--trajectory-id",
        action="append",
        default=[],
        help="restrict to these trajectories (debugging only)",
    )
    parser.add_argument(
        "--dev-trajectory",
        action="append",
        default=[],
        help="dev-split trajectory ids (default: traj_001)",
    )
    parser.add_argument("--checkpoint-stride", type=int, default=CHECKPOINT_STRIDE)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    paths = RepoPaths.default()
    sessions_dir = Path(args.sessions_dir)
    ensure_dialogue_sessions(sessions_dir)

    prefixes = list(read_prefix_gold(Path(args.prefix_gold)))
    if args.trajectory_id:
        wanted = set(args.trajectory_id)
        prefixes = [p for p in prefixes if p.get("trajectory_id") in wanted]
    if not prefixes:
        raise SystemExit("empty prefix gold — run export_prefix_gold.py first")
    trajectory_ids = sorted({p["trajectory_id"] for p in prefixes})

    sessions_by_traj = load_session_records(sessions_dir, trajectory_ids)

    templates = load_life_event_templates(paths)
    taxonomy = build_public_taxonomy(templates)
    digest = taxonomy_hash(taxonomy)
    domain_by_event = {
        event_id: getattr(template, "domain", "") or ""
        for event_id, template in templates.items()
    }

    items = build_natural_items(
        prefixes,
        sessions_by_traj,
        taxonomy_digest=digest,
        checkpoint_stride=args.checkpoint_stride,
    )
    if not items:
        raise SystemExit("no RQ1 items produced — check checkpoint stride")

    output_dir = Path(args.output_dir)
    natural_dir = output_dir / "natural"
    natural_dir.mkdir(parents=True, exist_ok=True)

    progressive_path = natural_dir / "progressive_items.jsonl"
    write_jsonl(progressive_path, (item.model_dump(mode="json") for item in items))

    final_by_traj: dict[str, object] = {}
    for item in items:
        current = final_by_traj.get(item.trajectory_id)
        if current is None or item.checkpoint_session_count > current.checkpoint_session_count:
            final_by_traj[item.trajectory_id] = item
    final_items = [final_by_traj[t] for t in sorted(final_by_traj)]
    final_path = natural_dir / "final_items.jsonl"
    write_jsonl(final_path, (item.model_dump(mode="json") for item in final_items))

    taxonomy_path = output_dir / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {"taxonomy": taxonomy, "taxonomy_hash": digest},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # ------------------------------------------------------------------ manifest
    prompt_path = paths.root / PROMPT_RELPATH
    counts_by_checkpoint = Counter(i.checkpoint_session_count for i in items)
    status_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    for item in final_items:
        for event in item.gold.full_observed_ledger:
            status_counts[event.event_status] += 1
            event_counts[event.event_id] += 1
            domain_counts[domain_by_event.get(event.event_id, "unknown")] += 1

    dev = list(args.dev_trajectory) or [
        t for t in DEFAULT_DEV_TRAJECTORIES if t in trajectory_ids
    ]
    test = [t for t in trajectory_ids if t not in dev]

    hf_revision = os.getenv("HF_DIALOGUE_REVISION") or None
    manifest = {
        "task": "stage1_event_trajectory",
        "builder_version": RQ1_BUILDER_VERSION,
        "metrics_version": RQ1_METRICS_VERSION,
        "seed": args.seed,
        "checkpoint_stride": args.checkpoint_stride,
        "git_commit": _git(["rev-parse", "HEAD"], paths.root),
        "git_branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], paths.root),
        "git_worktree_dirty": bool(_git(["status", "--porcelain"], paths.root)),
        "hf_repo": os.getenv("HF_DIALOGUE_REPO")
        or "hangyeul-lee/life-event-benchmark-v2-dialogues",
        # only claim a revision when one was actually pinned via env
        "hf_revision_pinned": hf_revision,
        "trajectory_file_sha256": {
            p.name: _sha256_file(p)
            for p in sorted(Path(args.trajectories_dir).glob("traj_*.json"))
            if p.stem in trajectory_ids
        },
        "sessions_file_sha256": {
            p.name: _sha256_file(p)
            for p in sorted(sessions_dir.glob("sessions_*.jsonl"))
            if p.stem.removeprefix("sessions_") in trajectory_ids
        },
        "prefix_gold_sha256": _sha256_file(Path(args.prefix_gold)),
        "taxonomy_hash": digest,
        "prompt_file": str(PROMPT_RELPATH),
        "prompt_sha256": (
            hashlib.sha256(prompt_path.read_bytes()).hexdigest()
            if prompt_path.exists()
            else None
        ),
        "dev_trajectories": dev,
        "test_trajectories": test,
        "trajectory_ids": trajectory_ids,
        "item_counts": {
            "natural_progressive": len(items),
            "natural_final": len(final_items),
            "by_checkpoint": {
                str(cp): counts_by_checkpoint[cp]
                for cp in sorted(counts_by_checkpoint)
            },
        },
        "final_ledger_counts": {
            "by_status": dict(sorted(status_counts.items())),
            "by_event_id": dict(sorted(event_counts.items())),
            "by_domain": dict(sorted(domain_counts.items())),
        },
        "distractor_case_counts": None,  # filled by build_rq1_distractor_cases.py
        "model_visible_fields": list(MODEL_VISIBLE_FIELDS),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"rq1 natural items: {len(items)} progressive "
        f"({len(final_items)} final) -> {natural_dir}"
    )
    print(f"taxonomy: {len(taxonomy)} events -> {taxonomy_path}")
    print(f"manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
