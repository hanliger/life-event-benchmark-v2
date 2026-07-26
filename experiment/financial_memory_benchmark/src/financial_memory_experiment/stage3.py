from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

from fin_life_benchmark.benchmark.multihop import (
    audit_stage3_multihop_items,
    build_stage3_multihop_targets,
    load_multihop_session_records,
    load_stage3_multihop_policy,
    load_stage3_multihop_representative_policy,
)
from fin_life_benchmark.benchmark.stage3_item_builder import Stage3ItemBuilder
from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.gold.prefix_gold_exporter import serialize_memory_state
from fin_life_benchmark.trajectory.models import Trajectory

from .config import load_experiment_config
from .data_pipeline import active_prepared_manifest
from .paths import ExperimentPaths
from .util import read_jsonl, session_number, sha256_file, write_json, write_jsonl


def _prepared_root(paths: ExperimentPaths) -> Path:
    return Path(active_prepared_manifest(paths)["root"])


def _initial_memory_by_trajectory(directory: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("traj_*.json")):
        trajectory = Trajectory.model_validate_json(path.read_text(encoding="utf-8"))
        result[trajectory.trajectory_id] = serialize_memory_state(
            trajectory.initial_financial_memory_state
        )
    return result


def _normalize_stage3_item(item: dict[str, Any]) -> dict[str, Any]:
    """Adapt the upstream Stage 3 item to the experiment's S000/evidence contract."""

    row = copy.deepcopy(item)
    metadata = row.setdefault("metadata", {})
    metadata.pop("initial_memory", None)
    gold = row.get("gold") or {}
    hops = gold.get("hops") or []
    query_checkpoint = int(
        metadata.get("first_visible_checkpoint")
        or len(row.get("visible_sessions") or [])
    )
    evidence_sessions = sorted(
        {
            str(session_id)
            for hop in hops
            for session_id in hop.get("evidence_sessions") or []
        },
        key=session_number,
    )
    metadata.update(
        {
            "query_checkpoint": query_checkpoint,
            "hop_checkpoints": [
                int(hop["checkpoint_session_count"]) for hop in hops
            ],
            "evidence_sessions": evidence_sessions,
            "initial_state_protocol": "S000_ingest_once",
            "task_semantics": "two_checkpoint_multi_hop",
        }
    )
    return row


def build_stage3_items(paths: ExperimentPaths) -> dict[str, Any]:
    cfg = load_experiment_config(paths)
    expected = cfg["dataset"]["expected"]
    stride = int(cfg["benchmark"]["checkpoint_stride"])
    seed = int(cfg["benchmark"]["option_seed"])
    root = _prepared_root(paths)
    prefix_path = root / "prefix_gold" / "prefix_gold_checkpoints_15.jsonl"
    sessions_dir = root / "sessions_joined"
    trajectories_dir = root / "trajectories_fixed"
    policy_path = (
        paths.repo_root / "configs" / "registries" / "stage3_multihop_policy.yaml"
    )
    if not prefix_path.exists():
        raise FileNotFoundError("prefix gold is absent; run build-prefix-gold")

    prefixes = list(read_prefix_gold(prefix_path))
    sessions_by_trajectory = load_multihop_session_records(sessions_dir)
    initial_memory = _initial_memory_by_trajectory(trajectories_dir)
    policy = load_stage3_multihop_policy(policy_path)
    representative_policy = load_stage3_multihop_representative_policy(policy_path)
    result = build_stage3_multihop_targets(
        prefixes,
        sessions_by_trajectory,
        policy,
        initial_memory_by_traj=initial_memory,
        representative_policy=representative_policy,
        window_size=stride,
    )
    raw_items = [
        item.model_dump(mode="json")
        for item in Stage3ItemBuilder(
            seed=seed,
            shuffle_options=True,
        ).build_stage3_multihop(
            result.targets,
            initial_memory_by_traj=initial_memory,
        )
    ]
    audit = audit_stage3_multihop_items(
        raw_items,
        prefixes,
        sessions_by_trajectory,
        policy=policy,
        expected_representatives=result.report["representative_selection"],
    )
    if not audit["passed"]:
        raise ValueError(
            "Stage 3 upstream provenance audit failed: "
            f"{audit['failed_items']} item failures, "
            f"{len(audit['selection_failures'])} selection failures"
        )

    rows = [_normalize_stage3_item(item) for item in raw_items]
    expected_count = int(expected["stage3_items"])
    if len(rows) != expected_count:
        raise ValueError(f"expected {expected_count} Stage 3 items, got {len(rows)}")

    output_dir = root / "canonical_items"
    items_path = output_dir / "stage3_multi_hop_mcq.jsonl"
    report_path = output_dir / "stage3_multi_hop_build_report.json"
    audit_path = output_dir / "stage3_multi_hop_audit.json"
    write_jsonl(items_path, rows)
    write_json(
        report_path,
        {
            **result.report,
            "written_item_count": len(rows),
            "option_seed": seed,
            "options_shuffled": True,
            "initial_state_protocol": "S000_ingest_once",
            "output": str(items_path),
        },
    )
    write_json(audit_path, audit)
    return {
        "path": items_path,
        "count": len(rows),
        "sha256": sha256_file(items_path),
        "audit_path": audit_path,
        "audit_sha256": sha256_file(audit_path),
        "by_derivation_type": dict(
            sorted(
                Counter(
                    str((row.get("metadata") or {})["derivation_type"])
                    for row in rows
                ).items()
            )
        ),
    }


def validate_stage3_items(paths: ExperimentPaths) -> dict[str, Any]:
    cfg = load_experiment_config(paths)
    expected = int(cfg["dataset"]["expected"]["stage3_items"])
    root = _prepared_root(paths)
    path = root / "canonical_items" / "stage3_multi_hop_mcq.jsonl"
    audit_path = root / "canonical_items" / "stage3_multi_hop_audit.json"
    rows = list(read_jsonl(path))
    errors: list[str] = []
    if len(rows) != expected:
        errors.append(f"Stage 3 count {len(rows)}")
    if not audit_path.exists():
        errors.append("Stage 3 upstream audit report is absent")
    else:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if not audit.get("passed") or int(audit.get("items", -1)) != len(rows):
            errors.append("Stage 3 upstream audit did not pass for the frozen item set")

    for row in rows:
        item_id = str(row.get("item_id") or "")
        metadata = row.get("metadata") or {}
        gold = row.get("gold") or {}
        visible = list(row.get("visible_sessions") or [])
        checkpoint = int(metadata.get("query_checkpoint") or 0)
        evidence = list(metadata.get("evidence_sessions") or [])
        options = list(row.get("options") or [])
        if row.get("stage") != "stage3_multi_hop_mcq":
            errors.append(f"{item_id}: invalid stage")
        if metadata.get("reasoning_type") != "multi_hop":
            errors.append(f"{item_id}: invalid reasoning type")
        if "initial_memory" in metadata:
            errors.append(f"{item_id}: query-time initial_memory leakage")
        if len(visible) != checkpoint:
            errors.append(f"{item_id}: visible prefix/checkpoint mismatch")
        if evidence and max(map(session_number, evidence)) > checkpoint:
            errors.append(f"{item_id}: future evidence")
        if gold.get("hop_count") != 2 or len(gold.get("hops") or []) != 2:
            errors.append(f"{item_id}: invalid hop contract")
        if len(options) != 4 or [option.get("option_id") for option in options] != list(
            "ABCD"
        ):
            errors.append(f"{item_id}: invalid A-D options")
        if sum(bool(option.get("correct")) for option in options) != 1:
            errors.append(f"{item_id}: invalid correct option count")
        if gold.get("correct_option") not in set("ABCD"):
            errors.append(f"{item_id}: invalid gold option")

    report = {
        "decision": "PASS" if not errors else "FAIL",
        "stage3_items": len(rows),
        "by_derivation_type": dict(
            sorted(
                Counter(
                    str((row.get("metadata") or {}).get("derivation_type"))
                    for row in rows
                ).items()
            )
        ),
        "errors": errors,
    }
    if errors:
        raise ValueError("Stage 3 validation failed:\n- " + "\n- ".join(errors[:30]))
    return report
