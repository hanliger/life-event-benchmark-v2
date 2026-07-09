"""Dialogue session models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

SESSION_TYPES = (
    "routine_financial",
    "weak_signal_evidence",
    "upcoming_evidence",
    "occurred_evidence",
    "cancellation_evidence",
    "consequence_session",
    "hard_negative",
    "stale_recall_session",
    "evaluation_target_session",
)


class Turn(BaseModel):
    speaker: str  # user|assistant
    text: str


class CueAnnotation(BaseModel):
    turn_index: int
    cue_type: str
    cue_text: str | None = None
    linked_memory_path: str | None = None


class DialogueGenerationPlan(BaseModel):
    session_id: str = ""  # assigned after chronological ordering
    trajectory_id: str
    month_index: int
    age: int
    session_type: str
    linked_event_instance_id: str | None = None
    event_status_after_session: str = "no_event"
    near_miss_event_label: str | None = None
    mapped_action: str | None = None  # FA code
    financial_task: str = ""
    must_include_cues: list[str] = Field(default_factory=list)
    must_not_include_terms: list[str] = Field(default_factory=list)
    target_memory_paths: list[str] = Field(default_factory=list)
    target_action_ids: list[str] = Field(default_factory=list)
    structured_context: dict[str, Any] = Field(default_factory=dict)
    desired_single_session_recoverability: str = "medium"  # low|medium|high
    desired_cumulative_recoverability: str = "high"  # medium|high


class QualitySelfCheck(BaseModel):
    no_direct_life_event_mention: bool = True
    no_assistant_label_leakage: bool = True
    financial_task_clear: bool = True
    turn_count_ok: bool = True


class Session(BaseModel):
    session_id: str
    trajectory_id: str
    month_index: int
    age: int
    session_type: str
    linked_event_instance_id: str | None = None
    event_status_after_session: str = "no_event"
    mapped_action: str | None = None
    financial_task: str = ""
    turns: list[Turn] = Field(default_factory=list)
    cue_annotations: list[CueAnnotation] = Field(default_factory=list)
    quality_self_check: QualitySelfCheck = Field(default_factory=QualitySelfCheck)
    generator: str = "mock"  # mock|openai|anthropic|dry_run
    plan: DialogueGenerationPlan | None = None
