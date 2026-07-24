"""Monthly-tick life-state trajectory simulator.

State-first: the hidden LifeState + financial memory + standing actions evolve
via a hazard-sampled, guard-constrained FSM; dialogue is generated FROM this
state later, never the other way around.
"""

from __future__ import annotations

import copy
import random
from typing import Any

from ..actions.impact_engine import ImpactEngine
from ..actions.models import StandingAction
from ..fsm.event_lifecycle import (
    apply_occurred_to_life_state,
    education_previous_stage,
    plan_lifecycle,
    sample_event_params,
    validate_event_params,
)
from ..fsm.life_state_machine import LifeStateMachine
from ..fsm.models import EventInstance, EventStatus, EventStatusHistoryItem, LifeEventTemplate
from ..locale.loader import LocaleConfig
from ..memory.delta_engine import DeltaEngine
from ..memory.models import FinancialMemoryState
from ..persona.models import NormalizedPersona
from .models import ChildState, LifeState, PersonaState, PropertyState, StatusTransition, Trajectory, TrajectoryStep

ForcedEvent = (
    tuple[str, int]
    | tuple[str, int, dict[str, Any]]
    | tuple[str, int, dict[str, Any], dict[str, Any]]
)


def life_state_from_persona(
    persona: NormalizedPersona,
    initial_memory: FinancialMemoryState | None = None,
) -> LifeState:
    marital = persona.household.marital_status
    if marital == "unknown":
        marital = "single"
    children = [
        ChildState(
            child_id=f"child_{index:03d}",
            age=age,
            education_stage=(
                "high" if age >= 16 else "middle" if age >= 13 else "primary" if age >= 7 else "pre_school"
            ),
        )
        for index, age in enumerate(persona.household.children_ages, start=1)
    ]
    employer = None
    address = "unknown"
    mortgage_status = "unknown"
    if initial_memory is not None:
        employer_cell = initial_memory.latest("employment.employer")
        address_cell = initial_memory.latest("housing.address")
        mortgage_cell = initial_memory.latest("housing.mortgage_status")
        employer = employer_cell.value if employer_cell else None
        address = str(address_cell.value) if address_cell else address
        mortgage_status = str(mortgage_cell.value) if mortgage_cell else mortgage_status
    properties: list[PropertyState] = []
    primary_property_id = None
    if persona.housing.residence_status == "owner":
        primary_property_id = f"property_initial_{persona.persona_id}"
        properties.append(PropertyState(
            property_id=primary_property_id,
            address=address,
            role="primary_residence",
            mortgage_status=mortgage_status,
        ))
    return LifeState(
        marital_status=marital,
        employment_status=persona.occupation_state.employment_status
        if persona.occupation_state.employment_status != "unknown"
        else "unemployed",
        residence_status=persona.housing.residence_status
        if persona.housing.residence_status != "unknown"
        else "other",
        children_ages=list(persona.household.children_ages),
        children=children,
        dependents_count=persona.household.dependents_count,
        lives_with_parents=persona.household.lives_with_parents,
        home_owned=persona.housing.residence_status == "owner",
        properties=properties,
        primary_residence_property_id=primary_property_id,
        current_employer=str(employer) if employer else None,
        retirement_prepared=persona.occupation_state.employment_status == "retired",
        pension_receiving=persona.occupation_state.employment_status == "retired",
    )


class TrajectorySimulator:
    def __init__(
        self,
        templates: dict[str, LifeEventTemplate],
        locale: LocaleConfig,
        sim_config: dict,
        delta_engine: DeltaEngine | None = None,
        impact_engine: ImpactEngine | None = None,
    ):
        self.templates = templates
        self.locale = locale
        self.cfg = sim_config
        self.fsm = LifeStateMachine(templates, global_hazard_scale=float(sim_config.get("global_hazard_scale", 1.0)))
        self.delta_engine = delta_engine or DeltaEngine()
        self.impact_engine = impact_engine or ImpactEngine()

    def simulate(
        self,
        persona: NormalizedPersona,
        initial_memory: FinancialMemoryState,
        initial_actions: list[StandingAction],
        horizon_years: int,
        seed: int,
        trajectory_id: str,
        forced_events: list[ForcedEvent] | None = None,
        target_occurred_events: int | None = None,
    ) -> Trajectory:
        """Simulate a trajectory.

        ``forced_events`` is an optional list of (event_id, target_start_month)
        pairs, or (event_id, target_start_month, param_overrides) triples
        (e.g. from a life_generator episode via
        ``trajectory.episode_bridge``). Forced events bypass the hazard roll and
        disable lifecycle cancellation, but still respect age/state guards,
        event caps, and active-event limits. If a guard never passes or the
        horizon/cap is exhausted, the requested event may be dropped or
        cancelled rather than producing an inconsistent occurred event. If
        ``target_occurred_events`` is set, the run ends when that many
        occurred instances have been recorded; ``horizon_years`` is then only
        a computational safety boundary."""
        rng = random.Random(seed)
        horizon_months = horizon_years * 12
        start_age = persona.age
        forced_queue = sorted(forced_events or [], key=lambda item: item[1])

        life_state = life_state_from_persona(persona, initial_memory)
        memory = copy.deepcopy(initial_memory)
        actions = [a.model_copy(deep=True) for a in initial_actions]

        initial_state = PersonaState(month_index=0, age=start_age, life_state=life_state.model_copy(deep=True))

        instances: list[EventInstance] = []
        # scheduled transitions: (month, instance, status)
        pending: list[tuple[int, EventInstance, EventStatus]] = []
        last_end_month: dict[str, int] = {}
        last_start_month = -999
        steps: list[TrajectoryStep] = []
        snapshots: dict[str, PersonaState] = {"0": initial_state.model_copy(deep=True)}
        memory_snaps: dict[str, FinancialMemoryState] = {"0": copy.deepcopy(memory)}
        action_snaps: dict[str, list[StandingAction]] = {"0": [a.model_copy(deep=True) for a in actions]}
        ordered_snapshots: dict[str, PersonaState] = {}
        ordered_memory_snaps: dict[str, FinancialMemoryState] = {}
        ordered_action_snaps: dict[str, list[StandingAction]] = {}
        instance_counter = 0
        occurred_count = 0
        last_group_occurrence_month: dict[str, int] = {}
        month_transition_order = 0

        max_active = int(self.cfg.get("max_concurrent_active_events", 2))
        max_total = int(self.cfg.get("max_events_per_trajectory", 12))
        min_gap = int(self.cfg.get("min_months_between_event_starts", 2))

        def repeat_policy_pass(template: LifeEventTemplate) -> bool:
            """Apply registry repeat policy to pending and occurred instances."""
            existing = [
                instance
                for instance in instances
                if instance.event_id == template.event_id
                and instance.status != EventStatus.CANCELLED
            ]
            if template.repeat_policy == "once" and existing:
                return False
            if template.max_occurrences is not None and len(existing) >= template.max_occurrences:
                return False
            return True

        def start_instance(
            template: LifeEventTemplate,
            month: int,
            force_occur: bool = False,
            param_overrides: dict[str, Any] | None = None,
            generation_source: str = "hazard",
            source_metadata: dict[str, Any] | None = None,
        ) -> None:
            """Create + schedule one event instance (shared by hazard and
            forced starts). force_occur guarantees the instance reaches
            OCCURRED (episode/coverage events)."""
            nonlocal instance_counter, last_start_month
            instance_counter += 1
            params = sample_event_params(template, life_state, self.locale, rng)
            if param_overrides:
                params.update(param_overrides)
                if template.event_id == "education_child_stage_entry":
                    child = next(
                        (candidate for candidate in life_state.children if candidate.child_id == params.get("child_id")),
                        None,
                    )
                    if child is not None:
                        params["child_age_months"] = child.age * 12
                        # Prior stage is the step below new_stage in the ordered
                        # progression, not the shared education cell (which may
                        # hold another child's or a later stage).
                        params["previous_stage"] = education_previous_stage(
                            params.get("new_stage")
                        )
                validate_event_params(template, life_state, params)
            event_instance_id = f"{trajectory_id}_ev{instance_counter:03d}"
            if template.event_id == "housing_home_purchase":
                params["property_id"] = f"property_{event_instance_id}"
            if template.event_id in {"relationship_childbirth", "relationship_adoption"}:
                params.setdefault("child_id", f"child_{event_instance_id}")
            metadata = source_metadata or {}
            instance = EventInstance(
                event_instance_id=event_instance_id,
                event_id=template.event_id,
                label_ko=template.label_ko,
                domain=template.domain,
                start_month=month,
                params=params,
                memory_delta_template_id=(
                    template.memory_delta_template_id or template.event_id
                ),
                action_impact_template_id=(
                    template.action_impact_template_id or template.event_id
                ),
                generation_source=generation_source,
                causal_bundle_id=metadata.get("causal_bundle_id"),
                bundle_event_index=metadata.get("bundle_event_index"),
                source_template_id=metadata.get("source_template_id"),
            )
            schedule = plan_lifecycle(template, month, rng, force_occur=force_occur)
            schedule = [(min(m, horizon_months - 1), s) for m, s in schedule]
            instances.append(instance)
            for sched_month, status in schedule:
                pending.append((sched_month, instance, status))
            last_start_month = month

        def active_or_pending_instances() -> list[EventInstance]:
            """Instances that should block another start of the same event.

            A newly forced instance can have due transitions scheduled for the
            current month while its status is still NO_EVENT until due_now is
            processed. Treat pending instances as active for guard purposes so
            episode injection cannot start duplicate same-event instances in
            the same tick.
            """
            pending_ids = {instance.event_instance_id for _, instance, _ in pending}
            return [
                i
                for i in instances
                if i.status in {EventStatus.WEAK_SIGNAL, EventStatus.UPCOMING}
                or i.event_instance_id in pending_ids
            ]

        def process_transition(instance: EventInstance, to_status: EventStatus, month: int, age: int, step: TrajectoryStep) -> None:
            nonlocal occurred_count, month_transition_order
            # Several active instances may mature in the same month. Once the
            # requested target is reached, leave any later occurrence pending
            # instead of overshooting the exact trajectory target.
            if (
                to_status == EventStatus.OCCURRED
                and target_occurred_events is not None
                and occurred_count >= target_occurred_events
            ):
                return
            # Re-verify age + state guards at the OCCURRED transition: an
            # event may have started validly but been overtaken by a concurrent
            # event or an age boundary (e.g. a job change started at 65 but
            # would occur at 66). Downgrade to CANCELLED rather than ship an
            # inconsistent occurred event.
            if to_status == EventStatus.OCCURRED:
                template = self.templates.get(instance.event_id)
                if template is not None:
                    try:
                        validate_event_params(template, life_state, instance.params)
                    except ValueError:
                        to_status = EventStatus.CANCELLED
                    if to_status == EventStatus.OCCURRED and not self.fsm.occurrence_guards_pass(
                        template, life_state, age
                    ):
                        to_status = EventStatus.CANCELLED
                    if to_status == EventStatus.OCCURRED and template.cooldown_group:
                        previous = last_group_occurrence_month.get(template.cooldown_group)
                        if previous is not None and month - previous < template.cooldown_group_months:
                            to_status = EventStatus.CANCELLED

            from_status = instance.status
            month_transition_order += 1
            instance.status = to_status
            instance.status_history.append(EventStatusHistoryItem(
                status=to_status,
                month_index=month,
                age=age,
                transition_order=month_transition_order,
            ))
            if to_status == EventStatus.OCCURRED:
                instance.occurred_month = month
                instance.occurred_transition_order = month_transition_order
                last_end_month[instance.event_id] = month
                template = self.templates.get(instance.event_id)
                if template is not None and template.cooldown_group:
                    last_group_occurrence_month[template.cooldown_group] = month
                apply_occurred_to_life_state(
                    instance.event_id,
                    life_state,
                    instance.params,
                    event_instance_id=instance.event_instance_id,
                    month_index=month,
                )
                occurred_count += 1
            elif to_status == EventStatus.CANCELLED:
                instance.cancelled_month = month
                last_end_month[instance.event_id] = month
            step.transitions.append(
                StatusTransition(
                    event_instance_id=instance.event_instance_id,
                    event_id=instance.event_id,
                    from_status=from_status.value,
                    to_status=to_status.value,
                    transition_order=month_transition_order,
                )
            )
            step.memory_updates.extend(self.delta_engine.apply_transition(memory, instance, to_status, month, rng))
            step.action_impacts.extend(self.impact_engine.apply_transition(actions, instance, to_status, month))
            ordered_key = f"{month}:{month_transition_order}"
            ordered_snapshots[ordered_key] = PersonaState(
                month_index=month, age=age, life_state=life_state.model_copy(deep=True)
            )
            ordered_memory_snaps[ordered_key] = copy.deepcopy(memory)
            ordered_action_snaps[ordered_key] = [a.model_copy(deep=True) for a in actions]

        actual_end_month = horizon_months
        for month in range(horizon_months):
            age = start_age + month // 12
            month_transition_order = 0
            if month > 0 and month % 12 == 0:
                life_state.tick_year()

            step = TrajectoryStep(month_index=month, age=age)

            # 1. process due lifecycle transitions
            due = sorted((p for p in pending if p[0] == month), key=lambda p: p[1].event_instance_id)
            for _, instance, to_status in due:
                process_transition(instance, to_status, month, age, step)
            pending = [p for p in pending if p[0] != month]

            active = active_or_pending_instances()

            # 2a. forced event starts (episode-guided coverage). Bypass hazard,
            # respect guards; retry on later months if the guard is not yet met.
            still_queued: list[ForcedEvent] = []
            for forced in forced_queue:
                event_id, target_month = forced[0], forced[1]
                param_overrides = forced[2] if len(forced) > 2 else None
                source_metadata = forced[3] if len(forced) > 3 else None
                if target_month > month:
                    still_queued.append(forced)
                    continue
                template = self.templates.get(event_id)
                if template is None:
                    continue  # unknown event id — drop
                if len(instances) >= max_total:
                    continue  # respect the trajectory-wide event cap
                if not repeat_policy_pass(template):
                    if month < horizon_months - 1:
                        still_queued.append((event_id, month + 1, param_overrides or {}, source_metadata or {}))
                    continue
                if len(active) >= max_active:
                    if month < horizon_months - 1:
                        still_queued.append((event_id, month + 1, param_overrides or {}, source_metadata or {}))
                    continue  # retry after an active event completes
                if self.fsm.guards_pass(template, life_state, age, month, last_end_month, active):
                    start_instance(
                        template,
                        month,
                        force_occur=True,
                        param_overrides=param_overrides,
                        generation_source="forced",
                        source_metadata=source_metadata,
                    )
                    active = active_or_pending_instances()
                elif month < horizon_months - 1:
                    still_queued.append((event_id, month + 1, param_overrides or {}, source_metadata or {}))
                # else: horizon reached, guard never met -> drop
            forced_queue = still_queued

            # 2. sample new (background) event starts via hazard
            can_start = (
                len(active) < max_active
                and len(instances) < max_total
                and month - last_start_month >= min_gap
            )
            if can_start:
                shuffled = sorted(self.templates.values(), key=lambda t: rng.random())
                for template in shuffled:
                    if template.sampling_source == "subgraph_only":
                        continue
                    if not repeat_policy_pass(template):
                        continue
                    if not self.fsm.guards_pass(template, life_state, age, month, last_end_month, active):
                        continue
                    monthly_hazard = self.fsm.monthly_hazard(template, life_state, age, persona)
                    if rng.random() >= monthly_hazard:
                        continue
                    start_instance(template, month)
                    break  # at most one new hazard start per month

            # 2b. process transitions scheduled for *this* month by a fresh start
            due_now = [p for p in pending if p[0] == month]
            for _, instance, to_status in due_now:
                process_transition(instance, to_status, month, age, step)
            pending = [p for p in pending if p[0] != month]

            # 3. record step + snapshots on any activity. The true initial
            # state is preserved separately on Trajectory; snapshot "0" is
            # allowed to represent the post-transition state at month zero.
            if step.transitions:
                steps.append(step)
                if self.cfg.get("snapshot_every_transition", True):
                    key = str(month)
                    snapshots[key] = PersonaState(month_index=month, age=age, life_state=life_state.model_copy(deep=True))
                    memory_snaps[key] = copy.deepcopy(memory)
                    action_snaps[key] = [a.model_copy(deep=True) for a in actions]

            if target_occurred_events is not None and occurred_count >= target_occurred_events:
                actual_end_month = max(1, month)
                break

        final_state = PersonaState(
            month_index=actual_end_month,
            age=start_age + actual_end_month // 12,
            life_state=life_state.model_copy(deep=True),
        )
        key = str(actual_end_month)
        snapshots[key] = final_state.model_copy(deep=True)
        memory_snaps[key] = copy.deepcopy(memory)
        action_snaps[key] = [a.model_copy(deep=True) for a in actions]

        return Trajectory(
            trajectory_id=trajectory_id,
            locale=self.locale.locale,
            seed=seed,
            horizon_months=actual_end_month,
            persona=persona,
            initial_persona_state=initial_state,
            initial_financial_memory_state=initial_memory,
            initial_standing_actions=initial_actions,
            life_event_instances=instances,
            timeline_steps=steps,
            state_snapshots=snapshots,
            memory_snapshots=memory_snaps,
            action_snapshots=action_snaps,
            ordered_state_snapshots=ordered_snapshots,
            ordered_memory_snapshots=ordered_memory_snaps,
            ordered_action_snapshots=ordered_action_snaps,
            final_persona_state=final_state,
        )
