"""Life-event FSM models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventStatus(str, Enum):
    NO_EVENT = "no_event"
    WEAK_SIGNAL = "weak_signal"
    UPCOMING = "upcoming"
    OCCURRED = "occurred"
    CANCELLED = "cancelled"


class AgeGuard(BaseModel):
    min_age: int = 0
    max_age: int = 120


class StateGuard(BaseModel):
    """required/forbidden map LifeState field -> allowed/blocked values."""

    required: dict[str, list[Any]] = Field(default_factory=dict)
    forbidden: dict[str, list[Any]] = Field(default_factory=dict)


class LifecycleConfig(BaseModel):
    weak_signal_months: tuple[int, int] = (1, 3)
    upcoming_months: tuple[int, int] = (1, 3)
    p_skip_weak_signal: float = 0.3
    p_skip_upcoming: float = 0.1
    p_cancel_from_weak: float = 0.1
    p_cancel_from_upcoming: float = 0.05


class DiscriminativeCues(BaseModel):
    required: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)


class LifeEventTemplate(BaseModel):
    event_id: str
    label_ko: str
    label_en: str
    domain: str
    active: bool = True
    mvp: bool = False
    age_guard: AgeGuard = Field(default_factory=AgeGuard)
    state_guards: StateGuard = Field(default_factory=StateGuard)
    cooldown_months: int = 12
    base_rate_per_year: float = 0.05
    sampling_multiplier: float = 1.0
    repeat_policy: str = "repeatable"  # once|repeatable
    occurrence_scope: str = "persona"  # persona|household|per_child
    max_occurrences: int | None = None
    sampling_source: str = "event_or_subgraph"  # event_or_subgraph|subgraph_only|fixed_or_subgraph
    age_weights: dict[str, float] = Field(default_factory=dict)
    requires_child_entry_age: bool = False
    lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)
    mapped_actions_by_status: dict[str, list[str]] = Field(default_factory=dict)
    discriminative_cues_ko: DiscriminativeCues = Field(default_factory=DiscriminativeCues)
    sibling_confusions: list[str] = Field(default_factory=list)
    memory_delta_template_id: str = ""
    action_impact_template_id: str = ""
    life_generator_node_ids: list[str] = Field(default_factory=list)
    event_parameter_schema: dict[str, Any] = Field(default_factory=dict)
    parameter_guards: dict[str, Any] = Field(default_factory=dict)

    def age_weight(self, age: int) -> float:
        for bracket, weight in self.age_weights.items():
            lo, hi = bracket.split("-")
            if int(lo) <= age <= int(hi):
                return float(weight)
        return 1.0


class EventStatusHistoryItem(BaseModel):
    status: EventStatus
    month_index: int
    age: int


class EventInstance(BaseModel):
    event_instance_id: str
    event_id: str
    label_ko: str
    domain: str
    status: EventStatus = EventStatus.NO_EVENT
    status_history: list[EventStatusHistoryItem] = Field(default_factory=list)
    start_month: int = 0
    occurred_month: int | None = None
    cancelled_month: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # Keep the benchmark event ID stable while allowing multiple benchmark
    # events to reuse an existing memory/action transition template.
    memory_delta_template_id: str = ""
    action_impact_template_id: str = ""
    generation_source: str = "hazard"  # hazard|forced

    def status_as_of(self, month_index: int) -> EventStatus:
        status = EventStatus.NO_EVENT
        for item in self.status_history:
            if item.month_index <= month_index:
                status = item.status
        return status
