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
        committed = memory.committed(update.path)
        matching_pending = [
            cell
            for cell in memory.history(update.path)
            if cell.status == CellStatus.PENDING
            and cell.source_event_instance_id == update.source_event_instance_id
        ]
        if update.operation in {MemoryOperation.CREATE, MemoryOperation.UPDATE}:
            # Repeated equal expenses are still distinct event facts; the
            # source event and validity interval carry their identity.
            if update.path == "cashflow.recent_one_off_expense":
                return False
            # Even an equal value must confirm/close a pending proposal.
            return (
                not matching_pending
                and committed is not None
                and committed.status == CellStatus.CURRENT
                and committed.value == update.new_value
            )
        if update.operation == MemoryOperation.MARK_STALE:
            return committed is None or committed.status == CellStatus.STALE
        if update.operation == MemoryOperation.NEEDS_VERIFICATION:
            return committed is not None and committed.status == CellStatus.NEEDS_VERIFICATION
        if update.operation == MemoryOperation.ARCHIVE:
            return committed is None
        if update.operation == MemoryOperation.SET_NOT_APPLICABLE:
            return (
                not matching_pending
                and committed is not None
                and committed.status == CellStatus.NOT_APPLICABLE
            )
        if update.operation == MemoryOperation.SET_PENDING:
            return bool(matching_pending and matching_pending[-1].value == update.new_value)
        if update.operation == MemoryOperation.CLEAR_PENDING:
            return not any(
                (
                    cell.status == CellStatus.PENDING
                    and cell.source_event_instance_id == update.source_event_instance_id
                )
                or (
                    cell.status == CellStatus.NEEDS_VERIFICATION
                    and cell.source_event_instance_id == update.source_event_instance_id
                )
                for cell in memory.history(update.path)
            )
        return False

    @staticmethod
    def _conditions_match(spec: dict[str, Any], instance: EventInstance) -> bool:
        """Match an optional declarative equality filter against event params."""
        conditions = spec.get("when") or {}
        for name, expected in conditions.items():
            actual = instance.params.get(name)
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

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
            if (
                instance.event_id == "housing_home_purchase"
                and not instance.params.get("post_purchase_move", True)
                and path in {
                    "housing.residence_status",
                    "housing.address",
                    "housing.contract_type",
                    "housing.mortgage_status",
                }
            ):
                return None
            if (
                instance.event_id == "housing_home_sale"
                and instance.params.get("sold_property_role") != "primary_residence"
                and path in {
                    "housing.residence_status",
                    "housing.address",
                    "housing.contract_type",
                    "housing.mortgage_status",
                }
            ):
                return None
            return op

        if instance.event_id == "housing_home_purchase" and not instance.params.get("post_purchase_move", True):
            return None
        if instance.event_id == "housing_home_sale" and instance.params.get("sold_property_role") != "primary_residence":
            return None

        if instance.event_id == "housing_move":
            residence = instance.params.get("new_residence_status")
            if residence != "wolse":
                if to_status == EventStatus.UPCOMING:
                    return None
                if to_status == EventStatus.OCCURRED:
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
        template_id = instance.memory_delta_template_id or instance.event_id
        template = self.registry.get(template_id) or {}
        hook_spec = template.get(hook) or {}
        specs = list(hook_spec.get("memory_updates") or []) + list(hook_spec.get("pending_memory") or [])

        applied: list[MemoryUpdate] = []
        allowed = _ALLOWED_OPS_BY_STATUS[to_status]
        for spec in specs:
            if not self._conditions_match(spec, instance):
                continue
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
