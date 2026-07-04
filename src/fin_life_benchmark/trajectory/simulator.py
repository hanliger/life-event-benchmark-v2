"""Monthly-tick life-state trajectory simulator.

State-first: the hidden LifeState + financial memory + standing actions evolve
via a hazard-sampled, guard-constrained FSM; dialogue is generated FROM this
state later, never the other way around.
"""

from __future__ import annotations

import copy
import random

from ..actions.impact_engine import ImpactEngine
from ..actions.models import StandingAction
from ..fsm.event_lifecycle import apply_occurred_to_life_state, plan_lifecycle, sample_event_params
from ..fsm.life_state_machine import LifeStateMachine
from ..fsm.models import EventInstance, EventStatus, EventStatusHistoryItem, LifeEventTemplate
from ..locale.loader import LocaleConfig
from ..memory.delta_engine import DeltaEngine
from ..memory.models import FinancialMemoryState
from ..persona.models import NormalizedPersona
from .models import LifeState, PersonaState, StatusTransition, Trajectory, TrajectoryStep


def life_state_from_persona(persona: NormalizedPersona) -> LifeState:
    marital = persona.household.marital_status
    if marital == "unknown":
        marital = "single"
    return LifeState(
        marital_status=marital,
        employment_status=persona.occupation_state.employment_status
        if persona.occupation_state.employment_status != "unknown"
        else "unemployed",
        residence_status=persona.housing.residence_status
        if persona.housing.residence_status != "unknown"
        else "other",
        children_ages=list(persona.household.children_ages),
        dependents_count=persona.household.dependents_count,
        lives_with_parents=persona.household.lives_with_parents,
        home_owned=persona.housing.residence_status == "owner",
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
        forced_events: list[tuple[str, int]] | None = None,
    ) -> Trajectory:
        """Simulate a trajectory.

        ``forced_events`` is an optional list of (event_id, target_start_month)
        pairs (e.g. from a life_generator episode via
        ``trajectory.episode_bridge``). Forced events bypass the hazard roll so
        the occurrence is guaranteed, but still respect state guards — if the
        guard fails at the target month the start is retried on later months
        until it passes or the horizon ends. This is how we guarantee coverage
        of rare (occurred event × impacted standing action) combinations that
        the probabilistic sampler produces only occasionally."""
        rng = random.Random(seed)
        horizon_months = horizon_years * 12
        start_age = persona.age
        forced_queue = sorted(forced_events or [], key=lambda item: item[1])

        life_state = life_state_from_persona(persona)
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
        instance_counter = 0

        max_active = int(self.cfg.get("max_concurrent_active_events", 2))
        max_total = int(self.cfg.get("max_events_per_trajectory", 12))
        min_gap = int(self.cfg.get("min_months_between_event_starts", 2))

        def start_instance(template: LifeEventTemplate, month: int, force_occur: bool = False) -> None:
            """Create + schedule one event instance (shared by hazard and
            forced starts). force_occur guarantees the instance reaches
            OCCURRED (episode/coverage events)."""
            nonlocal instance_counter, last_start_month
            instance_counter += 1
            instance = EventInstance(
                event_instance_id=f"{trajectory_id}_ev{instance_counter:03d}",
                event_id=template.event_id,
                label_ko=template.label_ko,
                domain=template.domain,
                start_month=month,
                params=sample_event_params(template, life_state, self.locale, rng),
            )
            schedule = plan_lifecycle(template, month, rng, force_occur=force_occur)
            schedule = [(min(m, horizon_months - 1), s) for m, s in schedule]
            instances.append(instance)
            for sched_month, status in schedule:
                pending.append((sched_month, instance, status))
            last_start_month = month

        def process_transition(instance: EventInstance, to_status: EventStatus, month: int, age: int, step: TrajectoryStep) -> None:
            # Re-verify state guards at the OCCURRED transition: an event may
            # have started validly but been overtaken by a concurrent event
            # (e.g. 취업/복직 started while unemployed, then employed another
            # way). Downgrade to CANCELLED rather than ship an inconsistent
            # occurred event.
            if to_status == EventStatus.OCCURRED:
                template = self.templates.get(instance.event_id)
                if template is not None and not self.fsm.state_guards_pass(template, life_state):
                    to_status = EventStatus.CANCELLED

            from_status = instance.status
            instance.status = to_status
            instance.status_history.append(EventStatusHistoryItem(status=to_status, month_index=month, age=age))
            if to_status == EventStatus.OCCURRED:
                instance.occurred_month = month
                last_end_month[instance.event_id] = month
                apply_occurred_to_life_state(instance.event_id, life_state, instance.params)
            elif to_status == EventStatus.CANCELLED:
                instance.cancelled_month = month
                last_end_month[instance.event_id] = month
            step.transitions.append(
                StatusTransition(
                    event_instance_id=instance.event_instance_id,
                    event_id=instance.event_id,
                    from_status=from_status.value,
                    to_status=to_status.value,
                )
            )
            step.memory_updates.extend(self.delta_engine.apply_transition(memory, instance, to_status, month, rng))
            step.action_impacts.extend(self.impact_engine.apply_transition(actions, instance, to_status, month))

        for month in range(horizon_months):
            age = start_age + month // 12
            if month > 0 and month % 12 == 0:
                life_state.tick_year()

            step = TrajectoryStep(month_index=month, age=age)

            # 1. process due lifecycle transitions
            due = sorted((p for p in pending if p[0] == month), key=lambda p: p[1].event_instance_id)
            for _, instance, to_status in due:
                process_transition(instance, to_status, month, age, step)
            pending = [p for p in pending if p[0] != month]

            active = [i for i in instances if i.status in {EventStatus.WEAK_SIGNAL, EventStatus.UPCOMING}]

            # 2a. forced event starts (episode-guided coverage). Bypass hazard,
            # respect guards; retry on later months if the guard is not yet met.
            still_queued: list[tuple[str, int]] = []
            for event_id, target_month in forced_queue:
                if target_month > month:
                    still_queued.append((event_id, target_month))
                    continue
                template = self.templates.get(event_id)
                if template is None:
                    continue  # unknown event id — drop
                if self.fsm.guards_pass(template, life_state, age, month, last_end_month, active):
                    start_instance(template, month, force_occur=True)
                    active = [i for i in instances if i.status in {EventStatus.WEAK_SIGNAL, EventStatus.UPCOMING}]
                elif month < horizon_months - 1:
                    still_queued.append((event_id, month + 1))  # retry next month
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
                    if not self.fsm.guards_pass(template, life_state, age, month, last_end_month, active):
                        continue
                    if rng.random() >= self.fsm.monthly_hazard(template, life_state, age, persona):
                        continue
                    start_instance(template, month)
                    break  # at most one new hazard start per month

            # 2b. process transitions scheduled for *this* month by a fresh start
            due_now = [p for p in pending if p[0] == month]
            for _, instance, to_status in due_now:
                process_transition(instance, to_status, month, age, step)
            pending = [p for p in pending if p[0] != month]

            # 3. record step + snapshots on any activity
            if step.transitions:
                steps.append(step)
                if self.cfg.get("snapshot_every_transition", True):
                    key = str(month)
                    snapshots[key] = PersonaState(month_index=month, age=age, life_state=life_state.model_copy(deep=True))
                    memory_snaps[key] = copy.deepcopy(memory)
                    action_snaps[key] = [a.model_copy(deep=True) for a in actions]

        final_state = PersonaState(
            month_index=horizon_months,
            age=start_age + horizon_years,
            life_state=life_state.model_copy(deep=True),
        )
        key = str(horizon_months)
        snapshots[key] = final_state.model_copy(deep=True)
        memory_snaps[key] = copy.deepcopy(memory)
        action_snaps[key] = [a.model_copy(deep=True) for a in actions]

        return Trajectory(
            trajectory_id=trajectory_id,
            locale=self.locale.locale,
            seed=seed,
            horizon_months=horizon_months,
            persona=persona,
            initial_persona_state=initial_state,
            initial_financial_memory_state=initial_memory,
            initial_standing_actions=initial_actions,
            life_event_instances=instances,
            timeline_steps=steps,
            state_snapshots=snapshots,
            memory_snapshots=memory_snaps,
            action_snapshots=action_snaps,
            final_persona_state=final_state,
        )
