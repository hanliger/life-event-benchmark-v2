"""Read prefix gold, restoring the carry-forward payload.

``export_prefix_gold`` blanks the five gold_* fields on prefixes whose payload
repeats the previous prefix of the same trajectory (``repeats_previous: true``),
shrinking the file ~20x. This loader carries the last full payload forward so
consumers see complete records again.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterator

from ..io import read_jsonl

_PAYLOAD_FIELDS = (
    "gold_life_events",
    "gold_memory_updates",
    "gold_action_decisions",
    "gold_full_memory_state",
    "gold_full_action_state",
)


def read_prefix_gold(path: Path | str) -> Iterator[dict[str, Any]]:
    """Yield prefix-gold records with the gold payload filled in (deep-copied
    so callers can mutate freely). Carry-forward is keyed per trajectory."""
    last: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(path):
        traj = record["trajectory_id"]
        if record.get("repeats_previous"):
            prev = last.get(traj)
            if prev is not None:
                for field in _PAYLOAD_FIELDS:
                    record[field] = copy.deepcopy(prev[field])
        else:
            last[traj] = {field: record.get(field) for field in _PAYLOAD_FIELDS}
        yield record
