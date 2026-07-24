#!/usr/bin/env python
"""Prototype: lifecycle-masking abstention probe.

For each life event with a full lifecycle, hold the evaluation checkpoint fixed
(the prefix ending at the event's terminal session) and peel back the event's
evidence in reverse order, replacing masked sessions with event-neutral routine
fillers (same length/position). Re-deriving prefix gold at each masking level
yields a counterfactual "abstention ladder": the gold event status should
downgrade occurred -> upcoming -> weak_signal -> no_event as evidence is removed.

Filler sessions are reused from the trajectory's own routine_financial sessions
(verified to carry no memory_fact cues -> zero gold side-effect); no new
generation is required. The gold recompute is driven by which sessions still
carry linked_event_instance_id, so masking flows through automatically.

    python scripts/mask_lifecycle_experiment.py \
        --trajectories-dir data/runs/hf_full/trajectories_fixed \
        --sessions-dir data/runs/hf_full/final_sessions \
        --out data/runs/hf_full/masking_ladder.json --max-events 12
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.gold.prefix_gold_exporter import export_prefix_gold
from fin_life_benchmark.trajectory.models import Trajectory

# Session types by lifecycle position (later = stronger evidence / downstream).
TERMINAL_TYPES = {"occurred_evidence", "cancellation_evidence"}
DOWNSTREAM_TYPES = {"consequence_session", "stale_recall_session"}
UPCOMING_TYPES = {"upcoming_evidence"}
WEAK_TYPES = {"weak_signal_evidence"}

# Reverse-peel masking levels: which session types of the target event to mask.
LEVELS = [
    ("full", set()),
    ("mask_terminal", TERMINAL_TYPES | DOWNSTREAM_TYPES),
    ("mask_upcoming", TERMINAL_TYPES | DOWNSTREAM_TYPES | UPCOMING_TYPES),
    ("mask_all", TERMINAL_TYPES | DOWNSTREAM_TYPES | UPCOMING_TYPES | WEAK_TYPES),
]


def _neutralize(session: dict, filler: dict) -> dict:
    """In-place content swap: keep identity/position, become a routine lookup."""
    s = copy.deepcopy(session)
    s["session_type"] = "routine_financial"
    s["linked_event_instance_id"] = None
    s["event_status_after_session"] = "no_event"
    s["turns"] = copy.deepcopy(filler["turns"])
    s["cue_annotations"] = []
    s["financial_task"] = filler.get("financial_task", s.get("financial_task"))
    s["action_resolution"] = {"mode": "information_only", "provided_slots": {}, "missing_slots": [],
                              "explicit_confirmation_turn_index": None, "completion_turn_index": None}
    if "plan" in s and "plan" in filler:
        s["plan"] = copy.deepcopy(filler["plan"])
    return s


def _event_status_in_gold(prefix, event_id: str):
    for ev in prefix.gold_life_events:
        if ev.event_instance_id == event_id:
            return {"event_status": ev.event_status, "occurred": ev.occurred,
                    "update_allowed": ev.update_allowed}
    return {"event_status": "no_event", "occurred": False, "update_allowed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", default="data/runs/hf_full/trajectories_fixed")
    parser.add_argument("--sessions-dir", default="data/runs/hf_full/final_sessions")
    parser.add_argument("--out", default="data/runs/hf_full/masking_ladder.json")
    parser.add_argument("--max-events", type=int, default=12)
    parser.add_argument("--trajectories", nargs="*", default=None, help="limit to these traj ids")
    args = parser.parse_args()

    traj_files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    results = []
    events_done = 0
    for tf in traj_files:
        traj_id = tf.stem
        if args.trajectories and traj_id not in args.trajectories:
            continue
        trajectory = Trajectory.model_validate(json.loads(tf.read_text(encoding="utf-8")))
        spath = Path(args.sessions_dir) / f"sessions_{traj_id}.jsonl"
        if not spath.exists():
            continue
        sessions = sorted((json.loads(l) for l in open(spath)), key=lambda s: s["session_id"])
        by_event: dict[str, list[dict]] = {}
        for s in sessions:
            if s.get("linked_event_instance_id"):
                by_event.setdefault(s["linked_event_instance_id"], []).append(s)
        filler_pool = [s for s in sessions if s.get("session_type") == "routine_financial"]

        for event_id, ev_sessions in by_event.items():
            types = {s["session_type"] for s in ev_sessions}
            if not (types & TERMINAL_TYPES):
                continue  # only events with a terminal (occurred/cancelled) stage
            if events_done >= args.max_events:
                break
            # fixed checkpoint = prefix ending at the event's last linked session
            terminal_sid = max(s["session_id"] for s in ev_sessions)
            cp = next(i + 1 for i, s in enumerate(sessions) if s["session_id"] == terminal_sid)

            ladder = []
            for level_name, mask_types in LEVELS:
                mask_ids = {s["session_id"] for s in ev_sessions if s["session_type"] in mask_types}
                # build variant prefix (only need up to checkpoint)
                variant = []
                fi = 0
                for s in sessions[:cp]:
                    if s["session_id"] in mask_ids:
                        # pick a distinct filler not linked to any event
                        while fi < len(filler_pool) and filler_pool[fi]["session_id"] in mask_ids:
                            fi += 1
                        filler = filler_pool[fi % len(filler_pool)]
                        fi += 1
                        variant.append(_neutralize(s, filler))
                    else:
                        variant.append(s)
                prefixes = export_prefix_gold(trajectory, variant, checkpoint_stride=cp)
                gold = prefixes[-1]
                st = _event_status_in_gold(gold, event_id)
                ladder.append({"level": level_name, "masked": len(mask_ids), **st})
            event_label = next((s.get("financial_task") for s in ev_sessions), "")
            mapped = next((s.get("mapped_action") for s in ev_sessions), "")
            results.append({
                "trajectory_id": traj_id, "event_instance_id": event_id,
                "checkpoint_session_count": cp, "task": event_label, "mapped_action": mapped,
                "ladder": ladder,
            })
            events_done += 1
        if events_done >= args.max_events:
            break

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(results, open(args.out, "w"), ensure_ascii=False, indent=1)

    # readable table
    print(f"\n{'trajectory/event':28} {'level':16} {'status':13} occ  upd")
    print("-" * 72)
    for r in results:
        head = f"{r['trajectory_id']}/{r['event_instance_id'].split('_')[-1]}"
        for i, l in enumerate(r["ladder"]):
            label = head if i == 0 else ""
            print(f"{label:28} {l['level']:16} {l['event_status']:13} "
                  f"{'Y' if l['occurred'] else '.'}    {'Y' if l['update_allowed'] else '.'}")
        print()
    print(f"{len(results)} events -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
