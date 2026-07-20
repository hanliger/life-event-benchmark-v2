"""Dialogue session models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    linked_memory_operation: str | None = None
    linked_memory_value: Any = None
    evidence_text: str | None = None


class PlannedCue(BaseModel):
    """Structured, lifecycle-calibrated evidence requested from a dialogue."""

    cue_id: str
    semantic_instruction_ko: str
    status: str
    linked_memory_paths: list[str] = Field(default_factory=list)
    required_value_source: str | list[str] | None = None
    required_value: Any = None
    exact_surface_required: bool = False
    surface_hint: str | None = None
    cue_role: str  # event_signal|memory_fact|cancellation|stale_value|current_value
    linked_memory_operation: str | None = None
    allow_reuse_across_statuses: bool = False


class StaleMemoryPair(BaseModel):
    path: str
    old_value: Any
    current_value: Any
    old_valid_until: int | None = None
    current_valid_from: int | None = None


class DialogueGenerationPlan(BaseModel):
    session_id: str = ""  # assigned after chronological ordering
    trajectory_id: str
    month_index: int
    age: int
    transition_order: int = 0
    window_index: int | None = None
    position_in_window: int | None = None
    window_event_instance_id: str | None = None
    session_type: str
    linked_event_instance_id: str | None = None
    event_status_after_session: str = "no_event"
    near_miss_event_label: str | None = None
    mapped_action: str | None = None  # FA code
    financial_task: str = ""
    task_template_id: str | None = None
    task_user_goal_instruction: str | None = None
    task_selection_score: float | None = None
    task_selection_reasons: list[str] = Field(default_factory=list)
    task_grounding_paths: list[str] = Field(default_factory=list)
    task_used_generic_fallback: bool = False
    must_include_cues: list[str] = Field(default_factory=list)
    planned_cues: list[PlannedCue] = Field(default_factory=list)
    must_not_include_terms: list[str] = Field(default_factory=list)
    evidence_memory_paths: list[str] = Field(default_factory=list)
    session_update_paths: list[str] = Field(default_factory=list)
    event_update_paths: list[str] = Field(default_factory=list)
    target_memory_paths: list[str] = Field(default_factory=list)
    target_action_ids: list[str] = Field(default_factory=list)
    action_impact_types: list[str] = Field(default_factory=list)
    evidence_bundle_id: str | None = None
    evidence_stage_index: int | None = None
    evidence_stage_count: int | None = None
    prior_planned_cue_ids: list[str] = Field(default_factory=list)
    cumulative_cue_ids_after_session: list[str] = Field(default_factory=list)
    stale_memory_pairs: list[StaleMemoryPair] = Field(default_factory=list)
    hard_negative_type: str | None = None
    near_miss_event_id: str | None = None
    near_miss_explanation: str | None = None
    protected_memory_paths: list[str] = Field(default_factory=list)
    expected_memory_operation: str | None = None
    filler_allowed_month_range: tuple[int, int] | None = None
    filler_placement_overflow: bool = False
    structured_context: dict[str, Any] = Field(default_factory=dict)
    desired_single_session_recoverability: str = "medium"  # low|medium|high
    desired_cumulative_recoverability: str = "high"  # medium|high


class QualitySelfCheck(BaseModel):
    no_direct_life_event_mention: bool = True
    no_assistant_label_leakage: bool = True
    financial_task_clear: bool = True
    turn_count_ok: bool = True


class RawDialogueResponse(BaseModel):
    """Provider structured-output contract before plan-aware validation."""

    model_config = ConfigDict(extra="forbid")

    turns: list[Turn]
    cue_annotations: list[CueAnnotation] = Field(default_factory=list)
    quality_self_check: QualitySelfCheck = Field(default_factory=QualitySelfCheck)


class Session(BaseModel):
    session_id: str
    trajectory_id: str
    month_index: int
    age: int
    transition_order: int = 0
    window_index: int | None = None
    position_in_window: int | None = None
    window_event_instance_id: str | None = None
    session_type: str
    linked_event_instance_id: str | None = None
    event_status_after_session: str = "no_event"
    mapped_action: str | None = None
    financial_task: str = ""
    turns: list[Turn] = Field(default_factory=list)
    cue_annotations: list[CueAnnotation] = Field(default_factory=list)
    quality_self_check: QualitySelfCheck = Field(default_factory=QualitySelfCheck)
    generator: str = "mock"  # mock|openai|anthropic|dry_run
    generation_metadata: dict[str, Any] = Field(default_factory=dict)
    plan: DialogueGenerationPlan | None = None
