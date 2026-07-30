#!/usr/bin/env python
"""Prototype: lifecycle-masking abstention probe.

For each life event with a full lifecycle, hold the evaluation checkpoint fixed
(the prefix ending at the event's terminal session) and peel back the event's
evidence in reverse order, replacing masked sessions with event-neutral routine
fillers (same length/position). Re-deriving prefix gold at each masking level
yields a counterfactual "abstention ladder": the gold event status should
downgrade from terminal evidence toward no_event as evidence is removed. A
lifecycle may skip an intermediate stage, so adjacent levels can be identical.

The preferred donor source is a separately generated, timeless 20-session
reserve bank for the same persona. Donors are mapped to all target-event slots
once per event, then reused consistently across masking levels. This makes the
levels nested counterfactuals rather than independently resampled perturbations.
The gold recompute is driven only by the sessions visible after replacement.

    python scripts/mask_lifecycle_experiment.py \
        --trajectories-dir data/runs/hf_full/trajectories_fixed \
        --sessions-dir data/runs/hf_full/dialogues/sessions \
        --fillers-dir data/runs/hf_full/counterfactual_fillers/sessions \
        --out data/runs/hf_full/masking_ladder.json --max-events 12
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.mask_lifecycle_experiment in tests
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.benchmark.lifecycle_masking import (
    DOWNSTREAM_TYPES,
    TERMINAL_TYPES,
    UPCOMING_TYPES,
    WEAK_TYPES,
    filler_provenance as _filler_provenance,
    is_neutral_filler as _is_neutral_filler,
    neutralize as _neutralize,
    pick_filler as _pick_filler,
)
from fin_life_benchmark.gold.prefix_gold_exporter import export_prefix_gold
from fin_life_benchmark.trajectory.models import Trajectory

# Reverse-peel masking levels: which session types of the target event to mask.
LEVELS = [
    ("full", set()),
    ("mask_terminal", TERMINAL_TYPES | DOWNSTREAM_TYPES),
    ("mask_upcoming", TERMINAL_TYPES | DOWNSTREAM_TYPES | UPCOMING_TYPES),
    ("mask_all", TERMINAL_TYPES | DOWNSTREAM_TYPES | UPCOMING_TYPES | WEAK_TYPES),
]


def _event_status_in_gold(prefix, event_id: str):
    for ev in prefix.gold_life_events:
        if ev.event_instance_id == event_id:
            return {"event_status": ev.event_status, "occurred": ev.occurred,
                    "update_allowed": ev.update_allowed}
    return {"event_status": "no_event", "occurred": False, "update_allowed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", default="data/runs/hf_full/trajectories_fixed")
    parser.add_argument("--sessions-dir", default="data/runs/hf_full/dialogues/sessions")
    parser.add_argument(
        "--fillers-dir",
        help="preferred timeless reserve bank containing fillers_<trajectory_id>.jsonl",
    )
    parser.add_argument("--out", default="data/runs/hf_full/masking_ladder.json")
    parser.add_argument(
        "--prefix-gold-out",
        help="JSONL counterfactual recipes plus complete recalculated PrefixGold",
    )
    parser.add_argument("--max-events", type=int, default=12)
    parser.add_argument("--trajectories", nargs="*", default=None, help="limit to these traj ids")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the per-event table (final counts are still printed)",
    )
    args = parser.parse_args()

    traj_files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    results: list[dict] = []
    exclusions: list[dict] = []
    prefix_gold_records: list[dict] = []
    events_done = 0
    for tf in traj_files:
        traj_id = tf.stem
        if args.trajectories and traj_id not in args.trajectories:
            continue
        trajectory = Trajectory.model_validate(json.loads(tf.read_text(encoding="utf-8")))
        spath = Path(args.sessions_dir) / f"sessions_{traj_id}.jsonl"
        if not spath.exists():
            continue
        with spath.open(encoding="utf-8") as f:
            sessions = sorted(
                (json.loads(line) for line in f if line.strip()),
                key=lambda session: session["session_id"],
            )
        by_event: dict[str, list[dict]] = {}
        for s in sessions:
            if s.get("linked_event_instance_id"):
                by_event.setdefault(s["linked_event_instance_id"], []).append(s)
        filler_bank_path = None
        if args.fillers_dir:
            filler_bank_path = Path(args.fillers_dir) / f"fillers_{traj_id}.jsonl"
            if not filler_bank_path.exists():
                raise SystemExit(f"missing counterfactual filler bank: {filler_bank_path}")
            with filler_bank_path.open(encoding="utf-8") as handle:
                raw_fillers = [
                    json.loads(line) for line in handle if line.strip()
                ]
            rejected = [
                filler["session_id"]
                for filler in raw_fillers
                if not _is_neutral_filler(filler)
            ]
            if rejected:
                raise SystemExit(
                    f"{filler_bank_path} contains non-neutral fillers: "
                    + ", ".join(rejected)
                )
            filler_pool = raw_fillers
            filler_source = "synthetic_reserve"
        else:
            filler_pool = [
                session for session in sessions if _is_neutral_filler(session)
            ]
            filler_source = "trajectory_session"

        for event_id, ev_sessions in by_event.items():
            types = {s["session_type"] for s in ev_sessions}
            if not (types & TERMINAL_TYPES):
                continue  # only events with a terminal (occurred/cancelled) stage
            if events_done >= args.max_events:
                break
            # fixed checkpoint = prefix ending at the event's last linked session
            terminal_sid = max(s["session_id"] for s in ev_sessions)
            cp = next(i + 1 for i, s in enumerate(sessions) if s["session_id"] == terminal_sid)
            prefix_ids = {s["session_id"] for s in sessions[:cp]}

            # Assign all event slots exactly once. Every level below reuses this
            # map, so already-masked slots never change filler content.
            largest_mask_types = LEVELS[-1][1]
            largest_mask_slots = [
                session
                for session in sessions[:cp]
                if session.get("linked_event_instance_id") == event_id
                and session.get("session_type") in largest_mask_types
            ]
            preflight_used: set[str] = set()
            try:
                filler_mapping: dict[str, dict] = {}
                for slot in largest_mask_slots:
                    filler_mapping[slot["session_id"]] = _pick_filler(
                        filler_pool,
                        prefix_ids,
                        preflight_used,
                        slot,
                    )
            except ValueError:
                available = [
                    filler
                    for filler in filler_pool
                    if filler["session_id"] not in prefix_ids
                    and filler.get("persona_id") == largest_mask_slots[0].get("persona_id")
                    and len(filler.get("turns") or []) == len(
                        largest_mask_slots[0].get("turns") or []
                    )
                ]
                exclusions.append({
                    "trajectory_id": traj_id,
                    "event_instance_id": event_id,
                    "checkpoint_session_count": cp,
                    "reason": "insufficient_unseen_neutral_fillers",
                    "required_fillers": len(largest_mask_slots),
                    "available_fillers": len(available),
                    "filler_source": filler_source,
                })
                continue

            ladder = []
            for level_name, mask_types in LEVELS:
                mask_ids = {s["session_id"] for s in ev_sessions if s["session_type"] in mask_types}
                # build variant prefix (only need up to checkpoint)
                variant = []
                fillers = []
                for s in sessions[:cp]:
                    if s["session_id"] in mask_ids:
                        filler = filler_mapping[s["session_id"]]
                        variant.append(_neutralize(s, filler))
                        fillers.append(_filler_provenance(s, filler, prefix_ids))
                    else:
                        variant.append(s)
                prefixes = export_prefix_gold(trajectory, variant, checkpoint_stride=cp)
                gold = prefixes[-1]
                st = _event_status_in_gold(gold, event_id)
                ladder.append({
                    "level": level_name,
                    "masked": len(mask_ids),
                    "fillers": fillers,
                    **st,
                })
                prefix_gold_records.append({
                    "case_id": f"{traj_id}__{event_id}__{level_name}",
                    "trajectory_id": traj_id,
                    "event_instance_id": event_id,
                    "level": level_name,
                    "checkpoint_session_count": cp,
                    "source_sessions_file": str(spath),
                    "filler_bank_file": (
                        str(filler_bank_path) if filler_bank_path else None
                    ),
                    "masked_session_ids": sorted(mask_ids),
                    "replacements": fillers,
                    "prefix_gold": gold.model_dump(mode="json"),
                })
            event_label = next((s.get("financial_task") for s in ev_sessions), "")
            mapped = next((s.get("mapped_action") for s in ev_sessions), "")
            results.append({
                "trajectory_id": traj_id, "event_instance_id": event_id,
                "checkpoint_session_count": cp, "task": event_label, "mapped_action": mapped,
                "filler_source": filler_source,
                "ladder": ladder,
            })
            events_done += 1
        if events_done >= args.max_events:
            break

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    exclusions_path = Path(args.out).with_suffix(".exclusions.json")
    with exclusions_path.open("w", encoding="utf-8") as f:
        json.dump(exclusions, f, ensure_ascii=False, indent=1)
    prefix_gold_path = (
        Path(args.prefix_gold_out)
        if args.prefix_gold_out
        else Path(args.out).with_name(f"{Path(args.out).stem}_prefix_gold.jsonl")
    )
    with prefix_gold_path.open("w", encoding="utf-8") as handle:
        for record in prefix_gold_records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if not args.quiet:
        print(f"\n{'trajectory/event':28} {'level':16} {'status':13} occ  upd")
        print("-" * 72)
        for r in results:
            head = f"{r['trajectory_id']}/{r['event_instance_id'].split('_')[-1]}"
            for i, l in enumerate(r["ladder"]):
                label = head if i == 0 else ""
                print(
                    f"{label:28} {l['level']:16} {l['event_status']:13} "
                    f"{'Y' if l['occurred'] else '.'}    "
                    f"{'Y' if l['update_allowed'] else '.'}"
                )
            print()
    distances = [
        filler["month_distance"]
        for result in results
        for level in result["ladder"]
        for filler in level["fillers"]
        if filler["month_distance"] is not None
    ]
    distance_summary = ""
    if distances:
        distance_summary = (
            f"; filler month distance median={statistics.median(distances):g}, "
            f"max={max(distances)}"
        )
    print(
        f"{len(results)} events -> {args.out}; "
        f"{len(exclusions)} excluded -> {exclusions_path}; "
        f"{len(prefix_gold_records)} prefix gold cases -> {prefix_gold_path}"
        f"{distance_summary}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
