"""Financial memory state: per-path cell histories with full provenance.

Design rule: never delete. ``update`` archives the previous current cell
(status=historical, valid_until set) and appends a new current cell. Old
values therefore remain available as stale-memory distractors.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MemoryOperation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    MARK_STALE = "mark_stale"
    ARCHIVE = "archive"
    NEEDS_VERIFICATION = "needs_verification"
    SET_PENDING = "set_pending"
    CLEAR_PENDING = "clear_pending"
    REACTIVATE = "reactivate"
    SET_NOT_APPLICABLE = "set_not_applicable"
    NO_UPDATE = "no_update"


class CellStatus(str, Enum):
    CURRENT = "current"
    HISTORICAL = "historical"
    STALE = "stale"
    NEEDS_VERIFICATION = "needs_verification"
    PENDING = "pending"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class MemoryCell(BaseModel):
    path: str
    value: Any = None
    status: CellStatus = CellStatus.UNKNOWN
    confidence: float = 1.0
    valid_from: int | None = None  # month_index
    valid_until: int | None = None  # month_index
    last_confirmed_at: int | None = None  # month_index
    evidence_turns: list[str] = Field(default_factory=list)
    source_event_instance_id: str | None = None
    provenance: str = "initial"  # initial|event_delta|dialogue|manual


class MemoryUpdate(BaseModel):
    path: str
    operation: MemoryOperation
    old_value: Any = None
    new_value: Any = None
    month_index: int | None = None
    source_event_instance_id: str | None = None
    event_status: str | None = None  # lifecycle status that triggered this update
    evidence_turns: list[str] = Field(default_factory=list)
    optional: bool = False


class FinancialMemoryState(BaseModel):
    """Maps memory path -> full cell history (oldest first)."""

    cells: dict[str, list[MemoryCell]] = Field(default_factory=dict)

    # -- queries ------------------------------------------------------------
    def history(self, path: str) -> list[MemoryCell]:
        return self.cells.get(path, [])

    def latest(self, path: str) -> MemoryCell | None:
        hist = self.history(path)
        return hist[-1] if hist else None

    def current_value(self, path: str) -> Any:
        cell = self.latest(path)
        if cell is None or cell.status in {CellStatus.HISTORICAL, CellStatus.CANCELLED, CellStatus.NOT_APPLICABLE}:
            return None
        return cell.value

    def historical_values(self, path: str) -> list[Any]:
        return [c.value for c in self.history(path) if c.status == CellStatus.HISTORICAL and c.value is not None]

    # -- mutation -----------------------------------------------------------
    def set_initial(self, path: str, value: Any, month_index: int = 0, status: CellStatus = CellStatus.CURRENT) -> None:
        self.cells.setdefault(path, []).append(
            MemoryCell(
                path=path,
                value=value,
                status=status,
                valid_from=month_index,
                last_confirmed_at=month_index,
                provenance="initial",
            )
        )

    def apply(self, update: MemoryUpdate) -> MemoryUpdate:
        """Apply a MemoryUpdate, preserving history. Returns the update with
        old_value filled in."""
        hist = self.cells.setdefault(update.path, [])
        latest = hist[-1] if hist else None
        op = update.operation
        month = update.month_index

        if latest is not None and update.old_value is None:
            update.old_value = latest.value

        def _append(value: Any, status: CellStatus, confidence: float = 1.0) -> None:
            hist.append(
                MemoryCell(
                    path=update.path,
                    value=value,
                    status=status,
                    confidence=confidence,
                    valid_from=month,
                    source_event_instance_id=update.source_event_instance_id,
                    provenance="event_delta",
                    evidence_turns=list(update.evidence_turns),
                )
            )

        if op in {MemoryOperation.CREATE, MemoryOperation.UPDATE}:
            if latest is not None and latest.status in {
                CellStatus.CURRENT,
                CellStatus.NEEDS_VERIFICATION,
                CellStatus.STALE,
                CellStatus.NOT_APPLICABLE,
                CellStatus.UNKNOWN,
            }:
                latest.status = CellStatus.HISTORICAL
                latest.valid_until = month
            _append(update.new_value, CellStatus.CURRENT)
        elif op == MemoryOperation.MARK_STALE:
            if latest is not None:
                latest.status = CellStatus.STALE
                latest.confidence = min(latest.confidence, 0.4)
        elif op == MemoryOperation.ARCHIVE:
            if latest is not None:
                latest.status = CellStatus.HISTORICAL
                latest.valid_until = month
        elif op == MemoryOperation.NEEDS_VERIFICATION:
            if latest is not None:
                latest.status = CellStatus.NEEDS_VERIFICATION
                latest.confidence = min(latest.confidence, 0.6)
            else:
                _append(None, CellStatus.NEEDS_VERIFICATION, confidence=0.3)
        elif op == MemoryOperation.SET_PENDING:
            _append(update.new_value, CellStatus.PENDING, confidence=0.5)
        elif op == MemoryOperation.CLEAR_PENDING:
            for cell in hist:
                if cell.status == CellStatus.PENDING:
                    cell.status = CellStatus.CANCELLED
                    cell.valid_until = month
                elif cell.status == CellStatus.NEEDS_VERIFICATION and cell.source_event_instance_id == update.source_event_instance_id:
                    cell.status = CellStatus.CURRENT
                    cell.confidence = 1.0
        elif op == MemoryOperation.REACTIVATE:
            for cell in reversed(hist):
                if cell.status == CellStatus.HISTORICAL:
                    cell.status = CellStatus.CURRENT
                    cell.valid_until = None
                    break
        elif op == MemoryOperation.SET_NOT_APPLICABLE:
            if latest is not None and latest.status != CellStatus.NOT_APPLICABLE:
                latest.status = CellStatus.HISTORICAL
                latest.valid_until = month
            _append(None, CellStatus.NOT_APPLICABLE)
        elif op == MemoryOperation.NO_UPDATE:
            pass
        return update
