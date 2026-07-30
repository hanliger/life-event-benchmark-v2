"""Exact multiset metrics for the official Stage 1 pair task.

Headline metric (the only accuracy number for this task): exact pair
precision / recall / F1 over the multiset of ``(event_id,
evidence_session_id)`` atoms, where gold is one atom per occurred event
instance at its canonical occurrence anchor.

    G = Counter(gold_pairs)
    P = Counter(valid_predicted_pairs)

    TP = sum(min(G[x], P[x]) for x in G | P)
    predicted_count = sum(P.values()) + invalid_record_count
    FP = predicted_count - TP
    FN = sum(G.values()) - TP

``collections.Counter`` rather than ``set`` is deliberate: a duplicated
prediction is a second claim and is charged as a false positive.

Consequences, all intended: a sibling or generic-entailed label at the right
session earns nothing; the right label at the wrong session earns nothing; a
pair anchored on weak-signal, upcoming, cancellation, consequence, stale-recall,
hard-negative or routine evidence is simply not in gold and therefore a false
positive; extra pairs cost precision; missing occurrences cost recall; each
invalid record costs exactly one unit of precision.

Empty/degenerate cases follow the repository's :func:`_prf` convention (shared
with rq1_metrics, imported so the two protocols cannot drift):

- gold empty and prediction empty -> 1.0 / 1.0 / 1.0;
- gold non-empty and prediction empty -> 0.0 / 0.0 / 0.0;
- gold empty and prediction non-empty -> precision 0.0, **recall 0.0**, F1 0.0
  (``_prf`` returns 0.0 for a zero-support recall; this task keeps that
  convention rather than inventing a 1.0). F1 is 0.0 in every non-trivial
  empty case, as required.

Aggregation contract: metrics are computed per item (one trajectory at one
checkpoint), macro-averaged across trajectories within a checkpoint, then a
checkpoint AUC equally weights the checkpoints. Pair atoms from different
checkpoints are never pooled into a headline score -- an early occurrence
appears in many more prefixes and would dominate.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Sequence

from .rq1_metrics import _mean, _prf
from .rq1_pair_models import (
    NON_OCCURRENCE_SESSION_TYPES,
    PairAtom,
    RQ1PairPrediction,
)

HEADLINE_METRICS = (
    "strict_occurred_event_evidence_precision",
    "strict_occurred_event_evidence_recall",
    "strict_occurred_event_evidence_f1",
)

# Numeric per-item keys that are macro-averaged and carried into the AUC. The
# optional event-only / session-only diagnostics ride along for debugging
# visibility but are never substituted into the headline triple.
MACRO_METRICS = HEADLINE_METRICS + (
    "gold_pair_count",
    "predicted_valid_pair_count",
    "predicted_pair_count",
    "invalid_record_count",
    "true_positive_pair_count",
    "false_positive_pair_count",
    "false_negative_pair_count",
    "signed_pair_count_bias",
    "absolute_pair_count_error",
    "exact_pair_multiset_match",
    "duplicate_prediction_count",
    "diagnostic_event_id_only_precision",
    "diagnostic_event_id_only_recall",
    "diagnostic_event_id_only_f1",
    "diagnostic_evidence_session_only_precision",
    "diagnostic_evidence_session_only_recall",
    "diagnostic_evidence_session_only_f1",
)

# Session-type buckets for false-positive evidence, summed (not averaged) at
# aggregation time. Debugging only.
FP_SESSION_TYPE_KEYS = tuple(
    f"false_positive_evidence_type_{stype}" for stype in NON_OCCURRENCE_SESSION_TYPES
) + (
    "false_positive_evidence_type_occurred_evidence",
    "false_positive_evidence_type_unknown",
)


def _multiset_true_positives(gold: Counter[Any], predicted: Counter[Any]) -> int:
    return sum(min(gold[atom], predicted[atom]) for atom in set(gold) | set(predicted))


def _false_positive_evidence_types(
    gold: Counter[PairAtom],
    predicted: Counter[PairAtom],
    session_type_by_public_id: dict[str, str] | None,
) -> dict[str, int]:
    """Count the session type each false-positive atom pointed at.

    Answers "when the model was wrong, did it commit to a plan, to a
    cancellation, or to a distractor?" -- diagnostics only, never scored.
    """

    counts = {key: 0 for key in FP_SESSION_TYPE_KEYS}
    if session_type_by_public_id is None:
        return counts
    for atom, count in predicted.items():
        surplus = count - min(count, gold[atom])
        if surplus <= 0:
            continue
        stype = session_type_by_public_id.get(atom[1]) or "unknown"
        key = f"false_positive_evidence_type_{stype}"
        if key not in counts:
            key = "false_positive_evidence_type_unknown"
        counts[key] += surplus
    return counts


def pair_item_metrics(
    gold_pairs: Sequence[PairAtom],
    prediction: RQ1PairPrediction,
    *,
    session_type_by_public_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Strict occurred-pair metrics plus minimal diagnostics for one item."""

    gold = Counter(gold_pairs)
    predicted = Counter(prediction.atoms())

    gold_count = sum(gold.values())
    valid_count = sum(predicted.values())
    invalid_count = int(prediction.invalid_record_count)
    predicted_count = valid_count + invalid_count

    true_positives = _multiset_true_positives(gold, predicted)
    false_positives = predicted_count - true_positives
    false_negatives = gold_count - true_positives

    precision, recall, f1 = _prf(true_positives, predicted_count, gold_count)

    metrics: dict[str, Any] = {
        "strict_occurred_event_evidence_precision": precision,
        "strict_occurred_event_evidence_recall": recall,
        "strict_occurred_event_evidence_f1": f1,
        "gold_pair_count": gold_count,
        "predicted_valid_pair_count": valid_count,
        "invalid_record_count": invalid_count,
        "predicted_pair_count": predicted_count,
        "true_positive_pair_count": true_positives,
        "false_positive_pair_count": false_positives,
        "false_negative_pair_count": false_negatives,
        "signed_pair_count_bias": predicted_count - gold_count,
        "absolute_pair_count_error": abs(predicted_count - gold_count),
        "exact_pair_multiset_match": float(
            predicted == gold and invalid_count == 0 and not prediction.parse_error
        ),
        "parse_error": prediction.parse_error,
        # optional debugging diagnostics; never a substitute for the headline
        "duplicate_prediction_count": valid_count - len(predicted),
    }

    # Component diagnostics over valid pairs only (no invalid-record charge):
    # they answer "did it find the right events / the right sessions" when the
    # strict pair score is 0.
    for name, index in (("event_id", 0), ("evidence_session", 1)):
        gold_side = Counter(atom[index] for atom in gold.elements())
        pred_side = Counter(atom[index] for atom in predicted.elements())
        p, r, f = _prf(
            _multiset_true_positives(gold_side, pred_side),
            sum(pred_side.values()),
            sum(gold_side.values()),
        )
        metrics[f"diagnostic_{name}_only_precision"] = p
        metrics[f"diagnostic_{name}_only_recall"] = r
        metrics[f"diagnostic_{name}_only_f1"] = f

    metrics.update(
        _false_positive_evidence_types(gold, predicted, session_type_by_public_id)
    )
    return metrics


def aggregate_pair_results(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-item results into per-checkpoint macro scores and AUC.

    Each element must carry ``trajectory_id``, ``checkpoint_session_count`` and
    ``metrics`` (from :func:`pair_item_metrics`).
    """

    by_checkpoint: dict[int, list[dict[str, Any]]] = {}
    for row in results:
        by_checkpoint.setdefault(int(row["checkpoint_session_count"]), []).append(row)

    per_checkpoint: dict[str, Any] = {}
    for checkpoint in sorted(by_checkpoint):
        rows = by_checkpoint[checkpoint]
        macro = {
            key: _mean([row["metrics"].get(key) for row in rows])
            for key in MACRO_METRICS
        }
        fp_types = {
            key: sum(int(row["metrics"].get(key) or 0) for row in rows)
            for key in FP_SESSION_TYPE_KEYS
        }
        # Micro within one checkpoint only. Reported for later multi-trajectory
        # runs; it is not the headline and is never pooled across checkpoints.
        tp = sum(row["metrics"]["true_positive_pair_count"] for row in rows)
        n_pred = sum(row["metrics"]["predicted_pair_count"] for row in rows)
        n_gold = sum(row["metrics"]["gold_pair_count"] for row in rows)
        mp, mr, mf = _prf(tp, n_pred, n_gold)
        per_checkpoint[str(checkpoint)] = {
            "n_trajectories": len(rows),
            "macro_by_trajectory": macro,
            "false_positive_evidence_types": fp_types,
            "micro_by_pair_atom": {
                "strict_occurred_event_evidence_precision": mp,
                "strict_occurred_event_evidence_recall": mr,
                "strict_occurred_event_evidence_f1": mf,
                "true_positive_pair_count": tp,
                "predicted_pair_count": n_pred,
                "gold_pair_count": n_gold,
            },
        }

    checkpoints = sorted(by_checkpoint)
    auc = {
        key: _mean(
            [
                per_checkpoint[str(checkpoint)]["macro_by_trajectory"].get(key)
                for checkpoint in checkpoints
            ]
        )
        for key in MACRO_METRICS
    }
    final_checkpoint = checkpoints[-1] if checkpoints else None
    return {
        "checkpoints": checkpoints,
        "n_checkpoints": len(checkpoints),
        "per_checkpoint": per_checkpoint,
        "checkpoint_macro_auc": auc,
        "final_checkpoint": final_checkpoint,
        "final_at_last_checkpoint": (
            per_checkpoint[str(final_checkpoint)]["macro_by_trajectory"]
            if final_checkpoint is not None
            else None
        ),
        "final_at_300": (
            per_checkpoint["300"]["macro_by_trajectory"]
            if "300" in per_checkpoint
            else None
        ),
    }
