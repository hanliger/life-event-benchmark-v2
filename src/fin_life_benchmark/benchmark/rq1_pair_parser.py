"""Strict parser/validator for occurred-event pair output.

Contract (see prompts/benchmark/rq1_occurred_event_pairs_ko.md):

- the response must be a JSON object with a ``pairs`` list;
- every record is an object carrying ``event_id`` and ``evidence_session_id``;
- ``event_id`` must belong to the active public taxonomy;
- ``evidence_session_id`` must be a public ``D###`` id visible in this call;
- duplicates are preserved: the metric penalizes them, the parser must not
  hide them;
- a rejected record is one false-positive prediction unit regardless of how
  many field errors it produced;
- if the whole response is unparseable the prediction is empty with
  ``parse_error`` set, and no synthetic false-positive unit is added.

A record is rejected only for the reasons above (non-object, unknown event id,
missing/malformed/invisible session id, missing event id). Extra keys -- the
old protocol's ``status``/``confidence``/``prediction_id`` and anything else --
are recorded as validation errors for contract-drift visibility but do not by
themselves invalidate an otherwise well-formed pair.
"""

from __future__ import annotations

from typing import Any

from .rq1_models import public_session_number
from .rq1_pair_models import (
    PAIR_RECORD_FIELDS,
    RQ1PairPrediction,
    RQ1PredictedPair,
)
from .rq1_parser import extract_json


def _validate_event_id(
    entry: dict[str, Any], taxonomy_event_ids: set[str]
) -> list[str]:
    if entry.get("event_id") is None:
        return ["missing_event_id"]
    event_id = entry["event_id"]
    if not isinstance(event_id, str):
        return [f"malformed_event_id:{event_id!r}"]
    if event_id not in taxonomy_event_ids:
        return [f"unknown_event_id:{event_id!r}"]
    return []


def _validate_session_id(
    entry: dict[str, Any], visible_public_ids: set[str]
) -> list[str]:
    if entry.get("evidence_session_id") is None:
        return ["missing_evidence_session_id"]
    session_id = entry["evidence_session_id"]
    if not isinstance(session_id, str):
        return [f"malformed_evidence_session_id:{session_id!r}"]
    try:
        public_session_number(session_id)
    except ValueError:
        return [f"malformed_evidence_session_id:{session_id!r}"]
    if session_id not in visible_public_ids:
        return [f"session_id_not_visible:{session_id}"]
    return []


def parse_pair_prediction(
    raw: str,
    *,
    visible_public_ids: set[str],
    taxonomy_event_ids: set[str],
) -> RQ1PairPrediction:
    """Parse raw model text into a validated :class:`RQ1PairPrediction`.

    ``visible_public_ids`` is the set of public ids actually rendered for this
    call.
    """

    prediction = RQ1PairPrediction()
    payload = extract_json(raw)
    if payload is None:
        prediction.parse_error = "invalid_json"
        return prediction
    if not isinstance(payload, dict) or not isinstance(payload.get("pairs"), list):
        prediction.parse_error = "missing_pairs_list"
        return prediction

    for index, entry in enumerate(payload["pairs"]):
        if not isinstance(entry, dict):
            prediction.validation_errors.append(f"pairs[{index}]: not_an_object")
            prediction.invalid_record_count += 1
            prediction.rejected_records.append({"index": index, "record": entry})
            continue

        errors = _validate_event_id(entry, taxonomy_event_ids)
        errors += _validate_session_id(entry, visible_public_ids)
        # logged, never a rejection reason on its own
        for key in sorted(k for k in entry if k not in PAIR_RECORD_FIELDS):
            prediction.validation_errors.append(
                f"pairs[{index}]: unexpected_field:{key!r}"
            )

        if errors:
            prediction.validation_errors.extend(
                f"pairs[{index}]: {error}" for error in errors
            )
            prediction.invalid_record_count += 1
            prediction.rejected_records.append({"index": index, "record": entry})
            continue

        prediction.valid_pairs.append(
            RQ1PredictedPair(
                event_id=str(entry["event_id"]),
                evidence_session_id=str(entry["evidence_session_id"]),
            )
        )

    # Ordering contract check: logged only. The metric is order-independent, so
    # a mis-ordered response must not lose points here.
    keys = [
        (public_session_number(pair.evidence_session_id), pair.event_id)
        for pair in prediction.valid_pairs
    ]
    if keys != sorted(keys):
        prediction.validation_errors.append("pairs_not_ordered_by_evidence_session")
    return prediction
