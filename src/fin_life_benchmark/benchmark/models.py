"""Benchmark item models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

ERROR_TYPES = (
    "stale_memory_carryover",
    "missed_update",
    "wrong_sibling_event",
    "premature_update",
    "false_commit",
    "historical_state_contamination",
    "no_event_false_positive",
    "cancelled_ignored",
    "value_distractor",
    "first_hop_only",
    "second_hop_only",
    "difference_instead_of_sum",
    "arithmetic_distractor",
    "average_instead_of_sum",
    "underestimated_sum",
    "overestimated_sum",
    "wrong_first_hop",
    "wrong_second_hop",
    "wrong_both_hops",
    "reversed_hop_order",
    "first_state_carryover",
    "second_state_overgeneralization",
)


class CounterfactualOption(BaseModel):
    option_id: str  # A/B/C/...
    text: str
    correct: bool = False
    error_type: str | None = None


class BenchmarkItem(BaseModel):
    item_id: str
    # stage1_event_identification|stage2_memory_mcq|stage3_multi_hop_mcq
    stage: str
    reasoning_type: str | None = None  # single_hop|multi_hop
    trajectory_id: str
    prefix_id: str
    visible_sessions: list[str]
    question: str
    options: list[CounterfactualOption] = Field(default_factory=list)
    gold: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    filter_status: str = "keep"  # keep|too_easy|leakage_suspected|ambiguous
    filter_votes: list[dict[str, Any]] = Field(default_factory=list)
