"""Lifecycle-masking primitives shared by counterfactual experiments.

Extracted verbatim from scripts/mask_lifecycle_experiment.py so that more than
one experiment can reuse the same donor selection and slot-neutralization rules
instead of growing a second masking engine. The script re-exports these names,
so its behavior and its existing tests are unchanged.

The contract a replacement must preserve:

- the slot keeps its session id, position and turn count;
- the donor comes from the same persona and is never already visible;
- the donor plan is dropped rather than carried along as hidden metadata;
- donor assignment happens once per target, so nested masking levels are true
  nested counterfactuals rather than independently resampled perturbations.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from ..dialogue.counterfactual_fillers import SAFE_FILLER_TASK_TEMPLATE_IDS

# Session types by lifecycle position (later = stronger evidence / downstream).
TERMINAL_TYPES = {"occurred_evidence", "cancellation_evidence"}
DOWNSTREAM_TYPES = {"consequence_session", "stale_recall_session"}
UPCOMING_TYPES = {"upcoming_evidence"}
WEAK_TYPES = {"weak_signal_evidence"}


def task_template_id(session: dict[str, Any]) -> str | None:
    return (session.get("plan") or {}).get("task_template_id")


def is_neutral_filler(session: dict[str, Any]) -> bool:
    """Whether a routine session is safe to transplant as masked content."""

    cue_types = {cue.get("cue_type") for cue in session.get("cue_annotations") or []}
    return (
        session.get("session_type") == "routine_financial"
        and not session.get("linked_event_instance_id")
        and task_template_id(session) in SAFE_FILLER_TASK_TEMPLATE_IDS
        and cue_types <= {"task_intent"}
        and bool(session.get("turns"))
    )


def pick_filler(
    filler_pool: list[dict[str, Any]],
    prefix_ids: set[str],
    used: set[str],
    slot: dict[str, Any],
) -> dict[str, Any]:
    """Pick one deterministic donor; call once for every event slot."""

    candidates = [
        filler
        for filler in filler_pool
        if filler["session_id"] not in prefix_ids
        and filler["session_id"] not in used
        and filler.get("persona_id") == slot.get("persona_id")
        and len(filler.get("turns") or []) == len(slot.get("turns") or [])
    ]
    if not candidates:
        raise ValueError(
            "no unused unseen neutral filler for "
            f"{slot.get('trajectory_id')}/{slot.get('session_id')}"
        )

    def score(item: dict[str, Any]) -> tuple:
        donor_month = item.get("month_index")
        if donor_month is not None:
            return (
                0,
                abs(int(donor_month) - int(slot["month_index"])),
                item["session_id"],
            )
        # Timeless reserve donors have no chronological distance. Hashing the
        # slot+donor pair distributes reuse across the 20-item persona bank.
        stable_rank = hashlib.sha256(
            f"{slot['trajectory_id']}:{slot['session_id']}:{item['session_id']}".encode()
        ).hexdigest()
        return (1, stable_rank, item["session_id"])

    filler = min(candidates, key=score)
    used.add(filler["session_id"])
    return filler


def filler_provenance(
    slot: dict[str, Any], filler: dict[str, Any], prefix_ids: set[str]
) -> dict[str, Any]:
    donor_month = filler.get("month_index")
    return {
        "slot_session_id": slot["session_id"],
        "slot_month_index": int(slot["month_index"]),
        "donor_session_id": filler["session_id"],
        "donor_source_kind": filler.get("source_kind", "trajectory_session"),
        "donor_month_index": int(donor_month) if donor_month is not None else None,
        "month_distance": (
            abs(int(donor_month) - int(slot["month_index"]))
            if donor_month is not None
            else None
        ),
        "donor_task_template_id": task_template_id(filler),
        "donor_financial_task": filler.get("financial_task"),
        "donor_already_visible": filler["session_id"] in prefix_ids,
        "same_persona": filler.get("persona_id") == slot.get("persona_id"),
    }


def neutralize(session: dict[str, Any], filler: dict[str, Any]) -> dict[str, Any]:
    """Swap visible content while keeping the original slot identity/position."""

    if filler.get("persona_id") != session.get("persona_id"):
        raise ValueError("counterfactual filler must come from the same persona")
    if len(filler.get("turns") or []) != len(session.get("turns") or []):
        raise ValueError("counterfactual filler must preserve the turn count")

    s = copy.deepcopy(session)
    s["session_type"] = "routine_financial"
    s["window_event_instance_id"] = None
    s["linked_event_instance_id"] = None
    s["event_status_after_session"] = "no_event"
    s["turns"] = copy.deepcopy(filler["turns"])
    s["cue_annotations"] = []
    s["financial_task"] = filler.get("financial_task", s.get("financial_task"))
    s["mapped_action"] = filler.get("mapped_action")
    s["action_resolution"] = copy.deepcopy(
        filler.get("action_resolution")
        or {
            "mode": "information_only",
            "provided_slots": {},
            "missing_slots": [],
            "explicit_confirmation_turn_index": None,
            "completion_turn_index": None,
        }
    )
    s["quality_self_check"] = copy.deepcopy(filler.get("quality_self_check"))
    s["generation_metadata"] = copy.deepcopy(filler.get("generation_metadata"))
    # A donor plan contains its future month/current persona state. It is neither
    # needed by gold recomputation nor exposed to the model, so remove it rather
    # than creating hidden counterfactual metadata leakage.
    s["plan"] = None
    return s


def load_filler_bank(path: Any) -> list[dict[str, Any]]:
    """Load and validate a per-trajectory neutral filler bank."""

    path = Path(path)
    if not path.exists():
        raise ValueError(f"missing counterfactual filler bank: {path}")
    with path.open(encoding="utf-8") as handle:
        fillers = [json.loads(line) for line in handle if line.strip()]
    rejected = [f["session_id"] for f in fillers if not is_neutral_filler(f)]
    if rejected:
        raise ValueError(
            f"{path} contains non-neutral fillers: " + ", ".join(rejected)
        )
    return fillers
