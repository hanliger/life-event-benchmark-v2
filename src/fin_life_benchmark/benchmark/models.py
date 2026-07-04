"""Benchmark item models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

ERROR_TYPES = (
    "stale_memory_carryover",
    "stale_action_carryover",
    "unsafe_premature_execution",
    "missed_update",
    "wrong_sibling_event",
    "overreaction",
    "premature_update",
    "false_commit",
    "historical_state_contamination",
    "no_event_false_positive",
    "cancelled_ignored",
)


class CounterfactualOption(BaseModel):
    option_id: str  # A/B/C/...
    text: str
    correct: bool = False
    error_type: str | None = None


class BenchmarkItem(BaseModel):
    item_id: str
    stage: str  # stage1_event_status|stage2_memory_mcq|stage3_action_decision|stage3_action_mcq
    trajectory_id: str
    prefix_id: str
    visible_sessions: list[str]
    question: str
    options: list[CounterfactualOption] = Field(default_factory=list)
    gold: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    filter_status: str = "keep"  # keep|too_easy|leakage_suspected|ambiguous
    filter_votes: list[dict[str, Any]] = Field(default_factory=list)
