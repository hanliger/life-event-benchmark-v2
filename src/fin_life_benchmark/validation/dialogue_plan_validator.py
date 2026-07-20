"""Validation and audit summaries for saved DialogueGenerationPlans."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Iterable

from pydantic import BaseModel

from ..dialogue.models import DialogueGenerationPlan
from ..fsm.models import LifeEventTemplate
from ..io import RepoPaths, load_yaml
from ..trajectory.models import Trajectory


class PlanViolation(BaseModel):
    code: str
    message: str
    trajectory_id: str
    session_id: str | None = None


class DialoguePlanValidator:
    def __init__(
        self,
        templates: dict[str, LifeEventTemplate],
        paths: RepoPaths | None = None,
    ):
        self.paths = paths or RepoPaths.default()
        self.templates = templates
        self.task_registry = load_yaml(
            self.paths.registries / "dialogue_task_templates.yaml"
        )
        routine_registry = load_yaml(
            self.paths.registries / "dialogue_routine_tasks.yaml"
        )
        self.routine_tasks = {
            item["task_template_id"]: item
            for item in routine_registry.get("routine_tasks") or []
        }
        raw_cues = load_yaml(self.paths.registries / "dialogue_cue_templates.yaml")
        self.cue_registry = {
            key: value for key, value in raw_cues.items() if not key.startswith("_")
        }
        self.cfg = load_yaml(self.paths.generation / "dialogue.yaml")

    @staticmethod
    def _violation(
        plan: DialogueGenerationPlan | None,
        trajectory_id: str,
        code: str,
        message: str,
    ) -> PlanViolation:
        return PlanViolation(
            code=code,
            message=message,
            trajectory_id=trajectory_id,
            session_id=plan.session_id if plan is not None else None,
        )

    @staticmethod
    def _conditions_match_context(when: dict[str, Any] | None, context: dict[str, Any]) -> bool:
        if not when:
            return True
        event = context.get("event") or {}
        params = event.get("params") or {}
        life_state = (context.get("current_state") or {}).get("life_state") or {}
        memory = context.get("current_financial_memory") or {}
        action_types = {
            action.get("type") for action in context.get("current_standing_actions") or []
        }
        for key, expected in (when.get("param_equals") or {}).items():
            if params.get(key) != expected:
                return False
        for path, expected in (when.get("memory_status") or {}).items():
            if (memory.get(path) or {}).get("status") != expected:
                return False
        for key, expected in (when.get("state_equals") or {}).items():
            if life_state.get(key) != expected:
                return False
        for key in when.get("state_truthy") or []:
            if key == "has_children":
                actual = bool(life_state.get("children") or life_state.get("children_ages"))
            elif key == "has_dependents":
                actual = int(life_state.get("dependents_count") or 0) > 0
            else:
                actual = bool(life_state.get(key))
            if not actual:
                return False
        if not set(when.get("action_type_exists") or []).issubset(action_types):
            return False
        return True

    @staticmethod
    def _memory_update_signature(update: dict[str, Any]) -> str:
        payload = {
            key: update.get(key)
            for key in (
                "month_index",
                "path",
                "operation",
                "old_value",
                "new_value",
                "event_status",
                "source_event_instance_id",
            )
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def validate_plans(
        self,
        plans: Iterable[DialogueGenerationPlan],
        trajectory: Trajectory | None = None,
    ) -> list[PlanViolation]:
        plans = list(plans)
        trajectory_id = (
            trajectory.trajectory_id
            if trajectory is not None
            else plans[0].trajectory_id if plans else "unknown"
        )
        violations: list[PlanViolation] = []
        expected_count = int(self.cfg.get("target_sessions_per_trajectory", 300))
        window_size = int(
            self.cfg.get("controlled_layout", {}).get(
                "window_size", self.cfg.get("controlled_window_size", 15)
            )
        )
        if len(plans) != expected_count:
            violations.append(
                self._violation(
                    None, trajectory_id, "structure.plan_count", f"expected {expected_count}, got {len(plans)}"
                )
            )
        expected_ids = [f"S{index:03d}" for index in range(1, len(plans) + 1)]
        if [plan.session_id for plan in plans] != expected_ids:
            violations.append(
                self._violation(None, trajectory_id, "structure.session_ids", "session IDs are not deterministic S001..")
            )
        cursors = [(plan.month_index, plan.transition_order) for plan in plans]
        if cursors != sorted(cursors):
            violations.append(
                self._violation(None, trajectory_id, "structure.chronology", "plans are not globally chronological")
            )

        windows: dict[int, list[DialogueGenerationPlan]] = defaultdict(list)
        for plan in plans:
            if plan.window_index is not None:
                windows[plan.window_index].append(plan)
        expected_windows = expected_count // window_size
        if len(windows) != expected_windows:
            violations.append(
                self._violation(None, trajectory_id, "structure.window_count", f"expected {expected_windows} windows, got {len(windows)}")
            )
        for index, window in sorted(windows.items()):
            if len(window) != window_size:
                violations.append(
                    self._violation(window[0], trajectory_id, "structure.window_size", f"window {index} has {len(window)} plans")
                )
            anchors = [plan for plan in window if plan.session_type == "occurred_evidence"]
            if len(anchors) != 1:
                violations.append(
                    self._violation(window[0], trajectory_id, "structure.anchor_count", f"window {index} has {len(anchors)} occurred anchors")
                )
            elif anchors[0].linked_event_instance_id != anchors[0].window_event_instance_id:
                violations.append(
                    self._violation(anchors[0], trajectory_id, "structure.anchor_mismatch", "occurred anchor does not match window event")
                )

        instance_event = (
            {instance.event_instance_id: instance.event_id for instance in trajectory.life_event_instances}
            if trajectory is not None else {}
        )
        occurred_month_by_instance = (
            {
                instance.event_instance_id: instance.occurred_month
                for instance in trajectory.life_event_instances
                if instance.occurred_month is not None
            }
            if trajectory is not None
            else {}
        )
        occurred_updates_by_instance: dict[str, Counter] = defaultdict(Counter)
        if trajectory is not None:
            for step in trajectory.timeline_steps:
                for update in step.memory_updates:
                    source = update.source_event_instance_id or ""
                    operation = getattr(update.operation, "value", update.operation)
                    if (
                        step.month_index != occurred_month_by_instance.get(source)
                        or (
                            operation == "archive"
                            and update.old_value is None
                            and update.new_value is None
                        )
                    ):
                        continue
                    occurred_updates_by_instance[source][
                        self._memory_update_signature(
                            {
                                "month_index": step.month_index,
                                "path": update.path,
                                "operation": operation,
                                "old_value": update.old_value,
                                "new_value": update.new_value,
                                "event_status": update.event_status,
                                "source_event_instance_id": source,
                            }
                        )
                    ] += 1
        cue_use_by_bundle: dict[str, dict[str, str]] = defaultdict(dict)
        filler_counts: Counter = Counter()
        filler_overflow_months: set[int] = set()
        evidence_types = {
            "weak_signal_evidence",
            "upcoming_evidence",
            "occurred_evidence",
            "cancellation_evidence",
        }

        for plan in plans:
            event_context = plan.structured_context.get("event") or {}
            event_id = instance_event.get(plan.linked_event_instance_id or "") or event_context.get("event_id")
            status = plan.event_status_after_session
            if plan.task_template_id and not plan.task_user_goal_instruction:
                violations.append(
                    self._violation(
                        plan,
                        trajectory_id,
                        "task.missing_user_goal",
                        "task-bearing plan has no single user-goal instruction",
                    )
                )
            if plan.session_type in evidence_types:
                if not plan.task_template_id:
                    violations.append(self._violation(plan, trajectory_id, "task.missing_template", "evidence plan has no task_template_id"))
                candidates = list((self.task_registry.get(event_id) or {}).get(status) or [])
                matched = next(
                    (item for item in candidates if item.get("task_template_id") == plan.task_template_id),
                    None,
                )
                if matched is None:
                    violations.append(self._violation(plan, trajectory_id, "task.event_status_mismatch", f"task does not belong to {event_id}+{status}"))
                else:
                    allowed = set(self.templates[event_id].mapped_actions_by_status.get(status) or [])
                    if plan.mapped_action not in allowed or matched.get("fa_code") != plan.mapped_action:
                        violations.append(self._violation(plan, trajectory_id, "task.fa_not_allowed", f"{plan.mapped_action} is not allowed for {event_id}+{status}"))
                    if matched.get("visible_task_ko") != plan.financial_task:
                        violations.append(self._violation(plan, trajectory_id, "task.visible_task_mismatch", "financial_task differs from the selected registry task"))
                    if matched.get("user_goal_instruction") != plan.task_user_goal_instruction:
                        violations.append(self._violation(plan, trajectory_id, "task.user_goal_mismatch", "single user-goal instruction differs from the selected registry task"))
                    compatible = set(matched.get("compatible_memory_paths") or [])
                    grounding = set(plan.task_grounding_paths)
                    if compatible and not compatible.intersection(grounding):
                        violations.append(self._violation(plan, trajectory_id, "task.ungrounded", "task has no evidence/update/action grounding path"))
                    if not self._conditions_match_context(matched.get("when"), plan.structured_context):
                        violations.append(self._violation(plan, trajectory_id, "task.condition_mismatch", "task predicates do not match the current snapshot"))
                if plan.task_used_generic_fallback:
                    violations.append(self._violation(plan, trajectory_id, "task.generic_evidence_fallback", "evidence plan used a generic FA example"))

                life_state = (plan.structured_context.get("current_state") or {}).get("life_state") or {}
                task_text = plan.financial_task
                target_residence = (
                    (event_context.get("params") or {}).get("new_residence_status")
                    if event_id == "housing_move"
                    else None
                )
                target_introduces = status in {"weak_signal", "upcoming", "occurred", "cancelled"}
                employment_event = str(event_id).startswith("career_") or str(event_id).startswith("retirement_")
                if (
                    any(term in task_text for term in ("급여계좌", "급여 입금"))
                    and life_state.get("employment_status") not in {"employed", "on_leave"}
                    and not (employment_event and target_introduces)
                ):
                    violations.append(self._violation(plan, trajectory_id, "state.payroll_impossible", "payroll task conflicts with current employment state"))
                if (
                    any(term in task_text for term in ("월세", "집주인"))
                    and life_state.get("residence_status") != "wolse"
                    and not (
                        event_id == "housing_move"
                        and target_introduces
                        and target_residence == "wolse"
                    )
                ):
                    violations.append(self._violation(plan, trajectory_id, "state.rent_impossible", "rent task conflicts with current residence state"))
                if (
                    "대출" in task_text and "상환" in task_text
                    and not life_state.get("home_owned")
                    and event_id not in {"housing_home_purchase", "housing_home_sale"}
                ):
                    violations.append(self._violation(plan, trajectory_id, "state.loan_impossible", "loan task has no current or pending loan context"))

            session_updates = list(plan.structured_context.get("session_memory_updates") or [])
            if plan.session_type == "occurred_evidence":
                if plan.desired_single_session_recoverability != "high":
                    violations.append(
                        self._violation(
                            plan,
                            trajectory_id,
                            "memory.occurred_not_single_session",
                            "occurred anchor is not marked high single-session recoverability",
                        )
                    )
                expected = occurred_updates_by_instance.get(
                    plan.linked_event_instance_id or "", Counter()
                )
                actual = Counter(
                    self._memory_update_signature(update)
                    for update in session_updates
                )
                if trajectory is not None and actual != expected:
                    violations.append(
                        self._violation(
                            plan,
                            trajectory_id,
                            "memory.occurred_anchor_not_atomic",
                            "occurred anchor does not contain the complete occurrence delta",
                        )
                    )
            for update in session_updates:
                matches = [
                    cue for cue in plan.planned_cues
                    if cue.cue_role == "memory_fact"
                    and update.get("path") in cue.linked_memory_paths
                    and cue.linked_memory_operation == update.get("operation")
                    and cue.required_value == update.get("new_value")
                ]
                if not matches:
                    violations.append(self._violation(plan, trajectory_id, "memory.missing_fact_cue", f"missing exact memory_fact cue for {update.get('path')}"))
            if set(plan.session_update_paths) != {str(item.get("path")) for item in session_updates}:
                violations.append(self._violation(plan, trajectory_id, "memory.session_paths_mismatch", "session_update_paths differ from actual updates"))
            union = set(plan.evidence_memory_paths) | set(plan.session_update_paths) | set(plan.event_update_paths)
            if not union.issubset(set(plan.target_memory_paths)):
                violations.append(self._violation(plan, trajectory_id, "memory.target_union_missing", "target_memory_paths does not contain the path union"))

            if status == "weak_signal" and any(item.get("operation") not in {"set_pending", "no_update"} for item in session_updates):
                violations.append(self._violation(plan, trajectory_id, "cue.weak_confirmed_value", "weak signal commits a confirmed value"))
            if status == "upcoming" and any(item.get("operation") != "set_pending" for item in session_updates):
                violations.append(self._violation(plan, trajectory_id, "cue.upcoming_not_pending", "upcoming update is not pending"))
            if status == "cancelled":
                if not any(cue.cue_role == "cancellation" for cue in plan.planned_cues):
                    violations.append(self._violation(plan, trajectory_id, "cue.cancel_missing", "cancelled plan has no cancellation cue"))
                if any(item.get("operation") not in {"clear_pending", "no_update"} for item in session_updates):
                    violations.append(self._violation(plan, trajectory_id, "memory.cancelled_commit", "cancelled plan commits proposed value"))

            for cue in plan.planned_cues:
                spec = self.cue_registry.get(cue.cue_id)
                if spec and status not in (spec.get("allowed_statuses") or []):
                    violations.append(self._violation(plan, trajectory_id, "cue.status_mismatch", f"{cue.cue_id} is not allowed for {status}"))
                if plan.evidence_bundle_id and cue.cue_role != "memory_fact":
                    previous_status = cue_use_by_bundle[plan.evidence_bundle_id].get(cue.cue_id)
                    if previous_status and previous_status != status and not cue.allow_reuse_across_statuses:
                        violations.append(self._violation(plan, trajectory_id, "cue.cross_status_reuse", f"{cue.cue_id} reused across statuses"))
                    cue_use_by_bundle[plan.evidence_bundle_id][cue.cue_id] = status

            if plan.session_type == "stale_recall_session":
                if not plan.stale_memory_pairs:
                    violations.append(self._violation(plan, trajectory_id, "stale.missing_pair", "stale recall has no old/current pair"))
                for pair in plan.stale_memory_pairs:
                    if pair.old_value == pair.current_value:
                        violations.append(self._violation(plan, trajectory_id, "stale.identical_values", f"stale pair is identical for {pair.path}"))
            if plan.session_type == "hard_negative":
                if session_updates or plan.session_update_paths:
                    violations.append(self._violation(plan, trajectory_id, "negative.committed_update", "hard negative commits a memory update"))
                if not plan.hard_negative_type or not plan.near_miss_explanation:
                    violations.append(self._violation(plan, trajectory_id, "negative.missing_metadata", "hard negative lacks type/explanation"))
                if plan.expected_memory_operation != "no_update":
                    violations.append(self._violation(plan, trajectory_id, "negative.operation", "hard negative expected operation is not no_update"))
            if plan.session_type == "routine_financial" and plan.planned_cues:
                violations.append(self._violation(plan, trajectory_id, "negative.routine_event_cue", "routine plan contains an event cue"))
            if plan.session_type == "routine_financial":
                routine = self.routine_tasks.get(plan.task_template_id or "")
                if routine is None:
                    violations.append(self._violation(plan, trajectory_id, "task.unknown_routine", "routine plan does not use the routine registry"))
                elif (
                    routine.get("fa_code") != plan.mapped_action
                    or routine.get("visible_task_ko") != plan.financial_task
                    or routine.get("user_goal_instruction")
                    != plan.task_user_goal_instruction
                ):
                    violations.append(self._violation(plan, trajectory_id, "task.routine_mismatch", "routine plan differs from its registry entry"))
                if plan.expected_memory_operation != "no_update" or session_updates:
                    violations.append(self._violation(plan, trajectory_id, "memory.routine_update", "routine plan must be an explicit no-update session"))

            if plan.filler_allowed_month_range is not None:
                lo, hi = plan.filler_allowed_month_range
                if not lo <= plan.month_index <= hi:
                    violations.append(self._violation(plan, trajectory_id, "temporal.filler_range", f"filler month {plan.month_index} outside {lo}..{hi}"))
                filler_counts[plan.month_index] += 1
                if plan.filler_placement_overflow:
                    filler_overflow_months.add(plan.month_index)

        cap = int(self.cfg.get("controlled_layout", {}).get("max_filler_sessions_per_month", 5))
        for month, count in filler_counts.items():
            if count > cap and month not in filler_overflow_months:
                violations.append(self._violation(None, trajectory_id, "temporal.unreported_overflow", f"month {month} has {count} filler plans without overflow report"))
        return violations

    @staticmethod
    def audit_counts(plans: Iterable[DialogueGenerationPlan]) -> dict[str, Any]:
        plans = list(plans)

        def counts(values: Iterable[Any]) -> dict[str, int]:
            return dict(sorted(Counter(str(value) for value in values if value is not None).items()))

        event_ids = [
            (plan.structured_context.get("event") or {}).get("event_id")
            for plan in plans
        ]
        evidence_paths = [path for plan in plans for path in plan.evidence_memory_paths]
        impact_types = [impact for plan in plans for impact in plan.action_impact_types]
        return {
            "plan_count": len(plans),
            "session_type": counts(plan.session_type for plan in plans),
            "event_id": counts(event_ids),
            "lifecycle_status": counts(plan.event_status_after_session for plan in plans),
            "fa_code": counts(plan.mapped_action for plan in plans),
            "task_template_id": counts(plan.task_template_id for plan in plans),
            "hard_negative_type": counts(plan.hard_negative_type for plan in plans),
            "evidence_path": counts(evidence_paths),
            "action_impact": counts(impact_types),
            "evidence_paths_without_committed_update": sum(
                bool(plan.evidence_memory_paths) and not bool(plan.session_update_paths)
                for plan in plans
            ),
            "filler_by_month": counts(
                plan.month_index for plan in plans if plan.filler_allowed_month_range is not None
            ),
            "filler_overflow_plans": sum(plan.filler_placement_overflow for plan in plans),
        }
