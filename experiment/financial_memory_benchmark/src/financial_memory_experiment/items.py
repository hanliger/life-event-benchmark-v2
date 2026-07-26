from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.benchmark.mcq_input import (
    build_stage2_checkpoints,
    load_mcq_windows,
    load_stage2_question_policy,
)
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.gold.prefix_gold_exporter import (
    export_prefix_gold,
    serialize_memory_state,
)
from fin_life_benchmark.io.paths import RepoPaths
from fin_life_benchmark.trajectory.models import Trajectory

from .config import load_experiment_config
from .data_pipeline import active_prepared_manifest
from .paths import ExperimentPaths
from .util import read_jsonl, session_number, sha256_file, write_json, write_jsonl


def _prepared_root(paths: ExperimentPaths) -> Path:
    return Path(active_prepared_manifest(paths)["root"])


def _load_trajectories(directory: Path) -> dict[str, Trajectory]:
    return {
        trajectory.trajectory_id: trajectory
        for path in sorted(directory.glob("traj_*.json"))
        for trajectory in [Trajectory.model_validate_json(path.read_text(encoding="utf-8"))]
    }


def _load_sessions(directory: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(directory.glob("sessions_traj_*.jsonl")):
        rows = list(read_jsonl(path))
        if rows:
            result[str(rows[0]["trajectory_id"])] = rows
    return result


def build_prefix_gold_artifact(paths: ExperimentPaths) -> Path:
    cfg = load_experiment_config(paths)
    stride = int(cfg["benchmark"]["checkpoint_stride"])
    root = _prepared_root(paths)
    trajectories = _load_trajectories(root / "trajectories_fixed")
    sessions = _load_sessions(root / "sessions_joined")
    missing = sorted(set(trajectories) - set(sessions))
    if missing:
        raise ValueError(f"missing joined sessions: {missing}")

    records: list[dict[str, Any]] = []
    for trajectory_id in sorted(trajectories):
        prefixes = export_prefix_gold(
            trajectories[trajectory_id],
            sessions[trajectory_id],
            checkpoint_stride=stride,
        )
        records.extend(prefix.model_dump(mode="json") for prefix in prefixes)
    output = root / "prefix_gold" / "prefix_gold_checkpoints_15.jsonl"
    count = write_jsonl(output, records)
    expected = int(cfg["dataset"]["expected"]["stage1_items"])
    if count != expected:
        raise ValueError(f"expected {expected} prefix checkpoints, got {count}")
    return output


def _initial_memory(trajectories: dict[str, Trajectory]) -> dict[str, dict[str, Any]]:
    return {
        trajectory_id: serialize_memory_state(trajectory.initial_financial_memory_state)
        for trajectory_id, trajectory in trajectories.items()
    }


def _strip_query_time_initial_memory(item: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(item)
    metadata = result.setdefault("metadata", {})
    metadata.pop("initial_memory", None)
    metadata["initial_state_protocol"] = "S000_ingest_once"
    return result


def build_canonical_items(paths: ExperimentPaths) -> dict[str, Path]:
    cfg = load_experiment_config(paths)
    expected = cfg["dataset"]["expected"]
    stride = int(cfg["benchmark"]["checkpoint_stride"])
    seed = int(cfg["benchmark"]["option_seed"])
    root = _prepared_root(paths)
    prefix_path = root / "prefix_gold" / "prefix_gold_checkpoints_15.jsonl"
    if not prefix_path.exists():
        raise FileNotFoundError("prefix gold is absent; run build-prefix-gold")

    trajectories_dir = root / "trajectories_fixed"
    sessions_dir = root / "sessions_joined"
    trajectories = _load_trajectories(trajectories_dir)
    sessions = _load_sessions(sessions_dir)

    windows = load_mcq_windows(
        sessions_dir,
        None,
        trajectories_dir,
        window_size=stride,
    )
    stage1 = ItemBuilder().build_stage1_event_identification(
        windows,
        load_life_event_templates(RepoPaths(root=paths.repo_root)),
    )
    stage1_rows: list[dict[str, Any]] = []
    for item in stage1:
        row = item.model_dump(mode="json")
        checkpoint = int(str(item.metadata["target_session_end"]).removeprefix("S"))
        row["visible_sessions"] = [
            str(session["session_id"])
            for session in sessions[item.trajectory_id][:checkpoint]
        ]
        row["metadata"] = {
            **item.metadata,
            "query_checkpoint": checkpoint,
            "n_visible_sessions": checkpoint,
            "input_semantics": "full_prefix_with_target_window_date_filter",
            "retention_lag_sessions": 0,
            "retention_lag_windows": 0,
            "initial_state_protocol": "S000_ingest_once",
        }
        stage1_rows.append(row)

    prefixes = list(read_prefix_gold(prefix_path))
    policy = load_stage2_question_policy(
        paths.repo_root / "configs" / "registries" / "stage2_question_policy.yaml"
    )
    checkpoints = build_stage2_checkpoints(
        prefixes,
        sessions_by_traj=sessions,
        initial_memory_by_traj=_initial_memory(trajectories),
        question_policy=policy,
        strict_event_targets=True,
        window_size=stride,
    )
    stage2 = ItemBuilder(seed=seed, shuffle_options=True).build_stage2(
        checkpoints,
        initial_memory_by_traj=_initial_memory(trajectories),
        window_size=stride,
    )
    stage2_rows: list[dict[str, Any]] = []
    for item in stage2:
        row = _strip_query_time_initial_memory(item.model_dump(mode="json"))
        metadata = row["metadata"]
        query_checkpoint = int(metadata["checkpoint_session_count"])
        first_checkpoint = int(metadata["first_visible_checkpoint"])
        metadata.update(
            {
                "query_checkpoint": query_checkpoint,
                "target_checkpoint": first_checkpoint,
                "retention_lag_sessions": query_checkpoint - first_checkpoint,
                "retention_lag_windows": (query_checkpoint - first_checkpoint) // stride,
                "task_semantics": "historical_state_as_of_target_window",
            }
        )
        stage2_rows.append(row)

    if len(stage1_rows) != int(expected["stage1_items"]):
        raise ValueError(f"expected {expected['stage1_items']} Stage 1 items, got {len(stage1_rows)}")
    if len(stage2_rows) != int(expected["stage2_items"]):
        raise ValueError(f"expected {expected['stage2_items']} Stage 2 items, got {len(stage2_rows)}")

    output_dir = root / "canonical_items"
    stage1_path = output_dir / "stage1_event_identification.jsonl"
    stage2_path = output_dir / "stage2_historical_memory_mcq.jsonl"
    write_jsonl(stage1_path, stage1_rows)
    write_jsonl(stage2_path, stage2_rows)
    manifest = {
        "schema_version": "canonical-items-manifest-v1",
        "stage1_items": len(stage1_rows),
        "stage2_items": len(stage2_rows),
        "stage1_sha256": sha256_file(stage1_path),
        "stage2_sha256": sha256_file(stage2_path),
        "option_seed": seed,
        "initial_state_protocol": "S000_ingest_once",
        "stage2_semantics": "historical_state_as_of_target_window",
        "stage2_lag_distribution": dict(
            sorted(Counter(row["metadata"]["retention_lag_windows"] for row in stage2_rows).items())
        ),
    }
    write_json(output_dir / "manifest.json", manifest)
    return {"stage1": stage1_path, "stage2": stage2_path}


def validate_canonical_items(paths: ExperimentPaths) -> dict[str, Any]:
    cfg = load_experiment_config(paths)
    expected = cfg["dataset"]["expected"]
    root = _prepared_root(paths)
    stage1 = list(read_jsonl(root / "canonical_items" / "stage1_event_identification.jsonl"))
    stage2 = list(read_jsonl(root / "canonical_items" / "stage2_historical_memory_mcq.jsonl"))
    errors: list[str] = []
    if len(stage1) != int(expected["stage1_items"]):
        errors.append(f"Stage 1 count {len(stage1)}")
    if len(stage2) != int(expected["stage2_items"]):
        errors.append(f"Stage 2 count {len(stage2)}")
    item_ids = [row["item_id"] for row in stage1 + stage2]
    if len(item_ids) != len(set(item_ids)):
        errors.append("duplicate canonical item_id")
    for row in stage1:
        metadata = row.get("metadata") or {}
        checkpoint = int(metadata.get("query_checkpoint") or 0)
        if len(row.get("visible_sessions") or []) != checkpoint:
            errors.append(f"{row['item_id']}: Stage 1 is not a full-prefix input")
        if row.get("visible_sessions") and session_number(
            str(row["visible_sessions"][-1])
        ) != checkpoint:
            errors.append(f"{row['item_id']}: Stage 1 checkpoint/session mismatch")
    for row in stage2:
        metadata = row.get("metadata") or {}
        if "initial_memory" in metadata:
            errors.append(f"{row['item_id']}: query-time initial_memory leakage")
        if metadata.get("retention_lag_sessions", -1) < 0:
            errors.append(f"{row['item_id']}: negative retention lag")
        options = row.get("options") or []
        if len(options) != 4 or sum(bool(option.get("correct")) for option in options) != 1:
            errors.append(f"{row['item_id']}: invalid A-D option contract")
    report = {
        "decision": "PASS" if not errors else "FAIL",
        "stage1_items": len(stage1),
        "stage2_items": len(stage2),
        "errors": errors,
    }
    if errors:
        raise ValueError("canonical item validation failed:\n- " + "\n- ".join(errors[:30]))
    return report
