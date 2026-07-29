"""Builders for RQ1 natural progressive items and model-visible inputs.

Gold is derived from PrefixGold (evaluator-only) joined with canonical
session records. The model-visible rendering exposes only deterministic
public session ids (``D###``) and turns.

Status-anchor definition (deterministic; ties cannot occur because session
ids are unique, and multiple candidate sessions are resolved by session
order):

- ``occurred``: the first visible ``occurred_evidence`` session whose
  ``event_status_after_session`` is ``occurred`` (fallback: first visible
  ``occurred_evidence`` session, then last visible core session).
- ``cancelled``: same rule with ``cancellation_evidence`` / ``cancelled``.
- ``upcoming``: the latest visible ``upcoming_evidence`` session
  (fallback: latest visible core session).
- ``weak_signal``: the latest visible ``weak_signal_evidence`` session
  (fallback: latest visible core session).

Sorting is deterministic: the full observed ledger by
``(first_evidence_session, event_instance_id)``; the occurred trajectory by
``(status_anchor_session, event_instance_id)``.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from ..io.jsonl import read_jsonl
from .rq1_models import (
    CORE_EVIDENCE_TYPES,
    NON_EVIDENCE_TYPES,
    RQ1_STAGE,
    RQ1GoldEventInstance,
    RQ1Item,
    RQ1ItemGold,
    SUPPORTING_TYPES,
    session_number,
    to_public_session_id,
)

# Rough chars-per-token for Korean consultation text; recorded as an
# estimate only, never used for gating.
_CHARS_PER_TOKEN = 2.5

CHECKPOINT_STRIDE = 15


# ---------------------------------------------------------------------------
# session loading


def load_session_records(
    sessions_dir: Path, trajectory_ids: Iterable[str] | None = None
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load joined session records as {trajectory_id: {session_id: record}}."""

    sessions_dir = Path(sessions_dir)
    wanted = set(trajectory_ids) if trajectory_ids else None
    by_traj: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(sessions_dir.glob("sessions_*.jsonl")):
        traj_id = path.stem.removeprefix("sessions_")
        if wanted is not None and traj_id not in wanted:
            continue
        records: dict[str, dict[str, Any]] = {}
        for row in read_jsonl(path):
            sid = row.get("session_id")
            if not sid:
                raise ValueError(f"{path}: session record without session_id")
            if sid in records:
                raise ValueError(f"{path}: duplicate session_id {sid}")
            records[sid] = row
        if records:
            by_traj[traj_id] = records
    if wanted is not None:
        missing = wanted - set(by_traj)
        if missing:
            raise ValueError(
                f"missing sessions files under {sessions_dir} for: {sorted(missing)}"
            )
    return by_traj


# ---------------------------------------------------------------------------
# taxonomy


def build_public_taxonomy(templates: dict[str, Any]) -> list[dict[str, str]]:
    """Answer-space taxonomy: only event_id + short neutral Korean label."""

    rows = []
    for event_id in sorted(templates):
        template = templates[event_id]
        active = getattr(template, "active", None)
        if active is None and isinstance(template, dict):
            active = template.get("active", True)
        if not active:
            continue
        label = getattr(template, "label_ko", None)
        if label is None and isinstance(template, dict):
            label = template.get("label_ko", "")
        rows.append({"event_id": event_id, "label_ko": str(label or "")})
    return rows


def taxonomy_hash(taxonomy: list[dict[str, str]]) -> str:
    payload = json.dumps(taxonomy, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# gold ledger


def _classify_evidence(
    evidence_sessions: list[str],
    sessions: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    core: list[str] = []
    supporting: list[str] = []
    other: list[str] = []
    for sid in sorted(set(evidence_sessions), key=session_number):
        record = sessions.get(sid)
        stype = (record or {}).get("session_type", "")
        if stype in CORE_EVIDENCE_TYPES:
            core.append(sid)
        elif stype in SUPPORTING_TYPES:
            supporting.append(sid)
        else:
            other.append(sid)
    return core, supporting, other


def _status_anchor(
    status: str,
    core: list[str],
    sessions: dict[str, dict[str, Any]],
) -> str:
    def of_type(session_type: str) -> list[str]:
        return [
            sid for sid in core if sessions[sid].get("session_type") == session_type
        ]

    def establishing(session_type: str, status_after: str) -> list[str]:
        return [
            sid
            for sid in of_type(session_type)
            if sessions[sid].get("event_status_after_session") == status_after
        ]

    if not core:
        raise ValueError("cannot anchor an event with no visible core evidence")
    if status == "occurred":
        candidates = establishing("occurred_evidence", "occurred") or of_type(
            "occurred_evidence"
        )
        return candidates[0] if candidates else core[-1]
    if status == "cancelled":
        candidates = establishing("cancellation_evidence", "cancelled") or of_type(
            "cancellation_evidence"
        )
        return candidates[0] if candidates else core[-1]
    if status == "upcoming":
        candidates = of_type("upcoming_evidence")
        return candidates[-1] if candidates else core[-1]
    if status == "weak_signal":
        candidates = of_type("weak_signal_evidence")
        return candidates[-1] if candidates else core[-1]
    raise ValueError(f"unsupported visible event status: {status!r}")


def build_gold_ledger(
    gold_life_events: list[dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
) -> tuple[list[RQ1GoldEventInstance], list[RQ1GoldEventInstance]]:
    """Derive (full_observed_ledger, occurred_trajectory) for one checkpoint."""

    ledger: list[RQ1GoldEventInstance] = []
    for event in gold_life_events:
        evidence = list(event.get("evidence_sessions") or [])
        core, supporting, other = _classify_evidence(evidence, sessions)
        if other:
            raise ValueError(
                "gold evidence contains non-evidence session types for "
                f"{event.get('event_instance_id')}: {other}"
            )
        if not core:
            # An instance visible only through supporting sessions cannot be
            # anchored; the corpus never produces this (audited), so treat it
            # as a data defect rather than silently inventing gold.
            raise ValueError(
                f"no visible core evidence for {event.get('event_instance_id')}"
            )
        status = event.get("event_status", "")
        ledger.append(
            RQ1GoldEventInstance(
                event_instance_id=event.get("event_instance_id", ""),
                event_id=event.get("event_id", ""),
                life_event_label=event.get("life_event_label", ""),
                event_status=status,
                first_evidence_session=core[0],
                status_anchor_session=_status_anchor(status, core, sessions),
                core_evidence_sessions=core,
                supporting_sessions=supporting,
                first_recoverable_session=event.get("first_recoverable_session"),
            )
        )
    ledger.sort(
        key=lambda e: (session_number(e.first_evidence_session), e.event_instance_id)
    )
    occurred = [e for e in ledger if e.event_status == "occurred"]
    occurred.sort(
        key=lambda e: (session_number(e.status_anchor_session), e.event_instance_id)
    )
    return ledger, occurred


# ---------------------------------------------------------------------------
# natural items


def _input_char_count(
    visible: list[str], sessions: dict[str, dict[str, Any]]
) -> int:
    total = 0
    for sid in visible:
        for turn in sessions[sid].get("turns") or []:
            total += len(turn.get("text") or "")
    return total


def build_natural_item(
    prefix_record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    *,
    taxonomy_digest: str,
) -> RQ1Item:
    trajectory_id = prefix_record["trajectory_id"]
    visible = list(prefix_record.get("visible_sessions") or [])
    checkpoint = int(
        prefix_record.get("checkpoint_session_count") or len(visible)
    )
    missing = [sid for sid in visible if sid not in sessions]
    if missing:
        raise ValueError(
            f"{trajectory_id}: visible sessions missing from records: {missing[:5]}"
        )
    ledger, occurred = build_gold_ledger(
        list(prefix_record.get("gold_life_events") or []), sessions
    )
    type_counts: dict[str, int] = {}
    for sid in visible:
        stype = sessions[sid].get("session_type", "")
        type_counts[stype] = type_counts.get(stype, 0) + 1
    char_count = _input_char_count(visible, sessions)
    first_recoverable = {
        e.event_instance_id: {
            "session_id": e.first_recoverable_session,
            "checkpoint": (
                math.ceil(
                    session_number(e.first_recoverable_session) / CHECKPOINT_STRIDE
                )
                * CHECKPOINT_STRIDE
                if e.first_recoverable_session
                else None
            ),
        }
        for e in ledger
    }
    gold = RQ1ItemGold(
        full_observed_ledger=ledger,
        occurred_trajectory=occurred,
        session_id_map={sid: to_public_session_id(sid) for sid in visible},
        input_session_count=len(visible),
        input_char_count=char_count,
        input_token_estimate=math.ceil(char_count / _CHARS_PER_TOKEN),
        accumulated_hard_negative_count=type_counts.get("hard_negative", 0),
        accumulated_routine_count=type_counts.get("routine_financial", 0),
        accumulated_event_count=len(ledger),
        first_recoverable=first_recoverable,
    )
    return RQ1Item(
        item_id=f"{trajectory_id}_cp{checkpoint:03d}_rq1",
        stage=RQ1_STAGE,
        trajectory_id=trajectory_id,
        prefix_id=prefix_record.get("prefix_id", f"{trajectory_id}_pfx{checkpoint:03d}"),
        checkpoint_session_count=checkpoint,
        visible_sessions=visible,
        taxonomy_hash=taxonomy_digest,
        gold=gold,
        metadata={
            "session_type_counts": type_counts,
            "occurred_event_count": len(occurred),
        },
    )


def build_natural_items(
    prefix_records: Iterable[dict[str, Any]],
    sessions_by_traj: dict[str, dict[str, dict[str, Any]]],
    *,
    taxonomy_digest: str,
    checkpoint_stride: int = CHECKPOINT_STRIDE,
) -> list[RQ1Item]:
    items: list[RQ1Item] = []
    for record in prefix_records:
        visible = list(record.get("visible_sessions") or [])
        checkpoint = int(record.get("checkpoint_session_count") or len(visible))
        if checkpoint <= 0 or checkpoint % checkpoint_stride:
            continue
        trajectory_id = record.get("trajectory_id", "")
        sessions = sessions_by_traj.get(trajectory_id)
        if sessions is None:
            raise ValueError(f"no session records for {trajectory_id}")
        items.append(
            build_natural_item(record, sessions, taxonomy_digest=taxonomy_digest)
        )
    items.sort(key=lambda i: (i.trajectory_id, i.checkpoint_session_count))
    return items


# ---------------------------------------------------------------------------
# model-visible materialization


def visible_ids_for_condition(item: RQ1Item, condition: str) -> list[str]:
    if condition == "full_prefix":
        return list(item.visible_sessions)
    if condition == "last_15":
        return list(item.visible_sessions[-15:])
    if condition == "oracle_evidence":
        core: set[str] = set()
        for event in item.gold.full_observed_ledger:
            core.update(event.core_evidence_sessions)
        return sorted(core, key=session_number)
    raise ValueError(f"unknown condition: {condition!r}")


def apply_replacement_turns(
    records: list[dict[str, Any]],
    replacement_map: dict[str, str],
    filler_bank: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Swap the turns of replaced slots with donor filler turns.

    Only ``turns`` matter for the model-visible rendering; positional fields
    (session id, order) stay with the slot. Donor identity never reaches the
    rendering.
    """

    out: list[dict[str, Any]] = []
    for record in records:
        sid = record.get("session_id", "")
        donor_id = replacement_map.get(sid)
        if donor_id is None:
            out.append(record)
            continue
        donor = filler_bank.get(donor_id)
        if donor is None:
            raise ValueError(f"replacement donor {donor_id} not in filler bank")
        clone = dict(record)
        clone["turns"] = [dict(t) for t in donor.get("turns") or []]
        out.append(clone)
    return out


def render_sessions_block(
    records: list[dict[str, Any]],
    session_id_map: dict[str, str],
) -> str:
    """Render the model-visible session block: public id + turns only."""

    blocks: list[str] = []
    for record in records:
        sid = record.get("session_id", "")
        public_id = session_id_map.get(sid)
        if not public_id:
            raise ValueError(f"session {sid} missing from public id map")
        lines = [f"[세션 {public_id}]"]
        for turn in record.get("turns") or []:
            speaker = "고객" if turn.get("speaker") == "user" else "상담원"
            lines.append(f"{speaker}: {turn.get('text', '')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_taxonomy_block(taxonomy: list[dict[str, str]]) -> str:
    return "\n".join(f"- {row['event_id']}: {row['label_ko']}" for row in taxonomy)
