"""Trajectory models: hidden life/financial state over monthly ticks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..actions.models import ActionImpact, StandingAction
from ..fsm.models import EventInstance
from ..memory.models import FinancialMemoryState, MemoryUpdate
from ..persona.models import NormalizedPersona


class LifeState(BaseModel):
    """Hidden life state consulted by FSM guards. Field names are the guard
    vocabulary used in configs/registries/life_events.yaml."""

    marital_status: str = "single"
    employment_status: str = "unemployed"
    residence_status: str = "other"
    children_ages: list[int] = Field(default_factory=list)
    dependents_count: int = 0
    lives_with_parents: bool = False
    home_owned: bool = False
    retirement_prepared: bool = False
    pension_receiving: bool = False
    in_education: bool = False

    @property
    def has_children(self) -> bool:
        return len(self.children_ages) > 0

    def guard_value(self, field: str) -> Any:
        if field == "has_children":
            return self.has_children
        return getattr(self, field)

    def tick_year(self) -> None:
        self.children_ages = [a + 1 for a in self.children_ages]


class PersonaState(BaseModel):
    """LifeState + bookkeeping at a point in time."""

    month_index: int = 0
    age: int = 0
    life_state: LifeState = Field(default_factory=LifeState)


class StatusTransition(BaseModel):
    event_instance_id: str
    event_id: str
    from_status: str
    to_status: str


class TrajectoryStep(BaseModel):
    month_index: int
    age: int
    transitions: list[StatusTransition] = Field(default_factory=list)
    memory_updates: list[MemoryUpdate] = Field(default_factory=list)
    action_impacts: list[ActionImpact] = Field(default_factory=list)


class Trajectory(BaseModel):
    trajectory_id: str
    locale: str
    seed: int
    horizon_months: int
    persona: NormalizedPersona
    initial_persona_state: PersonaState
    initial_financial_memory_state: FinancialMemoryState
    initial_standing_actions: list[StandingAction]
    life_event_instances: list[EventInstance] = Field(default_factory=list)
    timeline_steps: list[TrajectoryStep] = Field(default_factory=list)
    state_snapshots: dict[str, PersonaState] = Field(default_factory=dict)  # month_index(str) -> state
    memory_snapshots: dict[str, FinancialMemoryState] = Field(default_factory=dict)
    action_snapshots: dict[str, list[StandingAction]] = Field(default_factory=dict)
    final_persona_state: PersonaState | None = None


class GoldLifeEvent(BaseModel):
    event_instance_id: str
    event_id: str = ""
    life_event_label: str
    event_status: str
    occurred: bool
    update_allowed: bool
    first_recoverable_session: str | None = None
    evidence_sessions: list[str] = Field(default_factory=list)
    evidence_turns: list[str] = Field(default_factory=list)


class GoldMemoryUpdate(BaseModel):
    path: str
    operation: str
    old_value: Any = None
    new_value: Any = None
    evidence_turns: list[str] = Field(default_factory=list)


class GoldActionDecision(BaseModel):
    action_id: str
    impact_type: str
    funds_movement: bool
    risk: str
    expected_decision: str
    must_not_execute: bool
    source_event_instance_id: str | None = None


class PrefixGold(BaseModel):
    prefix_id: str
    trajectory_id: str
    visible_sessions: list[str]
    time: dict[str, int]  # {age, month_index}
    gold_life_events: list[GoldLifeEvent] = Field(default_factory=list)
    gold_memory_updates: list[GoldMemoryUpdate] = Field(default_factory=list)
    gold_action_decisions: list[GoldActionDecision] = Field(default_factory=list)
    gold_full_memory_state: dict[str, Any] = Field(default_factory=dict)
    gold_full_action_state: list[dict[str, Any]] = Field(default_factory=list)
