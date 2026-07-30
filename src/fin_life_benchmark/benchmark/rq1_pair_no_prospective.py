"""No-prospective-evidence diagnostic for the occurred-pair pilot.

One question, one call per model per checkpoint:

    can a model reconstruct every occurred Life Event pair when the *only*
    thing taken away is prospective evidence -- the weak signals and the
    upcoming plans that precede an event -- with the terminal lifecycle
    sessions, the downstream sessions and the full distractor mass all left
    exactly as they were?

This is a subtraction, not a counterfactual: removed sessions are not replaced
with neutral fillers. Unlike a terminal-only reduction it removes very little
(36 of 300 sessions at cp300), so context *length* is nearly held constant and
what changes is close to purely the presence of the prospective evidence
channel. Every distractor -- routine and hard-negative alike -- stays visible,
and so do the cancellation sessions, which must remain negatives: a cancelled
plan never earns a gold pair.

Gold is **not** recomputed from the filtered context. It stays the full-prefix
projection at that checkpoint, which is what makes this score directly
comparable with the full-prefix score recorded for the same item.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Sequence

from .lifecycle_masking import UPCOMING_TYPES, WEAK_TYPES
from .rq1_models import public_session_number, session_number
from .rq1_pair_models import (
    OCCURRED_ANCHOR_SESSION_TYPE,
    PairAtom,
    RQ1PairPrediction,
    sort_atoms,
)

NO_PROSPECTIVE_CONDITION = "no_prospective"

# The length-matched arm. Instead of dropping the prospective sessions it reads
# a corpus in which each was replaced in place by a neutral routine filler
# (scripts/build_no_prospective_corpus.py), so the model sees the full
# checkpoint count -- 300 sessions at cp300, not 264 -- and the only thing that
# changed is the prospective *content*. Context length, session ids, positions
# and dates are all held constant, which removes the length confound the
# subtraction arm still carries.
NO_PROSPECTIVE_SUBSTITUTED_CONDITION = "no_prospective_substituted"

# The checkpoint the diagnostic was first run at, and the audit's default. It is
# no longer a restriction: the condition now runs at any checkpoint so the
# ablation can be read as a ladder (cp30, cp60, ... cp300) rather than a single
# point. The evaluator still requires --checkpoint to be given explicitly, so a
# sweep is always a deliberate choice.
NO_PROSPECTIVE_DEFAULT_CHECKPOINT = 300

# The two prospective (pre-occurrence) evidence types, taken from the shared
# lifecycle vocabulary rather than redefined here. These -- and only these --
# are removed; every other session type in the prefix survives untouched.
PROSPECTIVE_EVIDENCE_SESSION_TYPES = frozenset(WEAK_TYPES | UPCOMING_TYPES)

CANCELLATION_SESSION_TYPE = "cancellation_evidence"
HARD_NEGATIVE_SESSION_TYPE = "hard_negative"

# False-positive buckets for the error decomposition. Distractors stay visible
# in this condition, so an anchor on a hard negative is its own bucket rather
# than an "other" -- it is the negative control the distractor mass exists for.
FP_ERROR_CATEGORIES = (
    "wrong_event_at_gold_occurred_session",
    "correct_event_at_wrong_occurred_session",
    "prediction_at_cancellation_session",
    "prediction_at_hard_negative_session",
    "duplicate_pair",
    "invalid_record",
    "other",
)


def no_prospective_visible_ids(
    prefix_session_ids: Iterable[str],
    sessions: dict[str, dict[str, Any]],
) -> list[str]:
    """Canonical ids of a prefix with its prospective evidence removed.

    Drops only sessions whose ``session_type`` is ``weak_signal_evidence`` or
    ``upcoming_evidence``; occurred, cancellation, consequence, stale-recall,
    hard-negative and routine sessions are all retained, in chronological
    order. Ordering is enforced here rather than inherited from the caller so
    that "chronological" is a property of the filter itself.

    Session ids are canonical (``S###``); the caller maps them to public
    ``D###`` ids through the item's existing map, which is what preserves the
    original public numbering -- nothing is renumbered.
    """

    retained = [
        sid
        for sid in prefix_session_ids
        if (sessions.get(sid) or {}).get("session_type")
        not in PROSPECTIVE_EVIDENCE_SESSION_TYPES
    ]
    return sorted(retained, key=session_number)


def surviving_prospective_sessions(
    session_ids: Iterable[str],
    sessions: dict[str, dict[str, Any]],
) -> list[str]:
    """Prospective sessions still present in a supposedly substituted corpus.

    The substituted arm renders the *whole* prefix, so nothing in the render
    path can reveal that ``--sessions-dir`` points at the original corpus: the
    run would simply be a ``full_prefix`` run wearing the ablation's name. This
    is the check that makes that failure loud, and it is the reason the arm is a
    named condition rather than a swapped directory.
    """

    return [
        sid
        for sid in session_ids
        if (sessions.get(sid) or {}).get("session_type")
        in PROSPECTIVE_EVIDENCE_SESSION_TYPES
    ]


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
    single remaining logical claim is classified by content. A cancellation or
    hard-negative anchor is reported as such even when the event label happens to
    be a gold label, because committing to a distractor is the more informative
    failure.

    ``false_positive_anchor_session_types`` histograms *every* false positive by
    the type of session it anchors on, so no anchor is invisible merely because
    it has no dedicated bucket.

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
    anchor_types: Counter[str] = Counter()

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
        anchor_types[stype] += remaining
        if stype == CANCELLATION_SESSION_TYPE:
            category = "prediction_at_cancellation_session"
        elif stype == HARD_NEGATIVE_SESSION_TYPE:
            category = "prediction_at_hard_negative_session"
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
        "false_positive_anchor_session_types": dict(sorted(anchor_types.items())),
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
    """Compare this no-prospective item against a stored full-prefix prediction.

    ``baseline_row`` is one record from an existing full-prefix predictions
    JSONL -- the comparison never calls a model again. The retained/lost/new
    pair lists are set-shaped (duplicates are already charged by the strict
    metric and counted in the error decomposition), while every P/R/F1 number
    comes from the strict metric on each side untouched.
    """

    types = session_type_by_public_id or {}
    gold = Counter(gold_pairs)
    ablated = Counter(prediction.atoms())
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
    correct_ablated = {atom for atom in gold if ablated[atom] > 0}
    fp_ablated = {atom for atom in ablated if ablated[atom] > gold[atom]}
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
        "no_prospective": term,
        "delta": delta,
        "pairs_retained_from_full": _atom_list(correct_full & correct_ablated),
        "full_correct_pairs_lost": _atom_list(correct_full - correct_ablated),
        "new_no_prospective_true_positives": _atom_list(
            correct_ablated - correct_full
        ),
        "new_no_prospective_false_positives": _atom_list(fp_ablated - fp_full),
        "cancelled_event_false_positives": _atom_list(
            atom
            for atom in fp_ablated
            if types.get(atom[1]) == CANCELLATION_SESSION_TYPE
        ),
        "hard_negative_false_positives": _atom_list(
            atom
            for atom in fp_ablated
            if types.get(atom[1]) == HARD_NEGATIVE_SESSION_TYPE
        ),
        "prediction_count_change": (
            metrics.get("predicted_pair_count", 0)
            - int(baseline_metrics.get("predicted_pair_count") or 0)
        ),
        "full_prefix_predicted_pair_count": baseline_metrics.get(
            "predicted_pair_count"
        ),
        "no_prospective_predicted_pair_count": metrics.get("predicted_pair_count"),
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
