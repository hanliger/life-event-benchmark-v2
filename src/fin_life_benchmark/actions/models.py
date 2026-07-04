"""Standing financial actions — first-class benchmark objects."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ActionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    PENDING = "pending"
    STALE = "stale"
    CANCELLED = "cancelled"
    HISTORICAL = "historical"


class ActionDecisionEnum(str, Enum):
    KEEP = "keep"
    UPDATE = "update"
    PAUSE = "pause"
    CANCEL = "cancel"
    ASK_CONFIRMATION = "ask_confirmation"
    EXECUTE = "execute"
    REJECT = "reject"
    NO_ACTION = "no_action"


class StandingAction(BaseModel):
    action_id: str
    type: str  # action type key from standing_action_schema.yaml
    label: str
    status: ActionStatus = ActionStatus.ACTIVE
    source_account: str | None = None
    destination: str | None = None
    amount: int | None = None
    frequency: str = "monthly"
    trigger_rule: str = "fixed_day"
    trigger_day: int | None = None
    funds_movement: bool = True
    risk: str = "high"  # low|high
    linked_memory_paths: list[str] = Field(default_factory=list)
    validity_status: str = "valid"  # valid|stale|needs_review
    last_confirmed_at: int | None = None  # month_index
    history: list[dict[str, Any]] = Field(default_factory=list)  # audit trail

    def snapshot(self, month_index: int, note: str) -> None:
        self.history.append(
            {
                "month_index": month_index,
                "status": self.status.value,
                "validity_status": self.validity_status,
                "destination": self.destination,
                "amount": self.amount,
                "trigger_day": self.trigger_day,
                "note": note,
            }
        )


class ActionImpact(BaseModel):
    action_id: str
    action_type: str
    impact_type: str
    expected_decision: ActionDecisionEnum
    risk: str = "high"
    funds_movement: bool = True
    must_not_execute: bool = True
    month_index: int | None = None
    source_event_instance_id: str | None = None
    event_status: str | None = None


class ActionDecision(BaseModel):
    action_id: str
    decision: ActionDecisionEnum
    reason: str = ""
    requires_user_confirmation: bool = False
