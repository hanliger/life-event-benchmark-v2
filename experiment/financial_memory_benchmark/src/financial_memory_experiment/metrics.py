from __future__ import annotations

import csv
import itertools
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from .config import load_experiment_config
from .methods import method_ids as configured_method_ids
from .paths import ExperimentPaths
from .util import read_jsonl, sha256_file, write_json


def _accuracy(rows: list[dict[str, Any]]) -> float | None:
    return mean(bool(row["correct"]) for row in rows) if rows else None


def _target_key(row: dict[str, Any]) -> str:
    metadata = row.get("item_metadata") or {}
    return str(
        metadata.get("canonical_target_id")
        or metadata.get("target_event_instance_id")
        or metadata.get("event_instance_id")
        or row["item_id"]
    )


def hierarchical_stage2(rows: list[dict[str, Any]]) -> tuple[float | None, dict[str, float]]:
    by_trajectory_target: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_trajectory_target[(str(row["trajectory_id"]), _target_key(row))].append(row)
    target_scores = {key: float(_accuracy(group) or 0.0) for key, group in by_trajectory_target.items()}
    by_trajectory: dict[str, list[float]] = defaultdict(list)
    for (trajectory_id, _), score in target_scores.items():
        by_trajectory[trajectory_id].append(score)
    trajectory_scores = {
        trajectory_id: mean(scores) for trajectory_id, scores in by_trajectory.items()
    }
    return (
        mean(trajectory_scores.values()) if trajectory_scores else None,
        trajectory_scores,
    )


def _bootstrap_ci(
    values: dict[str, float], *, samples: int, seed: int
) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    keys = sorted(values)
    rng = random.Random(seed)
    estimates = sorted(
        mean(values[rng.choice(keys)] for _ in keys)
        for _ in range(samples)
    )
    return (
        estimates[int(0.025 * (samples - 1))],
        estimates[int(0.975 * (samples - 1))],
    )


def _trajectory_scores(
    stage: str, rows: list[dict[str, Any]]
) -> dict[str, float]:
    if stage == "stage2_memory_mcq":
        return hierarchical_stage2(rows)[1]
    by_trajectory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_trajectory[str(row["trajectory_id"])].append(row)
    return {
        trajectory_id: float(_accuracy(group) or 0.0)
        for trajectory_id, group in sorted(by_trajectory.items())
    }


def _paired_bootstrap_delta(
    left: dict[str, float],
    right: dict[str, float],
    *,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    if set(left) != set(right):
        raise ValueError("paired comparison requires identical trajectory units")
    keys = sorted(left)
    if not keys:
        return {"delta": None, "ci95": [None, None], "units": 0}
    deltas = {key: left[key] - right[key] for key in keys}
    low, high = _bootstrap_ci(deltas, samples=samples, seed=seed)
    return {
        "delta": mean(deltas.values()),
        "ci95": [low, high],
        "units": len(keys),
    }


def _expected_ids(paths: ExperimentPaths, scope: str) -> set[str]:
    from .data_pipeline import active_prepared_manifest

    root = Path(active_prepared_manifest(paths)["root"])
    files = {
        "canonical": [
            root / "canonical_items" / "stage1_event_identification.jsonl",
            root / "canonical_items" / "stage2_historical_memory_mcq.jsonl",
            root / "canonical_items" / "stage3_multi_hop_mcq.jsonl",
        ],
        "masking": [root / "masking_items" / "masking_questions.jsonl"],
    }
    selected = files["canonical"] + files["masking"] if scope == "all" else files[scope]
    return {str(row["item_id"]) for path in selected for row in read_jsonl(path)}


def _validate_inputs(
    paths: ExperimentPaths,
    prediction_paths: list[Path],
    rows: list[dict[str, Any]],
    *,
    allow_partial: bool,
    expected_scope: str | None,
) -> dict[str, Any]:
    for path in prediction_paths:
        manifest_path = path.with_suffix(".manifest.json")
        if not manifest_path.exists():
            raise ValueError(f"missing run manifest: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "COMPLETE":
            raise ValueError(f"run is not COMPLETE: {manifest_path}")
        if manifest.get("output_sha256") != sha256_file(path):
            raise ValueError(f"prediction hash mismatch: {path}")
        file_rows = list(read_jsonl(path))
        file_ids = sorted(str(row["item_id"]) for row in file_rows)
        if file_ids != sorted(map(str, manifest.get("input_item_ids") or [])):
            raise ValueError(f"manifest item set mismatch: {path}")
        if len(file_ids) != int(manifest.get("completed_items", -1)):
            raise ValueError(f"manifest completed count mismatch: {path}")
        if any(str(row["method_id"]) != str(manifest["method_id"]) for row in file_rows):
            raise ValueError(f"method mismatch between manifest and rows: {path}")

    seen = [(str(row["method_id"]), str(row["item_id"])) for row in rows]
    if len(seen) != len(set(seen)):
        raise ValueError("duplicate method/item predictions")
    methods = sorted({method for method, _ in seen})
    configured = sorted(configured_method_ids(paths))
    if not allow_partial and methods != configured:
        raise ValueError(
            f"report requires all configured methods; expected={configured}, actual={methods}"
        )
    by_method = {
        method: {item_id for row_method, item_id in seen if row_method == method}
        for method in methods
    }
    if by_method and len({frozenset(ids) for ids in by_method.values()}) != 1:
        raise ValueError("methods were evaluated on different item sets")
    actual_ids = next(iter(by_method.values()), set())
    if expected_scope is not None:
        expected = _expected_ids(paths, expected_scope)
        if actual_ids != expected:
            raise ValueError(
                f"{expected_scope} completeness failed: "
                f"missing={len(expected - actual_ids)}, extra={len(actual_ids - expected)}"
            )
    return {
        "methods": methods,
        "identical_item_sets": True,
        "item_count_per_method": len(actual_ids),
        "expected_scope": expected_scope,
        "reporting_ready": (
            not allow_partial
            and expected_scope is not None
            and methods == configured
        ),
    }
    return (
        estimates[int(0.025 * (samples - 1))],
        estimates[int(0.975 * (samples - 1))],
    )


def summarize_predictions(
    paths: ExperimentPaths,
    prediction_paths: Iterable[Path],
    *,
    allow_partial: bool = False,
    expected_scope: str | None = None,
) -> dict[str, Any]:
    cfg = load_experiment_config(paths)
    samples = int(cfg["statistics"]["bootstrap_samples"])
    seed = int(cfg["statistics"]["seed"])
    prediction_paths = list(prediction_paths)
    rows = [row for path in prediction_paths for row in read_jsonl(path)]
    completeness = _validate_inputs(
        paths,
        prediction_paths,
        rows,
        allow_partial=allow_partial,
        expected_scope=expected_scope,
    )
    results: dict[str, Any] = {}
    for method_id in sorted({str(row["method_id"]) for row in rows}):
        method_rows = [row for row in rows if row["method_id"] == method_id]
        stages: dict[str, Any] = {}
        for stage in sorted({str(row["stage"]) for row in method_rows}):
            subset = [row for row in method_rows if row["stage"] == stage]
            if stage == "stage2_memory_mcq":
                score, trajectory_scores = hierarchical_stage2(subset)
                aggregation = "trajectory_target_checkpoint_macro"
            else:
                trajectory_scores = _trajectory_scores(stage, subset)
                score = mean(trajectory_scores.values()) if trajectory_scores else None
                aggregation = "trajectory_macro"
            low, high = _bootstrap_ci(trajectory_scores, samples=samples, seed=seed)
            lag_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in subset:
                metadata = row.get("item_metadata") or {}
                if "retention_lag_windows" in metadata:
                    lag_groups[int(metadata["retention_lag_windows"])].append(row)
            stages[stage] = {
                "items": len(subset),
                "score": score,
                "question_micro_accuracy": _accuracy(subset),
                "ci95": [low, high],
                "aggregation": aggregation,
                "parse_errors": sum(bool(row.get("parse_error")) for row in subset),
                "accuracy_by_retention_lag_windows": {
                    str(lag): _accuracy(group) for lag, group in sorted(lag_groups.items())
                },
            }
            retrieval_rows = [
                row
                for row in subset
                if (row.get("item_metadata") or {}).get("evidence_sessions")
            ]
            if retrieval_rows:
                latest_hits = []
                complete_hits = []
                for row in retrieval_rows:
                    gold_evidence = set(
                        map(str, (row.get("item_metadata") or {})["evidence_sessions"])
                    )
                    retrieved = set(map(str, row.get("evidence_session_ids") or []))
                    latest_hits.append(bool(gold_evidence & retrieved))
                    complete_hits.append(gold_evidence <= retrieved)
                stages[stage]["retrieval"] = {
                    "latest_state_recall_at_k": mean(latest_hits),
                    "complete_evidence_recall_at_k": mean(complete_hits),
                    "items": len(retrieval_rows),
                }
                if stage == "stage3_multi_hop_mcq":
                    stages[stage]["retrieval"]["both_hops_recall_at_k"] = mean(
                        complete_hits
                    )
            if stage.startswith("masking_"):
                arm_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in subset:
                    arm_groups[str((row.get("item_metadata") or {}).get("masking_level"))].append(row)
                stages[stage]["accuracy_by_masking_arm"] = {
                    arm: _accuracy(group) for arm, group in sorted(arm_groups.items())
                }
            if stage == "stage3_multi_hop_mcq":
                derivation_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in subset:
                    derivation_groups[
                        str((row.get("item_metadata") or {}).get("derivation_type"))
                    ].append(row)
                stages[stage]["accuracy_by_derivation_type"] = {
                    derivation: _accuracy(group)
                    for derivation, group in sorted(derivation_groups.items())
                }
        results[method_id] = stages
    paired: dict[str, Any] = {}
    methods = sorted(results)
    stages = sorted({stage for method in results.values() for stage in method})
    for stage in stages:
        stage_rows = [row for row in rows if str(row["stage"]) == stage]
        stage_scores = {
            method: _trajectory_scores(
                stage,
                [row for row in stage_rows if str(row["method_id"]) == method],
            )
            for method in methods
        }
        paired[stage] = {
            f"{left}__minus__{right}": _paired_bootstrap_delta(
                stage_scores[left],
                stage_scores[right],
                samples=samples,
                seed=seed,
            )
            for left, right in itertools.combinations(methods, 2)
        }
    return {
        "schema_version": "financial-memory-metrics-v1",
        "bootstrap": {"samples": samples, "seed": seed, "unit": "trajectory"},
        "completeness": completeness,
        "methods": results,
        "paired_method_deltas": paired,
    }


def write_tables(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    masking_rows: list[dict[str, Any]] = []
    stage3_rows: list[dict[str, Any]] = []
    lag_rows: list[dict[str, Any]] = []
    for method_id, stages in report["methods"].items():
        family = (
            "Full Context"
            if method_id.startswith("fc_")
            else "Retrieval"
            if method_id.startswith(("bm25_", "dense_"))
            else "Memory"
        )
        for stage, values in stages.items():
            rows.append(
                {
                    "method_family": family,
                    "method_id": method_id,
                    "stage": stage,
                    "items": values["items"],
                    "score": values["score"],
                    "ci95_low": values["ci95"][0],
                    "ci95_high": values["ci95"][1],
                    "parse_errors": values["parse_errors"],
                    "aggregation": values["aggregation"],
                }
            )
            for arm, accuracy in (values.get("accuracy_by_masking_arm") or {}).items():
                masking_rows.append(
                    {
                        "method_family": family,
                        "method_id": method_id,
                        "stage": stage,
                        "masking_arm": arm,
                        "accuracy": accuracy,
                    }
                )
            for derivation, accuracy in (
                values.get("accuracy_by_derivation_type") or {}
            ).items():
                stage3_rows.append(
                    {
                        "method_family": family,
                        "method_id": method_id,
                        "derivation_type": derivation,
                        "accuracy": accuracy,
                    }
                )
            for lag, accuracy in (
                values.get("accuracy_by_retention_lag_windows") or {}
            ).items():
                lag_rows.append(
                    {
                        "method_family": family,
                        "method_id": method_id,
                        "stage": stage,
                        "retention_lag_windows": lag,
                        "accuracy": accuracy,
                    }
                )
    with (output_dir / "main_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["method_id"])
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "| Family | Method | Stage | Score | 95% CI | N | Aggregation |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        score = "—" if row["score"] is None else f"{100 * row['score']:.2f}"
        ci = (
            "—"
            if row["ci95_low"] is None
            else f"[{100 * row['ci95_low']:.2f}, {100 * row['ci95_high']:.2f}]"
        )
        lines.append(
            f"| {row['method_family']} | {row['method_id']} | {row['stage']} | {score} | {ci} | "
            f"{row['items']} | {row['aggregation']} |"
        )
    (output_dir / "main_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for filename, table_rows in (
        ("masking_by_arm.csv", masking_rows),
        ("stage3_by_derivation.csv", stage3_rows),
        ("retention_lag.csv", lag_rows),
    ):
        with (output_dir / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(table_rows[0]) if table_rows else ["method_id"],
            )
            writer.writeheader()
            writer.writerows(table_rows)
    paired_rows = [
        {
            "stage": stage,
            "comparison": comparison,
            "delta": values["delta"],
            "ci95_low": values["ci95"][0],
            "ci95_high": values["ci95"][1],
            "trajectory_units": values["units"],
        }
        for stage, comparisons in report.get("paired_method_deltas", {}).items()
        for comparison, values in comparisons.items()
    ]
    with (output_dir / "paired_method_deltas.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(paired_rows[0]) if paired_rows else ["comparison"],
        )
        writer.writeheader()
        writer.writerows(paired_rows)
    write_json(output_dir / "metrics.json", report)
