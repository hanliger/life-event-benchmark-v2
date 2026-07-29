"""Metrics for the RQ1 event-trajectory task.

Definitions (all deterministic):

- ``ordered_occurred_event_f1`` (primary): LCS precision/recall/F1 over the
  ``event_id`` sequences of occurred instances, gold ordered by occurred
  anchor, predictions ordered by predicted anchor. Both sequences empty
  scores 1.0; exactly one empty scores 0.0.
- ``full_ledger_event_*``: precision/recall/F1 of alignment-matched
  instance pairs over the full observed ledger.
- ``status_macro_f1``: macro-F1 over {weak_signal, upcoming, occurred,
  cancelled, no_event} on the aligned union (unmatched gold -> predicted
  ``no_event``; unmatched prediction -> gold ``no_event``). Classes with no
  support on either side are skipped.
- ``core_evidence_*``: per matched pair, set precision/recall/F1 between
  predicted and gold core evidence sessions, averaged over matched pairs.
  ``core_evidence_f1_end_to_end`` averages per-gold-instance F1 with
  unmatched gold instances scored 0.
- ``supporting_evidence_f1``: same pairwise F1 over supporting sessions
  (both-empty pairs score 1.0).
- anchor metrics: over matched pairs, in session units.
- ``event_count_mae``: | #predicted - #gold | per item.
- ``exact_occurred_trajectory_match``: 1.0 iff occurred sequences equal.
- ``normalized_sequence_edit_distance``: Levenshtein distance between the
  occurred ``event_id`` sequences divided by the longer length (0.0 when
  both are empty).
- confidence metrics: a prediction is "correct" iff matched with correct
  status. Predictions without a usable confidence are excluded from
  confidence/calibration metrics only.

Aggregation contract: metrics are computed per item (= one trajectory at
one checkpoint), macro-averaged across trajectories per checkpoint, then a
context-length AUC equally weights the checkpoints. Event instances from
different checkpoints are never pooled into one headline micro score.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any, Sequence

from .rq1_alignment import AlignmentResult, align_events, order_predictions
from .rq1_models import (
    NO_EVENT,
    RQ1GoldEventInstance,
    RQ1PredictedEvent,
    STATUS_CLASSES,
    session_number,
)

# ---------------------------------------------------------------------------
# sequence helpers


def lcs_length(a: Sequence[str], b: Sequence[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            cur[j] = prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


def levenshtein(a: Sequence[str], b: Sequence[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, start=1):
            cur[j] = min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + (0 if x == y else 1),
            )
        prev = cur
    return prev[-1]


def _prf(matched: int, n_pred: int, n_gold: int) -> tuple[float, float, float]:
    if n_pred == 0 and n_gold == 0:
        return 1.0, 1.0, 1.0
    precision = matched / n_pred if n_pred else 0.0
    recall = matched / n_gold if n_gold else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return precision, recall, f1


def _set_prf(pred: set[str], gold: set[str]) -> tuple[float, float, float]:
    return _prf(len(pred & gold), len(pred), len(gold))


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


# ---------------------------------------------------------------------------
# per-item metrics


def occurred_sequence(events: list[RQ1GoldEventInstance]) -> list[str]:
    return [e.event_id for e in events]


def predicted_occurred_sequence(
    predicted: list[RQ1PredictedEvent],
) -> list[str]:
    occ = [
        (session_number(p.status_anchor_session), i, p.event_id)
        for i, p in enumerate(predicted)
        if p.status == "occurred"
    ]
    occ.sort()
    return [event_id for _, _, event_id in occ]


def item_metrics(
    gold_ledger: list[RQ1GoldEventInstance],
    occurred_gold: list[RQ1GoldEventInstance],
    predicted: list[RQ1PredictedEvent],
    *,
    alignment: AlignmentResult | None = None,
) -> dict[str, Any]:
    """All natural metrics for one item, plus per-instance records."""

    if alignment is None:
        alignment = align_events(gold_ledger, predicted)

    metrics: dict[str, Any] = {}

    # 1. ordered occurred event F1 (LCS)
    gold_seq = occurred_sequence(occurred_gold)
    pred_seq = predicted_occurred_sequence(predicted)
    lcs = lcs_length(gold_seq, pred_seq)
    p, r, f1 = _prf(lcs, len(pred_seq), len(gold_seq))
    metrics["ordered_occurred_event_precision"] = p
    metrics["ordered_occurred_event_recall"] = r
    metrics["ordered_occurred_event_f1"] = f1

    # 2-4. full ledger P/R/F1 via alignment
    p, r, f1 = _prf(alignment.matched_count, len(predicted), len(gold_ledger))
    metrics["full_ledger_event_precision"] = p
    metrics["full_ledger_event_recall"] = r
    metrics["full_ledger_event_f1"] = f1

    # 5. status macro F1 over aligned union
    status_pairs: list[tuple[str, str]] = [
        (pair.gold_status, pair.pred_status) for pair in alignment.pairs
    ]
    status_pairs += [
        (gold_ledger[i].event_status, NO_EVENT) for i in alignment.unmatched_gold
    ]
    status_pairs += [
        (NO_EVENT, predicted[i].status) for i in alignment.unmatched_pred
    ]
    per_class: list[float] = []
    status_by_class: dict[str, float | None] = {}
    for cls in STATUS_CLASSES:
        tp = sum(1 for g, q in status_pairs if g == cls and q == cls)
        fp = sum(1 for g, q in status_pairs if g != cls and q == cls)
        fn = sum(1 for g, q in status_pairs if g == cls and q != cls)
        if tp + fp + fn == 0:
            status_by_class[cls] = None
            continue
        f1_cls = 2 * tp / (2 * tp + fp + fn)
        status_by_class[cls] = f1_cls
        per_class.append(f1_cls)
    metrics["status_macro_f1"] = _mean(per_class)
    metrics["status_f1_by_class"] = status_by_class

    # 6-10. evidence metrics
    core_p: list[float] = []
    core_r: list[float] = []
    core_f: list[float] = []
    supp_f: list[float] = []
    evidence_f1_by_gold: dict[int, float] = {}
    for pair in alignment.pairs:
        gold = gold_ledger[pair.gold_index]
        pred = predicted[pair.pred_index]
        cp, cr, cf = _set_prf(
            set(pred.core_evidence_sessions), set(gold.core_evidence_sessions)
        )
        core_p.append(cp)
        core_r.append(cr)
        core_f.append(cf)
        evidence_f1_by_gold[pair.gold_index] = cf
        _, _, sf = _set_prf(
            set(pred.supporting_sessions), set(gold.supporting_sessions)
        )
        supp_f.append(sf)
    metrics["core_evidence_precision"] = _mean(core_p)
    metrics["core_evidence_recall"] = _mean(core_r)
    metrics["core_evidence_f1"] = _mean(core_f)
    if gold_ledger:
        metrics["core_evidence_f1_end_to_end"] = sum(
            evidence_f1_by_gold.get(i, 0.0) for i in range(len(gold_ledger))
        ) / len(gold_ledger)
    else:
        metrics["core_evidence_f1_end_to_end"] = None
    metrics["supporting_evidence_f1"] = _mean(supp_f)

    # 11-15. anchor metrics
    distances = [pair.anchor_distance for pair in alignment.pairs]
    if distances:
        metrics["anchor_exact_accuracy"] = sum(
            1 for d in distances if d == 0
        ) / len(distances)
        metrics["anchor_mean_absolute_error"] = sum(distances) / len(distances)
        metrics["anchor_median_absolute_error"] = float(
            statistics.median(distances)
        )
        metrics["anchor_within_1_session"] = sum(
            1 for d in distances if d <= 1
        ) / len(distances)
        metrics["anchor_within_3_sessions"] = sum(
            1 for d in distances if d <= 3
        ) / len(distances)
    else:
        for key in (
            "anchor_exact_accuracy",
            "anchor_mean_absolute_error",
            "anchor_median_absolute_error",
            "anchor_within_1_session",
            "anchor_within_3_sessions",
        ):
            metrics[key] = None

    # 16-18. counts and sequences
    metrics["event_count_mae"] = float(abs(len(predicted) - len(gold_ledger)))
    metrics["exact_occurred_trajectory_match"] = float(gold_seq == pred_seq)
    longer = max(len(gold_seq), len(pred_seq))
    metrics["normalized_sequence_edit_distance"] = (
        levenshtein(gold_seq, pred_seq) / longer if longer else 0.0
    )

    # 19-21. confidence / calibration
    correct_pred_indices = {
        pair.pred_index for pair in alignment.pairs if pair.status_correct
    }
    conf_outcomes: list[tuple[float, int]] = []
    for i, pred in enumerate(predicted):
        conf = pred.confidence
        if conf is None or not (0.0 <= conf <= 1.0):
            continue
        conf_outcomes.append((conf, 1 if i in correct_pred_indices else 0))
    metrics["mean_confidence_correct"] = _mean(
        [c for c, o in conf_outcomes if o == 1]
    )
    metrics["mean_confidence_incorrect"] = _mean(
        [c for c, o in conf_outcomes if o == 0]
    )
    metrics["brier_score"] = (
        _mean([(c - o) ** 2 for c, o in conf_outcomes]) if conf_outcomes else None
    )

    # bookkeeping for aggregation / progressive metrics
    matched_by_gold = {pair.gold_index: pair for pair in alignment.pairs}
    metrics["instance_records"] = [
        {
            "event_instance_id": gold.event_instance_id,
            "event_id": gold.event_id,
            "gold_status": gold.event_status,
            "matched": idx in matched_by_gold,
            "status_correct": (
                matched_by_gold[idx].status_correct if idx in matched_by_gold else False
            ),
            "pred_status": (
                matched_by_gold[idx].pred_status if idx in matched_by_gold else NO_EVENT
            ),
            "anchor_distance": (
                matched_by_gold[idx].anchor_distance if idx in matched_by_gold else None
            ),
            "evidence_f1": evidence_f1_by_gold.get(idx),
        }
        for idx, gold in enumerate(gold_ledger)
    ]
    metrics["unmatched_pred_records"] = [
        {
            "event_id": predicted[i].event_id,
            "status": predicted[i].status,
            "anchor_session": predicted[i].status_anchor_session,
            "confidence": predicted[i].confidence,
        }
        for i in alignment.unmatched_pred
    ]
    metrics["n_gold_events"] = len(gold_ledger)
    metrics["n_predicted_events"] = len(predicted)
    metrics["n_matched_events"] = alignment.matched_count
    metrics["confidence_outcomes"] = conf_outcomes
    return metrics


# scalar metrics that aggregate by plain averaging
SCALAR_METRICS = (
    "ordered_occurred_event_precision",
    "ordered_occurred_event_recall",
    "ordered_occurred_event_f1",
    "full_ledger_event_precision",
    "full_ledger_event_recall",
    "full_ledger_event_f1",
    "status_macro_f1",
    "core_evidence_precision",
    "core_evidence_recall",
    "core_evidence_f1",
    "core_evidence_f1_end_to_end",
    "supporting_evidence_f1",
    "anchor_exact_accuracy",
    "anchor_mean_absolute_error",
    "anchor_median_absolute_error",
    "anchor_within_1_session",
    "anchor_within_3_sessions",
    "event_count_mae",
    "exact_occurred_trajectory_match",
    "normalized_sequence_edit_distance",
    "mean_confidence_correct",
    "mean_confidence_incorrect",
    "brier_score",
)


def expected_calibration_error(
    conf_outcomes: list[tuple[float, int]], *, bins: int = 10, min_samples: int = 10
) -> float | None:
    if len(conf_outcomes) < min_samples:
        return None
    total = len(conf_outcomes)
    ece = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [
            (c, o)
            for c, o in conf_outcomes
            if (lo <= c < hi) or (b == bins - 1 and c == 1.0)
        ]
        if not bucket:
            continue
        avg_conf = sum(c for c, _ in bucket) / len(bucket)
        avg_acc = sum(o for _, o in bucket) / len(bucket)
        ece += (len(bucket) / total) * abs(avg_conf - avg_acc)
    return ece


def aggregate_item_results(
    results: list[dict[str, Any]],
    *,
    domain_by_event_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate per-item results (each carrying trajectory/checkpoint keys).

    Each element of ``results`` must contain ``trajectory_id``,
    ``checkpoint_session_count`` and ``metrics`` (from :func:`item_metrics`).
    """

    by_checkpoint: dict[int, list[dict[str, Any]]] = {}
    for row in results:
        by_checkpoint.setdefault(int(row["checkpoint_session_count"]), []).append(row)

    per_checkpoint: dict[str, Any] = {}
    for checkpoint in sorted(by_checkpoint):
        rows = by_checkpoint[checkpoint]
        macro = {
            key: _mean([row["metrics"].get(key) for row in rows])
            for key in SCALAR_METRICS
        }
        # micro over event instances within this checkpoint
        instances = [
            rec for row in rows for rec in row["metrics"]["instance_records"]
        ]
        n_pred = sum(row["metrics"]["n_predicted_events"] for row in rows)
        n_gold = len(instances)
        n_matched = sum(1 for rec in instances if rec["matched"])
        mp, mr, mf = _prf(n_matched, n_pred, n_gold)
        conf_outcomes = [
            pair for row in rows for pair in row["metrics"]["confidence_outcomes"]
        ]
        micro = {
            "event_precision": mp,
            "event_recall": mr,
            "event_f1": mf,
            "status_accuracy": (
                sum(1 for rec in instances if rec["status_correct"]) / n_gold
                if n_gold
                else None
            ),
            "anchor_mean_absolute_error": _mean(
                [rec["anchor_distance"] for rec in instances]
            ),
            "core_evidence_f1_end_to_end": (
                sum(rec["evidence_f1"] or 0.0 for rec in instances) / n_gold
                if n_gold
                else None
            ),
            "expected_calibration_error": expected_calibration_error(conf_outcomes),
        }
        per_checkpoint[str(checkpoint)] = {
            "n_trajectories": len(rows),
            "macro_by_trajectory": macro,
            "micro_by_event_instance": micro,
        }

    checkpoints = sorted(by_checkpoint)
    auc = {
        key: _mean(
            [
                per_checkpoint[str(cp)]["macro_by_trajectory"].get(key)
                for cp in checkpoints
            ]
        )
        for key in SCALAR_METRICS
    }
    final_cp = checkpoints[-1] if checkpoints else None
    summary: dict[str, Any] = {
        "checkpoints": checkpoints,
        "per_checkpoint": per_checkpoint,
        "checkpoint_macro_auc": auc,
        "final_checkpoint": final_cp,
        "final_at_last_checkpoint": (
            per_checkpoint[str(final_cp)]["macro_by_trajectory"] if final_cp else None
        ),
    }

    if final_cp is not None:
        final_rows = by_checkpoint[final_cp]
        summary["by_event_id_at_final"] = _group_instances(
            final_rows, key=lambda rec: rec["event_id"]
        )
        if domain_by_event_id:
            summary["by_event_domain_at_final"] = _group_instances(
                final_rows,
                key=lambda rec: domain_by_event_id.get(rec["event_id"], "unknown"),
            )
    return summary


def _group_instances(rows: list[dict[str, Any]], *, key) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for rec in row["metrics"]["instance_records"]:
            grouped.setdefault(key(rec), []).append(rec)
    out: dict[str, Any] = {}
    for name in sorted(grouped):
        recs = grouped[name]
        out[name] = {
            "n_gold_instances": len(recs),
            "event_recall": sum(1 for r in recs if r["matched"]) / len(recs),
            "status_accuracy": sum(1 for r in recs if r["status_correct"])
            / len(recs),
            "anchor_mean_absolute_error": _mean(
                [r["anchor_distance"] for r in recs]
            ),
            "core_evidence_f1_end_to_end": sum(
                r["evidence_f1"] or 0.0 for r in recs
            )
            / len(recs),
        }
    return out


# ---------------------------------------------------------------------------
# progressive (cross-checkpoint) metrics


def progressive_metrics(
    results: list[dict[str, Any]],
    *,
    first_recoverable: dict[str, dict[str, dict[str, Any]]],
    checkpoint_stride: int = 15,
    hallucination_anchor_tolerance: int = 3,
) -> dict[str, Any]:
    """Longitudinal metrics over successive checkpoints of each trajectory.

    Cross-checkpoint identity is stable for gold instances (their
    ``event_instance_id``). Unmatched predictions ("hallucinations") are
    chained between consecutive checkpoints greedily by identical
    ``event_id`` and anchor distance <= ``hallucination_anchor_tolerance``.
    """

    by_traj: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_traj.setdefault(row["trajectory_id"], []).append(row)

    detection_lags_cp: list[float] = []
    detection_lags_sessions: list[float] = []
    undetected = 0
    retention_values: list[float] = []
    regressions = 0
    regression_opportunities = 0
    anchor_drifts: list[float] = []
    evidence_drifts: list[float] = []
    hallucination_chains: list[int] = []

    for traj_id, rows in by_traj.items():
        rows = sorted(rows, key=lambda r: int(r["checkpoint_session_count"]))
        checkpoints = [int(r["checkpoint_session_count"]) for r in rows]
        # per checkpoint: instance_id -> record
        per_cp: list[dict[str, dict[str, Any]]] = [
            {rec["event_instance_id"]: rec for rec in r["metrics"]["instance_records"]}
            for r in rows
        ]
        instance_ids: list[str] = []
        seen: set[str] = set()
        for cp_map in per_cp:
            for iid in cp_map:
                if iid not in seen:
                    seen.add(iid)
                    instance_ids.append(iid)

        fr_map = first_recoverable.get(traj_id, {})
        for iid in instance_ids:
            visible_idx = [k for k, cp_map in enumerate(per_cp) if iid in cp_map]
            matched_idx = [k for k in visible_idx if per_cp[k][iid]["matched"]]
            fr_info = fr_map.get(iid) or {}
            fr_cp = fr_info.get("checkpoint")
            if matched_idx:
                first_k = matched_idx[0]
                if fr_cp is not None:
                    lag_sessions = checkpoints[first_k] - int(fr_cp)
                    detection_lags_sessions.append(float(lag_sessions))
                    detection_lags_cp.append(lag_sessions / checkpoint_stride)
                later = [k for k in visible_idx if k > first_k]
                if later:
                    retention_values.append(
                        sum(1 for k in later if per_cp[k][iid]["matched"])
                        / len(later)
                    )
                # anchor / evidence drift between first and last matched
                if len(matched_idx) >= 2:
                    first_rec = per_cp[matched_idx[0]][iid]
                    last_rec = per_cp[matched_idx[-1]][iid]
                    if (
                        first_rec["anchor_distance"] is not None
                        and last_rec["anchor_distance"] is not None
                    ):
                        anchor_drifts.append(
                            float(
                                last_rec["anchor_distance"]
                                - first_rec["anchor_distance"]
                            )
                        )
                    if (
                        first_rec["evidence_f1"] is not None
                        and last_rec["evidence_f1"] is not None
                    ):
                        evidence_drifts.append(
                            float(first_rec["evidence_f1"] - last_rec["evidence_f1"])
                        )
            else:
                undetected += 1
            # status regression: occurred correctly, then lost while gold
            # stays occurred
            for k_prev, k_next in zip(visible_idx, visible_idx[1:]):
                prev_rec = per_cp[k_prev][iid]
                next_rec = per_cp[k_next][iid]
                if (
                    prev_rec["gold_status"] == "occurred"
                    and next_rec["gold_status"] == "occurred"
                    and prev_rec["matched"]
                    and prev_rec["pred_status"] == "occurred"
                ):
                    regression_opportunities += 1
                    if (
                        not next_rec["matched"]
                        or next_rec["pred_status"] != "occurred"
                    ):
                        regressions += 1

        # hallucination persistence
        open_chains: list[dict[str, Any]] = []
        for r in rows:
            preds = r["metrics"]["unmatched_pred_records"]
            unused = list(preds)
            next_chains: list[dict[str, Any]] = []
            for chain in open_chains:
                candidate = None
                for pred in unused:
                    if pred["event_id"] != chain["event_id"]:
                        continue
                    try:
                        dist = abs(
                            session_number(pred["anchor_session"])
                            - session_number(chain["anchor_session"])
                        )
                    except ValueError:
                        continue
                    if dist <= hallucination_anchor_tolerance:
                        candidate = pred
                        break
                if candidate is not None:
                    unused.remove(candidate)
                    chain["length"] += 1
                    chain["anchor_session"] = candidate["anchor_session"]
                    next_chains.append(chain)
                else:
                    hallucination_chains.append(chain["length"])
            for pred in unused:
                next_chains.append(
                    {
                        "event_id": pred["event_id"],
                        "anchor_session": pred["anchor_session"],
                        "length": 1,
                    }
                )
            open_chains = next_chains
        hallucination_chains.extend(chain["length"] for chain in open_chains)

    return {
        "detection_lag_mean_checkpoints": _mean(detection_lags_cp),
        "detection_lag_mean_sessions": _mean(detection_lags_sessions),
        "undetected_gold_instances": undetected,
        "post_detection_retention": _mean(retention_values),
        "status_regression_rate": (
            regressions / regression_opportunities
            if regression_opportunities
            else None
        ),
        "status_regression_opportunities": regression_opportunities,
        "anchor_drift_mean": _mean(anchor_drifts),
        "evidence_overlap_drift_mean": _mean(evidence_drifts),
        "hallucination_persistence_mean_checkpoints": _mean(
            [float(c) for c in hallucination_chains]
        ),
        "hallucination_persistence_max_checkpoints": (
            max(hallucination_chains) if hallucination_chains else None
        ),
        "hallucination_chain_count": len(hallucination_chains),
    }


# ---------------------------------------------------------------------------
# paired distractor analysis


def paired_differences(
    values_a: dict[str, float | None],
    values_b: dict[str, float | None],
) -> dict[str, float]:
    """Per-case ``a - b`` for cases present with a value in both maps."""

    return {
        case_id: values_a[case_id] - values_b[case_id]
        for case_id in values_a
        if case_id in values_b
        and values_a[case_id] is not None
        and values_b[case_id] is not None
    }


def clustered_bootstrap_ci(
    diffs: dict[str, float],
    cluster_of: dict[str, str],
    *,
    n_boot: int = 2000,
    seed: int = 42,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Percentile bootstrap CI for the mean paired difference, resampling
    whole clusters (trajectories) with replacement."""

    clusters: dict[str, list[float]] = {}
    for case_id, diff in diffs.items():
        clusters.setdefault(cluster_of.get(case_id, case_id), []).append(diff)
    names = sorted(clusters)
    if not names:
        return {"mean": None, "ci_low": None, "ci_high": None, "n_cases": 0}
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(n_boot):
        sample: list[float] = []
        for _ in names:
            sample.extend(clusters[rng.choice(names)])
        if sample:
            means.append(sum(sample) / len(sample))
    means.sort()
    all_values = [d for vals in clusters.values() for d in vals]
    lo = means[int((alpha / 2) * len(means))] if means else None
    hi = means[min(len(means) - 1, int((1 - alpha / 2) * len(means)))] if means else None
    return {
        "mean": sum(all_values) / len(all_values),
        "median": float(statistics.median(all_values)),
        "ci_low": lo,
        "ci_high": hi,
        "n_cases": len(all_values),
        "n_clusters": len(names),
        "n_boot": n_boot,
    }


def clustered_sign_flip_pvalue(
    diffs: dict[str, float],
    cluster_of: dict[str, str],
    *,
    n_perm: int = 2000,
    seed: int = 42,
) -> float | None:
    """Two-sided paired permutation test flipping signs per cluster."""

    clusters: dict[str, list[float]] = {}
    for case_id, diff in diffs.items():
        clusters.setdefault(cluster_of.get(case_id, case_id), []).append(diff)
    names = sorted(clusters)
    if not names:
        return None
    all_values = [d for vals in clusters.values() for d in vals]
    observed = abs(sum(all_values) / len(all_values))
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_perm):
        total = 0.0
        count = 0
        for name in names:
            sign = 1.0 if rng.random() < 0.5 else -1.0
            for d in clusters[name]:
                total += sign * d
                count += 1
        if count and abs(total / count) >= observed - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def summarize_grouped(
    diffs: dict[str, float], group_of: dict[str, str]
) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    for case_id, diff in diffs.items():
        grouped.setdefault(group_of.get(case_id, "unknown"), []).append(diff)
    return {
        name: {
            "mean": sum(vals) / len(vals),
            "median": float(statistics.median(vals)),
            "n_cases": len(vals),
        }
        for name, vals in sorted(grouped.items())
    }


def auc_sanity_weights(checkpoints: list[int]) -> dict[int, float]:
    """Equal checkpoint weights used by the AUC (documented for tests)."""

    if not checkpoints:
        return {}
    weight = 1.0 / len(checkpoints)
    return {cp: weight for cp in checkpoints}


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
