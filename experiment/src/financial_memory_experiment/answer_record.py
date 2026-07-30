"""Normalize a Stage 1 LLM answer into the item's Gold schema so it can be diffed.

Stage 1 prediction rows score a bare `event_id`, so `prediction="E001",
gold="E003"` says nothing without joining back to the item file, and nothing
records what the model claimed in Gold terms. `build_answer_record` lifts the
parsed answer into the same shape as the item's Gold, lists the fields that
differ, and keeps the raw model text alongside.

Stage 2's reported task is `stage2_2_reconstruct`, which already gets this
treatment from `stage2_2_runner._materialize_state_pairs` (prediction_state,
gold_state, prediction_gold_diff). The legacy MCQ task `stage2_memory_value` is
deliberately not covered here.
"""

from __future__ import annotations

from typing import Any

from .stage1 import STAGE1


# Gold carries provenance the model cannot be expected to produce; comparing it
# would report a difference on every single item.
_UNPREDICTABLE_GOLD_FIELDS = ("event_instance_id",)
_COMPARED_FIELDS = ("event_id", "event_label")


def build_answer_record(
    item: dict[str, Any], *, prediction: str, raw_answer: str
) -> dict[str, Any] | None:
    """Return the Gold-shaped record, or None for stages without a mapping."""

    if item.get("stage") != STAGE1:
        return None
    metadata = item.get("metadata") or {}
    labels = {
        str(candidate["event_id"]): candidate.get("label_ko")
        for candidate in metadata.get("candidate_events") or []
    }
    gold = item.get("gold") or {}
    predicted = {
        "event_id": prediction,
        # None when the model answered an ID outside the candidate list.
        "event_label": labels.get(prediction),
        "event_instance_id": None,
    }
    gold_shaped = {
        "event_id": str(gold.get("event_id") or ""),
        "event_label": gold.get("event_label"),
        "event_instance_id": gold.get("event_instance_id"),
    }
    return {
        "schema_version": "stage1_answer_record-v1",
        "stage": STAGE1,
        "prediction": predicted,
        "gold": gold_shaped,
        "prediction_in_candidate_set": prediction in labels,
        "unpredictable_gold_fields": list(_UNPREDICTABLE_GOLD_FIELDS),
        "diff": [
            {
                "field": field,
                "prediction": predicted.get(field),
                "gold": gold_shaped.get(field),
            }
            for field in _COMPARED_FIELDS
            if predicted.get(field) != gold_shaped.get(field)
        ],
        "raw_answer": raw_answer,
    }
