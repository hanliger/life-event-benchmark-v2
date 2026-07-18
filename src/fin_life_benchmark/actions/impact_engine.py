"""Apply event->standing-action impact templates on lifecycle transitions.

Risk policy (hard-enforced): any impact on a funds-moving action is
must_not_execute — the gold decision is ask_confirmation (or a
pause/cancel/update AFTER user confirmation). Impacts also flip the action's
validity_status to needs_review/stale so stale-action distractors exist.
"""

from __future__ import annotations

from typing import Any

from ..fsm.models import EventInstance, EventStatus
from ..io import RepoPaths, load_yaml
from .models import ActionDecisionEnum, ActionImpact, StandingAction

_HOOK_BY_STATUS = {
    EventStatus.WEAK_SIGNAL: "on_weak_signal",
    EventStatus.UPCOMING: "on_upcoming",
    EventStatus.OCCURRED: "on_occurred",
    EventStatus.CANCELLED: "on_cancelled",
}


class ImpactEngine:
    def __init__(self, paths: RepoPaths | None = None):
        paths = paths or RepoPaths.default()
        self.registry: dict[str, Any] = load_yaml(paths.registries / "event_to_action_impact.yaml")

    @staticmethod
    def _matches(selector: dict[str, Any], action: StandingAction) -> bool:
        if "label" in selector:
            if action.type != selector["label"]:
                return False
        if "linked_memory_path" in selector:
            if selector["linked_memory_path"] not in action.linked_memory_paths:
                return False
        return bool(selector)

    @staticmethod
    def _conditions_match(spec: dict[str, Any], instance: EventInstance) -> bool:
        conditions = spec.get("when") or {}
        for name, expected in conditions.items():
            actual = instance.params.get(name)
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    def apply_transition(
        self,
        actions: list[StandingAction],
        instance: EventInstance,
        to_status: EventStatus,
        month_index: int,
    ) -> list[ActionImpact]:
        hook = _HOOK_BY_STATUS.get(to_status)
        if hook is None:
            return []
        template_id = instance.action_impact_template_id or instance.event_id
        template = self.registry.get(template_id) or {}
        specs = (template.get(hook) or {}).get("action_impacts") or []

        impacts: list[ActionImpact] = []
        for spec in specs:
            if not self._conditions_match(spec, instance):
                continue
            selector = spec.get("selector") or {}
            expected = ActionDecisionEnum(spec.get("expected_decision", "ask_confirmation"))
            risk = spec.get("risk", "high")
            for action in actions:
                if action.status.value in {"cancelled", "historical"}:
                    continue
                if not self._matches(selector, action):
                    continue
                if action.validity_status == "needs_review":
                    continue
                must_not_execute = bool(action.funds_movement)
                if action.funds_movement and expected == ActionDecisionEnum.EXECUTE:
                    # risk policy: never auto-execute funds-moving changes
                    expected = ActionDecisionEnum.ASK_CONFIRMATION
                impacts.append(
                    ActionImpact(
                        action_id=action.action_id,
                        action_type=action.type,
                        impact_type=spec["impact_type"],
                        expected_decision=expected,
                        risk=risk,
                        funds_movement=action.funds_movement,
                        must_not_execute=must_not_execute,
                        month_index=month_index,
                        source_event_instance_id=instance.event_instance_id,
                        event_status=to_status.value,
                    )
                )
                # mark the action itself so stale-action distractors exist
                action.validity_status = "needs_review"
                action.snapshot(month_index, f"impact:{spec['impact_type']} from {instance.event_instance_id}")
        return impacts
