"""Validation and conflict rules for generated life paths."""

from __future__ import annotations

import copy
from dataclasses import dataclass

from life_event_graph import build_graphs

from .models import EpisodeInstance, ExtraNode, GeneratedLifePath, GeneratorState, Rejection, TimelineEvent
from .templates import EPISODE_TEMPLATES, EXTRA_NODES


@dataclass(frozen=True)
class EventInfo:
    id: str
    name: str
    domain: str
    actions: tuple[str, ...]
    description: str


def event_registry() -> dict[str, EventInfo]:
    registry: dict[str, EventInfo] = {}
    for graph in build_graphs().values():
        for node in graph.nodes.values():
            registry[node.id] = EventInfo(
                id=node.id,
                name=node.name,
                domain=node.domain,
                actions=tuple(node.actions),
                description=node.description or "",
            )
    for node in EXTRA_NODES.values():
        registry[node.id] = EventInfo(
            id=node.id,
            name=node.name,
            domain=node.domain,
            actions=node.actions,
            description=node.description,
        )
    return registry


def validate_template_library() -> list[str]:
    errors: list[str] = []
    registry = event_registry()
    ids = set()
    for template in EPISODE_TEMPLATES:
        if template.id in ids:
            errors.append(f"duplicate template id: {template.id}")
        ids.add(template.id)
        if len(template.gap_ranges) != max(0, len(template.event_ids) - 1):
            errors.append(f"{template.id}: gap_ranges must match event transitions")
        if template.actors and len(template.actors) != len(template.event_ids):
            errors.append(f"{template.id}: actors must match event_ids")
        if template.start_age_range[0] > template.start_age_range[1]:
            errors.append(f"{template.id}: invalid start_age_range")
        for min_gap, max_gap in template.gap_ranges:
            if min_gap < 0 or min_gap > max_gap:
                errors.append(f"{template.id}: invalid gap range {min_gap}-{max_gap}")
        for event_id in template.event_ids:
            if event_id not in registry:
                errors.append(f"{template.id}: unknown event id {event_id}")
        for event_id, child_age_range in template.child_age_by_event:
            if event_id not in template.event_ids:
                errors.append(f"{template.id}: child age rule references missing event {event_id}")
            if child_age_range[0] > child_age_range[1]:
                errors.append(f"{template.id}: invalid child age range for {event_id}")
    return errors


def timeline_events_from_episodes(episodes: tuple[EpisodeInstance, ...]) -> list[tuple[EpisodeInstance, str, int, int]]:
    events: list[tuple[EpisodeInstance, str, int, int]] = []
    for episode in episodes:
        for sequence_index, (event_id, age) in enumerate(episode.event_ages):
            events.append((episode, event_id, age, sequence_index))
    events.sort(key=lambda item: (item[2], -item[0].priority, item[0].template_id, item[3]))
    return events


def validate_episode_set(
    episodes: tuple[EpisodeInstance, ...],
    initial_state: GeneratorState | None = None,
) -> tuple[bool, Rejection | None]:
    # Seed from the persona's month-0 state when provided so state-dependent
    # arcs (a married person's divorce, an owner's home sale, an employed
    # person's job change) are not over-rejected. Deep-copy because the
    # validation loop mutates ``state`` and this may be called repeatedly with
    # the same seed object. Default None preserves the blank-state behavior.
    state = copy.deepcopy(initial_state) if initial_state is not None else GeneratorState()
    seen_episode_ids = {_source_template_id(episode) for episode in episodes}
    templates_by_id = {template.id: template for template in EPISODE_TEMPLATES}

    for episode in episodes:
        template = templates_by_id[_source_template_id(episode)]
        for blocked_id in template.cannot_overlap_episode_ids:
            if blocked_id in seen_episode_ids:
                return False, Rejection(
                    episode_id=episode.template_id,
                    reason=f"cannot overlap with episode {blocked_id}",
                )

    for episode, event_id, age, _ in timeline_events_from_episodes(episodes):
        state.locks = [lock for lock in state.locks if lock[0] >= age]
        for _, blocked_ids, reason in state.locks:
            if event_id in blocked_ids:
                return False, Rejection(
                    episode_id=episode.template_id,
                    reason=reason,
                    conflicted_event_id=event_id,
                    age=age,
                )

        rejection = _validate_event_against_state(episode, event_id, age, state)
        if rejection:
            return False, rejection

        _apply_event(event_id, state)
        template = templates_by_id[_source_template_id(episode)]
        for lock in template.locks:
            if lock.after_event_id == event_id:
                state.locks.append((age + lock.duration_years, lock.blocked_event_ids, lock.reason))

    return True, None


def materialize_generated_path(
    *,
    seed: int,
    selected_episode_ids: tuple[str, ...],
    episodes: tuple[EpisodeInstance, ...],
    rejections: tuple[Rejection, ...],
) -> GeneratedLifePath:
    registry = event_registry()
    templates_by_id = {template.id: template for template in EPISODE_TEMPLATES}
    events: list[TimelineEvent] = []
    for episode, event_id, age, sequence_index in timeline_events_from_episodes(episodes):
        template = templates_by_id[_source_template_id(episode)]
        actor = _actor_for_event(template, episode.event_ages, event_id, sequence_index)
        child_age = _child_age_for(template.child_age_by_event, event_id, age, episode.event_ages)
        events.append(
            TimelineEvent(
                age=age,
                event_id=event_id,
                name=registry[event_id].name,
                domain=registry[event_id].domain,
                episode_id=episode.template_id,
                episode_name=episode.template_name,
                episode_kind=episode.kind,
                priority=episode.priority,
                actor=actor,
                child_age=child_age,
            )
        )
    return GeneratedLifePath(
        seed=seed,
        selected_episode_ids=selected_episode_ids,
        episodes=episodes,
        events=tuple(events),
        rejections=rejections,
    )


def _source_template_id(episode: EpisodeInstance) -> str:
    return episode.source_template_id or episode.template_id.split("#", 1)[0]


def _actor_for_event(
    template,
    event_ages: tuple[tuple[str, int], ...],
    event_id: str,
    sequence_index: int,
) -> str:
    if not template.actors:
        return "self"
    occurrence = sum(1 for candidate_id, _ in event_ages[:sequence_index] if candidate_id == event_id)
    matching_indexes = [index for index, candidate_id in enumerate(template.event_ids) if candidate_id == event_id]
    if occurrence < len(matching_indexes):
        template_index = matching_indexes[occurrence]
        return template.actors[template_index]
    if sequence_index < len(template.actors):
        return template.actors[sequence_index]
    return "self"


def _child_age_for(
    child_age_by_event: tuple[tuple[str, tuple[int, int]], ...],
    event_id: str,
    age: int,
    event_ages: tuple[tuple[str, int], ...],
) -> int | None:
    rules = dict(child_age_by_event)
    if event_id not in rules:
        return None
    min_age, max_age = rules[event_id]

    birth_or_adoption_age = None
    found_anchor_id = None
    for anchor_id in ("relationship_childbirth", "relationship_adoption"):
        for candidate_id, candidate_age in event_ages:
            if candidate_id == anchor_id:
                birth_or_adoption_age = candidate_age
                found_anchor_id = anchor_id
                break
        if birth_or_adoption_age is not None:
            break
    if found_anchor_id == "relationship_adoption" and birth_or_adoption_age is not None:
        adopted_child_age = _infer_adoption_child_age(event_ages, birth_or_adoption_age)
        if event_id == "relationship_adoption":
            return adopted_child_age
        return max(min_age, min(max_age, adopted_child_age + age - birth_or_adoption_age))
    if min_age == max_age:
        return min_age
    if birth_or_adoption_age is None:
        return min_age
    return max(min_age, min(max_age, age - birth_or_adoption_age))


def _infer_adoption_child_age(event_ages: tuple[tuple[str, int], ...], adoption_age: int) -> int:
    event_age_by_id = dict(event_ages)
    if "relationship_child_primary_school_entry" in event_age_by_id:
        return max(0, min(7, 6 - (event_age_by_id["relationship_child_primary_school_entry"] - adoption_age)))
    if "relationship_child_middle_school_entry" in event_age_by_id:
        return max(8, min(13, 12 - (event_age_by_id["relationship_child_middle_school_entry"] - adoption_age)))
    if "relationship_child_high_school_entry" in event_age_by_id:
        return max(14, min(16, 15 - (event_age_by_id["relationship_child_high_school_entry"] - adoption_age)))
    return 17


def _validate_event_against_state(
    episode: EpisodeInstance,
    event_id: str,
    age: int,
    state: GeneratorState,
) -> Rejection | None:
    if event_id == "relationship_marriage" and state.marital_status == "married":
        return Rejection(episode.template_id, "already married", event_id, age)
    if event_id == "relationship_separation" and state.marital_status != "married":
        return Rejection(episode.template_id, "separation requires marital_status married", event_id, age)
    if event_id == "relationship_divorce" and state.marital_status not in {"married", "separated"}:
        return Rejection(episode.template_id, "divorce requires marriage or separation", event_id, age)
    if event_id in {
        "relationship_child_primary_school_entry",
        "relationship_child_middle_school_entry",
        "relationship_child_high_school_entry",
        "relationship_child_independence",
    }:
        if state.children_count <= 0:
            return Rejection(episode.template_id, "child milestone requires children_count > 0", event_id, age)
    episode_event_ids = {candidate_id for candidate_id, _ in episode.event_ages}
    if (
        event_id == "relationship_child_middle_school_entry"
        and "primary_school" not in state.child_milestones
        and not _adoption_episode_omits(episode_event_ids, "relationship_child_primary_school_entry")
    ):
        return Rejection(episode.template_id, "middle school requires prior primary school entry", event_id, age)
    if (
        event_id == "relationship_child_high_school_entry"
        and "middle_school" not in state.child_milestones
        and not _adoption_episode_omits(episode_event_ids, "relationship_child_middle_school_entry")
    ):
        return Rejection(episode.template_id, "high school requires prior middle school entry", event_id, age)
    if (
        event_id == "relationship_child_independence"
        and "high_school" not in state.child_milestones
        and not _adoption_episode_omits(episode_event_ids, "relationship_child_high_school_entry")
    ):
        return Rejection(episode.template_id, "child independence requires prior high school entry", event_id, age)
    if event_id == "career_employment" and state.employment_status == "employed":
        return Rejection(episode.template_id, "employment already active", event_id, age)
    if event_id in {"career_leave", "career_resignation", "career_job_change", "career_transfer", "career_study_abroad"}:
        if state.employment_status != "employed":
            return Rejection(episode.template_id, f"{event_id} requires active employment", event_id, age)
    if event_id == "career_reinstatement" and state.employment_status not in {"on_leave", "studying_abroad"}:
        return Rejection(episode.template_id, "reinstatement requires leave or study abroad", event_id, age)
    if event_id == "career_freelance" and state.employment_status != "unemployed":
        return Rejection(episode.template_id, "startup/freelance requires unemployment after resignation", event_id, age)
    if event_id == "career_business_closure" and state.employment_status != "self_employed":
        return Rejection(episode.template_id, "business closure requires self employment", event_id, age)
    if event_id == "residence_home_sale" and state.property_count <= 0 and not state.purchased_home:
        return Rejection(episode.template_id, "home sale requires prior home purchase", event_id, age)
    if event_id == "career_pension_start" and not state.retirement_prepared:
        return Rejection(episode.template_id, "pension start requires retirement preparation", event_id, age)
    return None


def _adoption_episode_omits(episode_event_ids: set[str], event_id: str) -> bool:
    return "relationship_adoption" in episode_event_ids and event_id not in episode_event_ids


def _apply_event(event_id: str, state: GeneratorState) -> None:
    if event_id == "relationship_marriage":
        state.marital_status = "married"
    elif event_id == "relationship_separation":
        state.marital_status = "separated"
    elif event_id == "relationship_divorce":
        state.marital_status = "divorced"
    elif event_id in {"relationship_childbirth", "relationship_adoption"}:
        state.children_count += 1
        state.dependents_count += 1
    elif event_id == "relationship_child_primary_school_entry":
        state.child_milestones.add("primary_school")
    elif event_id == "relationship_child_middle_school_entry":
        state.child_milestones.add("middle_school")
    elif event_id == "relationship_child_high_school_entry":
        state.child_milestones.add("high_school")
    elif event_id == "relationship_child_independence":
        state.dependents_count = max(0, state.dependents_count - 1)
        state.child_milestones.add("independent")
    elif event_id in {"relationship_dependent_added", "accident_dependent_added"}:
        state.dependents_count += 1
    elif event_id in {"relationship_parent_care_end", "relationship_family_death", "accident_parent_care_end"}:
        state.dependents_count = max(0, state.dependents_count - 1)
    elif event_id in {"career_employment", "career_reinstatement"}:
        state.employment_status = "employed"
    elif event_id == "career_leave":
        state.employment_status = "on_leave"
    elif event_id in {"career_resignation", "career_unemployment"}:
        state.employment_status = "unemployed"
    elif event_id == "career_freelance":
        state.employment_status = "self_employed"
    elif event_id == "career_study_abroad":
        state.employment_status = "studying_abroad"
    elif event_id in {"career_job_change", "career_transfer", "career_education"}:
        state.employment_status = "employed"
    elif event_id == "career_business_closure":
        state.employment_status = "closed_business"
    elif event_id == "career_retirement_prep":
        state.retirement_prepared = True
    elif event_id == "career_pension_start":
        state.employment_status = "retired"
    elif event_id == "residence_jeonse_contract":
        state.housing_status = "jeonse"
    elif event_id == "residence_rent_contract":
        state.housing_status = "monthly_rent"
    elif event_id == "residence_home_purchase":
        state.housing_status = "owned"
        state.purchased_home = True
        state.property_count += 1
    elif event_id == "residence_home_sale":
        state.property_count = max(0, state.property_count - 1)
        state.purchased_home = state.property_count > 0
        if not state.purchased_home:
            state.housing_status = "sold"
    elif event_id == "residence_move_out":
        state.housing_status = "moved_out"
