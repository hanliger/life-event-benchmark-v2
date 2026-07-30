"""Models and gold projection for the temporary occurred-event pair pilot.

Stage id ``stage1_occurred_event_evidence_pairs``. A deliberately minimal,
temporary protocol that runs *beside* ``stage1_event_trajectory``: it reuses the
same progressive items, the same PrefixGold-derived gold and the same public
``S### -> D###`` aliasing, but asks one question --

    which Life Events have actually occurred by this checkpoint, and which
    session first establishes each occurrence?

No lifecycle status output, no anchors beyond that one session, no confidence,
no instance alignment. Weak-signal, upcoming and cancellation evidence stays
visible in the context on purpose: committing to them is the error this pilot
measures.

A pair atom is ``(event_id, public_evidence_session_id)``. Atoms live in the
*public* id space because that is what the model emits; canonical ``S###`` ids
never leave the evaluator.

Gold is a multiset: two occurrences of the same ``event_id`` contribute two
pairs through their two distinct occurrence anchors, and multiplicity is never
collapsed.
"""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, Field

from .rq1_models import (
    RQ1GoldEventInstance,
    public_session_number,
    session_number,
)

RQ1_PAIR_STAGE = "stage1_occurred_event_evidence_pairs"
RQ1_PAIR_PROTOCOL_VERSION = "rq1-occurred-event-pairs-temp-v1"
RQ1_PAIR_METRICS_VERSION = "rq1-exact-occurred-pair-metrics-v1"
RQ1_PAIR_PROMPT_CONTRACT = "rq1_occurred_event_pairs_ko"
RQ1_PAIR_PROMPT_FILE = "prompts/benchmark/rq1_occurred_event_pairs_ko.md"

# full_prefix is the protocol condition. The two no_prospective arms are
# temporary diagnostics that take away only the weak-signal and upcoming
# sessions -- every distractor, terminal and downstream session stays -- and
# reuse this gold unchanged (see rq1_pair_no_prospective). They differ in how:
# no_prospective drops those sessions, so the context also gets shorter;
# no_prospective_substituted replaces each with a neutral routine filler, so the
# session count is held constant and only the content changes.
RQ1_PAIR_CONDITIONS = (
    "full_prefix",
    "no_prospective",
    "no_prospective_substituted",
)

PAIR_CHECKPOINT_STRIDE = 15
PAIR_CHECKPOINT_GRID = tuple(range(15, 301, 15))

# The canonical occurrence anchor: earliest visible session linked to the event
# instance with this session type *and* this post-session status. No fallback.
OCCURRED_STATUS = "occurred"
OCCURRED_ANCHOR_SESSION_TYPE = "occurred_evidence"

# Session types that can never be an occurrence anchor. Predicting one of these
# is a false positive; the diagnostics count them by type.
NON_OCCURRENCE_SESSION_TYPES = (
    "weak_signal_evidence",
    "upcoming_evidence",
    "cancellation_evidence",
    "hard_negative",
    "routine_financial",
    "consequence_session",
    "stale_recall_session",
)

# The only two fields a prediction record carries.
PAIR_RECORD_FIELDS = ("event_id", "evidence_session_id")

PairAtom = tuple[str, str]


class RQ1PredictedPair(BaseModel):
    """One normalized predicted pair (public evidence session id)."""

    event_id: str
    evidence_session_id: str

    def atom(self) -> PairAtom:
        return (self.event_id, self.evidence_session_id)


class RQ1PairPrediction(BaseModel):
    """Parsed + validated pair output for one item.

    ``invalid_record_count`` counts *records*, not field errors: one rejected
    record is exactly one false-positive prediction unit however many
    validation errors it produced.
    """

    valid_pairs: list[RQ1PredictedPair] = Field(default_factory=list)
    invalid_record_count: int = 0
    validation_errors: list[str] = Field(default_factory=list)
    parse_error: str | None = None
    # raw records dropped by validation, kept for audit (never re-scored)
    rejected_records: list[dict[str, Any]] = Field(default_factory=list)

    def atoms(self) -> list[PairAtom]:
        """Predicted atoms, duplicates preserved (they are penalized)."""

        return [pair.atom() for pair in self.valid_pairs]


def sort_atoms(atoms: Iterable[PairAtom]) -> list[PairAtom]:
    """Deterministic ordering by (evidence session number, event_id).

    Scoring is order-independent; this only stabilizes stored artifacts.
    """

    return sorted(atoms, key=lambda atom: (public_session_number(atom[1]), atom[0]))


def occurred_anchor_session(
    event_instance_id: str,
    *,
    sessions: dict[str, dict[str, Any]],
    visible_session_ids: Iterable[str],
) -> str:
    """Canonical occurrence anchor for one event instance (canonical id).

    Among the *visible* sessions linked to ``event_instance_id``, keep those
    with ``session_type == "occurred_evidence"`` and
    ``event_status_after_session == "occurred"``, and take the chronologically
    earliest. Raises :class:`ValueError` when none qualifies -- there is
    deliberately no fallback to weak, upcoming, consequence or cancellation
    evidence.
    """

    visible = set(visible_session_ids)
    candidates = [
        sid
        for sid, record in sessions.items()
        if sid in visible
        and record.get("linked_event_instance_id") == event_instance_id
        and record.get("session_type") == OCCURRED_ANCHOR_SESSION_TYPE
        and record.get("event_status_after_session") == OCCURRED_STATUS
    ]
    if not candidates:
        raise ValueError(
            "no visible establishing occurrence session for event instance "
            f"{event_instance_id!r}"
        )
    return min(candidates, key=session_number)


def gold_pairs_from_occurred_trajectory(
    occurred: Iterable[RQ1GoldEventInstance],
    *,
    session_id_map: dict[str, str],
    sessions: dict[str, dict[str, Any]],
    taxonomy_event_ids: set[str] | None = None,
) -> list[PairAtom]:
    """Project occurred event instances into exactly one pair atom each.

    ``session_id_map`` is the item's canonical -> public map for every session
    visible at this checkpoint; membership in it *is* the visibility check.

    Cancelled, weak-signal and upcoming instances contribute nothing: they are
    absent from ``occurred_trajectory`` and any non-occurred instance passed in
    is rejected rather than silently skipped.

    Raises :class:`ValueError` when an instance is not occurred, has no visible
    establishing occurrence session, resolves to a session outside the visible
    map or to a non-``D###`` public id, or (when ``taxonomy_event_ids`` is
    given) carries an event id outside the active taxonomy.
    """

    atoms: list[PairAtom] = []
    for event in occurred:
        if event.event_status != OCCURRED_STATUS:
            raise ValueError(
                f"non-occurred instance in the occurred projection: "
                f"{event.event_instance_id!r} status={event.event_status!r}"
            )
        if not event.event_id:
            raise ValueError(
                f"gold event instance without event_id: {event.event_instance_id!r}"
            )
        if taxonomy_event_ids is not None and event.event_id not in taxonomy_event_ids:
            raise ValueError(
                f"gold event_id outside the active taxonomy: {event.event_id!r} "
                f"({event.event_instance_id})"
            )
        anchor = occurred_anchor_session(
            event.event_instance_id,
            sessions=sessions,
            visible_session_ids=session_id_map,
        )
        public = session_id_map.get(anchor)
        if public is None:
            raise ValueError(
                f"occurrence anchor not visible at this checkpoint: {anchor} "
                f"({event.event_instance_id})"
            )
        # raises on anything that is not a public D### id
        public_session_number(public)
        atoms.append((event.event_id, public))
    return sort_atoms(atoms)
