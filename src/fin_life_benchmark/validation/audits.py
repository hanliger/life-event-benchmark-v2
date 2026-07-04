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
    solvable = [
        s for s in evidence
        if (s.get("plan") or {}).get("desired_single_session_recoverability") == "high"
    ]
    by_type = Counter(s["session_type"] for s in sessions)
    return {
        "total_sessions": len(sessions),
        "evidence_sessions": len(evidence),
        "single_session_solvable": len(solvable),
        "single_session_solvable_rate": round(len(solvable) / len(evidence), 4) if evidence else None,
        "sessions_by_type": dict(by_type),
    }


def audit_full_prefix_recoverability(prefixes: list[dict[str, Any]]) -> dict[str, Any]:
    events_seen: dict[str, dict[str, Any]] = {}
    for prefix in prefixes:
        for e in prefix["gold_life_events"]:
            events_seen[e["event_instance_id"]] = e
    recoverable = [e for e in events_seen.values() if e.get("first_recoverable_session")]
    occurred = [e for e in events_seen.values() if e["event_status"] == "occurred"]
    return {
        "distinct_gold_events": len(events_seen),
        "occurred_events": len(occurred),
        "cumulatively_recoverable": len(recoverable),
        "cumulative_recoverability_rate": round(len(recoverable) / len(events_seen), 4) if events_seen else None,
        "events_by_status": dict(Counter(e["event_status"] for e in events_seen.values())),
        "events_by_label": dict(Counter(e["life_event_label"] for e in events_seen.values())),
    }


def audit_stale_distractors(items: list[dict[str, Any]], prefixes: list[dict[str, Any]]) -> dict[str, Any]:
    mcq = [i for i in items if i.get("stage") == "stage3_action_mcq"]
    with_stale = [i for i in mcq if (i.get("metadata") or {}).get("has_stale_distractor")]
    # memory-level availability: prefixes where at least one path has historical values
    prefix_with_hist = [
        p for p in prefixes
        if any((v.get("historical_values") or []) for v in p["gold_full_memory_state"].values())
    ]
    return {
        "mcq_items": len(mcq),
        "mcq_with_stale_distractor": len(with_stale),
        "stale_distractor_rate": round(len(with_stale) / len(mcq), 4) if mcq else None,
        "prefixes_with_historical_values": len(prefix_with_hist),
        "prefixes_total": len(prefixes),
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
