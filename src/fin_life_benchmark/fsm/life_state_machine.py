"""Guard checking and hazard computation for the life-state FSM.

This is deliberately NOT a Markov transition matrix. Event starts depend on:
current LifeState, age, per-event cooldowns, currently active instances,
event history, and persona modifiers. Rates are heuristic plausibility
weights, not empirical probabilities.
"""

from __future__ import annotations

from ..persona.models import NormalizedPersona
from ..trajectory.models import LifeState
from .models import EventInstance, EventStatus, LifeEventTemplate


class LifeStateMachine:
    def __init__(self, templates: dict[str, LifeEventTemplate], global_hazard_scale: float = 1.0):
        self.templates = templates
        self.global_hazard_scale = global_hazard_scale

    # -- guards ---------------------------------------------------------------
    def guards_pass(
        self,
        template: LifeEventTemplate,
        state: LifeState,
        age: int,
        month_index: int,
        last_end_month_by_event: dict[str, int],
        active_instances: list[EventInstance],
    ) -> bool:
        if not (template.age_guard.min_age <= age <= template.age_guard.max_age):
            return False
        for field, allowed in template.state_guards.required.items():
            if state.guard_value(field) not in allowed:
                return False
        for field, blocked in template.state_guards.forbidden.items():
            if state.guard_value(field) in blocked:
                return False
        # cooldown since the last instance of the same event ended
        last_end = last_end_month_by_event.get(template.event_id)
        if last_end is not None and month_index - last_end < template.cooldown_months:
            return False
        # no concurrent duplicate of the same event
        if any(inst.event_id == template.event_id for inst in active_instances):
            return False
        # domain-specific: child education entry needs a child at an entry age
        if template.requires_child_entry_age:
            if not any(abs(a - entry) <= 1 for a in state.children_ages for entry in (7, 13, 16)):
                return False
        return True

    def state_guards_pass(self, template: LifeEventTemplate, state: LifeState) -> bool:
        """Only the required/forbidden state guards (no age/cooldown/active).

        Used to re-verify at the OCCURRED transition: an event may start
        validly (guard held at weak_signal) but be overtaken by a concurrent
        event before it occurs (e.g. 취업/복직 started while unemployed, but the
        person got employed another way in the meantime). Such an event must
        not occur — it is cancelled instead of shipping an inconsistent state."""
        for field, allowed in template.state_guards.required.items():
            if state.guard_value(field) not in allowed:
                return False
        for field, blocked in template.state_guards.forbidden.items():
            if state.guard_value(field) in blocked:
                return False
        return True

    def occurrence_guards_pass(
        self,
        template: LifeEventTemplate,
        state: LifeState,
        age: int,
    ) -> bool:
        """Check the guards that must still hold when an event occurs.

        Start-time cooldown and active-instance checks are intentionally not
        repeated here. Age and state guards are repeated because an event may
        spend several months in weak/upcoming status while another event
        changes the person's state or the person crosses an age boundary.
        """
        if not (template.age_guard.min_age <= age <= template.age_guard.max_age):
            return False
        return self.state_guards_pass(template, state)

    # -- hazard ---------------------------------------------------------------
    def annual_propensity(
        self,
        template: LifeEventTemplate,
        state: LifeState,
        age: int,
        persona: NormalizedPersona,
    ) -> float:
        """Relative, unclamped annual propensity used as a *selection weight*.

        Same age/state/persona conditioning chain as ``monthly_hazard`` but
        WITHOUT the ``/12`` and the ``[0, 0.5]`` clamp: this is a weight, not a
        probability, and the clamp/constant would distort relative ordering.
        The registry's ``sampling_multiplier`` is applied once after the
        event-level hazard factors.
        Used by the subgraph sampler to weight episodes by their entry event's
        hazard (see trajectory.subgraph_bridge)."""
        p = template.base_rate_per_year
        p *= template.age_weight(age)
        p *= self._state_modifier(template, state)
        p *= self._persona_modifier(template, persona)
        p *= template.sampling_multiplier
        p *= self.global_hazard_scale
        return p

    def monthly_hazard(
        self,
        template: LifeEventTemplate,
        state: LifeState,
        age: int,
        persona: NormalizedPersona,
    ) -> float:
        return max(0.0, min(0.5, self.annual_propensity(template, state, age, persona) / 12.0))

    def _state_modifier(self, template: LifeEventTemplate, state: LifeState) -> float:
        if template.event_id == "housing_move" and state.home_owned:
            return 0.4
        if template.event_id == "relationship_dependent_addition" and state.dependents_count >= 2:
            return 0.5
        return 1.0

    def _persona_modifier(self, template: LifeEventTemplate, persona: NormalizedPersona) -> float:
        # income stability influences job-loss / job-change propensity
        if template.event_id == "career_employment_end":
            return 1.5 if persona.occupation_state.income_stability in {"variable", "unstable"} else 1.0
        if template.event_id == "career_job_change" and persona.age < 35:
            return 1.2
        if template.event_id == "housing_home_purchase":
            return 1.3 if persona.financial_profile.savings_propensity == "high" else 1.0
        return 1.0

    # -- lifecycle transitions -------------------------------------------------
    @staticmethod
    def allowed_transitions(status: EventStatus) -> tuple[EventStatus, ...]:
        return {
            EventStatus.NO_EVENT: (EventStatus.WEAK_SIGNAL, EventStatus.UPCOMING, EventStatus.OCCURRED),
            EventStatus.WEAK_SIGNAL: (EventStatus.UPCOMING, EventStatus.OCCURRED, EventStatus.CANCELLED),
            EventStatus.UPCOMING: (EventStatus.OCCURRED, EventStatus.CANCELLED),
            EventStatus.OCCURRED: (),
            EventStatus.CANCELLED: (),
        }[status]
