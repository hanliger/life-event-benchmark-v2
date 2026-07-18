"""Persona-aware subgraph sampling for trajectory generation.

Unlike ``episode_bridge`` (which samples life_generator episodes by their
static, persona-agnostic ``sampling_weight`` and forces them for coverage),
this module drives the *primary* trajectory generation:

  * **samples a benchmark event first by its hazard**
    (``base_rate_per_year × age_weight(persona age) × state/persona modifiers``
    from life_events.yaml, via ``LifeStateMachine.annual_propensity``), so an
    event's probability does not increase merely because it appears in more
    episode templates;
  * **conditions every step on the persona's month-0 ``LifeState``**: which
    episodes are eligible, which entry door is taken, and the state that seeds
    ``validate_episode_set``;
  * **anchors episode ages to the persona's actual age** so ``age_weight`` is
    evaluated at realistic ages and events land inside the horizon;
  * **supports mid-entry**: an already-employed / married / owner persona
    enters a career / childrearing / housing arc partway (e.g. job-change
    instead of first employment). When several entry doors are open, the door
    is sampled in proportion to each candidate event's hazard.

After the event is selected, one compatible episode branch is chosen
conditionally. All nodes in that branch become reserved forced events; they
are not independently sampled again. The result is a list of
``(benchmark_event_id, start_month)`` pairs fed to
``TrajectorySimulator.simulate(..., forced_events=...)`` (force_occur=True), so
the selected arcs become the backbone of the trajectory while simulator age,
state, repeat policy, concurrency, and total-event guards remain authoritative.

life_generator imports are done lazily inside functions (mirroring
``episode_bridge``) to keep module import order robust.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..fsm.event_lifecycle import apply_occurred_to_life_state
from ..fsm.life_state_machine import LifeStateMachine
from ..fsm.models import LifeEventTemplate
from ..fsm.registry import load_life_event_templates
from ..io import RepoPaths
from ..persona.models import NormalizedPersona
from .episode_bridge import build_reverse_map, scripted_events_from_path
from .models import LifeState
from .simulator import ForcedEvent, life_state_from_persona

CHILD_EDUCATION_STAGES: tuple[tuple[str, int], ...] = (
    ("primary", 7),
    ("middle", 13),
    ("high", 16),
)


def episode_entry_age(template, persona_age: int) -> int:
    """Age at which the arc is anchored: the persona's age, but never earlier
    than the template's own minimum entry age."""
    return max(persona_age, template.start_age_range[0])


def persona_prior_events(life_state: LifeState) -> set[str]:
    """life_generator node ids the persona has effectively already reached.

    Used by ``_can_enter_template_at_event`` to decide which mid-entry doors of
    an arc are open (e.g. an employed persona can enter a career arc at
    job-change because ``career_employment`` is a prior state anchor)."""
    prior: set[str] = set()
    if life_state.employment_status == "employed":
        prior.add("career_employment")
    if life_state.marital_status in {"married", "separated", "divorced"}:
        prior.add("relationship_marriage")
    if life_state.children_ages:
        prior.add("relationship_childbirth")
    if life_state.home_owned:
        prior.add("residence_home_purchase")
    return prior


def valid_entry_doors(
    template,
    persona: NormalizedPersona,
    life_state: LifeState,
    fsm: LifeStateMachine,
    reverse_map: dict[str, str],
    templates_by_id: dict[str, LifeEventTemplate],
    prior: set[str],
    entry_age: int,
) -> list[tuple[int, float]]:
    """Return ``[(start_event_index, annual_propensity)]`` for every entry door
    of ``template`` that is open given the persona's initial state.

    * index 0 is always a structural entry, but only counts as a door if the
      entry event does not conflict with the persona's state (benchmark
      ``guards_pass`` on the month-0 ``life_state``);
    * index i>0 is a door only if the persona's prior state anchors allow
      mid-entry there (``_can_enter_template_at_event``) and the entry event's
      benchmark guard passes;
    * doors whose node has no benchmark mapping or zero propensity are dropped.
    """
    doors: list[tuple[int, float]] = []
    for index, node_id in enumerate(template.event_ids):
        structural = True if index == 0 else _can_enter_template_at_event(
            node_id, prior, life_state
        )
        if not structural:
            continue
        benchmark_id = reverse_map.get(node_id)
        if benchmark_id is None:
            continue
        bench_template = templates_by_id.get(benchmark_id)
        if bench_template is None:
            continue
        if not fsm.guards_pass(bench_template, life_state, entry_age, 0, {}, []):
            continue
        propensity = fsm.annual_propensity(bench_template, life_state, entry_age, persona)
        if propensity > 0:
            doors.append((index, propensity))
    return doors


def episode_weight(doors: list[tuple[int, float]]) -> float:
    """Legacy episode weight helper.

    The production sampler no longer uses this aggregate because the same
    event can be an entry door in several episodes. Keeping the helper makes
    the old weighting contract explicit for callers/tests that inspect one
    episode in isolation."""
    return sum(propensity for _, propensity in doors)


def _can_enter_template_at_event(node_id: str, prior: set[str], life_state: LifeState) -> bool:
    """Return whether a life-generator node can be used as a mid-entry door.

    The vendored generator intentionally blocks child anchors as generic
    mid-entry points. For the benchmark, a married persona who already has a
    marriage anchor may enter a childrearing arc at a later childbirth node;
    this is what permits a second/third childbirth without replaying marriage.
    """
    from life_generator.sampler import _can_enter_template_at_event as generator_can_enter

    if node_id in {"relationship_childbirth", "relationship_adoption"}:
        return life_state.marital_status == "married" and "relationship_marriage" in prior
    return generator_can_enter(node_id, prior)


@dataclass(frozen=True)
class EventEntryCandidate:
    event_id: str
    template: object | None
    start_event_index: int
    direct_start_age: int | None = None


def _repeat_allowed(template: LifeEventTemplate, occurrence_count: int) -> bool:
    if template.repeat_policy == "once" and occurrence_count > 0:
        return False
    if template.max_occurrences is not None and occurrence_count >= template.max_occurrences:
        return False
    return True


def event_entry_candidates(
    *,
    persona: NormalizedPersona,
    life_state: LifeState,
    anchor_age: int,
    prior_nodes: set[str],
    occurrence_counts: dict[str, int],
    fsm: LifeStateMachine,
    reverse_map: dict[str, str],
    templates_by_id: dict[str, LifeEventTemplate],
) -> tuple[dict[str, list[EventEntryCandidate]], dict[str, float]]:
    """Build an event-first candidate pool.

    Each benchmark event receives one hazard weight, regardless of how many
    episode templates contain it. Episode/door multiplicity is retained only
    as a conditional branch choice after the event has been selected.
    """
    from life_generator.templates import EPISODE_TEMPLATES

    prior = set(prior_nodes) | persona_prior_events(life_state)
    branches: dict[str, list[EventEntryCandidate]] = {}
    weights: dict[str, float] = {}
    for episode_template in EPISODE_TEMPLATES:
        for index, node_id in enumerate(episode_template.event_ids):
            structural = index == 0 or _can_enter_template_at_event(node_id, prior, life_state)
            if not structural:
                continue
            event_id = reverse_map.get(node_id)
            benchmark_template = templates_by_id.get(event_id or "")
            if benchmark_template is None:
                continue
            if not _repeat_allowed(benchmark_template, occurrence_counts.get(event_id, 0)):
                continue
            if not fsm.guards_pass(benchmark_template, life_state, anchor_age, 0, {}, []):
                continue
            propensity = fsm.annual_propensity(
                benchmark_template, life_state, anchor_age, persona
            )
            if propensity <= 0:
                continue
            candidate = EventEntryCandidate(event_id, episode_template, index)
            branches.setdefault(event_id, []).append(candidate)
            # Deliberately assign, not add: event probability is independent of
            # the number of episodes/doors that happen to contain this event.
            weights[event_id] = propensity

    # Registry events without a life_generator node still participate in the
    # same trajectory. They are direct candidates, not a separate coverage
    # trajectory. retirement_start is currently the main example.
    mapped_event_ids = set(reverse_map.values())
    for event_id, benchmark_template in templates_by_id.items():
        if event_id in mapped_event_ids:
            continue
        if not _repeat_allowed(benchmark_template, occurrence_counts.get(event_id, 0)):
            continue
        candidate_age = max(anchor_age, benchmark_template.age_guard.min_age)
        if candidate_age > benchmark_template.age_guard.max_age:
            continue
        if not fsm.guards_pass(benchmark_template, life_state, candidate_age, 0, {}, []):
            continue
        propensity = fsm.annual_propensity(
            benchmark_template, life_state, candidate_age, persona
        )
        if propensity <= 0:
            continue
        branches[event_id] = [
            EventEntryCandidate(event_id, None, 0, direct_start_age=candidate_age)
        ]
        weights[event_id] = propensity
    return branches, weights


def _planning_state(
    persona: NormalizedPersona,
    planned_events: list[tuple[str, int, dict[str, object]]],
    through_age: int,
) -> LifeState:
    """Replay planned occurred events to obtain the state at ``through_age``."""
    state = life_state_from_persona(persona)
    current_age = persona.age
    for event_id, age, _ in sorted(planned_events, key=lambda item: (item[1], item[0])):
        if age > through_age:
            break
        while current_age < age:
            state.tick_year()
            current_age += 1
        params: dict[str, object] = {}
        if event_id == "housing_home_sale":
            owned = [prop for prop in state.properties if prop.ownership_status == "owned"]
            sold = next(
                (prop for prop in owned if prop.property_id == state.primary_residence_property_id),
                owned[0] if owned else None,
            )
            if sold is not None:
                params = {
                    "sold_property_id": sold.property_id,
                    "post_sale_residence_status": "jeonse",
                }
        apply_occurred_to_life_state(event_id, state, params)
    return state


def _instance_events(
    instance: object,
    reverse_map: dict[str, str],
    causal_bundle_id: str,
) -> list[tuple[str, int, dict[str, object]]]:
    """Convert one episode instance's person-age nodes to benchmark events."""
    events: list[tuple[str, int, dict[str, object]]] = []
    for index, (node_id, age) in enumerate(getattr(instance, "event_ages", ())):
        event_id = reverse_map.get(node_id)
        if event_id is not None:
            events.append((event_id, age, {
                "causal_bundle_id": causal_bundle_id,
                "bundle_event_index": index,
                "source_template_id": (
                    getattr(instance, "source_template_id", None)
                    or getattr(instance, "template_id", None)
                ),
                "source_node_id": node_id,
            }))
    return events


def _forced_events_from_planned(
    persona: NormalizedPersona,
    planned_events: list[tuple[str, int, dict[str, object]]],
    horizon_months: int,
) -> list[ForcedEvent]:
    forced: list[ForcedEvent] = []
    for event_id, age, metadata in planned_events:
        month = (age - persona.age) * 12
        if 0 <= month < horizon_months:
            forced.append((event_id, month, {}, metadata))
    forced.sort(key=lambda item: (item[1], item[0]))
    return forced


def eligible_templates(
    persona: NormalizedPersona,
    life_state: LifeState,
    horizon_years: int,
    fsm: LifeStateMachine,
    reverse_map: dict[str, str],
    templates_by_id: dict[str, LifeEventTemplate],
) -> list[tuple[object, list[tuple[int, float]], float]]:
    """Return ``[(EpisodeTemplate, doors, weight)]`` for arcs that (a) start
    inside the horizon and (b) have at least one open, positively-weighted
    entry door for this persona."""
    from life_generator.templates import EPISODE_TEMPLATES

    prior = persona_prior_events(life_state)
    out: list[tuple[object, list[tuple[int, float]], float]] = []
    for template in EPISODE_TEMPLATES:
        entry_age = episode_entry_age(template, persona.age)
        if entry_age - persona.age >= horizon_years:
            continue  # arc would only begin after the horizon ends
        doors = valid_entry_doors(
            template, persona, life_state, fsm, reverse_map, templates_by_id, prior, entry_age
        )
        if not doors:
            continue
        weight = episode_weight(doors)
        if weight <= 0:
            continue
        out.append((template, doors, weight))
    return out


def generator_state_from_persona(life_state: LifeState) -> object:
    """Map the benchmark ``LifeState`` into life_generator's ``GeneratorState``
    vocabulary so ``validate_episode_set`` starts from the persona's real
    month-0 state instead of a blank single/unemployed default."""
    from life_generator.models import GeneratorState

    marital = life_state.marital_status
    if marital not in {"single", "married", "separated", "divorced"}:
        marital = "single"  # widowed / unknown / other -> single

    employment = life_state.employment_status
    if employment not in {"employed", "self_employed", "unemployed", "retired", "on_leave", "studying_abroad"}:
        employment = "unemployed"  # student / homemaker / unknown -> unemployed

    housing_status: str | None = None
    if life_state.home_owned:
        housing_status = "owned"
    elif life_state.residence_status == "jeonse":
        housing_status = "jeonse"
    elif life_state.residence_status in {"wolse", "monthly_rent"}:
        housing_status = "monthly_rent"

    milestones: set[str] = set()
    for child_age in life_state.children_ages:
        if child_age >= 7:
            milestones.add("primary_school")
        if child_age >= 13:
            milestones.add("middle_school")
        if child_age >= 16:
            milestones.add("high_school")

    return GeneratorState(
        marital_status=marital,
        employment_status=employment,
        housing_status=housing_status,
        children_count=len(life_state.children_ages),
        dependents_count=life_state.dependents_count,
        retirement_prepared=life_state.retirement_prepared,
        purchased_home=life_state.home_owned,
        property_count=len([p for p in life_state.properties if p.ownership_status == "owned"]),
        child_milestones=milestones,
    )


def fixed_child_education_events(
    persona: NormalizedPersona,
    horizon_months: int,
) -> list[ForcedEvent]:
    """Deterministic child school-entry events within the forward horizon.

    Child school entry is calendar-like, not a stochastic life choice. For each
    existing child, schedule an education event when the child reaches the
    configured entry ages. ``horizon_months`` is authoritative, so this works
    for non-10-year runs as well.
    """
    forced: list[ForcedEvent] = []
    for child_index, child_age in enumerate(persona.household.children_ages, start=1):
        child_id = f"child_{child_index:03d}"
        for stage, target_age in CHILD_EDUCATION_STAGES:
            years_until = target_age - child_age
            if years_until < 0:
                continue
            month = years_until * 12
            if month >= horizon_months:
                continue
            previous_stage = (
                "pre_school" if stage == "primary" else "primary" if stage == "middle" else "middle"
            )
            forced.append((
                "education_child_stage_entry",
                month,
                {
                    "child_id": child_id,
                    "child_age_months": target_age * 12,
                    "previous_stage": previous_stage,
                    "new_stage": stage,
                },
                {
                    "causal_bundle_id": f"fixed_education_{child_id}_{stage}",
                    "bundle_event_index": 0,
                    "source_template_id": "fixed_child_education",
                },
            ))
    return sorted(forced, key=lambda item: (item[1], item[2]["child_id"]))


def select_episode_instances(
    eligible: list[tuple[object, list[tuple[int, float]], float]],
    persona: NormalizedPersona,
    init_gen_state: object,
    seed: int,
    episode_count: int,
) -> list[object]:
    """Weighted, without-replacement acceptance of episodes.

    Each round picks an episode ∝ its hazard weight, then picks the entry door
    ∝ each door's hazard (confirmed: proportional-random mid-entry), schedules
    the instance, and keeps it only if the whole accepted set validates against
    the persona-seeded state."""
    from life_generator.rules import validate_episode_set
    from life_generator.sampler import schedule_template_instance

    rng = random.Random(f"subgraph:{seed}")
    pool = list(eligible)
    accepted: list[object] = []
    while pool and len(accepted) < episode_count:
        weights = [weight for _, _, weight in pool]
        picked = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        template, doors, _ = pool.pop(picked)  # without replacement
        door_indexes = [index for index, _ in doors]
        door_weights = [propensity for _, propensity in doors]
        start_event_index = rng.choices(door_indexes, weights=door_weights, k=1)[0]
        instance = schedule_template_instance(
            template=template,
            rng=rng,
            start_age=episode_entry_age(template, persona.age),
            start_event_index=start_event_index,
        )
        ok, _ = validate_episode_set(tuple(accepted) + (instance,), initial_state=init_gen_state)
        if ok:
            accepted.append(instance)
    return accepted


def subgraph_scripted_events(
    *,
    persona: NormalizedPersona,
    seed: int,
    horizon_months: int,
    episode_count: int = 6,
    target_event_count: int | None = None,
    max_age: int | None = None,
    templates: dict[str, LifeEventTemplate] | None = None,
    paths: RepoPaths | None = None,
) -> list[ForcedEvent]:
    """Sample event-first subgraphs and return forced event starts.

    The production path samples one benchmark event at a time. Its hazard is
    computed once per event ID; episode/entry multiplicity is used only to
    choose a conditional subgraph branch. Each accepted branch is scheduled
    after the previous branch, so its future nodes are reservations rather
    than independent global samples. Repeatable events (notably childbirth
    and housing lifecycle nodes) can be selected again after their guards and
    cooldowns permit it.

    ``target_event_count`` is a projected occurred-instance target. The
    simulator remains authoritative and may return fewer events if a final
    occurrence guard rejects a planned node. ``max_age`` is only an internal
    planning safety boundary; it is not persisted as the trajectory horizon.
    """
    from life_generator.rules import validate_episode_set
    from life_generator.sampler import schedule_template_instance

    paths = paths or RepoPaths.default()
    templates = templates or load_life_event_templates(paths)
    fsm = LifeStateMachine(templates)
    reverse = build_reverse_map(paths)
    if episode_count == 0 or target_event_count == 0:
        return []
    target_event_count = target_event_count if target_event_count is not None else max(1, episode_count)
    # A planned node can still be cancelled by the simulator's occurrence
    # guard after another node changes the live state. Reserve a small margin
    # so the event-first planner can still reach the requested occurred count.
    planning_target_count = target_event_count + max(5, target_event_count // 2)
    init_state = life_state_from_persona(persona)
    init_gen = generator_state_from_persona(init_state)
    rng = random.Random(f"event-first:{seed}")

    accepted_instances: list[object] = []
    planned_events: list[tuple[str, int, dict[str, object]]] = []
    occurrence_counts: dict[str, int] = {}
    prior_nodes: set[str] = set()
    cursor_age = persona.age
    attempts = 0
    max_attempts = max(50, target_event_count * 20)

    while len(planned_events) < planning_target_count and attempts < max_attempts:
        attempts += 1
        state = _planning_state(persona, planned_events, cursor_age)
        branches, weights = event_entry_candidates(
            persona=persona,
            life_state=state,
            anchor_age=cursor_age,
            prior_nodes=prior_nodes,
            occurrence_counts=occurrence_counts,
            fsm=fsm,
            reverse_map=reverse,
            templates_by_id=templates,
        )
        if not weights:
            break

        event_ids = list(weights)
        accepted_this_round = False
        while event_ids and not accepted_this_round:
            event_id = rng.choices(event_ids, weights=[weights[eid] for eid in event_ids], k=1)[0]
            event_ids.remove(event_id)
            branch_candidates = list(branches[event_id])
            rng.shuffle(branch_candidates)
            for candidate in branch_candidates:
                gap = rng.randint(1, 3) if planned_events else 0
                if candidate.template is None:
                    start_age = max(cursor_age + gap, candidate.direct_start_age or cursor_age)
                    if max_age is not None and start_age > max_age:
                        continue
                    event_template = templates[event_id]
                    if start_age > event_template.age_guard.max_age:
                        continue
                    planned_events.append((event_id, start_age, {
                        "causal_bundle_id": f"direct_{attempts:03d}",
                        "bundle_event_index": 0,
                        "source_template_id": "direct_registry_event",
                    }))
                    occurrence_counts[event_id] = occurrence_counts.get(event_id, 0) + 1
                    cursor_age = start_age
                    accepted_this_round = True
                    break
                start_age = max(cursor_age + gap, candidate.template.start_age_range[0])
                if max_age is not None and start_age > max_age:
                    continue
                instance = schedule_template_instance(
                    template=candidate.template,
                    rng=rng,
                    start_age=start_age,
                    start_event_index=candidate.start_event_index,
                )
                bundle_id = f"subgraph_{len(accepted_instances) + 1:03d}"
                instance_events = _instance_events(instance, reverse, bundle_id)
                if not instance_events or instance_events[0][0] != event_id:
                    continue

                proposed_counts = dict(occurrence_counts)
                policy_ok = True
                for proposed_event_id, _, _ in instance_events:
                    proposed_counts[proposed_event_id] = proposed_counts.get(proposed_event_id, 0) + 1
                    event_template = templates.get(proposed_event_id)
                    if event_template is None or not _repeat_allowed(
                        event_template, proposed_counts[proposed_event_id] - 1
                    ):
                        policy_ok = False
                        break
                if not policy_ok:
                    continue

                ok, _ = validate_episode_set(
                    tuple(accepted_instances) + (instance,), initial_state=init_gen
                )
                if not ok:
                    continue

                accepted_instances.append(instance)
                occurrence_counts = proposed_counts
                planned_events.extend(instance_events)
                prior_nodes.update(node_id for node_id, _ in instance.event_ages)
                cursor_age = max(age for _, age, _ in instance_events)
                accepted_this_round = True
                break

        if not accepted_this_round:
            break

    return _forced_events_from_planned(persona, planned_events, horizon_months)
