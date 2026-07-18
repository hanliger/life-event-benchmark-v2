#!/usr/bin/env python
"""Regenerate checked-in JSON Schemas from the authoritative Pydantic models."""

from __future__ import annotations

import json

import _bootstrap  # noqa: F401

from fin_life_benchmark.actions.models import ActionImpact, StandingAction
from fin_life_benchmark.benchmark.models import BenchmarkItem
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan, Session
from fin_life_benchmark.fsm.models import EventInstance, LifeEventTemplate
from fin_life_benchmark.memory.models import FinancialMemoryState, MemoryUpdate
from fin_life_benchmark.persona.models import NormalizedPersona
from fin_life_benchmark.trajectory.models import PrefixGold, Trajectory
from fin_life_benchmark.io import RepoPaths


MODELS = {
    "action_impact.schema.json": ActionImpact,
    "benchmark_item.schema.json": BenchmarkItem,
    "dialogue_generation_plan.schema.json": DialogueGenerationPlan,
    "event_instance.schema.json": EventInstance,
    "financial_memory_state.schema.json": FinancialMemoryState,
    "life_event_template.schema.json": LifeEventTemplate,
    "memory_update.schema.json": MemoryUpdate,
    "normalized_persona.schema.json": NormalizedPersona,
    "prefix_gold.schema.json": PrefixGold,
    "session.schema.json": Session,
    "standing_action.schema.json": StandingAction,
    "trajectory.schema.json": Trajectory,
}


def main() -> int:
    schema_dir = RepoPaths.default().root / "schemas"
    for filename, model in MODELS.items():
        path = schema_dir / filename
        path.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
