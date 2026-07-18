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
    for path in sorted(Path(args.trajectories_dir).glob("traj_*.json")):
        trajectory = Trajectory.model_validate(json.loads(path.read_text(encoding="utf-8")))
        snapshot_invariant_issues = _snapshot_invariant_issues(trajectory)
        trajectory_reports.append(
            {
                "trajectory_id": trajectory.trajectory_id,
                "initial_consistency_issues": _initial_consistency_issues(trajectory),
                "delta_issues": _delta_issues(trajectory),
                "snapshot_invariant_issues": snapshot_invariant_issues,
            }
        )

    dialogue = _audit_dialogues(Path(args.sessions_dir) if args.sessions_dir else None)
    report = {
        "trajectories": len(trajectory_reports),
        "trajectories_with_initial_issues": sum(bool(r["initial_consistency_issues"]) for r in trajectory_reports),
        "trajectories_with_delta_issues": sum(bool(r["delta_issues"]) for r in trajectory_reports),
        "trajectories_with_snapshot_invariant_issues": sum(bool(r["snapshot_invariant_issues"]) for r in trajectory_reports),
        "dialogue_summary": dialogue["summary"],
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
