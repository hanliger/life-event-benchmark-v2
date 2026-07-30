"""Benchmark item models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

class CounterfactualOption(BaseModel):
    option_id: str  # A/B/C/...
    text: str
    correct: bool = False
    error_type: str | None = None


class BenchmarkItem(BaseModel):
    item_id: str
    stage: str  # stage1_event_status|stage2_memory_value|stage3_multi_hop_mcq
    trajectory_id: str
    prefix_id: str
    visible_sessions: list[str]
    question: str
    options: list[CounterfactualOption] = Field(default_factory=list)
    gold: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    filter_status: str = "keep"  # keep|too_easy|leakage_suspected|ambiguous
    filter_votes: list[dict[str, Any]] = Field(default_factory=list)
