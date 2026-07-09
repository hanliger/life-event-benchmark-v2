"""Apply event->memory delta templates on lifecycle transitions.

Lifecycle policy (hard-enforced here, independent of what a template declares):
  weak_signal -> only set_pending / needs_verification (optional)
  upcoming    -> only set_pending / needs_verification
  occurred    -> update / create / mark_stale / archive / needs_verification / reactivate
  cancelled   -> only clear_pending / no_update
  no_event    -> no_update
Full memory history is preserved (old values archived, never deleted), so
stale/historical values remain available as benchmark distractors.
"""

from __future__ import annotations

import random
from typing import Any

from ..fsm.models import EventInstance, EventStatus
from ..io import RepoPaths, load_yaml
from .models import CellStatus, FinancialMemoryState, MemoryOperation, MemoryUpdate

_ALLOWED_OPS_BY_STATUS: dict[EventStatus, set[MemoryOperation]] = {
    EventStatus.WEAK_SIGNAL: {MemoryOperation.SET_PENDING, MemoryOperation.NEEDS_VERIFICATION, MemoryOperation.NO_UPDATE},
    EventStatus.UPCOMING: {MemoryOperation.SET_PENDING, MemoryOperation.NEEDS_VERIFICATION, MemoryOperation.NO_UPDATE},
    EventStatus.OCCURRED: {
        MemoryOperation.CREATE,
        MemoryOperation.UPDATE,
        MemoryOperation.MARK_STALE,
        MemoryOperation.ARCHIVE,
        MemoryOperation.NEEDS_VERIFICATION,
        MemoryOperation.REACTIVATE,
        MemoryOperation.SET_NOT_APPLICABLE,
        MemoryOperation.NO_UPDATE,
    },
    EventStatus.CANCELLED: {MemoryOperation.CLEAR_PENDING, MemoryOperation.NO_UPDATE},
    EventStatus.NO_EVENT: {MemoryOperation.NO_UPDATE},
}

_HOOK_BY_STATUS = {
    EventStatus.WEAK_SIGNAL: "on_weak_signal",
    EventStatus.UPCOMING: "on_upcoming",
    EventStatus.OCCURRED: "on_occurred",
    EventStatus.CANCELLED: "on_cancelled",
}


class DeltaEngine:
    def __init__(self, paths: RepoPaths | None = None):
        paths = paths or RepoPaths.default()
        self.registry: dict[str, Any] = load_yaml(paths.registries / "event_to_memory_delta.yaml")

    def _resolve_value(self, spec: dict[str, Any], instance: EventInstance) -> Any:
        value_from = spec.get("value_from")
        if value_from is None:
            return None
        if value_from.startswith("param:"):
            return instance.params.get(value_from.split(":", 1)[1])
        if value_from.startswith("literal:"):
            return value_from.split(":", 1)[1]
        return value_from

    @staticmethod
    def _is_noop(memory: FinancialMemoryState, update: MemoryUpdate) -> bool:
        latest = memory.latest(update.path)
        if update.operation in {MemoryOperation.CREATE, MemoryOperation.UPDATE}:
            return latest is not None and latest.status == CellStatus.CURRENT and latest.value == update.new_value
        if update.operation == MemoryOperation.MARK_STALE:
            return latest is None or latest.status == CellStatus.STALE
        if update.operation == MemoryOperation.NEEDS_VERIFICATION:
            return latest is not None and latest.status == CellStatus.NEEDS_VERIFICATION
        if update.operation == MemoryOperation.ARCHIVE:
            return latest is None or latest.status in {CellStatus.HISTORICAL, CellStatus.CANCELLED}
        if update.operation == MemoryOperation.SET_NOT_APPLICABLE:
            return latest is not None and latest.status == CellStatus.NOT_APPLICABLE
        if update.operation == MemoryOperation.CLEAR_PENDING:
            return not any(
                cell.status == CellStatus.PENDING
                or (
                    cell.status == CellStatus.NEEDS_VERIFICATION
                    and cell.source_event_instance_id == update.source_event_instance_id
                )
                for cell in memory.history(update.path)
            )
        return False

    def _normalize_housing_operation(
        self,
        memory: FinancialMemoryState,
        instance: EventInstance,
        to_status: EventStatus,
        path: str,
        op: MemoryOperation,
    ) -> MemoryOperation | None:
        """Keep rent-only memory paths inapplicable outside wolse.

        Jeonse has a deposit/contract but no monthly rent amount/payee. Owner
        and other non-wolse residence states likewise must not carry current
        rent fields just because a move/rental event touched housing memory.
        """
        if path not in {"housing.rent_amount", "housing.rent_payee"}:
            return op

        if instance.event_id == "housing_rental_contract":
            contract_type = instance.params.get("new_contract_type")
            if contract_type != "wolse":
                if to_status == EventStatus.UPCOMING:
                    return None
                if to_status == EventStatus.OCCURRED:
                    return MemoryOperation.SET_NOT_APPLICABLE

        if instance.event_id == "housing_move" and path == "housing.rent_payee":
            residence = memory.current_value("housing.residence_status")
            if residence != "wolse":
                return MemoryOperation.SET_NOT_APPLICABLE

        return op

    def apply_transition(
        self,
        memory: FinancialMemoryState,
        instance: EventInstance,
        to_status: EventStatus,
        month_index: int,
        rng: random.Random | None = None,
    ) -> list[MemoryUpdate]:
        """Apply the delta template hook for this transition. Returns applied
        updates (with old_value provenance filled in)."""
        rng = rng or random.Random(0)
        hook = _HOOK_BY_STATUS.get(to_status)
        if hook is None:
            return []
        template = self.registry.get(instance.event_id) or {}
        hook_spec = template.get(hook) or {}
        specs = list(hook_spec.get("memory_updates") or []) + list(hook_spec.get("pending_memory") or [])

        applied: list[MemoryUpdate] = []
        allowed = _ALLOWED_OPS_BY_STATUS[to_status]
        for spec in specs:
            op = MemoryOperation(spec["operation"])
            op = self._normalize_housing_operation(memory, instance, to_status, spec["path"], op)
            if op is None:
                continue
            if op not in allowed:
                raise ValueError(
                    f"{instance.event_id}.{hook}: operation '{op.value}' not allowed for status {to_status.value}"
                )
            if spec.get("optional") and rng.random() < 0.5:
                continue
            update = MemoryUpdate(
                path=spec["path"],
                operation=op,
                new_value=self._resolve_value(spec, instance),
                month_index=month_index,
                source_event_instance_id=instance.event_instance_id,
                event_status=to_status.value,
                optional=bool(spec.get("optional", False)),
            )
            if self._is_noop(memory, update):
                continue
            applied.append(memory.apply(update))
        return applied
