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
from .stage2_2 import STAGE2_2


_STAGE2_2_SCALAR_METRICS = (
    "final_state_accuracy",
    "dynamic_path_final_state_accuracy",
    "value_accuracy",
    "status_accuracy",
    "changed_state_accuracy",
    "unchanged_state_accuracy",
    "exact_state_match",
    "change_detection_precision",
    "change_detection_recall",
    "change_detection_f1",
    "correct_change_precision",
    "correct_change_recall",
    "correct_change_f1",
    "evidence_hit_rate",
    "evidence_citation_precision",
)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    return (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )


def _correct_change_metrics(counts: dict[str, int]) -> dict[str, float | None]:
    predicted_change = (
        counts["tp_correct"] + counts["tp_wrong_value"] + counts["fp"]
    )
    gold_change = counts["tp_correct"] + counts["tp_wrong_value"] + counts["fn"]
    precision = (
        _ratio(counts["tp_correct"], predicted_change)
        if predicted_change
        else 0.0
    )
    recall = _ratio(counts["tp_correct"], gold_change)
    return {
        "correct_change_precision": precision,
        "correct_change_recall": recall,
        "correct_change_f1": _f1(precision, recall),
    }


def _stage2_2_path_macro(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts_by_path: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "tn": 0,
            "fp": 0,
            "fn": 0,
            "tp_correct": 0,
            "tp_wrong_value": 0,
        }
    )
    changed_trajectories: dict[str, set[str]] = defaultdict(set)
    update_events: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        trajectory_id = str(row["trajectory_id"])
        outcomes = (row.get("metrics") or {}).get("path_outcomes") or {}
        for path, outcome in outcomes.items():
            classification = str(outcome["classification"])
            counts_by_path[str(path)][classification] += 1
            if outcome.get("gold_changed"):
                changed_trajectories[str(path)].add(trajectory_id)
                for event_id in outcome.get("gold_event_session_ids") or []:
                    update_events[str(path)].add(
                        (trajectory_id, str(event_id))
                    )

    path_metrics: dict[str, dict[str, Any]] = {}
    eligible_f1: list[float] = []
    for path, counts in sorted(counts_by_path.items()):
        metrics = _correct_change_metrics(counts)
        changed_items = (
            counts["tp_correct"] + counts["tp_wrong_value"] + counts["fn"]
        )
        eligible = changed_items > 0
        if eligible and metrics["correct_change_f1"] is not None:
            eligible_f1.append(float(metrics["correct_change_f1"]))
        path_metrics[path] = {
            "eligible": eligible,
            "changed_items": changed_items,
            "changed_trajectories": len(changed_trajectories[path]),
            "update_events": len(update_events[path]),
            "change_confusion": counts,
            **metrics,
        }
    return {
        "correct_change_f1": mean(eligible_f1) if eligible_f1 else None,
        "eligible_path_count": len(eligible_f1),
        "reported_path_count": len(path_metrics),
        "path_metrics": path_metrics,
    }


def _event_number(event_id: str) -> int:
    if not event_id.startswith("D") or not event_id[1:].isdigit():
        raise ValueError(f"invalid Stage 2.2 update-event session ID: {event_id}")
    return int(event_id[1:])


def _retention_lag_bucket(lag: int) -> str:
    if lag < 0:
        raise ValueError(f"negative retention lag: {lag}")
    if lag == 0:
        return "0"
    for upper in (15, 30, 60, 120, 180, 240):
        lower = 1 if upper == 15 else {
            30: 16,
            60: 31,
            120: 61,
            180: 121,
            240: 181,
        }[upper]
        if lag <= upper:
            return f"{lower}-{upper}"
    return "241+"


def _stage2_2_event_and_retention(rows: list[dict[str, Any]]) -> dict[str, Any]:
    observations: dict[
        tuple[str, str], dict[int, dict[str, bool]]
    ] = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        trajectory_id = str(row["trajectory_id"])
        checkpoint = int(row["query_checkpoint"])
        outcomes = (row.get("metrics") or {}).get("path_outcomes") or {}
        for path, outcome in outcomes.items():
            if not outcome.get("gold_changed"):
                continue
            event_ids = list(outcome.get("gold_event_session_ids") or [])
            if len(event_ids) != 1:
                raise ValueError(
                    f"{row['item_id']}/{path}: expected exactly one Gold "
                    "update-event session"
                )
            event_id = str(event_ids[0])
            if _event_number(event_id) > checkpoint:
                raise ValueError(
                    f"{row['item_id']}/{path}: future update event {event_id}"
                )
            observations[(trajectory_id, event_id)][checkpoint][str(path)] = bool(
                outcome.get("cell_correct")
            )

    update_scores_by_trajectory: dict[str, list[float]] = defaultdict(list)
    update_exact_by_trajectory: dict[str, list[float]] = defaultdict(list)
    first_lags: list[int] = []
    lag_event_scores: dict[
        int, dict[str, list[float]]
    ] = defaultdict(lambda: defaultdict(list))
    bucket_event_scores: dict[
        str, dict[str, dict[str, list[float]]]
    ] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    retention_by_trajectory: dict[str, list[float]] = defaultdict(list)

    for (trajectory_id, event_id), checkpoints in sorted(observations.items()):
        first_checkpoint = min(checkpoints)
        first_values = list(checkpoints[first_checkpoint].values())
        update_scores_by_trajectory[trajectory_id].append(mean(first_values))
        update_exact_by_trajectory[trajectory_id].append(
            float(all(first_values))
        )
        first_lags.append(first_checkpoint - _event_number(event_id))

        event_retention_scores: list[float] = []
        for checkpoint, outcomes in sorted(checkpoints.items()):
            score = mean(outcomes.values())
            lag = checkpoint - _event_number(event_id)
            lag_event_scores[lag][trajectory_id].append(score)
            bucket_event_scores[_retention_lag_bucket(lag)][trajectory_id][
                event_id
            ].append(score)
            event_retention_scores.append(score)
        retention_by_trajectory[trajectory_id].append(
            mean(event_retention_scores)
        )

    trajectory_update = {
        trajectory_id: mean(scores)
        for trajectory_id, scores in sorted(update_scores_by_trajectory.items())
    }
    trajectory_exact = {
        trajectory_id: mean(scores)
        for trajectory_id, scores in sorted(update_exact_by_trajectory.items())
    }
    trajectory_retention = {
        trajectory_id: mean(scores)
        for trajectory_id, scores in sorted(retention_by_trajectory.items())
    }
    by_lag = {
        str(lag): mean(
            mean(scores) for scores in trajectory_scores.values()
        )
        for lag, trajectory_scores in sorted(lag_event_scores.items())
    }
    by_lag_support = {
        str(lag): {
            "events": sum(len(scores) for scores in trajectory_scores.values()),
            "trajectories": len(trajectory_scores),
        }
        for lag, trajectory_scores in sorted(lag_event_scores.items())
    }
    bucket_order = {
        label: index
        for index, label in enumerate(
            ("0", "1-15", "16-30", "31-60", "61-120", "121-180", "181-240", "241+")
        )
    }
    by_bucket = {
        bucket: mean(
            mean(
                mean(event_scores)
                for event_scores in events.values()
            )
            for events in trajectories.values()
        )
        for bucket, trajectories in sorted(
            bucket_event_scores.items(),
            key=lambda item: bucket_order[item[0]],
        )
    }
    by_bucket_support = {
        bucket: {
            "events": sum(len(events) for events in trajectories.values()),
            "trajectories": len(trajectories),
        }
        for bucket, trajectories in sorted(
            bucket_event_scores.items(),
            key=lambda item: bucket_order[item[0]],
        )
    }
    return {
        "event_macro": {
            "aggregation": "event_then_trajectory_macro",
            "update_accuracy": (
                mean(trajectory_update.values()) if trajectory_update else None
            ),
            "exact_update_accuracy": (
                mean(trajectory_exact.values()) if trajectory_exact else None
            ),
            "event_count": len(observations),
            "trajectory_count": len(trajectory_update),
            "mean_first_evaluation_lag_sessions": (
                mean(first_lags) if first_lags else None
            ),
            "trajectory_update_accuracy": trajectory_update,
            "trajectory_exact_update_accuracy": trajectory_exact,
        },
        "retention_after_update": {
            "aggregation": "event_lag_then_trajectory_macro",
            "mean_over_observed_lags": (
                mean(trajectory_retention.values())
                if trajectory_retention
                else None
            ),
            "event_count": len(observations),
            "by_lag_sessions": by_lag,
            "support_by_lag_sessions": by_lag_support,
            "by_lag_bucket": by_bucket,
            "support_by_lag_bucket": by_bucket_support,
            "trajectory_mean_over_observed_lags": trajectory_retention,
        },
    }


def summarize_stage2_2_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    required_metric_fields = {
        "dynamic_path_final_state_accuracy",
        "path_outcomes",
    }
    for row in rows:
        missing = required_metric_fields - set(row.get("metrics") or {})
        if missing:
            raise ValueError(
                f"{row.get('item_id')}: Stage 2.2 metrics-v2 fields missing; "
                f"rerun prediction under the frozen protocol: {sorted(missing)}"
            )
    by_trajectory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_trajectory[str(row["trajectory_id"])].append(row)
    trajectory_metrics: dict[str, dict[str, float | None]] = {}
    for trajectory_id, group in sorted(by_trajectory.items()):
        trajectory_metrics[trajectory_id] = {}
        for metric in _STAGE2_2_SCALAR_METRICS:
            values = [
                float(row["metrics"][metric])
                for row in group
                if row.get("metrics", {}).get(metric) is not None
            ]
            trajectory_metrics[trajectory_id][metric] = (
                mean(values) if values else None
            )
    aggregate = {
        metric: (
            mean(
                value
                for values in trajectory_metrics.values()
                if (value := values[metric]) is not None
            )
            if any(values[metric] is not None for values in trajectory_metrics.values())
            else None
        )
        for metric in _STAGE2_2_SCALAR_METRICS
    }
    change_confusion: dict[str, int] = defaultdict(int)
    status_confusion: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for row in rows:
        for key, value in row["metrics"]["change_confusion"].items():
            change_confusion[key] += int(value)
        for gold, predictions in row["metrics"]["status_confusion"].items():
            for predicted, value in predictions.items():
                status_confusion[gold][predicted] += int(value)
    path_macro = _stage2_2_path_macro(rows)
    event_and_retention = _stage2_2_event_and_retention(rows)
    return {
        "items": len(rows),
        "aggregation": "checkpoint_then_trajectory_macro",
        "metrics": aggregate,
        "trajectory_metrics": trajectory_metrics,
        "change_confusion": dict(change_confusion),
        "status_confusion": {
            gold: dict(predictions)
            for gold, predictions in status_confusion.items()
        },
        "path_macro": path_macro,
        **event_and_retention,
        "parse_errors": sum(bool(row.get("parse_error")) for row in rows),
        "validation_error_count": sum(
            len(row.get("validation_errors") or []) for row in rows
        ),
    }


def _accuracy(rows: list[dict[str, Any]]) -> float | None:
    return mean(bool(row["correct"]) for row in rows) if rows else None


def _target_key(row: dict[str, Any]) -> str:
    metadata = row.get("item_metadata") or {}
    return str(
        metadata.get("canonical_target_id")
        or metadata.get("target_event_instance_id")
        or metadata.get("event_instance_id")
        or "::".join(
            (
                str(metadata.get("introduced_by_event_instance_id") or ""),
                str(metadata.get("memory_path") or ""),
                str(metadata.get("value_selector") or ""),
            )
        ).strip(":")
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
    if stage == "stage2_memory_value":
        return hierarchical_stage2(rows)[1]
    if stage == STAGE2_2:
        by_trajectory: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            by_trajectory[str(row["trajectory_id"])].append(
                float(row["metrics"]["final_state_accuracy"])
            )
        return {
            trajectory_id: mean(scores)
            for trajectory_id, scores in sorted(by_trajectory.items())
        }
    by_trajectory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_trajectory[str(row["trajectory_id"])].append(row)
    return {
        trajectory_id: float(_accuracy(group) or 0.0)
        for trajectory_id, group in sorted(by_trajectory.items())
    }


def _stage2_2_metric_trajectory_scores(
    rows: list[dict[str, Any]], metric: str
) -> dict[str, float]:
    by_trajectory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_trajectory[str(row["trajectory_id"])].append(row)
    scores: dict[str, float] = {}
    for trajectory_id, group in sorted(by_trajectory.items()):
        summary = summarize_stage2_2_rows(group)
        if metric in _STAGE2_2_SCALAR_METRICS:
            value = summary["metrics"][metric]
        elif metric == "path_macro_correct_change_f1":
            value = summary["path_macro"]["correct_change_f1"]
        elif metric == "event_macro_update_accuracy":
            value = summary["event_macro"]["update_accuracy"]
        elif metric == "retention_mean_over_observed_lags":
            value = summary["retention_after_update"][
                "mean_over_observed_lags"
            ]
        else:
            raise ValueError(f"unsupported Stage 2.2 comparison metric: {metric}")
        if value is not None:
            scores[trajectory_id] = float(value)
    return scores


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
            root / "canonical_items" / "stage2_memory_value.jsonl",
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
            if stage == STAGE2_2:
                stage2_2 = summarize_stage2_2_rows(subset)
                trajectory_scores = {
                    trajectory_id: float(
                        values["final_state_accuracy"] or 0.0
                    )
                    for trajectory_id, values in stage2_2[
                        "trajectory_metrics"
                    ].items()
                }
                score = stage2_2["metrics"]["final_state_accuracy"]
                aggregation = stage2_2["aggregation"]
            elif stage == "stage2_memory_value":
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
            if stage == STAGE2_2:
                stages[stage]["state_reconstruction"] = stage2_2
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
                    derivation: (
                        mean(_trajectory_scores(stage, group).values())
                        if group
                        else None
                    )
                    for derivation, group in sorted(derivation_groups.items())
                }
                stages[stage]["question_micro_accuracy_by_derivation_type"] = {
                    derivation: _accuracy(group)
                    for derivation, group in sorted(derivation_groups.items())
                }
        results[method_id] = stages
    paired: dict[str, Any] = {}
    oracle_relevant_comparisons: dict[str, Any] = {}
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
    oracle_pairs = (
        ("oracle_rel_gpt_5_6_sol", "fc_gpt_5_6_sol"),
    )
    comparison_metrics = (
        "final_state_accuracy",
        "dynamic_path_final_state_accuracy",
        "correct_change_f1",
        "path_macro_correct_change_f1",
        "event_macro_update_accuracy",
        "retention_mean_over_observed_lags",
    )
    for oracle_method, full_method in oracle_pairs:
        if oracle_method not in methods or full_method not in methods:
            continue
        stage_rows = [
            row for row in rows if str(row["stage"]) == STAGE2_2
        ]
        oracle_rows = [
            row
            for row in stage_rows
            if str(row["method_id"]) == oracle_method
        ]
        full_rows = [
            row
            for row in stage_rows
            if str(row["method_id"]) == full_method
        ]
        if not oracle_rows or not full_rows:
            continue
        comparison_id = f"{oracle_method}__minus__{full_method}"
        oracle_relevant_comparisons[comparison_id] = {
            "direction": "oracle_relevant_minus_full_context",
            "metrics": {
                metric: _paired_bootstrap_delta(
                    _stage2_2_metric_trajectory_scores(
                        oracle_rows, metric
                    ),
                    _stage2_2_metric_trajectory_scores(
                        full_rows, metric
                    ),
                    samples=samples,
                    seed=seed,
                )
                for metric in comparison_metrics
            },
        }
    return {
        "schema_version": "financial-memory-metrics-v2",
        "bootstrap": {"samples": samples, "seed": seed, "unit": "trajectory"},
        "completeness": completeness,
        "methods": results,
        "paired_method_deltas": paired,
        "oracle_relevant_comparisons": oracle_relevant_comparisons,
    }


def write_tables(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    masking_rows: list[dict[str, Any]] = []
    stage3_rows: list[dict[str, Any]] = []
    lag_rows: list[dict[str, Any]] = []
    stage2_2_path_rows: list[dict[str, Any]] = []
    stage2_2_event_rows: list[dict[str, Any]] = []
    stage2_2_retention_rows: list[dict[str, Any]] = []
    for method_id, stages in report["methods"].items():
        family = (
            "Oracle Relevant"
            if method_id.startswith("oracle_rel_")
            else "Full Context"
            if method_id.startswith("fc_")
            else "Retrieval"
            if method_id.startswith(("bm25_", "dense_"))
            else "Memory"
        )
        for stage, values in stages.items():
            state_reconstruction = values.get("state_reconstruction") or {}
            state_metrics = state_reconstruction.get("metrics") or {}
            path_macro = state_reconstruction.get("path_macro") or {}
            event_macro = state_reconstruction.get("event_macro") or {}
            retention = (
                state_reconstruction.get("retention_after_update") or {}
            )
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
                    "dynamic_path_final_state_accuracy": state_metrics.get(
                        "dynamic_path_final_state_accuracy"
                    ),
                    "correct_change_f1": state_metrics.get(
                        "correct_change_f1"
                    ),
                    "path_macro_correct_change_f1": path_macro.get(
                        "correct_change_f1"
                    ),
                    "event_macro_update_accuracy": event_macro.get(
                        "update_accuracy"
                    ),
                    "event_exact_update_accuracy": event_macro.get(
                        "exact_update_accuracy"
                    ),
                    "retention_mean_over_observed_lags": retention.get(
                        "mean_over_observed_lags"
                    ),
                }
            )
            for path, path_values in (
                path_macro.get("path_metrics") or {}
            ).items():
                stage2_2_path_rows.append(
                    {
                        "method_id": method_id,
                        "path": path,
                        **{
                            key: value
                            for key, value in path_values.items()
                            if key != "change_confusion"
                        },
                        **{
                            f"confusion_{key}": value
                            for key, value in (
                                path_values.get("change_confusion") or {}
                            ).items()
                        },
                    }
                )
            if event_macro:
                stage2_2_event_rows.append(
                    {
                        "method_id": method_id,
                        "event_count": event_macro.get("event_count"),
                        "trajectory_count": event_macro.get(
                            "trajectory_count"
                        ),
                        "update_accuracy": event_macro.get(
                            "update_accuracy"
                        ),
                        "exact_update_accuracy": event_macro.get(
                            "exact_update_accuracy"
                        ),
                        "mean_first_evaluation_lag_sessions": event_macro.get(
                            "mean_first_evaluation_lag_sessions"
                        ),
                        "aggregation": event_macro.get("aggregation"),
                    }
                )
            for lag, accuracy in (
                retention.get("by_lag_sessions") or {}
            ).items():
                support = (
                    retention.get("support_by_lag_sessions") or {}
                ).get(lag, {})
                stage2_2_retention_rows.append(
                    {
                        "method_id": method_id,
                        "lag_type": "exact_sessions",
                        "lag": lag,
                        "accuracy": accuracy,
                        "events": support.get("events"),
                        "trajectories": support.get("trajectories"),
                        "aggregation": retention.get("aggregation"),
                    }
                )
            for bucket, accuracy in (
                retention.get("by_lag_bucket") or {}
            ).items():
                support = (
                    retention.get("support_by_lag_bucket") or {}
                ).get(bucket, {})
                stage2_2_retention_rows.append(
                    {
                        "method_id": method_id,
                        "lag_type": "session_bucket",
                        "lag": bucket,
                        "accuracy": accuracy,
                        "events": support.get("events"),
                        "trajectories": support.get("trajectories"),
                        "aggregation": retention.get("aggregation"),
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
                        "trajectory_macro_accuracy": accuracy,
                        "question_micro_accuracy": (
                            values.get(
                                "question_micro_accuracy_by_derivation_type"
                            )
                            or {}
                        ).get(derivation),
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
        "| Family | Method | Stage | Score | Dynamic Final | "
        "Correct-change F1 | Path-macro F1 | Event Update | Retention | "
        "95% CI | N | Aggregation |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        score = "—" if row["score"] is None else f"{100 * row['score']:.2f}"
        stage2_values = [
            (
                "—"
                if row[key] is None
                else f"{100 * float(row[key]):.2f}"
            )
            for key in (
                "dynamic_path_final_state_accuracy",
                "correct_change_f1",
                "path_macro_correct_change_f1",
                "event_macro_update_accuracy",
                "retention_mean_over_observed_lags",
            )
        ]
        ci = (
            "—"
            if row["ci95_low"] is None
            else f"[{100 * row['ci95_low']:.2f}, {100 * row['ci95_high']:.2f}]"
        )
        lines.append(
            f"| {row['method_family']} | {row['method_id']} | "
            f"{row['stage']} | {score} | {' | '.join(stage2_values)} | "
            f"{ci} | {row['items']} | {row['aggregation']} |"
        )
    (output_dir / "main_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for filename, table_rows in (
        ("masking_by_arm.csv", masking_rows),
        ("stage3_by_derivation.csv", stage3_rows),
        ("retention_lag.csv", lag_rows),
        ("stage2_2_path_metrics.csv", stage2_2_path_rows),
        ("stage2_2_event_metrics.csv", stage2_2_event_rows),
        ("stage2_2_retention_after_update.csv", stage2_2_retention_rows),
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
    oracle_rows = [
        {
            "comparison": comparison,
            "metric": metric,
            "delta": values["delta"],
            "ci95_low": values["ci95"][0],
            "ci95_high": values["ci95"][1],
            "trajectory_units": values["units"],
        }
        for comparison, comparison_values in report.get(
            "oracle_relevant_comparisons", {}
        ).items()
        for metric, values in comparison_values.get("metrics", {}).items()
    ]
    with (output_dir / "oracle_relevant_deltas.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                list(oracle_rows[0]) if oracle_rows else ["comparison"]
            ),
        )
        writer.writeheader()
        writer.writerows(oracle_rows)
    write_json(output_dir / "metrics.json", report)
