"""Data models for episode-based life path generation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtraNode:
    id: str
    name: str
    domain: str
    actions: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class LockRule:
    after_event_id: str
    blocked_event_ids: tuple[str, ...]
    duration_years: int
    reason: str


@dataclass(frozen=True)
class EpisodeTemplate:
    id: str
    name: str
    domain: str
    kind: str
    event_ids: tuple[str, ...]
    gap_ranges: tuple[tuple[int, int], ...]
    start_age_range: tuple[int, int]
    priority: int
    sampling_weight: float = 1.0
    actors: tuple[str, ...] = ()
    child_age_by_event: tuple[tuple[str, tuple[int, int]], ...] = ()
    can_overlap_domains: tuple[str, ...] = ()
    cannot_overlap_episode_ids: tuple[str, ...] = ()
    locks: tuple[LockRule, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    notes: str = ""

    def internal_edges(self) -> tuple[tuple[str, str], ...]:
        return tuple(zip(self.event_ids, self.event_ids[1:]))


@dataclass(frozen=True)
class EpisodeInstance:
    template_id: str
    template_name: str
    domain: str
    kind: str
    event_ages: tuple[tuple[str, int], ...]
    priority: int
    source_template_id: str = ""

    @property
    def event_steps(self) -> tuple[tuple[str, int], ...]:
        """Backward-compatible alias; values are now person ages in years."""
        return self.event_ages


@dataclass(frozen=True)
class TimelineEvent:
    age: int
    event_id: str
    name: str
    domain: str
    episode_id: str
    episode_name: str
    episode_kind: str
    priority: int
    actor: str
    child_age: int | None = None

    @property
    def step(self) -> int:
        """Backward-compatible alias; values are now person ages in years."""
        return self.age


@dataclass(frozen=True)
class Rejection:
    episode_id: str
    reason: str
    conflicted_event_id: str | None = None
    age: int | None = None

    @property
    def step(self) -> int | None:
        """Backward-compatible alias; values are now person ages in years."""
        return self.age


@dataclass
class GeneratorState:
    marital_status: str | None = "single"
    employment_status: str | None = None
    housing_status: str | None = None
    children_count: int = 0
    dependents_count: int = 0
    retirement_prepared: bool = False
    purchased_home: bool = False
    child_milestones: set[str] = field(default_factory=set)
    locks: list[tuple[int, tuple[str, ...], str]] = field(default_factory=list)


@dataclass(frozen=True)
class GeneratedLifePath:
    seed: int
    selected_episode_ids: tuple[str, ...]
    episodes: tuple[EpisodeInstance, ...]
    events: tuple[TimelineEvent, ...]
    rejections: tuple[Rejection, ...]
