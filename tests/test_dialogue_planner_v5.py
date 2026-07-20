"""Dialogue planner v5 registry, grounding, and controlled-layout regression tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.trajectory.models import Trajectory
from fin_life_benchmark.validation.dialogue_plan_validator import DialoguePlanValidator


@pytest.fixture(scope="module")
def planner_bundle():
    paths = RepoPaths.default()
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, load_locale("ko_KR", paths), paths)
    validator = DialoguePlanValidator(templates, paths)
    return paths, templates, planner, validator


def _trajectory(path: Path) -> Trajectory:
    return Trajectory.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_registry_covers_every_active_event_status(planner_bundle):
    _, templates, planner, _ = planner_bundle
    for event_id, template in templates.items():
        if not template.active:
            continue
        for status, allowed_fa in template.mapped_actions_by_status.items():
            if not allowed_fa:
                continue
            candidates = (planner.task_registry.get(event_id) or {}).get(status) or []
            assert candidates, f"missing {event_id}+{status}"
            assert all(candidate["fa_code"] in allowed_fa for candidate in candidates)


@pytest.mark.parametrize(
    ("event_id", "status", "forbidden_task"),
    [
        ("career_employment", "upcoming", "주소 변경"),
        ("career_reinstatement", "occurred", "주소 변경"),
        ("relationship_marriage", "weak_signal", "외화통장"),
        ("relationship_childbirth", "occurred", "월세 정기이체"),
        ("relationship_childbirth", "occurred", "관리비 자동납부"),
        ("relationship_dependent_end", "occurred", "장례비 이체"),
        ("education_self_program_start", "occurred", "병원비 이체"),
        ("education_child_stage_entry", "upcoming", "환율 알림"),
        ("relationship_adoption", "cancelled", "환율 알림"),
    ],
)
def test_observed_bad_mappings_are_impossible_by_registry(
    planner_bundle, event_id, status, forbidden_task
):
    _, _, planner, _ = planner_bundle
    tasks = [
        item["visible_task_ko"]
        for item in planner.task_registry[event_id][status]
    ]
    assert forbidden_task not in tasks


def test_v4_plans_have_grounded_lifecycle_and_roundtrip(planner_bundle):
    paths, templates, planner, validator = planner_bundle
    trajectory = _trajectory(paths.root / "data/runs/v4/trajectories/traj_001.json")
    plans = planner.build_plans(trajectory, seed=42)

    assert len(plans) == 300
    assert len({plan.window_index for plan in plans}) == 20
    assert validator.validate_plans(plans, trajectory) == []
    assert any(
        plan.session_type == "weak_signal_evidence"
        and plan.evidence_memory_paths
        and not plan.session_update_paths
        for plan in plans
    )
    for plan in plans:
        if plan.session_type.endswith("_evidence"):
            assert plan.task_template_id
            assert not plan.task_used_generic_fallback
        for update in plan.structured_context.get("session_memory_updates") or []:
            assert not (
                update["operation"] == "archive"
                and update["old_value"] is None
                and update["new_value"] is None
            )
            assert any(
                cue.cue_role == "memory_fact"
                and update["path"] in cue.linked_memory_paths
                and cue.linked_memory_operation == update["operation"]
                and cue.required_value == update["new_value"]
                for cue in plan.planned_cues
            )
        if plan.session_type == "cancellation_evidence":
            assert all(
                item["operation"] in {"clear_pending", "no_update"}
                for item in plan.structured_context["session_memory_updates"]
            )
        if plan.session_type == "stale_recall_session":
            assert all(pair.old_value != pair.current_value for pair in plan.stale_memory_pairs)
        if plan.session_type == "hard_negative":
            assert plan.expected_memory_operation == "no_update"
            assert plan.session_update_paths == []
            assert plan.structured_context["session_memory_updates"] == []

        restored = DialogueGenerationPlan.model_validate_json(plan.model_dump_json())
        assert restored == plan


def test_task_condition_predicates_use_current_state(planner_bundle):
    paths, _, planner, _ = planner_bundle
    trajectory = _trajectory(paths.root / "data/runs/v4/trajectories/traj_001.json")
    state, memory, actions = planner._state_parts(trajectory, 0)
    action_types = {action.type for action in actions}
    actual_has_children = state.life_state.has_children
    assert planner._conditions_match(
        {"state_truthy": ["has_children"]}, {}, state, memory, action_types
    ) is actual_has_children
    assert not planner._conditions_match(
        {"state_equals": {"employment_status": "impossible_status"}},
        {}, state, memory, action_types,
    )


def test_routine_registry_is_expanded_balanced_and_no_update(planner_bundle):
    paths, _, planner, _ = planner_bundle
    trajectory = _trajectory(paths.root / "data/runs/v4/trajectories/traj_001.json")
    plans = planner.build_plans(trajectory, seed=42)
    routine = [plan for plan in plans if plan.session_type == "routine_financial"]

    assert len(planner.routine_tasks) >= 24
    assert len({plan.task_template_id for plan in routine}) >= 24
    counts = {}
    for plan in routine:
        counts[plan.task_template_id] = counts.get(plan.task_template_id, 0) + 1
        assert plan.expected_memory_operation == "no_update"
        assert plan.task_user_goal_instruction
        assert plan.structured_context["session_memory_updates"] == []
    assert max(counts.values()) - min(counts.values()) <= 1


@pytest.mark.parametrize("trajectory_number", range(1, 21))
def test_housing_move_tasks_follow_target_residence_subtype(
    planner_bundle, trajectory_number
):
    paths, _, planner, _ = planner_bundle
    trajectory = _trajectory(
        paths.root / f"data/runs/v4/trajectories/traj_{trajectory_number:03d}.json"
    )
    plans = planner.build_plans(trajectory, seed=42)

    for plan in plans:
        event = plan.structured_context.get("event") or {}
        if event.get("event_id") != "housing_move" or not plan.session_type.endswith(
            "_evidence"
        ):
            continue
        subtype = (event.get("params") or {}).get("new_residence_status")
        selected = next(
            item
            for item in planner.task_registry["housing_move"][
                plan.event_status_after_session
            ]
            if item["task_template_id"] == plan.task_template_id
        )
        assert selected["when"]["param_equals"]["new_residence_status"] == subtype
        if subtype == "family_home":
            assert "보증금" not in plan.financial_task
            assert "월세" not in plan.financial_task


def test_occurred_event_is_one_atomic_high_recoverability_anchor(planner_bundle):
    paths, _, planner, validator = planner_bundle
    trajectory = _trajectory(paths.root / "data/runs/v4/trajectories/traj_006.json")
    plans = planner.build_plans(trajectory, seed=42)

    occurred = [plan for plan in plans if plan.session_type == "occurred_evidence"]
    assert len(occurred) == 20
    assert len({plan.linked_event_instance_id for plan in occurred}) == 20
    assert all(
        plan.desired_single_session_recoverability == "high" for plan in occurred
    )
    assert not {
        item.code for item in validator.validate_plans(plans, trajectory)
    }.intersection(
        {"memory.occurred_not_single_session", "memory.occurred_anchor_not_atomic"}
    )


def test_same_seed_is_identical_and_different_seed_preserves_structure(planner_bundle):
    paths, _, planner, _ = planner_bundle
    trajectory = _trajectory(paths.root / "data/runs/v4/trajectories/traj_002.json")
    first = planner.build_plans(trajectory, seed=42)
    repeat = planner.build_plans(trajectory, seed=42)
    other = planner.build_plans(trajectory, seed=43)
    assert [plan.model_dump(mode="json") for plan in first] == [
        plan.model_dump(mode="json") for plan in repeat
    ]
    assert [plan.model_dump(mode="json") for plan in first] != [
        plan.model_dump(mode="json") for plan in other
    ]
    assert [
        (plan.session_id, plan.window_index, plan.position_in_window)
        for plan in first
    ] == [
        (plan.session_id, plan.window_index, plan.position_in_window)
        for plan in other
    ]


@pytest.mark.parametrize("trajectory_number", range(1, 21))
def test_every_v4_trajectory_keeps_300_and_20_by_15(planner_bundle, trajectory_number):
    paths, _, planner, validator = planner_bundle
    trajectory = _trajectory(
        paths.root / f"data/runs/v4/trajectories/traj_{trajectory_number:03d}.json"
    )
    plans = planner.build_plans(trajectory, seed=42)
    assert len(plans) == 300
    assert validator.validate_plans(plans, trajectory) == []
    for window_index in range(1, 21):
        window = [plan for plan in plans if plan.window_index == window_index]
        assert len(window) == 15
        assert sum(plan.session_type == "occurred_evidence" for plan in window) == 1
