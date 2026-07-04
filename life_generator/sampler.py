"""Episode sampling and temporal interleaving."""

from __future__ import annotations

import random
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .models import EpisodeInstance, EpisodeTemplate, GeneratedLifePath, Rejection
from .rules import materialize_generated_path, validate_episode_set, validate_template_library
from .templates import EPISODE_TEMPLATES


def validate_templates() -> None:
    errors = validate_template_library()
    if errors:
        raise ValueError("\n".join(errors))


def sample_life_path(
    *,
    seed: int = 42,
    episode_count: int = 6,
    template_ids: Iterable[str] | None = None,
) -> GeneratedLifePath:
    """Sample episode templates, schedule them by age, and return an interleaved path."""
    validate_templates()
    rng = random.Random(seed)
    templates_by_id = {template.id: template for template in EPISODE_TEMPLATES}
    if template_ids is None:
        candidates = _weighted_template_candidates(rng, episode_count)
    else:
        candidates = [templates_by_id[template_id] for template_id in template_ids]

    accepted: list[EpisodeInstance] = []
    selected_ids: list[str] = []
    latest_rejections: dict[str, Rejection] = {}
    pending = list(candidates)
    occurrence_counts: dict[str, int] = {}

    for _ in range(3):
        if len(accepted) >= episode_count or not pending:
            break
        next_pending: list[EpisodeTemplate] = []
        accepted_this_pass = False
        for template in pending:
            if len(accepted) >= episode_count:
                break
            occurrence_counts[template.id] = occurrence_counts.get(template.id, 0) + 1
            instance = _schedule_compatible_instance(
                template=template,
                rng=rng,
                accepted=tuple(accepted),
                sequential=template_ids is not None,
                occurrence_index=occurrence_counts[template.id],
            )
            valid, rejection = validate_episode_set(tuple(accepted + [instance]))
            if valid:
                accepted.append(instance)
                if instance.template_id not in selected_ids:
                    selected_ids.append(instance.template_id)
                latest_rejections.pop(instance.template_id, None)
                accepted_this_pass = True
            else:
                if rejection:
                    latest_rejections[instance.template_id] = Rejection(
                        episode_id=instance.template_id,
                        reason=rejection.reason,
                        conflicted_event_id=rejection.conflicted_event_id,
                        age=rejection.age,
                    )
                next_pending.append(template)
        if not accepted_this_pass:
            break
        pending = next_pending

    return materialize_generated_path(
        seed=seed,
        selected_episode_ids=tuple(selected_ids),
        episodes=tuple(accepted),
        rejections=tuple(latest_rejections.values()),
    )


def write_life_path_json(path: GeneratedLifePath, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_path = output_dir / f"sample_seed_{path.seed}.json"
    rejection_path = output_dir / f"sample_seed_{path.seed}_rejections.json"
    sample_path.write_text(_to_json(asdict(path)), encoding="utf-8")
    rejection_path.write_text(_to_json([asdict(item) for item in path.rejections]), encoding="utf-8")
    return {"sample": sample_path, "rejections": rejection_path}


def _weighted_template_candidates(rng: random.Random, episode_count: int) -> list[EpisodeTemplate]:
    templates = list(EPISODE_TEMPLATES)
    candidates: list[EpisodeTemplate] = []
    unique_pool = list(templates)
    initial_count = min(len(unique_pool), max(episode_count * 2, episode_count))
    for _ in range(initial_count):
        picked = _weighted_choice(rng, unique_pool)
        candidates.append(picked)
        unique_pool.remove(picked)

    target_count = max(len(templates), episode_count * 4)
    while len(candidates) < target_count:
        candidates.append(_weighted_choice(rng, templates))
    return candidates


def _weighted_choice(rng: random.Random, templates: list[EpisodeTemplate]) -> EpisodeTemplate:
    weights = [max(0.01, template.sampling_weight) for template in templates]
    return rng.choices(templates, weights=weights, k=1)[0]


def _schedule_template(template: EpisodeTemplate, rng: random.Random) -> EpisodeInstance:
    return schedule_template_instance(
        template=template,
        rng=rng,
        start_age=rng.randint(*template.start_age_range),
    )


def _schedule_compatible_instance(
    *,
    template: EpisodeTemplate,
    rng: random.Random,
    accepted: tuple[EpisodeInstance, ...],
    sequential: bool,
    occurrence_index: int,
) -> EpisodeInstance:
    fallback: EpisodeInstance | None = None
    for start_event_index in _candidate_start_indexes(template, accepted):
        start_age = _next_start_age(template, rng, accepted, sequential)
        instance = schedule_template_instance(
            template=template,
            rng=rng,
            start_age=start_age,
            occurrence_index=occurrence_index,
            start_event_index=start_event_index,
        )
        if fallback is None:
            fallback = instance
        valid, _ = validate_episode_set(tuple(accepted + (instance,)))
        if valid:
            return instance
    if fallback is None:
        raise ValueError(f"could not schedule template {template.id}")
    return fallback


def _candidate_start_indexes(template: EpisodeTemplate, accepted: tuple[EpisodeInstance, ...]) -> tuple[int, ...]:
    if not accepted:
        return (0,)
    prior_events = {event_id for episode in accepted for event_id, _ in episode.event_ages}
    indexes = [0]
    for index, event_id in enumerate(template.event_ids):
        if index == 0:
            continue
        if _can_enter_template_at_event(event_id, prior_events):
            indexes.append(index)
    return tuple(dict.fromkeys(indexes))


def _can_enter_template_at_event(event_id: str, prior_events: set[str]) -> bool:
    child_anchor_events = {
        "relationship_childbirth",
        "relationship_adoption",
    }
    if event_id.startswith("relationship_child_") or event_id in child_anchor_events:
        return False
    return event_id in prior_events or _event_has_state_anchor(event_id, prior_events)


def _event_has_state_anchor(event_id: str, prior_events: set[str]) -> bool:
    employed_events = {
        "career_employment",
        "career_reinstatement",
        "career_job_change",
        "career_transfer",
        "career_education",
    }
    if event_id in {"career_leave", "career_resignation", "career_job_change", "career_transfer", "career_study_abroad"}:
        return bool(prior_events & employed_events)
    if event_id == "career_reinstatement":
        return bool(prior_events & {"career_leave", "career_study_abroad"})
    return False


def _next_start_age(
    template: EpisodeTemplate,
    rng: random.Random,
    accepted: tuple[EpisodeInstance, ...],
    sequential: bool,
) -> int:
    remarriage_start_age = _remarriage_start_age(template, rng, accepted)
    if remarriage_start_age is not None:
        return remarriage_start_age
    if sequential and accepted:
        previous_end_age = max(age for episode in accepted for _, age in episode.event_ages)
        return max(template.start_age_range[0], previous_end_age + rng.randint(1, 4))
    return rng.randint(*template.start_age_range)


def _remarriage_start_age(
    template: EpisodeTemplate,
    rng: random.Random,
    accepted: tuple[EpisodeInstance, ...],
) -> int | None:
    if not accepted or not template.event_ids or template.event_ids[0] != "relationship_marriage":
        return None

    divorce_ages = [
        age
        for episode in accepted
        for event_id, age in episode.event_ages
        if event_id == "relationship_divorce"
    ]
    if not divorce_ages:
        return None

    latest_divorce_age = max(divorce_ages)
    remarriage_upper_age = {
        "marriage_only_core": 80,
        "marriage_childbirth_education_arc": 45,
        "marriage_adoption_education_arc": 65,
    }.get(template.id, template.start_age_range[1])
    lower = max(template.start_age_range[0], latest_divorce_age + 1)
    upper = max(template.start_age_range[1], remarriage_upper_age)
    if lower > upper:
        return upper
    return rng.randint(lower, upper)


def schedule_template_instance(
    *,
    template: EpisodeTemplate,
    rng: random.Random,
    start_age: int,
    occurrence_index: int = 1,
    start_event_index: int = 0,
    event_ages: tuple[tuple[str, int], ...] | None = None,
) -> EpisodeInstance:
    instance_id = template.id if occurrence_index == 1 else f"{template.id}#{occurrence_index}"
    if event_ages is None:
        event_ages = _event_ages_from_start(template, rng, start_age, start_event_index=start_event_index)
    return EpisodeInstance(
        template_id=instance_id,
        template_name=template.name,
        domain=template.domain,
        kind=template.kind,
        event_ages=event_ages,
        priority=template.priority,
        source_template_id=template.id,
    )


def _event_ages_from_start(
    template: EpisodeTemplate,
    rng: random.Random,
    start_age: int,
    *,
    start_event_index: int = 0,
) -> tuple[tuple[str, int], ...]:
    if start_event_index < 0 or start_event_index >= len(template.event_ids):
        raise ValueError(f"invalid start_event_index for {template.id}: {start_event_index}")
    if template.id == "marriage_adoption_education_arc" and start_event_index == 0:
        return _adoption_event_ages(rng, start_age)
    ages = [start_age]
    current = start_age
    for min_gap, max_gap in template.gap_ranges[start_event_index:]:
        current += rng.randint(min_gap, max_gap)
        ages.append(current)
    return tuple(zip(template.event_ids[start_event_index:], ages))


def _adoption_event_ages(rng: random.Random, marriage_age: int) -> tuple[tuple[str, int], ...]:
    adoption_age = marriage_age + rng.randint(1, 6)
    child_age_at_adoption = rng.randint(0, 17)
    event_ages: list[tuple[str, int]] = [
        ("relationship_marriage", marriage_age),
        ("relationship_adoption", adoption_age),
    ]

    school_milestones = (
        ("relationship_child_primary_school_entry", 6, 7),
        ("relationship_child_middle_school_entry", 12, 13),
        ("relationship_child_high_school_entry", 15, 16),
    )
    latest_child_age = child_age_at_adoption
    for event_id, min_child_age, max_child_age in school_milestones:
        if child_age_at_adoption > max_child_age:
            continue
        target_child_age = rng.randint(max(child_age_at_adoption, min_child_age), max_child_age)
        event_ages.append((event_id, adoption_age + target_child_age - child_age_at_adoption))
        latest_child_age = max(latest_child_age, target_child_age)

    independence_child_age = rng.randint(max(latest_child_age + 1, 18), 30)
    event_ages.append(("relationship_child_independence", adoption_age + independence_child_age - child_age_at_adoption))
    return tuple(sorted(event_ages, key=lambda item: (item[1], item[0])))


def _to_json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
