"""Strict parser/validator for RQ1 model output.

Contract (see prompts/benchmark/rq1_event_trajectory_ko.md):

- the response must be a JSON object with an ``events`` list;
- ``event_id`` must belong to the public taxonomy;
- ``status`` must be one of weak_signal/upcoming/occurred/cancelled;
- every referenced session id must be a public id (``D###``) visible in the
  current context;
- repeated session ids within one field are deduplicated (order kept);
- repeated event instances are preserved as distinct entries;
- malformed entries are rejected and logged, never silently repaired. An
  out-of-range or missing ``confidence`` keeps the event but is logged and
  excluded from confidence metrics.

Normalized predictions use canonical session ids internally; the mapping
never reaches the model.
"""

from __future__ import annotations

import json
from typing import Any

from .rq1_models import (
    PREDICTABLE_STATUSES,
    RQ1PredictedEvent,
    RQ1Prediction,
    from_public_session_id,
    session_number,
)


def extract_json(raw: str) -> Any | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        # tolerate fenced output: strip the first/last fence lines
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _canonical_visible_id(
    public_id: Any, public_to_canonical: dict[str, str]
) -> str:
    if not isinstance(public_id, str):
        raise ValueError(f"malformed session id: {public_id!r}")
    canonical = public_to_canonical.get(public_id)
    if canonical is None:
        # distinguishes malformed from invisible in the error message
        from_public_session_id(public_id)  # raises on malformed
        raise ValueError(f"session id not visible in context: {public_id}")
    return canonical


def parse_prediction(
    raw: str,
    *,
    visible_public_ids: dict[str, str],
    taxonomy_event_ids: set[str],
) -> RQ1Prediction:
    """Parse raw model text into a validated :class:`RQ1Prediction`.

    ``visible_public_ids`` maps public id -> canonical id for the sessions
    actually shown to the model in this call.
    """

    prediction = RQ1Prediction()
    payload = extract_json(raw)
    if payload is None:
        prediction.parse_error = "invalid_json"
        return prediction
    if not isinstance(payload, dict) or not isinstance(
        payload.get("events"), list
    ):
        prediction.parse_error = "missing_events_list"
        return prediction

    for index, entry in enumerate(payload["events"]):
        errors: list[str] = []
        if not isinstance(entry, dict):
            prediction.validation_errors.append(
                f"events[{index}]: not_an_object"
            )
            prediction.rejected_events.append({"index": index, "entry": entry})
            continue
        event_id = entry.get("event_id")
        if event_id not in taxonomy_event_ids:
            errors.append(f"unknown_event_id:{event_id!r}")
        status = entry.get("status")
        if status not in PREDICTABLE_STATUSES:
            errors.append(f"invalid_status:{status!r}")

        canonical: dict[str, Any] = {}
        for field, required in (
            ("first_evidence_session_id", True),
            ("status_anchor_session_id", True),
        ):
            try:
                canonical[field] = _canonical_visible_id(
                    entry.get(field), visible_public_ids
                )
            except ValueError as exc:
                errors.append(f"{field}:{exc}")
        for field in ("core_evidence_session_ids", "supporting_session_ids"):
            values = entry.get(field, [])
            if values is None:
                values = []
            if not isinstance(values, list):
                errors.append(f"{field}:not_a_list")
                continue
            resolved: list[str] = []
            for value in values:
                try:
                    resolved.append(
                        _canonical_visible_id(value, visible_public_ids)
                    )
                except ValueError as exc:
                    errors.append(f"{field}:{exc}")
            canonical[field] = _dedupe(resolved)

        confidence = entry.get("confidence")
        confidence_value: float | None = None
        if isinstance(confidence, (int, float)) and not isinstance(
            confidence, bool
        ) and 0.0 <= float(confidence) <= 1.0:
            confidence_value = float(confidence)
        else:
            prediction.validation_errors.append(
                f"events[{index}]: invalid_confidence:{confidence!r}"
            )

        if errors:
            prediction.validation_errors.extend(
                f"events[{index}]: {error}" for error in errors
            )
            prediction.rejected_events.append({"index": index, "entry": entry})
            continue

        prediction.events.append(
            RQ1PredictedEvent(
                prediction_id=str(entry.get("prediction_id") or f"P{index + 1:03d}"),
                event_id=str(event_id),
                status=str(status),
                first_evidence_session=canonical["first_evidence_session_id"],
                status_anchor_session=canonical["status_anchor_session_id"],
                core_evidence_sessions=canonical.get(
                    "core_evidence_session_ids", []
                ),
                supporting_sessions=canonical.get("supporting_session_ids", []),
                confidence=confidence_value,
            )
        )

    # ordering contract check (logged, not rejected; alignment re-orders
    # deterministically)
    numbers = [
        session_number(event.first_evidence_session)
        for event in prediction.events
    ]
    if numbers != sorted(numbers):
        prediction.validation_errors.append("events_not_ordered_by_first_evidence")
    return prediction
