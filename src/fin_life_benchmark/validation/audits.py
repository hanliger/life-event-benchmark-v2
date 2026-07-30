"""Audit computations shared by the audit scripts."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..fsm.registry import load_life_event_templates
from ..trajectory.models import LifeState
from ..fsm.life_state_machine import LifeStateMachine


def audit_single_session_recoverability(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = [s for s in sessions if s.get("linked_event_instance_id")]

    def visible_annotations(session: dict[str, Any]) -> list[dict[str, Any]]:
        turns = session.get("turns") or []
        return [
            cue for cue in (session.get("cue_annotations") or [])
            if cue.get("cue_type") != "memory_fact"
            and 0 <= int(cue.get("turn_index", -1)) < len(turns)
            and turns[int(cue["turn_index"])].get("speaker") == "user"
            and (cue.get("cue_text") or "") in turns[int(cue["turn_index"])].get("text", "")
        ]

    status_markers = {
        "weak_signal": "확정된 건 아닌데",
        "upcoming": "다음 달",
        "occurred": "이번에",
        "cancelled": "없던 일이 됐어요",
    }
    grounded = []
    for session in evidence:
        visible = " ".join(turn.get("text", "") for turn in session.get("turns") or [])
        status = session.get("event_status_after_session")
        marker = status_markers.get(status)
        if visible_annotations(session) and (marker is None or marker in visible):
            grounded.append(session)
    by_type = Counter(s["session_type"] for s in sessions)
    return {
        "total_sessions": len(sessions),
        "evidence_sessions": len(evidence),
        "single_session_dialogue_grounded": len(grounded),
        "single_session_dialogue_grounded_rate": round(len(grounded) / len(evidence), 4) if evidence else None,
        "sessions_by_type": dict(by_type),
    }


def audit_full_prefix_recoverability(
    prefixes: list[dict[str, Any]], sessions: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    events_seen: dict[str, dict[str, Any]] = {}
    for prefix in prefixes:
        for e in prefix["gold_life_events"]:
            events_seen[e["event_instance_id"]] = e
    declared_recoverable = [e for e in events_seen.values() if e.get("first_recoverable_session")]
    grounded_event_ids: set[str] = set()
    for session in sessions or []:
        event_instance_id = session.get("linked_event_instance_id")
        turns = session.get("turns") or []
        if not event_instance_id:
            continue
        if any(
            cue.get("cue_type") != "memory_fact"
            and 0 <= int(cue.get("turn_index", -1)) < len(turns)
            and turns[int(cue["turn_index"])].get("speaker") == "user"
            and (cue.get("cue_text") or "") in turns[int(cue["turn_index"])].get("text", "")
            for cue in (session.get("cue_annotations") or [])
        ):
            grounded_event_ids.add(event_instance_id)
    recoverable = [e for key, e in events_seen.items() if key in grounded_event_ids] if sessions is not None else declared_recoverable
    occurred = [e for e in events_seen.values() if e["event_status"] == "occurred"]
    return {
        "distinct_gold_events": len(events_seen),
        "occurred_events": len(occurred),
        "cumulatively_recoverable": len(recoverable),
        "cumulative_recoverability_rate": round(len(recoverable) / len(events_seen), 4) if events_seen else None,
        "gold_declared_recoverable": len(declared_recoverable),
        "recoverability_basis": "visible_dialogue_annotations" if sessions is not None else "gold_declaration",
        "events_by_status": dict(Counter(e["event_status"] for e in events_seen.values())),
        "events_by_label": dict(Counter(e["life_event_label"] for e in events_seen.values())),
    }


def audit_life_stage_constraints(trajectories: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay each trajectory's occurred events against guards; count violations."""
    templates = load_life_event_templates()
    fsm = LifeStateMachine(templates)
    from ..fsm.event_lifecycle import apply_occurred_to_life_state

    violations: list[dict[str, Any]] = []
    checked = 0
    for traj in trajectories:
        persona_state = traj["initial_persona_state"]
        state = LifeState.model_validate(persona_state["life_state"])
        start_age = persona_state["age"]
        occurred = sorted(
            (i for i in traj["life_event_instances"] if i.get("occurred_month") is not None),
            key=lambda i: i["occurred_month"],
        )
        sim_year = 0
        for instance in occurred:
            month = instance["occurred_month"]
            while sim_year < month // 12:
                state.tick_year()
                sim_year += 1
            age = start_age + month // 12
            template = templates.get(instance["event_id"])
            checked += 1
            if template is None:
                violations.append({"trajectory": traj["trajectory_id"], "event": instance["event_id"], "reason": "unknown template"})
                continue
            # age + state guards only (cooldown/actives already consumed at start time)
            ok = template.age_guard.min_age <= age <= template.age_guard.max_age
            reason = None if ok else f"age {age} outside guard"
            if ok:
                for field, allowed in template.state_guards.required.items():
                    if state.guard_value(field) not in allowed:
                        ok, reason = False, f"required {field}={state.guard_value(field)} not in {allowed}"
                        break
            if ok:
                for field, blocked in template.state_guards.forbidden.items():
                    if state.guard_value(field) in blocked:
                        ok, reason = False, f"forbidden {field}={state.guard_value(field)}"
                        break
            if not ok:
                violations.append({
                    "trajectory": traj["trajectory_id"],
                    "event": instance["event_instance_id"],
                    "event_id": instance["event_id"],
                    "month": month,
                    "reason": reason,
                })
            apply_occurred_to_life_state(instance["event_id"], state, instance.get("params") or {})
    _ = fsm
    return {
        "trajectories": len(trajectories),
        "occurred_events_checked": checked,
        "invalid_life_stage_transitions": len(violations),
        "violations": violations,
    }


def write_report(report: dict[str, Any], json_path: Path, md_title: str, md_path: Path | None = None) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if md_path is not None:
        lines = [f"# {md_title}", ""]
        for key, value in report.items():
            if isinstance(value, dict):
                lines.append(f"## {key}")
                for k, v in value.items():
                    lines.append(f"- {k}: {v}")
                lines.append("")
            elif isinstance(value, list):
                lines.append(f"- {key}: {len(value)} entries (see JSON)")
            else:
                lines.append(f"- {key}: {value}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
