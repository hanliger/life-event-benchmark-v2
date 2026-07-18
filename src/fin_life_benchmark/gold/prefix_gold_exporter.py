"""Export prefix-level gold: for every session prefix, the gold life-event
statuses, memory updates, action decisions, and full states at that time.

Gold changes over time — an event that is weak_signal at prefix k may be
occurred at prefix k+3; update_allowed flips accordingly.
"""

from __future__ import annotations

import json
from typing import Any

from ..fsm.models import EventStatus
from ..trajectory.models import (
    GoldActionDecision,
    GoldLifeEvent,
    GoldMemoryUpdate,
    PrefixGold,
    Trajectory,
)


def _snapshot_at(
    snapshots: dict[str, Any],
    month: int,
    transition_order: int | None = None,
) -> Any:
    """Latest monthly or ``month:transition_order`` snapshot at a cursor."""
    best_key, best_cursor = None, (-1, -1)
    for key in snapshots:
        parts = str(key).split(":", 1)
        cursor = (int(parts[0]), int(parts[1]) if len(parts) == 2 else 0)
        target = (month, transition_order if transition_order is not None else 10**9)
        if best_cursor < cursor <= target:
            best_key, best_cursor = key, cursor
    return snapshots[best_key] if best_key is not None else None


def _serialize_memory_state(memory: Any) -> dict[str, Any]:
    """Compact view: path -> {value, status, historical_values}."""
    cells = memory.cells if hasattr(memory, "cells") else memory.get("cells", {})
    out: dict[str, Any] = {}
    for path, hist in cells.items():
        rows = [c.model_dump(mode="json") if hasattr(c, "model_dump") else c for c in hist]
        latest = rows[-1] if rows else None
        out[path] = {
            "value": latest.get("value") if latest else None,
            "status": latest.get("status") if latest else "unknown",
            "historical_values": [r.get("value") for r in rows[:-1] if r.get("status") == "historical" and r.get("value") is not None]
            + ([latest.get("value")] if latest and latest.get("status") == "historical" else []),
        }
    return out


_STATUS_RANK = {
    "weak_signal": 1,
    "upcoming": 2,
    "occurred": 3,
    "cancelled": 4,
}


def _status_value(status: Any) -> str:
    return getattr(status, "value", status)


def _status_rank(status: Any) -> int:
    return _STATUS_RANK.get(str(_status_value(status)), -1)


def _visible_event_status(instance: Any, linked_sessions: list[dict[str, Any]]) -> EventStatus:
    """Return the latest event status actually evidenced in visible sessions."""
    latest = sorted(linked_sessions, key=lambda s: s["session_id"])[-1]
    status = latest.get("event_status_after_session")
    try:
        return EventStatus(status)
    except ValueError:
        return instance.status_as_of(latest["month_index"])


def export_prefix_gold(
    trajectory: Trajectory,
    sessions: list[dict[str, Any]],
    checkpoint_stride: int | None = None,
) -> list[PrefixGold]:
    """Export every prefix by default, or only stride-aligned checkpoints."""
    sessions = sorted(sessions, key=lambda s: s["session_id"])
    start_age = trajectory.initial_persona_state.age
    instances = {i.event_instance_id: i for i in trajectory.life_event_instances}

    # sessions linked to each instance, in order
    sessions_by_instance: dict[str, list[dict[str, Any]]] = {}
    for s in sessions:
        if s.get("linked_event_instance_id"):
            sessions_by_instance.setdefault(s["linked_event_instance_id"], []).append(s)

    # memory updates / action impacts by month (chronological)
    updates: list[Any] = []
    impacts: list[Any] = []
    for step in trajectory.timeline_steps:
        updates.extend(step.memory_updates)
        impacts.extend(step.action_impacts)

    action_by_id = {a.action_id: a for a in trajectory.initial_standing_actions}

    def first_recoverable(instance_id: str) -> str | None:
        """First session by which the event is identifiable. For drift events
        (low single-session recoverability) this is later than the first
        evidence session."""
        linked = sessions_by_instance.get(instance_id, [])
        strong = [
            s for s in linked
            if s["session_type"] in {"occurred_evidence", "cancellation_evidence"}
        ]
        if not strong:
            return linked[-1]["session_id"] if linked else None
        s = strong[0]
        plan = s.get("plan") or {}
        if plan.get("desired_single_session_recoverability") == "low":
            # cumulative: need at least one more linked session after
            later = [x for x in linked if x["session_id"] > s["session_id"]]
            return later[0]["session_id"] if later else s["session_id"]
        return s["session_id"]

    prefixes: list[PrefixGold] = []
    prefix_sizes = list(range(1, len(sessions) + 1))
    if checkpoint_stride is not None:
        if checkpoint_stride <= 0:
            raise ValueError("checkpoint_stride must be positive")
        prefix_sizes = list(range(checkpoint_stride, len(sessions) + 1, checkpoint_stride))
    for k in prefix_sizes:
        visible = sessions[:k]
        visible_ids = [s["session_id"] for s in visible]
        cursor_session = max(
            visible,
            key=lambda session: (session["month_index"], session.get("transition_order", 0)),
        )
        month = cursor_session["month_index"]
        transition_order = int(cursor_session.get("transition_order", 0))
        age = start_age + month // 12

        # gold life events: instances with >=1 evidence session in prefix
        gold_events: list[GoldLifeEvent] = []
        visible_instance_status: dict[str, EventStatus] = {}
        for instance_id, linked in sessions_by_instance.items():
            in_prefix = [s for s in linked if s["session_id"] in visible_ids]
            if not in_prefix:
                continue
            instance = instances[instance_id]
            status = _visible_event_status(instance, in_prefix)
            visible_instance_status[instance_id] = status
            evidence_turns = [
                f"{s['session_id']}:{c['turn_index']}"
                for s in in_prefix
                for c in (s.get("cue_annotations") or [])
            ]
            gold_events.append(
                GoldLifeEvent(
                    event_instance_id=instance_id,
                    event_id=instance.event_id,
                    life_event_label=instance.label_ko,
                    event_status=status.value,
                    occurred=status == EventStatus.OCCURRED,
                    update_allowed=status == EventStatus.OCCURRED,
                    first_recoverable_session=first_recoverable(instance_id),
                    evidence_sessions=[s["session_id"] for s in in_prefix],
                    evidence_turns=evidence_turns,
                )
            )

        # gold memory updates: updates whose source event/status has evidence in prefix
        visible_instances = {e.event_instance_id for e in gold_events}
        gold_updates = [
            GoldMemoryUpdate(
                path=u.path,
                operation=u.operation.value,
                old_value=u.old_value,
                new_value=u.new_value,
                evidence_turns=[
                    f"{s['session_id']}:{c['turn_index']}"
                    for s in visible
                    for c in (s.get("cue_annotations") or [])
                    if c.get("linked_memory_path") == u.path
                ],
            )
            for u in updates
            if u.month_index is not None
            and u.month_index <= month
            and u.source_event_instance_id in visible_instances
            and _status_rank(u.event_status) <= _status_rank(visible_instance_status.get(u.source_event_instance_id))
        ]

        gold_decisions = [
            GoldActionDecision(
                action_id=i.action_id,
                impact_type=i.impact_type,
                funds_movement=i.funds_movement,
                risk=i.risk,
                expected_decision=i.expected_decision.value,
                must_not_execute=i.must_not_execute,
                source_event_instance_id=i.source_event_instance_id,
            )
            for i in impacts
            if i.month_index is not None
            and i.month_index <= month
            and i.source_event_instance_id in visible_instances
        ]

        memory_snap = _snapshot_at(
            trajectory.ordered_memory_snapshots or trajectory.memory_snapshots,
            month,
            transition_order,
        )
        action_snap = _snapshot_at(
            trajectory.ordered_action_snapshots or trajectory.action_snapshots,
            month,
            transition_order,
        ) or []

        prefixes.append(
            PrefixGold(
                prefix_id=f"{trajectory.trajectory_id}_pfx{k:03d}",
                trajectory_id=trajectory.trajectory_id,
                visible_sessions=visible_ids,
                time={"age": age, "month_index": month, "transition_order": transition_order},
                checkpoint_session_count=k,
                occurred_event_count=sum(event.occurred for event in gold_events),
                gold_life_events=gold_events,
                gold_memory_updates=gold_updates,
                gold_action_decisions=gold_decisions,
                gold_full_memory_state=_serialize_memory_state(memory_snap) if memory_snap else {},
                gold_full_action_state=[
                    a.model_dump(mode="json") if hasattr(a, "model_dump") else a for a in action_snap
                ],
            )
        )
    _ = action_by_id  # (kept for future per-action gold enrichment)

    # Storage optimization: blank the (large) gold payload on prefixes whose
    # entire payload repeats the previous prefix. read_prefix_gold() carries it
    # forward on load. ~96% of prefixes sit between events and repeat.
    prev_payload: str | None = None
    for prefix in prefixes:
        payload = json.dumps(
            [
                [e.model_dump(mode="json") for e in prefix.gold_life_events],
                [u.model_dump(mode="json") for u in prefix.gold_memory_updates],
                [d.model_dump(mode="json") for d in prefix.gold_action_decisions],
                prefix.gold_full_memory_state,
                prefix.gold_full_action_state,
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        if payload == prev_payload:
            prefix.repeats_previous = True
            prefix.gold_life_events = []
            prefix.gold_memory_updates = []
            prefix.gold_action_decisions = []
            prefix.gold_full_memory_state = {}
            prefix.gold_full_action_state = []
        prev_payload = payload
    return prefixes
