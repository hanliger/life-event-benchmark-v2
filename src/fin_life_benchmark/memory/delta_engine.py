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
from .models import FinancialMemoryState, MemoryOperation, MemoryUpdate

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
            applied.append(memory.apply(update))
        return applied
