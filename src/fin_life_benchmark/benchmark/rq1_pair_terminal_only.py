"""cp300 terminal-evidence-only diagnostic for the occurred-pair pilot.

One question, one item, one call per model:

    can a model reconstruct every occurred Life Event pair when the context
    holds *only* terminal lifecycle evidence -- the sessions that settle an
    event either way -- with weak-signal, upcoming, consequence, stale-recall,
    hard-negative and routine sessions removed outright?

This is a subtraction, not a counterfactual: removed sessions are not replaced
with neutral fillers, no target is masked one at a time, and the visible context
is simply shorter than the prefix it came from. Cancellation evidence stays
visible precisely because it must remain a negative example -- a cancelled plan
never earns a gold pair.

Gold is **not** recomputed from the filtered context. It stays the full-prefix
cp300 projection, which is what makes a terminal-only score directly comparable
with the full-prefix score already recorded for the same item.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Sequence

from .lifecycle_masking import TERMINAL_TYPES
from .rq1_models import public_session_number, session_number
from .rq1_pair_models import (
    OCCURRED_ANCHOR_SESSION_TYPE,
    PairAtom,
    RQ1PairPrediction,
    sort_atoms,
)

TERMINAL_ONLY_CONDITION = "terminal_only"

# Temporary diagnostic: cp300 only. A progressive terminal-only ladder is
# deliberately out of scope.
TERMINAL_ONLY_CHECKPOINT = 300

# The canonical terminal lifecycle session types, shared with the counterfactual
# masking experiment rather than redefined here.
TERMINAL_EVIDENCE_SESSION_TYPES = frozenset(TERMINAL_TYPES)

CANCELLATION_SESSION_TYPE = "cancellation_evidence"

# False-positive buckets for the terminal-only error decomposition.
FP_ERROR_CATEGORIES = (
    "wrong_event_at_gold_occurred_session",
    "correct_event_at_wrong_occurred_session",
    "prediction_at_cancellation_session",
    "duplicate_pair",
    "invalid_record",
    "other",
)


def terminal_only_visible_ids(
    prefix_session_ids: Iterable[str],
    sessions: dict[str, dict[str, Any]],
) -> list[str]:
    """Canonical ids of the terminal-evidence sessions inside a prefix.

    Keeps only sessions whose ``session_type`` is exactly ``occurred_evidence``
    or ``cancellation_evidence``, in chronological order. Ordering is enforced
    here rather than inherited from the caller so that "chronological" is a
    property of the filter itself.

    Session ids are canonical (``S###``); the caller maps them to public
    ``D###`` ids through the item's existing map, which is what preserves the
    original public numbering -- nothing is renumbered.
    """

    retained = [
        sid
        for sid in prefix_session_ids
        if (sessions.get(sid) or {}).get("session_type")
        in TERMINAL_EVIDENCE_SESSION_TYPES
    ]
    return sorted(retained, key=session_number)


def session_type_counts(
    session_ids: Iterable[str], sessions: dict[str, dict[str, Any]]
) -> dict[str, int]:
    """Visible session-type histogram, for the report and the audit."""

    counts = Counter(
        (sessions.get(sid) or {}).get("session_type") or "unknown"
        for sid in session_ids
    )
    return dict(sorted(counts.items()))


def _atom_list(atoms: Iterable[PairAtom]) -> list[dict[str, str]]:
    return [
        {"event_id": event_id, "evidence_session_id": public_id}
        for event_id, public_id in sort_atoms(atoms)
    ]


def _row_atoms(pairs: Iterable[dict[str, Any]]) -> Counter[PairAtom]:
    return Counter(
        (str(pair["event_id"]), str(pair["evidence_session_id"])) for pair in pairs
    )


def classify_pair_errors(
    gold_pairs: Sequence[PairAtom],
    prediction: RQ1PairPrediction,
    *,
    session_type_by_public_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Decompose this item's false positives and list its missing gold.

    Only surplus prediction units are classified -- a predicted atom that gold
    also holds is a true positive and never enters a bucket. Precedence within a
    surplus unit: repeats of the same atom are ``duplicate_pair`` first, then the
    single remaining logical claim is classified by content. A cancellation
    anchor is reported as ``prediction_at_cancellation_session`` even when the
    event label happens to be a gold label, because committing to a cancelled
    plan is the more informative failure.

    No partial credit is awarded anywhere: this is diagnostics laid over the
    strict metric, never a second score.
    """

    types = session_type_by_public_id or {}
    gold = Counter(gold_pairs)
    predicted = Counter(prediction.atoms())

    # gold anchors are one-per-instance, so a gold session has one gold label
    gold_event_by_session = {public: event_id for event_id, public in gold}
    gold_event_ids = {event_id for event_id, _ in gold}

    counts = {category: 0 for category in FP_ERROR_CATEGORIES}
    examples: dict[str, list[dict[str, str]]] = {
        category: [] for category in FP_ERROR_CATEGORIES
    }

    for atom, count in predicted.items():
        surplus = count - min(count, gold[atom])
        if surplus <= 0:
            continue
        duplicates = min(count - 1, surplus)
        if duplicates:
            counts["duplicate_pair"] += duplicates
            examples["duplicate_pair"].append(
                {"event_id": atom[0], "evidence_session_id": atom[1]}
            )
        remaining = surplus - duplicates
        if remaining <= 0:
            continue

        event_id, public_id = atom
        stype = types.get(public_id) or "unknown"
        if stype == CANCELLATION_SESSION_TYPE:
            category = "prediction_at_cancellation_session"
        elif (
            public_id in gold_event_by_session
            and gold_event_by_session[public_id] != event_id
        ):
            category = "wrong_event_at_gold_occurred_session"
        elif event_id in gold_event_ids and stype == OCCURRED_ANCHOR_SESSION_TYPE:
            category = "correct_event_at_wrong_occurred_session"
        else:
            category = "other"
        counts[category] += remaining
        examples[category].append(
            {
                "event_id": event_id,
                "evidence_session_id": public_id,
                "session_type": stype,
            }
        )

    counts["invalid_record"] = int(prediction.invalid_record_count)

    missing = [atom for atom in gold if predicted[atom] < gold[atom]]
    return {
        "false_positive_categories": counts,
        "false_positive_examples": {
            category: rows for category, rows in examples.items() if rows
        },
        "invalid_records": list(prediction.rejected_records),
        "false_negatives": _atom_list(missing),
    }


def compare_with_baseline(
    *,
    gold_pairs: Sequence[PairAtom],
    prediction: RQ1PairPrediction,
    metrics: dict[str, Any],
    baseline_row: dict[str, Any],
    session_type_by_public_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compare this terminal-only item against a stored full-prefix prediction.

    ``baseline_row`` is one record from an existing full-prefix predictions
    JSONL -- the comparison never calls a model again. The retained/lost/new
    pair lists are set-shaped (duplicates are already charged by the strict
    metric and counted in the error decomposition), while every P/R/F1 number
    comes from the strict metric on each side untouched.
    """

    types = session_type_by_public_id or {}
    gold = Counter(gold_pairs)
    terminal = Counter(prediction.atoms())
    baseline = _row_atoms(baseline_row.get("predicted_pairs") or [])
    baseline_gold = _row_atoms(baseline_row.get("gold_pairs") or [])

    baseline_metrics = baseline_row.get("metrics") or {}

    def _triple(source: dict[str, Any]) -> dict[str, Any]:
        return {
            key: source.get(f"strict_occurred_event_evidence_{key}")
            for key in ("precision", "recall", "f1")
        }

    full = _triple(baseline_metrics)
    term = _triple(metrics)
    delta = {
        key: (
            None
            if full[key] is None or term[key] is None
            else round(term[key] - full[key], 6)
        )
        for key in full
    }

    correct_full = {atom for atom in gold if baseline[atom] > 0}
    correct_terminal = {atom for atom in gold if terminal[atom] > 0}
    fp_terminal = {atom for atom in terminal if terminal[atom] > gold[atom]}
    fp_full = {atom for atom in baseline if baseline[atom] > gold[atom]}

    return {
        "baseline_file_row": {
            "item_id": baseline_row.get("item_id"),
            "condition": baseline_row.get("condition"),
            "checkpoint_session_count": baseline_row.get("checkpoint_session_count"),
            "provider": baseline_row.get("provider"),
            "model": baseline_row.get("model"),
            "n_visible_sessions": baseline_row.get("n_visible_sessions"),
        },
        # a differing baseline gold makes every delta below meaningless
        "gold_identical_to_full": baseline_gold == gold,
        "gold_symmetric_difference": _atom_list(
            (baseline_gold - gold) + (gold - baseline_gold)
        ),
        "full_prefix": full,
        "terminal_only": term,
        "delta": delta,
        "pairs_retained_from_full": _atom_list(correct_full & correct_terminal),
        "full_correct_pairs_lost": _atom_list(correct_full - correct_terminal),
        "new_terminal_only_true_positives": _atom_list(
            correct_terminal - correct_full
        ),
        "new_terminal_only_false_positives": _atom_list(fp_terminal - fp_full),
        "cancelled_event_false_positives": _atom_list(
            atom
            for atom in fp_terminal
            if types.get(atom[1]) == CANCELLATION_SESSION_TYPE
        ),
        "prediction_count_change": (
            metrics.get("predicted_pair_count", 0)
            - int(baseline_metrics.get("predicted_pair_count") or 0)
        ),
        "full_prefix_predicted_pair_count": baseline_metrics.get(
            "predicted_pair_count"
        ),
        "terminal_only_predicted_pair_count": metrics.get("predicted_pair_count"),
    }


def find_baseline_row(
    rows: Iterable[dict[str, Any]],
    *,
    trajectory_id: str,
    checkpoint: int,
    condition: str = "full_prefix",
) -> dict[str, Any]:
    """The one baseline row for this trajectory/checkpoint/condition.

    Raises :class:`ValueError` on no match or more than one -- silently taking
    the first would let an unrelated run supply the numbers a delta is built on.
    """

    matches = [
        row
        for row in rows
        if row.get("trajectory_id") == trajectory_id
        and int(row.get("checkpoint_session_count") or -1) == checkpoint
        and row.get("condition") == condition
    ]
    if not matches:
        raise ValueError(
            f"no {condition} baseline row for {trajectory_id} at cp{checkpoint}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"{len(matches)} {condition} baseline rows for {trajectory_id} at "
            f"cp{checkpoint}; expected exactly one"
        )
    return matches[0]


def public_ids_are_chronological(public_ids: Sequence[str]) -> bool:
    numbers = [public_session_number(pid) for pid in public_ids]
    return numbers == sorted(numbers)
