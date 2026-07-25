"""Build deterministic, state-aware dialogue plans without calling an LLM."""

from __future__ import annotations

import random
import re
from collections import Counter
from typing import Any

from ..fsm.models import EventStatus, LifeEventTemplate
from ..io import RepoPaths, load_yaml
from ..locale.loader import LocaleConfig
from ..trajectory.models import Trajectory
from .models import (
    ActionExecutionContract,
    DialogueGenerationPlan,
    EvidenceDimension,
    PlannedCue,
    StaleMemoryPair,
)

_SESSION_TYPE_BY_STATUS = {
    "weak_signal": "weak_signal_evidence",
    "upcoming": "upcoming_evidence",
    "occurred": "occurred_evidence",
    "cancelled": "cancellation_evidence",
}

class PlannerCoverageError(ValueError):
    """Raised when an evidence event/status has no valid concrete task."""


def _slugify(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "_", text).strip("_")[:60]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class EvidencePlanner:
    def __init__(
        self,
        templates: dict[str, LifeEventTemplate],
        locale: LocaleConfig,
        paths: RepoPaths | None = None,
    ):
        self.paths = paths or RepoPaths.default()
        self.templates = templates
        self.locale = locale
        self.cfg = load_yaml(self.paths.generation / "dialogue.yaml")
        self.fa_registry = load_yaml(self.paths.registries / "financial_actions.yaml")
        self.task_registry = load_yaml(self.paths.registries / "dialogue_task_templates.yaml")
        routine_registry = load_yaml(
            self.paths.registries / "dialogue_routine_tasks.yaml"
        )
        self.routine_tasks = list(routine_registry.get("routine_tasks") or [])
        raw_cues = load_yaml(self.paths.registries / "dialogue_cue_templates.yaml")
        self.cue_registry = {key: value for key, value in raw_cues.items() if not key.startswith("_")}
        self.followup_registry = load_yaml(self.paths.registries / "dialogue_followup_tasks.yaml")
        self.hard_negative_registry = load_yaml(
            self.paths.registries / "dialogue_hard_negative_templates.yaml"
        )
        self.evidence_realization_registry = load_yaml(
            self.paths.registries / "dialogue_evidence_realization.yaml"
        )
        self.lifecycle_surface_registry = load_yaml(
            self.paths.registries / "dialogue_lifecycle_surface.yaml"
        )
        self.disclosure_registry = load_yaml(
            self.paths.registries / "dialogue_event_disclosure_patterns.yaml"
        )
        self.high_risk_contract_registry = load_yaml(
            self.paths.registries / "high_risk_action_contracts.yaml"
        )
        self.bank_policy_registry = load_yaml(
            self.paths.registries / "bank_policy_profile.yaml"
        )

        from ..fsm.registry import all_event_labels_ko

        self.all_labels = all_event_labels_ko(templates)

    def _evidence_realization_spec(self, event_id: str, status: str) -> dict[str, Any]:
        defaults = dict(
            (self.evidence_realization_registry.get("_defaults") or {}).get(status)
            or {}
        )
        override = dict(
            (self.evidence_realization_registry.get(event_id) or {}).get(status)
            or {}
        )
        defaults.update(override)
        if not defaults:
            raise PlannerCoverageError(
                f"no evidence realization contract for {event_id} + {status}"
            )
        return defaults

    def _realization_dimensions(
        self, event_id: str, status: str, evidence_paths: list[str]
    ) -> tuple[dict[str, Any], list[EvidenceDimension]]:
        spec = self._evidence_realization_spec(event_id, status)
        dimensions: list[EvidenceDimension] = []
        for item in spec.get("allowed_dimensions") or []:
            linked = _unique(list(item.get("linked_memory_paths") or []))
            compatible = [path for path in linked if path in evidence_paths]
            if linked:
                linked = compatible or linked
            elif evidence_paths:
                linked = list(evidence_paths)
            dimensions.append(
                EvidenceDimension(
                    dimension_id=item["dimension_id"],
                    role=item["role"],
                    semantic_instruction_ko=item["semantic_instruction_ko"],
                    linked_memory_paths=linked,
                    required=bool(item.get("required", True)),
                    must_be_user_expressed=bool(
                        item.get("must_be_user_expressed", True)
                    ),
                    exact_surface_required=bool(
                        item.get("exact_surface_required", False)
                    ),
                )
            )
        return spec, dimensions

    @staticmethod
    def _balanced_choice(
        choices: list[dict[str, Any]],
        key: str,
        counts: Counter,
        prefix: str,
        rng: random.Random,
    ) -> dict[str, Any]:
        if not choices:
            raise PlannerCoverageError(f"no choices for {prefix}")
        minimum = min(counts[f"{prefix}:{item[key]}"] for item in choices)
        balanced = [
            item for item in choices if counts[f"{prefix}:{item[key]}"] == minimum
        ]
        selected = rng.choice(sorted(balanced, key=lambda item: str(item[key])))
        counts[f"{prefix}:{selected[key]}"] += 1
        return selected

    def _surface_and_placement(
        self,
        status: str,
        spec: dict[str, Any],
        counts: Counter,
        rng: random.Random,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        allowed_families = set(spec.get("surface_families") or [])
        surfaces = [
            item
            for item in self.lifecycle_surface_registry.get(status) or []
            if not allowed_families or item.get("family") in allowed_families
        ]
        surface = self._balanced_choice(
            surfaces, "variant_id", counts, f"surface:{status}", rng
        )
        allowed_placements = set(spec.get("placement_strategies") or [])
        placements = [
            item
            for item in self.lifecycle_surface_registry.get("placement_strategies")
            or []
            if not allowed_placements
            or item.get("strategy_id") in allowed_placements
        ]
        placement = self._balanced_choice(
            placements, "strategy_id", counts, f"placement:{status}", rng
        )
        return surface, placement

    @staticmethod
    def _flatten_grounding(value: Any, result: dict[str, Any]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                present = item is not None and item != "" and item != [] and item != {}
                if isinstance(item, dict):
                    cell_value = item.get("value")
                    if (
                        cell_value is not None
                        and cell_value != ""
                        and cell_value != []
                        and cell_value != {}
                    ):
                        result.setdefault(str(key).split(".")[-1], cell_value)
                if key == "value" and present:
                    result.setdefault("value", item)
                if not isinstance(item, (dict, list)) and present:
                    result.setdefault(str(key), item)
                EvidencePlanner._flatten_grounding(item, result)
        elif isinstance(value, list):
            for item in value:
                EvidencePlanner._flatten_grounding(item, result)

    def _action_execution_contract(
        self, plan: DialogueGenerationPlan
    ) -> ActionExecutionContract:
        registry = self.high_risk_contract_registry.get(plan.mapped_action or "")
        if not registry:
            return ActionExecutionContract(action_mode="information_only")
        # The act comes from the task template when it declares one. Reading it
        # off the task string cannot work in general: "주담대 실행과 상환계좌 확인"
        # ends in 확인 yet executes, and "주거 변경 비용 이체 한도 확인" is a pure
        # lookup whose noun "주거 변경" trips any substring test for 변경.
        declared = (self.high_risk_contract_registry.get("task_subtypes") or {}).get(
            plan.task_template_id or ""
        )
        subtype = declared or registry.get("default_subtype")
        spec = (registry.get("subtypes") or {}).get(subtype) or {}
        if spec.get("information_only"):
            return ActionExecutionContract(
                action_mode="information_only", confirmation_required=False
            )
        if not declared:
            # Fallback for templates that declare nothing: a lookup/check stays
            # information-only even though its FA family can move funds.
            information_only_suffixes = ("조회", "확인", "점검", "비교")
            explicitly_executable = any(
                term in plan.financial_task for term in ("실행", "송금", "변경", "해지")
            )
            if (
                plan.financial_task.endswith(information_only_suffixes)
                or "내역" in plan.financial_task
            ) and not explicitly_executable:
                return ActionExecutionContract(
                    action_mode="information_only", confirmation_required=False
                )
        required = list(spec.get("required_for_execution") or [])
        if len(plan.target_action_ids) > 1 and "target_action_ids" not in required:
            required.append("target_action_ids")
        aliases = self.high_risk_contract_registry.get("slot_aliases") or {}
        context = plan.structured_context or {}
        # Resolve each slot with source priority, not just alias priority: the
        # triggering event's params are authoritative for THIS action, so they
        # win over the persona's pre-existing state. Without this, an alias early
        # in the list (e.g. a standing transfer's `amount`) would beat the
        # event's own value (e.g. `amount_krw`) purely by alias order, stamping a
        # persona-constant amount onto an unrelated action.
        event_available: dict[str, Any] = {}
        self._flatten_grounding((context.get("event") or {}).get("params") or {}, event_available)
        prior_available: dict[str, Any] = {}
        self._flatten_grounding(context.get("current_financial_memory") or {}, prior_available)
        self._flatten_grounding(context.get("current_standing_actions") or [], prior_available)
        self._flatten_grounding(context.get("action_impacts") or [], prior_available)
        grounded: dict[str, Any] = {}
        for slot in required:
            if slot == "explicit_confirmation":
                continue
            if slot == "target_action_ids":
                if plan.target_action_ids:
                    grounded[slot] = list(plan.target_action_ids)
                continue
            for source in (event_available, prior_available):
                value = next(
                    (
                        source[alias]
                        for alias in (aliases.get(slot) or [slot])
                        if source.get(alias) not in (None, "", [], {})
                    ),
                    None,
                )
                if value is not None:
                    grounded[slot] = value
                    break
        if "product_or_goal" in required and "product_or_goal" not in grounded:
            if any(term in plan.financial_task for term in ("저축", "연금", "목적")):
                grounded["product_or_goal"] = plan.financial_task
        missing = [
            slot
            for slot in required
            if slot != "explicit_confirmation" and slot not in grounded
        ]
        ready = not missing
        return ActionExecutionContract(
            action_mode=(
                "ready_for_confirmation" if ready else "pending_required_information"
            ),
            required_slots=required,
            grounded_slots=grounded,
            missing_slots=missing,
            completion_allowed=ready,
            confirmation_required="explicit_confirmation" in required,
        )

    @staticmethod
    def _enum_value(value: Any) -> Any:
        return getattr(value, "value", value)

    @classmethod
    def _memory_update_context(cls, update: Any, month_index: int) -> dict[str, Any]:
        return {
            "month_index": month_index,
            "path": update.path,
            "operation": cls._enum_value(update.operation),
            "old_value": update.old_value,
            "new_value": update.new_value,
            "event_status": update.event_status,
            "source_event_instance_id": update.source_event_instance_id,
        }

    @classmethod
    def _memory_cell_context(cls, memory: Any, path: str) -> dict[str, Any] | None:
        cell = memory.latest(path)
        if cell is None:
            return None
        return {
            "path": path,
            "value": cell.value,
            "status": cls._enum_value(cell.status),
            "valid_from": cell.valid_from,
            "valid_until": cell.valid_until,
            "source_event_instance_id": cell.source_event_instance_id,
        }

    @staticmethod
    def _snapshot_at(snapshots: dict[str, Any], month_index: int, initial: Any) -> Any:
        selected = initial
        selected_month = -1
        for key, value in snapshots.items():
            try:
                candidate_month = int(str(key).split(":", 1)[0])
            except (TypeError, ValueError):
                continue
            if selected_month <= candidate_month <= month_index:
                selected = value
                selected_month = candidate_month
        return selected

    def _state_parts(self, trajectory: Trajectory, month_index: int) -> tuple[Any, Any, list[Any]]:
        state = self._snapshot_at(
            trajectory.state_snapshots, month_index, trajectory.initial_persona_state
        )
        memory = self._snapshot_at(
            trajectory.memory_snapshots,
            month_index,
            trajectory.initial_financial_memory_state,
        )
        actions = self._snapshot_at(
            trajectory.action_snapshots, month_index, trajectory.initial_standing_actions
        )
        return state, memory, actions

    def _forbidden_terms(self, template: LifeEventTemplate, surface_hints: list[str]) -> list[str]:
        labels = [label for label in self.all_labels if not any(label in hint for hint in surface_hints)]
        return _unique(list(template.discriminative_cues_ko.forbidden) + labels)

    @staticmethod
    def _condition_value(memory: Any, state: Any, path: str) -> Any:
        if "." in path:
            cell = memory.latest(path)
            return cell.value if cell is not None else None
        life_state = state.life_state
        try:
            return life_state.guard_value(path)
        except AttributeError:
            return getattr(life_state, path, None)

    def _conditions_match(
        self,
        when: dict[str, Any] | None,
        params: dict[str, Any],
        state: Any,
        memory: Any,
        action_types: set[str],
    ) -> bool:
        if not when:
            return True
        for key, expected in (when.get("param_equals") or {}).items():
            if params.get(key) != expected:
                return False
        for path, expected in (when.get("memory_status") or {}).items():
            cell = memory.latest(path)
            actual = self._enum_value(cell.status) if cell is not None else None
            if actual != expected:
                return False
        for path, expected in (when.get("state_equals") or {}).items():
            if self._condition_value(memory, state, path) != expected:
                return False
        for key in when.get("state_truthy") or []:
            if not self._condition_value(memory, state, key):
                return False
        required_actions = set(when.get("action_type_exists") or [])
        if required_actions and not required_actions.issubset(action_types):
            return False
        return True

    def select_task_template(
        self,
        event_template: LifeEventTemplate,
        status: str,
        event_params: dict[str, Any],
        current_state: Any,
        current_memory: Any,
        session_update_paths: list[str],
        evidence_memory_paths: list[str],
        action_impacts: list[Any],
        current_actions: list[Any],
        previous_task_template_ids: list[str],
        rng: random.Random,
    ) -> tuple[dict[str, Any], float, list[str], list[str]]:
        candidates = list((self.task_registry.get(event_template.event_id) or {}).get(status) or [])
        allowed_fa = set(event_template.mapped_actions_by_status.get(status) or [])
        action_types = {getattr(action, "type", "") for action in current_actions}
        impact_action_types = {getattr(impact, "action_type", "") for impact in action_impacts}
        valid: list[tuple[dict[str, Any], float, list[str], list[str]]] = []

        for candidate in candidates:
            if candidate.get("fa_code") not in allowed_fa:
                continue
            required_params = candidate.get("required_event_params") or []
            if any(event_params.get(key) is None for key in required_params):
                continue
            required_actions = set(candidate.get("required_action_types") or [])
            if required_actions and not required_actions.issubset(action_types):
                continue
            if not self._conditions_match(
                candidate.get("when"), event_params, current_state, current_memory, action_types
            ):
                continue

            compatible = set(candidate.get("compatible_memory_paths") or [])
            session_overlap = compatible.intersection(session_update_paths)
            evidence_overlap = compatible.intersection(evidence_memory_paths)
            score = 0.0
            reasons: list[str] = []
            if session_overlap:
                score += 3
                reasons.append("session_update_path_overlap:+3")
            if evidence_overlap:
                score += 2
                reasons.append("evidence_path_overlap:+2")
            if required_actions.intersection(impact_action_types) or (
                impact_action_types and compatible.intersection(session_update_paths)
            ):
                score += 2
                reasons.append("standing_action_impact_match:+2")
            if required_params and any(event_params.get(key) is not None for key in required_params):
                score += 1
                reasons.append("event_parameter_match:+1")
            if (
                self.cfg.get("task_selection", {}).get("penalize_repeated_task", True)
                and candidate.get("task_template_id") in previous_task_template_ids
            ):
                score -= 2
                reasons.append("same_lifecycle_task_repeat:-2")
            grounding = sorted(session_overlap.union(evidence_overlap))
            valid.append((candidate, score, reasons or ["event_status_registry_match"], grounding))

        if not valid:
            raise PlannerCoverageError(
                f"no valid dialogue task for {event_template.event_id} + {status}"
            )
        best_score = max(item[1] for item in valid)
        tied = sorted(
            (item for item in valid if item[1] == best_score),
            key=lambda item: item[0]["task_template_id"],
        )
        return rng.choice(tied)

    def _planned_cues(
        self,
        task: dict[str, Any],
        status: str,
        session_updates: list[Any],
        evidence_paths: list[str],
        evidence_dimensions: list[EvidenceDimension] | None = None,
    ) -> list[PlannedCue]:
        planned: list[PlannedCue] = []
        for dimension in evidence_dimensions or []:
            planned.append(
                PlannedCue(
                    cue_id=f"dimension_{dimension.dimension_id}",
                    semantic_instruction_ko=dimension.semantic_instruction_ko,
                    status=status,
                    linked_memory_paths=list(dimension.linked_memory_paths),
                    exact_surface_required=dimension.exact_surface_required,
                    cue_role=dimension.role,
                    evidence_dimension_id=dimension.dimension_id,
                )
            )
        for cue_id in task.get("cue_template_ids") or []:
            spec = self.cue_registry.get(cue_id)
            if spec is None:
                raise PlannerCoverageError(f"unknown cue template: {cue_id}")
            if status not in (spec.get("allowed_statuses") or []):
                raise PlannerCoverageError(f"cue {cue_id} is not allowed for {status}")
            linked = _unique(list(spec.get("linked_memory_paths") or []))
            if evidence_paths:
                narrowed = [path for path in linked if path in evidence_paths]
                linked = narrowed or linked
            surfaces = list(spec.get("surface_examples") or [])
            planned.append(
                PlannedCue(
                    cue_id=cue_id,
                    semantic_instruction_ko=spec["semantic_instruction_ko"],
                    status=status,
                    linked_memory_paths=linked,
                    required_value_source=spec.get("required_value_source"),
                    exact_surface_required=bool(spec.get("exact_surface_required", False)),
                    surface_hint=surfaces[0] if surfaces else None,
                    cue_role=spec.get("cue_role", "event_signal"),
                    allow_reuse_across_statuses=bool(spec.get("allow_reuse_across_statuses", False)),
                )
            )
        for index, update in enumerate(session_updates):
            operation = str(self._enum_value(update.operation))
            planned.append(
                PlannedCue(
                    cue_id=f"memory_fact_{_slugify(update.path)}_{operation}_{index}",
                    semantic_instruction_ko=(
                        "이 값은 아직 예정 또는 가정임을 분명히 한다."
                        if operation == "set_pending"
                        else "이 실제 메모리 변경을 사용자 발화에서 금융 맥락으로 근거화한다."
                    ),
                    status=status,
                    linked_memory_paths=[update.path],
                    required_value_source="session_memory_updates",
                    required_value=update.new_value,
                    exact_surface_required=False,
                    cue_role="memory_fact",
                    linked_memory_operation=operation,
                    evidence_dimension_id=next(
                        (
                            dimension.dimension_id
                            for dimension in evidence_dimensions or []
                            if update.path in dimension.linked_memory_paths
                        ),
                        None,
                    ),
                )
            )
        return planned

    def _structured_context(
        self,
        trajectory: Trajectory,
        instance: Any | None,
        month_index: int,
        session_updates: list[Any],
        event_updates: list[tuple[int, Any]],
        target_paths: list[str],
        action_impacts: list[Any] | None = None,
    ) -> dict[str, Any]:
        state, memory, actions = self._state_parts(trajectory, month_index)
        persona = trajectory.persona
        current_state = {
            "month_index": month_index,
            "age": state.age,
            "life_state": state.life_state.model_dump(mode="json"),
        }
        persona_seed = {
            "persona_id": persona.persona_id,
            "sex": persona.sex,
            "source_age": persona.age,
            "has_loan": persona.financial_profile.has_loan,
            "style": persona.style.model_dump(mode="json"),
        }
        current_memory = {
            path: self._memory_cell_context(memory, path) for path in _unique(target_paths)
        }
        event_context = None
        if instance is not None:
            visible_history = [item for item in instance.status_history if item.month_index <= month_index]
            event_context = {
                "event_id": instance.event_id,
                "domain": instance.domain,
                "status": self._enum_value(visible_history[-1].status) if visible_history else "no_event",
                "status_history": [
                    {
                        "status": self._enum_value(item.status),
                        "month_index": item.month_index,
                        "age": item.age,
                        "transition_order": item.transition_order,
                    }
                    for item in visible_history
                ],
                "params": dict(instance.params),
            }
        return {
            "event": event_context,
            "persona_seed": persona_seed,
            "current_state": current_state,
            "current_financial_memory": current_memory,
            "current_standing_actions": [action.model_dump(mode="json") for action in actions],
            "session_memory_updates": [
                self._memory_update_context(update, month_index) for update in session_updates
            ],
            "event_memory_updates": [
                self._memory_update_context(update, update_month)
                for update_month, update in event_updates
                if update_month <= month_index
            ],
            "action_impacts": [
                impact.model_dump(mode="json") for impact in (action_impacts or [])
            ],
            # Transitional aliases consumed by the current generator/prompt.
            "persona_state": current_state,
            "current_memory": current_memory,
        }

    def _followup_plan(
        self,
        trajectory: Trajectory,
        instance: Any,
        kind: str,
        month: int,
        updates: list[Any],
        event_updates: list[tuple[int, Any]],
    ) -> DialogueGenerationPlan | None:
        state, memory, _ = self._state_parts(trajectory, month)
        del state
        for update in updates:
            spec = (self.followup_registry.get(update.path) or {}).get(kind)
            if spec is None:
                continue
            current_cell = memory.latest(update.path)
            current_value = current_cell.value if current_cell is not None else update.new_value
            pairs: list[StaleMemoryPair] = []
            cues: list[PlannedCue]
            session_type = "consequence_session"
            if kind == "stale_recall":
                if update.old_value is None or update.old_value == current_value:
                    continue
                session_type = "stale_recall_session"
                pairs = [
                    StaleMemoryPair(
                        path=update.path,
                        old_value=update.old_value,
                        current_value=current_value,
                        old_valid_until=instance.occurred_month,
                        current_valid_from=getattr(current_cell, "valid_from", None),
                    )
                ]
                cues = [
                    PlannedCue(
                        cue_id=f"stale_value_{_slugify(update.path)}",
                        semantic_instruction_ko="이전 값과 그것을 다시 확인하는 이유를 현재 값과 구분해 말한다.",
                        status="occurred",
                        linked_memory_paths=[update.path],
                        required_value_source="stale_memory_pairs.old_value",
                        required_value=update.old_value,
                        cue_role="stale_value",
                    ),
                    PlannedCue(
                        cue_id=f"current_value_{_slugify(update.path)}",
                        semantic_instruction_ko="현재 유효한 값을 이전 값과 혼동하지 않도록 함께 드러낸다.",
                        status="occurred",
                        linked_memory_paths=[update.path],
                        required_value_source="stale_memory_pairs.current_value",
                        required_value=current_value,
                        cue_role="current_value",
                    ),
                ]
            else:
                cues = [
                    PlannedCue(
                        cue_id=f"consequence_{_slugify(update.path)}",
                        semantic_instruction_ko="이미 발생한 변화의 현재 금융 결과를 확인하고 원래 사건 표현은 반복하지 않는다.",
                        status="occurred",
                        linked_memory_paths=[update.path],
                        required_value_source="current_financial_memory",
                        required_value=current_value,
                        cue_role="current_value",
                    )
                ]
            event_paths = _unique([item.path for update_month, item in event_updates if update_month <= month])
            target_paths = _unique([update.path] + event_paths)
            return DialogueGenerationPlan(
                trajectory_id=trajectory.trajectory_id,
                month_index=month,
                age=trajectory.initial_persona_state.age + month // 12,
                transition_order=instance.occurred_transition_order or 0,
                session_type=session_type,
                linked_event_instance_id=instance.event_instance_id,
                event_status_after_session="occurred",
                mapped_action=spec["fa_code"],
                financial_task=spec["visible_task_ko"],
                task_template_id=spec["task_template_id"],
                task_user_goal_instruction=(
                    f"이전 값과 현재 값을 구분하기 위해 {spec['visible_task_ko']} 업무만 수행한다."
                    if kind == "stale_recall"
                    else f"이미 반영된 현재 상태와 연결된 {spec['visible_task_ko']} 업무만 수행한다."
                ),
                task_selection_score=2,
                task_selection_reasons=["event_update_path_followup"],
                task_grounding_paths=[update.path],
                planned_cues=cues,
                evidence_memory_paths=[update.path],
                event_update_paths=event_paths,
                target_memory_paths=target_paths,
                stale_memory_pairs=pairs,
                evidence_bundle_id=instance.event_instance_id,
                structured_context=self._structured_context(
                    trajectory, instance, month, [], event_updates, target_paths
                ),
                desired_single_session_recoverability="low",
                desired_cumulative_recoverability="high",
            )
        return None

    def _hard_negative_candidates(self, trajectory: Trajectory, month: int) -> list[dict[str, Any]]:
        state, memory, actions = self._state_parts(trajectory, month)
        action_types = {getattr(action, "type", "") for action in actions}
        candidates: list[dict[str, Any]] = []
        for event_id, group in self.hard_negative_registry.items():
            domain = group.get("domain", "neutral")
            for hard_type, items in group.items():
                if hard_type == "domain":
                    continue
                for item in items or []:
                    if self._conditions_match(item.get("when"), {}, state, memory, action_types):
                        candidates.append({**item, "event_id": event_id, "domain": domain, "type": hard_type})
        return candidates

    def _make_filler(
        self,
        trajectory: Trajectory,
        month: int,
        transition_order: int,
        hard_negative: bool,
        rng: random.Random,
        hard_counts: Counter,
    ) -> DialogueGenerationPlan:
        if not hard_negative:
            state, memory, actions = self._state_parts(trajectory, month)
            action_types = {getattr(action, "type", "") for action in actions}
            candidates = [
                item
                for item in self.routine_tasks
                if self._conditions_match(
                    item.get("when"), {}, state, memory, action_types
                )
            ]
            if not candidates:
                raise PlannerCoverageError(
                    f"no state-compatible routine task at month {month}"
                )
            minimum = min(
                hard_counts[f"routine:{item['task_template_id']}"]
                for item in candidates
            )
            balanced = [
                item
                for item in candidates
                if hard_counts[f"routine:{item['task_template_id']}"] == minimum
            ]
            item = rng.choice(
                sorted(balanced, key=lambda value: value["task_template_id"])
            )
            hard_counts[f"routine:{item['task_template_id']}"] += 1
            return DialogueGenerationPlan(
                trajectory_id=trajectory.trajectory_id,
                month_index=month,
                age=trajectory.initial_persona_state.age + month // 12,
                transition_order=transition_order,
                session_type="routine_financial",
                event_status_after_session="no_event",
                mapped_action=item["fa_code"],
                financial_task=item["visible_task_ko"],
                task_template_id=item["task_template_id"],
                task_user_goal_instruction=item["user_goal_instruction"],
                task_selection_reasons=["balanced_state_compatible_routine_registry"],
                task_used_generic_fallback=False,
                must_not_include_terms=self.all_labels,
                expected_memory_operation="no_update",
                structured_context=self._structured_context(trajectory, None, month, [], [], []),
                desired_single_session_recoverability="low",
                desired_cumulative_recoverability="medium",
            )

        candidates = self._hard_negative_candidates(trajectory, month)
        type_weights = self.cfg.get("hard_negatives", {}).get("types", {})
        candidates = [item for item in candidates if item["type"] in type_weights]
        if not candidates:
            raise PlannerCoverageError(f"no state-compatible hard negative at month {month}")
        hard_total = sum(hard_counts[hard_type] for hard_type in type_weights)
        deficits = {
            hard_type: float(weight) * (hard_total + 1) - hard_counts[hard_type]
            for hard_type, weight in type_weights.items()
        }
        best_deficit = max(deficits[item["type"]] for item in candidates)
        balanced = [item for item in candidates if deficits[item["type"]] == best_deficit]
        min_domain_count = min(hard_counts[f"domain:{item['domain']}"] for item in balanced)
        balanced = [item for item in balanced if hard_counts[f"domain:{item['domain']}"] == min_domain_count]
        min_variant_count = min(
            hard_counts[f"variant:{item.get('surface_variant_id', item['task_template_id'])}"]
            for item in balanced
        )
        balanced = [
            item
            for item in balanced
            if hard_counts[
                f"variant:{item.get('surface_variant_id', item['task_template_id'])}"
            ]
            == min_variant_count
        ]
        item = rng.choice(sorted(balanced, key=lambda value: value["task_template_id"]))
        hard_counts[item["type"]] += 1
        hard_counts[f"domain:{item['domain']}"] += 1
        hard_counts[
            f"variant:{item.get('surface_variant_id', item['task_template_id'])}"
        ] += 1
        template = self.templates.get(item["event_id"])
        protected = list(item.get("protected_memory_paths") or [])
        cue = PlannedCue(
            cue_id=f"hard_negative_{item['task_template_id']}",
            semantic_instruction_ko=item["explanation"],
            status="no_event",
            linked_memory_paths=protected,
            required_value_source="current_financial_memory" if protected else None,
            surface_hint=item.get("surface_hint"),
            cue_role="event_signal",
        )
        return DialogueGenerationPlan(
            trajectory_id=trajectory.trajectory_id,
            month_index=month,
            age=trajectory.initial_persona_state.age + month // 12,
            transition_order=transition_order,
            session_type="hard_negative",
            event_status_after_session="no_event",
            near_miss_event_label=template.label_ko if template else None,
            near_miss_event_id=item["event_id"] if template else None,
            mapped_action=item["fa_code"],
            financial_task=item["visible_task_ko"],
            task_template_id=item["task_template_id"],
            task_user_goal_instruction=(
                f"{item['visible_task_ko']} 업무만 수행한다. {item['explanation']}"
            ),
            task_selection_score=0,
            task_selection_reasons=["state_compatible_typed_near_miss"],
            planned_cues=[cue],
            must_not_include_terms=(
                _unique(list(template.discriminative_cues_ko.required) + self.all_labels)
                if template else self.all_labels
            ),
            evidence_memory_paths=protected,
            target_memory_paths=protected,
            hard_negative_type=item["type"],
            hard_negative_surface_variant_id=item.get("surface_variant_id"),
            evidence_placement_strategy=item.get("evidence_placement_strategy"),
            evidence_placement_slots=list(
                next(
                    (
                        placement.get("slots") or []
                        for placement in self.lifecycle_surface_registry.get(
                            "placement_strategies"
                        )
                        or []
                        if placement.get("strategy_id")
                        == item.get("evidence_placement_strategy")
                    ),
                    [0],
                )
            ),
            near_miss_explanation=item["explanation"],
            protected_memory_paths=protected,
            expected_memory_operation="no_update",
            structured_context=self._structured_context(
                trajectory, None, month, [], [], protected
            ),
            desired_single_session_recoverability="low",
            desired_cumulative_recoverability="medium",
        )

    def build_plans(self, trajectory: Trajectory, seed: int = 0) -> list[DialogueGenerationPlan]:
        rng = random.Random(f"{trajectory.trajectory_id}:{seed}")
        plans: list[DialogueGenerationPlan] = []
        realization_counts: Counter = Counter()
        updates_by_key: dict[tuple[str, int], list[Any]] = {}
        updates_by_instance: dict[str, list[tuple[int, Any]]] = {}
        impacts_by_key: dict[tuple[str, int], list[Any]] = {}
        for step in trajectory.timeline_steps:
            for update in step.memory_updates:
                # Older trajectories may contain archive(null -> null)
                # records produced before DeltaEngine filtered this no-op.
                # Such records have no user-visible evidence and must not be
                # promoted into mandatory dialogue memory facts.
                if (
                    self._enum_value(update.operation) == "archive"
                    and update.old_value is None
                    and update.new_value is None
                ):
                    continue
                source = update.source_event_instance_id or ""
                updates_by_key.setdefault((source, step.month_index), []).append(update)
                updates_by_instance.setdefault(source, []).append((step.month_index, update))
            for impact in step.action_impacts:
                source = impact.source_event_instance_id or ""
                impacts_by_key.setdefault((source, step.month_index), []).append(impact)

        followup_cfg = self.cfg.get("followups", {})
        for instance in trajectory.life_event_instances:
            template = self.templates[instance.event_id]
            supported_history = [
                item for item in instance.status_history if item.status.value in _SESSION_TYPE_BY_STATUS
            ]
            prior_cue_ids: list[str] = []
            previous_tasks: list[str] = []
            for stage_index, item in enumerate(supported_history, start=1):
                status = item.status.value
                month_updates = updates_by_key.get((instance.event_instance_id, item.month_index), [])
                event_updates = updates_by_instance.get(instance.event_instance_id, [])
                cumulative_updates = [update for month, update in event_updates if month <= item.month_index]
                session_paths = _unique([update.path for update in month_updates])
                event_paths = _unique([update.path for update in cumulative_updates])
                state, memory, actions = self._state_parts(trajectory, item.month_index)
                action_types = {getattr(action, "type", "") for action in actions}
                raw_candidates = [
                    candidate
                    for candidate in (
                        (self.task_registry.get(instance.event_id) or {}).get(status)
                        or []
                    )
                    if self._conditions_match(
                        candidate.get("when"),
                        instance.params,
                        state,
                        memory,
                        action_types,
                    )
                ]
                registry_paths = _unique(
                    [path for candidate in raw_candidates for path in candidate.get("compatible_memory_paths", [])]
                )
                evidence_paths = _unique(session_paths + event_paths + registry_paths)
                realization_spec, evidence_dimensions = self._realization_dimensions(
                    instance.event_id, status, evidence_paths
                )
                lifecycle_surface, evidence_placement = self._surface_and_placement(
                    status, realization_spec, realization_counts, rng
                )
                month_impacts = impacts_by_key.get((instance.event_instance_id, item.month_index), [])
                task, score, reasons, grounding = self.select_task_template(
                    template,
                    status,
                    instance.params,
                    state,
                    memory,
                    session_paths,
                    evidence_paths,
                    month_impacts,
                    actions,
                    previous_tasks,
                    rng,
                )
                previous_tasks.append(task["task_template_id"])
                cues = self._planned_cues(
                    task,
                    status,
                    month_updates,
                    evidence_paths,
                    evidence_dimensions,
                )
                cue_ids = [cue.cue_id for cue in cues]
                exact_cues = [
                    cue.surface_hint for cue in cues if cue.exact_surface_required and cue.surface_hint
                ]
                surface_hints = [cue.surface_hint for cue in cues if cue.surface_hint]
                target_paths = _unique(evidence_paths + session_paths + event_paths)
                plan = DialogueGenerationPlan(
                    trajectory_id=trajectory.trajectory_id,
                    month_index=item.month_index,
                    age=item.age,
                    transition_order=item.transition_order,
                    session_type=_SESSION_TYPE_BY_STATUS[status],
                    linked_event_instance_id=instance.event_instance_id,
                    event_status_after_session=status,
                    mapped_action=task["fa_code"],
                    financial_task=task["visible_task_ko"],
                    task_template_id=task["task_template_id"],
                    task_user_goal_instruction=task["user_goal_instruction"],
                    task_selection_score=score,
                    task_selection_reasons=reasons,
                    task_grounding_paths=grounding or task.get("compatible_memory_paths", [])[:1],
                    planned_cues=cues,
                    must_include_cues=exact_cues,
                    must_not_include_terms=self._forbidden_terms(template, surface_hints),
                    evidence_memory_paths=evidence_paths,
                    session_update_paths=session_paths,
                    event_update_paths=event_paths,
                    target_memory_paths=target_paths,
                    target_action_ids=[impact.action_id for impact in month_impacts],
                    action_impact_types=[impact.impact_type for impact in month_impacts],
                    evidence_bundle_id=instance.event_instance_id,
                    evidence_stage_index=stage_index,
                    evidence_stage_count=len(supported_history),
                    prior_planned_cue_ids=list(prior_cue_ids),
                    cumulative_cue_ids_after_session=_unique(prior_cue_ids + cue_ids),
                    expected_memory_operation=("no_update" if not month_updates else None),
                    structured_context=self._structured_context(
                        trajectory,
                        instance,
                        item.month_index,
                        month_updates,
                        event_updates,
                        target_paths,
                        month_impacts,
                    ),
                    desired_single_session_recoverability=(
                        "low"
                        if status == "weak_signal"
                        else "high"
                        if status in {"occurred", "cancelled"}
                        else "medium"
                    ),
                    desired_cumulative_recoverability="high",
                    evidence_realization_strategy=(
                        f"{instance.event_id}:{status}:"
                        + "+".join(
                            dimension.dimension_id
                            for dimension in evidence_dimensions
                            if dimension.required
                        )
                    ),
                    evidence_placement_strategy=evidence_placement["strategy_id"],
                    evidence_placement_slots=list(evidence_placement.get("slots") or []),
                    evidence_dimensions=evidence_dimensions,
                    forbidden_direct_event_patterns=_unique(
                        list(realization_spec.get("forbidden_direct_paraphrases") or [])
                        + list(
                            (self.disclosure_registry.get(instance.event_id) or {}).get(
                                "disallowed"
                            )
                            or []
                        )
                    ),
                    lifecycle_surface_family=lifecycle_surface["family"],
                    lifecycle_surface_variant_id=lifecycle_surface["variant_id"],
                    directness_level="implicit",
                    bank_policy_profile_id=self.bank_policy_registry.get(
                        "profile_id", "benchmark_neutral_bank"
                    ),
                )
                plan.structured_context["dialogue_contract"] = {
                    "lifecycle_semantic_instruction_ko": lifecycle_surface[
                        "semantic_instruction_ko"
                    ],
                    "evidence_deadline_user_turn": int(
                        self.cfg.get("semantic_validity", {}).get(
                            "evidence_deadline_user_turn", 3
                        )
                    ),
                    "explicit_final_reveal": bool(
                        evidence_placement.get("explicit_final_reveal", False)
                    ),
                }
                plans.append(plan)
                prior_cue_ids = plan.cumulative_cue_ids_after_session

            if instance.occurred_month is None:
                continue
            occurred_updates = updates_by_key.get(
                (instance.event_instance_id, instance.occurred_month), []
            )
            event_updates = updates_by_instance.get(instance.event_instance_id, [])
            if occurred_updates and rng.random() < float(followup_cfg.get("consequence_ratio", 0.60)):
                lo, hi = followup_cfg.get("consequence_delay_months", [1, 4])
                month = min(instance.occurred_month + rng.randint(int(lo), int(hi)), trajectory.horizon_months - 1)
                if month > instance.occurred_month:
                    followup = self._followup_plan(
                        trajectory, instance, "consequence", month, occurred_updates, event_updates
                    )
                    if followup is not None:
                        plans.append(followup)
            stale_updates = [
                update
                for update in occurred_updates
                if self._enum_value(update.operation) in {"update", "archive", "mark_stale"}
                and update.old_value is not None
            ]
            if stale_updates and rng.random() < float(followup_cfg.get("stale_recall_ratio", 0.30)):
                lo, hi = followup_cfg.get("stale_delay_months", [2, 6])
                month = min(instance.occurred_month + rng.randint(int(lo), int(hi)), trajectory.horizon_months - 1)
                if month > instance.occurred_month:
                    stale = self._followup_plan(
                        trajectory, instance, "stale_recall", month, stale_updates, event_updates
                    )
                    if stale is not None:
                        plans.append(stale)

        window_size = int(
            self.cfg.get("controlled_layout", {}).get(
                "window_size", self.cfg.get("controlled_window_size", 15)
            )
        )
        occurred_instances = sorted(
            [instance for instance in trajectory.life_event_instances if instance.status == EventStatus.OCCURRED],
            key=lambda instance: (
                instance.occurred_month if instance.occurred_month is not None else 10**9,
                instance.occurred_transition_order or 0,
                instance.event_instance_id,
            ),
        )
        if not occurred_instances:
            return []
        windows: list[list[DialogueGenerationPlan]] = [[] for _ in occurred_instances]
        anchor_cursor = [
            (int(instance.occurred_month or 0), int(instance.occurred_transition_order or 0))
            for instance in occurred_instances
        ]
        anchor_index = {
            instance.event_instance_id: index for index, instance in enumerate(occurred_instances)
        }
        for plan in sorted(
            plans,
            key=lambda item: (
                item.month_index,
                item.transition_order,
                item.linked_event_instance_id or "",
                item.session_type,
            ),
        ):
            if plan.session_type == "occurred_evidence" and plan.linked_event_instance_id in anchor_index:
                index = anchor_index[plan.linked_event_instance_id]
            else:
                cursor = (plan.month_index, plan.transition_order)
                index = next(
                    (candidate for candidate, anchor in enumerate(anchor_cursor) if cursor <= anchor),
                    len(windows) - 1,
                )
            windows[index].append(plan)

        oversized = [index + 1 for index, window in enumerate(windows) if len(window) > window_size]
        if oversized:
            raise PlannerCoverageError(
                f"chronological controlled windows exceed {window_size} sessions: {oversized}"
            )
        total_capacity = len(windows) * window_size
        filler_count = total_capacity - sum(len(window) for window in windows)
        hard_target = min(
            filler_count,
            int(total_capacity * float(self.cfg.get("hard_negatives", {}).get("target_ratio", 0.30))),
        )
        hard_remaining = hard_target
        hard_counts: Counter = Counter()
        layout_cfg = self.cfg.get("controlled_layout", {})
        max_per_month = int(layout_cfg.get("max_filler_sessions_per_month", 5))
        filler_load: Counter = Counter()
        for index, window in enumerate(windows):
            anchor = occurred_instances[index]
            anchor_month = int(anchor.occurred_month or 0)
            previous_month = int(occurred_instances[index - 1].occurred_month or 0) if index else 0
            lower = previous_month if index else 0
            months = list(range(max(0, lower), min(anchor_month, trajectory.horizon_months - 1) + 1))
            if not months:
                months = [anchor_month]
            while len(window) < window_size:
                available = [month for month in months if filler_load[month] < max_per_month]
                overflow = not available
                pool = available or months
                minimum = min(filler_load[month] for month in pool)
                choices = [month for month in pool if filler_load[month] == minimum]
                month = choices[len(window) % len(choices)]
                transition_order = (
                    int(anchor.occurred_transition_order or 0)
                    if month == anchor_month
                    else int(occurred_instances[index - 1].occurred_transition_order or 0)
                    if index and month == previous_month
                    else 0
                )
                filler = self._make_filler(
                    trajectory,
                    month,
                    transition_order,
                    hard_remaining > 0,
                    rng,
                    hard_counts,
                )
                if hard_remaining > 0:
                    hard_remaining -= 1
                filler.filler_allowed_month_range = (lower, anchor_month)
                filler.filler_placement_overflow = overflow
                filler.structured_context["filler_placement"] = {
                    "allowed_month_range": [lower, anchor_month],
                    "overflow": overflow,
                    "configured_max_per_month": max_per_month,
                }
                filler_load[month] += 1
                window.append(filler)

        ordered: list[DialogueGenerationPlan] = []
        for window_index, (anchor, window) in enumerate(zip(occurred_instances, windows), start=1):
            window.sort(
                key=lambda plan: (
                    plan.month_index,
                    plan.transition_order,
                    plan.linked_event_instance_id or "",
                    plan.session_type,
                )
            )
            for position, plan in enumerate(window, start=1):
                plan.window_index = window_index
                plan.position_in_window = position
                plan.window_event_instance_id = anchor.event_instance_id
                plan.session_id = f"S{len(ordered) + 1:03d}"
                ordered.append(plan)

        cursors = [(plan.month_index, plan.transition_order) for plan in ordered]
        if cursors != sorted(cursors):
            raise AssertionError("controlled session order is not chronological")
        for plan in ordered:
            if plan.session_type == "occurred_evidence":
                if plan.linked_event_instance_id != plan.window_event_instance_id:
                    raise AssertionError("controlled window contains a second occurred event")
            plan.action_execution_contract = self._action_execution_contract(plan)
            plan.bank_policy_profile_id = self.bank_policy_registry.get(
                "profile_id", "benchmark_neutral_bank"
            )
        return ordered
