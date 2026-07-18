"""Export prefix-level gold: for every session prefix, the gold life-event
statuses, memory updates, action decisions, and full states at that time.

Gold changes over time — an event that is weak_signal at prefix k may be
occurred at prefix k+3; update_allowed flips accordingly.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from ..fsm.models import EventStatus
from ..memory.models import MemoryOperation, MemoryUpdate
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


def serialize_memory_state(memory: Any) -> dict[str, Any]:
    """Compact effective view with a separately represented live proposal."""
    from ..memory.models import FinancialMemoryState

    state = memory if isinstance(memory, FinancialMemoryState) else FinancialMemoryState.model_validate(memory)
    cells = state.cells
    out: dict[str, Any] = {}
    for path, hist in cells.items():
        effective = state.effective(path)
        pending = state.pending(path)
        out[path] = {
            "value": effective.value if effective else None,
            "status": effective.status.value if effective else "unknown",
            "source_event_instance_id": effective.source_event_instance_id if effective else None,
            "historical_values": [
                cell.value
                for cell in hist
                if cell.status.value == "historical" and cell.value is not None
            ],
            "pending_proposal": (
                {
                    "value": pending.value,
                    "valid_from": pending.valid_from,
                    "source_event_instance_id": pending.source_event_instance_id,
                }
                if pending is not None
                else None
            ),
        }
    return out


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

    # Stored action impacts are replayed only after their source event becomes
    # visible. Memory state is replayed from dialogue-grounded annotations.
    impacts: list[Any] = []
    for step in trajectory.timeline_steps:
        impacts.extend(step.action_impacts)
    impacts_by_source: dict[str, list[Any]] = {}
    for impact in impacts:
        impacts_by_source.setdefault(impact.source_event_instance_id or "", []).append(impact)

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
    prefix_size_set = set(prefix_sizes)

    visible_memory = copy.deepcopy(trajectory.initial_financial_memory_state)
    visible_actions = copy.deepcopy(trajectory.initial_standing_actions)
    actions_by_id = {action.action_id: action for action in visible_actions}
    applied_updates: list[MemoryUpdate] = []
    applied_update_by_key: dict[tuple[str, int, int, str, str], MemoryUpdate] = {}
    visible_impacts: list[Any] = []
    applied_impact_keys: set[tuple[str, str, int | None]] = set()

    for k, current_session in enumerate(sessions, start=1):
        source = current_session.get("linked_event_instance_id") or ""
        for cue in current_session.get("cue_annotations") or []:
            if cue.get("cue_type") != "memory_fact":
                continue
            path = cue.get("linked_memory_path")
            operation = cue.get("linked_memory_operation")
            if not path or not operation:
                continue
            key = (
                source,
                int(current_session["month_index"]),
                int(current_session.get("transition_order", 0)),
                path,
                operation,
            )
            evidence_turn = f"{current_session['session_id']}:{cue['turn_index']}"
            if key in applied_update_by_key:
                existing = applied_update_by_key[key]
                if evidence_turn not in existing.evidence_turns:
                    existing.evidence_turns.append(evidence_turn)
                continue
            update = MemoryUpdate(
                path=path,
                operation=MemoryOperation(operation),
                new_value=copy.deepcopy(cue.get("linked_memory_value")),
                month_index=int(current_session["month_index"]),
                source_event_instance_id=source or None,
                event_status=current_session.get("event_status_after_session"),
                evidence_turns=[evidence_turn],
            )
            visible_memory.apply(update)
            applied_updates.append(update)
            applied_update_by_key[key] = update

        if source and current_session.get("event_status_after_session") == "occurred":
            for impact in impacts_by_source.get(source, []):
                impact_key = (source, impact.action_id, impact.month_index)
                if impact_key in applied_impact_keys:
                    continue
                applied_impact_keys.add(impact_key)
                visible_impacts.append(impact)
                action = actions_by_id.get(impact.action_id)
                if action is not None:
                    action.validity_status = "needs_review"
                    action.snapshot(
                        int(current_session["month_index"]),
                        f"visible impact:{impact.impact_type} from {source}",
                    )

        if k not in prefix_size_set:
            continue
        visible = sessions[:k]
        visible_ids = [s["session_id"] for s in visible]
        month = int(current_session["month_index"])
        transition_order = int(current_session.get("transition_order", 0))
        age = int(current_session.get("age", start_age + month // 12))

        # gold life events: instances with >=1 evidence session in prefix
        gold_events: list[GoldLifeEvent] = []
        for instance_id, linked in sessions_by_instance.items():
            in_prefix = [s for s in linked if s["session_id"] in visible_ids]
            if not in_prefix:
                continue
            instance = instances[instance_id]
            status = _visible_event_status(instance, in_prefix)
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

        # Only dialogue-grounded updates are part of the prefix state/gold.
        gold_updates = [
            GoldMemoryUpdate(
                path=u.path,
                operation=u.operation.value,
                old_value=u.old_value,
                new_value=u.new_value,
                source_event_instance_id=u.source_event_instance_id,
                evidence_turns=list(u.evidence_turns),
            )
            for u in applied_updates
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
            for i in visible_impacts
        ]

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
                gold_full_memory_state=serialize_memory_state(visible_memory),
                gold_full_action_state=[
                    action.model_dump(mode="json") for action in visible_actions
                ],
            )
        )

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
