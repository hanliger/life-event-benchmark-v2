"""Trajectory models: hidden life/financial state over monthly ticks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..actions.models import ActionImpact, StandingAction
from ..fsm.models import EventInstance
from ..memory.models import FinancialMemoryState, MemoryUpdate
from ..persona.models import NormalizedPersona


class ChildState(BaseModel):
    """A stable child identity; age alone is not sufficient for longitudinal events."""

    child_id: str
    age: int
    education_stage: str = "pre_school"


class PropertyState(BaseModel):
    """One explicitly identified property in a potentially multi-home portfolio."""

    property_id: str
    address: str
    acquired_month: int = 0
    acquisition_event_instance_id: str | None = None
    role: str = "secondary_property"  # primary_residence|secondary_property
    mortgage_status: str = "unknown"
    ownership_status: str = "owned"  # owned|sold
    disposed_month: int | None = None
    disposal_event_instance_id: str | None = None


class LifeState(BaseModel):
    """Hidden life state consulted by FSM guards. Field names are the guard
    vocabulary used in configs/registries/life_events.yaml."""

    marital_status: str = "single"
    employment_status: str = "unemployed"
    residence_status: str = "other"
    children_ages: list[int] = Field(default_factory=list)
    children: list[ChildState] = Field(default_factory=list)
    dependents_count: int = 0
    lives_with_parents: bool = False
    home_owned: bool = False
    properties: list[PropertyState] = Field(default_factory=list)
    primary_residence_property_id: str | None = None
    current_employer: str | None = None
    retirement_prepared: bool = False
    pension_receiving: bool = False
    in_education: bool = False

    @property
    def has_children(self) -> bool:
        return len(self.children_ages) > 0

    @property
    def has_dependents(self) -> bool:
        return self.dependents_count > 0

    @property
    def can_add_child(self) -> bool:
        return len(self.children_ages) < 4

    @property
    def can_add_dependent(self) -> bool:
        return self.dependents_count < 4

    def guard_value(self, field: str) -> Any:
        if field == "has_children":
            return self.has_children
        if field == "has_dependents":
            return self.has_dependents
        if field == "can_add_child":
            return self.can_add_child
        if field == "can_add_dependent":
            return self.can_add_dependent
        return getattr(self, field)

    def tick_year(self) -> None:
        self.children_ages = [a + 1 for a in self.children_ages]
        for child in self.children:
            child.age += 1


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
    transition_order: int = 0


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
    # Ordered checkpoints preserve multiple transitions within the same month.
    # Keys are "<month_index>:<transition_order>".
    ordered_state_snapshots: dict[str, PersonaState] = Field(default_factory=dict)
    ordered_memory_snapshots: dict[str, FinancialMemoryState] = Field(default_factory=dict)
    ordered_action_snapshots: dict[str, list[StandingAction]] = Field(default_factory=dict)
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
    source_event_instance_id: str | None = None
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
    checkpoint_session_count: int = 0
    occurred_event_count: int = 0
    gold_life_events: list[GoldLifeEvent] = Field(default_factory=list)
    gold_memory_updates: list[GoldMemoryUpdate] = Field(default_factory=list)
    gold_action_decisions: list[GoldActionDecision] = Field(default_factory=list)
    gold_full_memory_state: dict[str, Any] = Field(default_factory=dict)
    gold_full_action_state: list[dict[str, Any]] = Field(default_factory=list)
    # Storage optimization: when the entire gold payload (the five gold_*
    # fields) is identical to the previous prefix of the same trajectory —
    # true for ~96% of prefixes, which sit between events — those fields are
    # blanked on disk and this flag is set. Reload with
    # gold.loader.read_prefix_gold() to carry them forward. ~20x smaller file.
    repeats_previous: bool = False
