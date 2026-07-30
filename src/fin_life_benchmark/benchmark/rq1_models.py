"""Data models for the RQ1 event-trajectory reconstruction task.

RQ1 asks a model to reconstruct the full ledger of implicit life-event
instances (type, lifecycle status, temporal order, evidence sessions) from a
chronological prefix of consultation sessions.

The task id is ``stage1_event_trajectory``. It is retained as a broader RQ1
research task; official Stage 1 uses its cumulative Gold projection but emits
the narrower occurred-event/evidence pair contract.

PrefixGold stays evaluator-only: items carry gold in a dedicated ``gold``
payload plus the private canonical->public session-id mapping, and the
model-visible rendering exposes only public session ids and turns.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

RQ1_STAGE = "stage1_event_trajectory"
RQ1_BUILDER_VERSION = "rq1-builder-v1"
RQ1_METRICS_VERSION = "rq1-metrics-v1"

# Lifecycle statuses a prediction may assert for an event instance.
PREDICTABLE_STATUSES = ("weak_signal", "upcoming", "occurred", "cancelled")
# Extra class used only by status scoring for unmatched gold/predicted rows.
NO_EVENT = "no_event"
STATUS_CLASSES = PREDICTABLE_STATUSES + (NO_EVENT,)

# Session types that constitute direct lifecycle evidence (gold core set).
CORE_EVIDENCE_TYPES = (
    "weak_signal_evidence",
    "upcoming_evidence",
    "occurred_evidence",
    "cancellation_evidence",
)
# Session types that support an event without being lifecycle evidence.
SUPPORTING_TYPES = ("consequence_session", "stale_recall_session")
# Session types that must never appear in gold event evidence.
NON_EVIDENCE_TYPES = ("hard_negative", "routine_financial")

EVALUATION_CONDITIONS = ("full_prefix", "last_15", "oracle_evidence")
DISTRACTOR_CONDITIONS = ("full", "mask_distractor", "sham")

_SESSION_ID_RE = re.compile(r"^S(\d{3,})$")
_PUBLIC_ID_RE = re.compile(r"^D(\d{3,})$")


def session_number(session_id: str) -> int:
    match = _SESSION_ID_RE.match(session_id or "")
    if not match:
        raise ValueError(f"invalid canonical session id: {session_id!r}")
    return int(match.group(1))


def to_public_session_id(session_id: str) -> str:
    """Deterministic public alias: S001 -> D001, S042 -> D042, ..."""

    return f"D{session_number(session_id):03d}"


def from_public_session_id(public_id: str) -> str:
    match = _PUBLIC_ID_RE.match(public_id or "")
    if not match:
        raise ValueError(f"invalid public session id: {public_id!r}")
    return f"S{int(match.group(1)):03d}"


def public_session_number(public_id: str) -> int:
    match = _PUBLIC_ID_RE.match(public_id or "")
    if not match:
        raise ValueError(f"invalid public session id: {public_id!r}")
    return int(match.group(1))


class RQ1GoldEventInstance(BaseModel):
    """One gold event instance as visible at a checkpoint."""

    event_instance_id: str
    event_id: str
    life_event_label: str = ""
    event_status: str
    first_evidence_session: str
    status_anchor_session: str
    core_evidence_sessions: list[str] = Field(default_factory=list)
    supporting_sessions: list[str] = Field(default_factory=list)
    first_recoverable_session: str | None = None


class RQ1ItemGold(BaseModel):
    """Private, evaluator-only payload of a natural RQ1 item."""

    full_observed_ledger: list[RQ1GoldEventInstance] = Field(default_factory=list)
    occurred_trajectory: list[RQ1GoldEventInstance] = Field(default_factory=list)
    # Official Stage 1 projection in the public D### id space. This avoids
    # requiring private session annotations at model-run time.
    occurred_event_evidence_pairs: list[dict[str, str]] = Field(
        default_factory=list
    )
    # canonical session id -> public session id, for every visible session
    session_id_map: dict[str, str] = Field(default_factory=dict)
    input_session_count: int = 0
    input_char_count: int = 0
    input_token_estimate: int = 0
    accumulated_hard_negative_count: int = 0
    accumulated_routine_count: int = 0
    accumulated_event_count: int = 0
    # event_instance_id -> first recoverable session / checkpoint
    first_recoverable: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RQ1Item(BaseModel):
    """One natural progressive item (trajectory x checkpoint)."""

    item_id: str
    stage: str = RQ1_STAGE
    trajectory_id: str
    prefix_id: str
    checkpoint_session_count: int
    question: str = ""
    # canonical ids of the chronological prefix; materialized at prompt time
    visible_sessions: list[str] = Field(default_factory=list)
    taxonomy_hash: str = ""
    prompt_contract: str = "rq1_event_trajectory_ko"
    gold: RQ1ItemGold = Field(default_factory=RQ1ItemGold)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RQ1PredictedEvent(BaseModel):
    """One normalized predicted event instance (canonical session ids)."""

    prediction_id: str = ""
    event_id: str
    status: str
    first_evidence_session: str
    status_anchor_session: str
    core_evidence_sessions: list[str] = Field(default_factory=list)
    supporting_sessions: list[str] = Field(default_factory=list)
    confidence: float | None = None


class RQ1Prediction(BaseModel):
    """Parsed + validated model output for one item."""

    events: list[RQ1PredictedEvent] = Field(default_factory=list)
    parse_error: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    # raw entries dropped by validation, kept for audit (never re-scored)
    rejected_events: list[dict[str, Any]] = Field(default_factory=list)


class RQ1DistractorCase(BaseModel):
    """One paired distractor unit materialized in three conditions."""

    case_id: str
    trajectory_id: str
    checkpoint_session_count: int
    target_session_id: str
    hard_negative_type: str
    near_miss_event_id: str
    # private only; must never reach model-visible input
    near_miss_explanation: str = ""
    masked_session_ids: list[str] = Field(default_factory=list)
    sham_session_ids: list[str] = Field(default_factory=list)
    donor_by_slot: dict[str, str] = Field(default_factory=dict)
    donor_provenance: list[dict[str, Any]] = Field(default_factory=list)
    source_sessions_file: str = ""
    filler_bank_file: str = ""
    gold: RQ1ItemGold = Field(default_factory=RQ1ItemGold)
    metadata: dict[str, Any] = Field(default_factory=dict)
