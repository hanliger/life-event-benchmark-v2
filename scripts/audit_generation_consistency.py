#!/usr/bin/env python
"""Audit generated trajectories/sessions for common consistency failures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import read_jsonl
from fin_life_benchmark.trajectory.models import Trajectory
from fin_life_benchmark.validation.audits import write_report
from fin_life_benchmark.validation.dialogue_validator import DialogueValidator, summarize_report


def _latest_status(trajectory: Trajectory, path: str) -> str | None:
    cell = trajectory.initial_financial_memory_state.latest(path)
    return cell.status.value if cell is not None else None


def _initial_consistency_issues(trajectory: Trajectory) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    persona = trajectory.persona
    employment = persona.occupation_state.employment_status
    residence = persona.housing.residence_status
    actions = trajectory.initial_standing_actions

    if employment != "employed" and _latest_status(trajectory, "employment.salary_day") == "current":
        issues.append({"code": "salary_day_for_non_employed", "employment_status": employment})
    if residence != "wolse" and _latest_status(trajectory, "housing.rent_amount") == "current":
        issues.append({"code": "rent_amount_for_non_wolse", "residence_status": residence})
    if any(a.type == "salary_linked_savings" for a in actions) and employment != "employed":
        issues.append({"code": "salary_action_for_non_employed", "employment_status": employment})
    if any(a.type == "rent_autopay" for a in actions) and residence != "wolse":
        issues.append({"code": "rent_action_for_non_wolse", "residence_status": residence})
    if any(a.type == "loan_repayment" for a in actions) and not persona.financial_profile.has_loan:
        issues.append({"code": "loan_action_without_loan"})
    return issues


def _delta_issues(trajectory: Trajectory) -> list[dict[str, Any]]:
    """Report structurally unsafe deltas, not benign confirmations.

    Equal old/new values can confirm a pending cell, and
    needs_verification is explicitly allowed to create an unknown cell. The
    DeltaEngine already removes true no-ops before persisting updates.
    """
    issues: list[dict[str, Any]] = []
    for step in trajectory.timeline_steps:
        for update in step.memory_updates:
            if update.source_event_instance_id is None:
                issues.append(
                    {
                        "code": "orphan_memory_update",
                        "month_index": step.month_index,
                        "path": update.path,
                        "operation": update.operation.value,
                        "source_event_instance_id": update.source_event_instance_id,
                    }
                )
    return issues


def _memory_history_issues(trajectory: Trajectory) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    snapshots = {
        **trajectory.memory_snapshots,
        **trajectory.ordered_memory_snapshots,
    }
    for cursor, memory in snapshots.items():
        for path, history in memory.cells.items():
            counts = {
                status: sum(cell.status.value == status for cell in history)
                for status in ("current", "pending")
            }
            if counts["current"] > 1 or counts["pending"] > 1:
                issues.append(
                    {
                        "code": "duplicate_live_memory_cell",
                        "cursor": cursor,
                        "path": path,
                        **counts,
                    }
                )
            for cell in history:
                if cell.status.value == "historical" and cell.valid_until is None:
                    issues.append(
                        {
                            "code": "historical_cell_without_valid_until",
                            "cursor": cursor,
                            "path": path,
                        }
                    )
    return issues


def _event_utility_issues(trajectory: Trajectory) -> list[dict[str, Any]]:
    updates_by_source: dict[str, int] = {}
    impacts_by_source: dict[str, int] = {}
    for step in trajectory.timeline_steps:
        for update in step.memory_updates:
            updates_by_source[update.source_event_instance_id or ""] = updates_by_source.get(
                update.source_event_instance_id or "", 0
            ) + 1
        for impact in step.action_impacts:
            impacts_by_source[impact.source_event_instance_id or ""] = impacts_by_source.get(
                impact.source_event_instance_id or "", 0
            ) + 1
    return [
        {
            "code": "occurred_event_without_financial_delta",
            "event_instance_id": instance.event_instance_id,
            "event_id": instance.event_id,
            "month_index": instance.occurred_month,
        }
        for instance in trajectory.life_event_instances
        if instance.occurred_month is not None
        and updates_by_source.get(instance.event_instance_id, 0) == 0
        and impacts_by_source.get(instance.event_instance_id, 0) == 0
    ]


def _dialogue_grounding(
    trajectories: list[Trajectory], sessions_dir: Path | None
) -> dict[str, Any]:
    if sessions_dir is None or not sessions_dir.exists():
        return {"occurred_memory_updates": 0, "grounded_updates": 0, "grounding_rate": None, "issues": []}
    sessions: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    annotation_surface_issues: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.glob("sessions_*.jsonl")):
        for session in read_jsonl(path):
            key = (
                session.get("trajectory_id") or "",
                session.get("linked_event_instance_id") or "",
                int(session.get("month_index", -1)),
            )
            sessions.setdefault(key, []).append(session)
            for annotation in session.get("cue_annotations") or []:
                if annotation.get("cue_type") != "memory_fact":
                    continue
                turn_index = int(annotation.get("turn_index", -1))
                turns = session.get("turns") or []
                cue_text = annotation.get("cue_text") or ""
                if turn_index < 0 or turn_index >= len(turns) or cue_text not in turns[turn_index].get("text", ""):
                    annotation_surface_issues.append(
                        {
                            "code": "memory_fact_annotation_not_visible",
                            "trajectory_id": key[0],
                            "session_id": session.get("session_id"),
                            "path": annotation.get("linked_memory_path"),
                        }
                    )

    total = 0
    grounded = 0
    issues = list(annotation_surface_issues)
    for trajectory in trajectories:
        for step in trajectory.timeline_steps:
            for update in step.memory_updates:
                if update.event_status != "occurred":
                    continue
                total += 1
                candidates = sessions.get(
                    (
                        trajectory.trajectory_id,
                        update.source_event_instance_id or "",
                        int(update.month_index if update.month_index is not None else step.month_index),
                    ),
                    [],
                )
                match = any(
                    annotation.get("cue_type") == "memory_fact"
                    and annotation.get("linked_memory_path") == update.path
                    and annotation.get("linked_memory_operation") == update.operation.value
                    and annotation.get("linked_memory_value") == update.new_value
                    for session in candidates
                    for annotation in (session.get("cue_annotations") or [])
                )
                if match:
                    grounded += 1
                else:
                    issues.append(
                        {
                            "code": "occurred_memory_update_not_dialogue_grounded",
                            "trajectory_id": trajectory.trajectory_id,
                            "event_instance_id": update.source_event_instance_id,
                            "month_index": update.month_index,
                            "path": update.path,
                            "operation": update.operation.value,
                        }
                    )
    return {
        "occurred_memory_updates": total,
        "grounded_updates": grounded,
        "grounding_rate": round(grounded / total, 4) if total else None,
        "issues": issues,
    }


def _is_active_valid(action: Any) -> bool:
    return action.status.value == "active" and action.validity_status == "valid"


def _memory_status(memory: Any, path: str) -> str | None:
    cell = memory.latest(path)
    return cell.status.value if cell is not None else None


def _snapshot_invariant_issues(trajectory: Trajectory) -> list[dict[str, Any]]:
    """Cross-check life state, current memory cells, and still-valid actions.

    This intentionally only flags active+valid actions. A funds-moving action
    that is active but already marked needs_review is an intended benchmark
    state: the assistant must ask before using it.
    """
    issues: list[dict[str, Any]] = []
    snapshot_keys = {"0"}
    snapshot_keys.update(trajectory.state_snapshots.keys())
    snapshot_keys.update(trajectory.memory_snapshots.keys())
    snapshot_keys.update(trajectory.action_snapshots.keys())

    def add(month_key: str, code: str, **extra: Any) -> None:
        issues.append({"code": code, "month_index": int(month_key), **extra})

    for key in sorted(snapshot_keys, key=lambda value: int(value)):
        state_snapshot = trajectory.state_snapshots.get(key) or trajectory.initial_persona_state
        memory = trajectory.memory_snapshots.get(key) or trajectory.initial_financial_memory_state
        actions = trajectory.action_snapshots.get(key) or trajectory.initial_standing_actions
        state = state_snapshot.life_state

        if len(state.children_ages) >= 5:
            add(key, "children_count_at_or_above_five", children_count=len(state.children_ages))
        if state.dependents_count >= 5:
            add(key, "dependents_count_at_or_above_five", dependents_count=state.dependents_count)
        if state.employment_status != "employed" and _memory_status(memory, "employment.salary_day") == "current":
            add(key, "current_salary_memory_for_non_employed", employment_status=state.employment_status)
        if state.residence_status != "wolse" and _memory_status(memory, "housing.rent_amount") == "current":
            add(key, "current_rent_memory_for_non_wolse", residence_status=state.residence_status)
        if state.marital_status != "married" and _memory_status(memory, "household.spouse_or_partner") == "current":
            add(key, "current_spouse_memory_for_non_married", marital_status=state.marital_status)

        for action in actions:
            if not _is_active_valid(action):
                continue
            if action.type == "salary_linked_savings" and state.employment_status != "employed":
                add(key, "valid_salary_action_for_non_employed", action_id=action.action_id, employment_status=state.employment_status)
            elif action.type == "rent_autopay" and state.residence_status != "wolse":
                add(key, "valid_rent_action_for_non_wolse", action_id=action.action_id, residence_status=state.residence_status)
            elif action.type == "spouse_living_expense_transfer" and state.marital_status != "married":
                add(key, "valid_spouse_action_for_non_married", action_id=action.action_id, marital_status=state.marital_status)
            elif action.type == "parent_support_transfer" and state.dependents_count <= 0:
                add(key, "valid_parent_support_action_without_dependents", action_id=action.action_id)
            elif action.type == "child_education_saving" and not any(age < 19 for age in state.children_ages):
                add(key, "valid_child_education_action_without_minor_children", action_id=action.action_id)
            elif action.type == "business_expense_autopay" and state.employment_status != "self_employed":
                add(key, "valid_business_action_for_non_self_employed", action_id=action.action_id, employment_status=state.employment_status)
    return issues


def _audit_dialogues(sessions_dir: Path | None) -> dict[str, Any]:
    if sessions_dir is None or not sessions_dir.exists():
        return {"summary": {"total_sessions": 0, "sessions_with_violations": 0, "pass_rate": None}, "results": []}

    validator = DialogueValidator(load_life_event_templates())
    results: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.glob("sessions_*.jsonl")):
        for session in read_jsonl(path):
            results.append(
                {
                    "trajectory_id": session.get("trajectory_id"),
                    "session_id": session.get("session_id"),
                    "violations": validator.validate_session(session),
                }
            )
    return {"summary": summarize_report(results), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--sessions-dir", default=None)
    parser.add_argument("--output-dir", default="data/generated/quality_reports")
    args = parser.parse_args()

    trajectory_reports = []
    trajectories: list[Trajectory] = []
    for path in sorted(Path(args.trajectories_dir).glob("traj_*.json")):
        trajectory = Trajectory.model_validate(json.loads(path.read_text(encoding="utf-8")))
        trajectories.append(trajectory)
        snapshot_invariant_issues = _snapshot_invariant_issues(trajectory)
        memory_history_issues = _memory_history_issues(trajectory)
        event_utility_issues = _event_utility_issues(trajectory)
        trajectory_reports.append(
            {
                "trajectory_id": trajectory.trajectory_id,
                "initial_consistency_issues": _initial_consistency_issues(trajectory),
                "delta_issues": _delta_issues(trajectory),
                "snapshot_invariant_issues": snapshot_invariant_issues,
                "memory_history_issues": memory_history_issues,
                "event_utility_issues": event_utility_issues,
            }
        )

    dialogue = _audit_dialogues(Path(args.sessions_dir) if args.sessions_dir else None)
    grounding = _dialogue_grounding(
        trajectories, Path(args.sessions_dir) if args.sessions_dir else None
    )
    needs_verification_cells = sum(
        cell.status.value == "needs_verification"
        for trajectory in trajectories
        for memory in trajectory.ordered_memory_snapshots.values()
        for history in memory.cells.values()
        for cell in history
    )
    report = {
        "trajectories": len(trajectory_reports),
        "trajectories_with_initial_issues": sum(bool(r["initial_consistency_issues"]) for r in trajectory_reports),
        "trajectories_with_delta_issues": sum(bool(r["delta_issues"]) for r in trajectory_reports),
        "trajectories_with_snapshot_invariant_issues": sum(bool(r["snapshot_invariant_issues"]) for r in trajectory_reports),
        "trajectories_with_memory_history_issues": sum(bool(r["memory_history_issues"]) for r in trajectory_reports),
        "occurred_events_without_financial_delta": sum(len(r["event_utility_issues"]) for r in trajectory_reports),
        "needs_verification_cells": needs_verification_cells,
        "dialogue_summary": dialogue["summary"],
        "dialogue_grounding": grounding,
        "trajectory_results": trajectory_reports,
        "dialogue_results": dialogue["results"],
    }

    output_dir = Path(args.output_dir)
    write_report(
        report,
        output_dir / "generation_consistency_report.json",
        "Generation Consistency Report",
        output_dir / "generation_consistency_report.md",
    )
    print(f"reports -> {output_dir}/generation_consistency_report.{{json,md}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
