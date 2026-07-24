#!/usr/bin/env python
"""Rule-based gold fix for education_child_stage_entry memory updates.

A shared (non child-specific) ``education.child_education_stage`` memory path let
the recorded transition pick up a wrong ``old_value`` -- another child's stage or
a later stage of the same child -- yielding same-stage (primary->primary) or
time-reversed (high->primary) updates that contradict the event's own params.

For events whose params describe a genuine forward transition
(previous_stage earlier than new_stage), the params are authoritative: this
rewrites every child_education_stage memory update in that session's structured
context to ``previous_stage -> new_stage``. Events whose params are themselves
degenerate (previous_stage == new_stage, an initial-state/trajectory conflict)
are NOT touched here -- they need a trajectory-level fix -- and are only reported.

    python scripts/fix_education_stage_gold.py \
        --gold-dir   data/runs/<RUN>/gold_reconciled \
        --output-dir data/runs/<RUN>/gold_edu_fixed
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

STAGES = ["pre_school", "primary", "middle", "high"]
_NORMALIZE = {"preschool": "pre_school"}


def _norm(stage):
    return _NORMALIZE.get(stage, stage)


def _idx(stage):
    stage = _norm(stage)
    return STAGES.index(stage) if stage in STAGES else None


def _predecessor(new_stage):
    new = _norm(new_stage)
    i = _idx(new)
    if i is None or i == 0:
        return STAGES[0]
    return STAGES[i - 1]


def _fix_updates(updates, new) -> int:
    """Rewrite child_education_stage updates to predecessor(new)->new.

    Matches the simulator fix (event_lifecycle.education_previous_stage): the
    prior stage is derived from the ordered progression, so the recorded
    transition is always a real forward step regardless of what the shared cell
    or the (possibly degenerate) event params say.
    """
    prev = _predecessor(new)
    changed = 0
    for u in updates:
        if "child_education_stage" not in u.get("path", ""):
            continue
        if u.get("old_value") != prev or u.get("new_value") != new:
            u["old_value"] = prev
            u["new_value"] = new
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--gold-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    gold_dir = Path(args.gold_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(gold_dir.glob("*.jsonl"))
    if not files:
        raise SystemExit(f"no *.jsonl under {gold_dir}")

    fixed_sessions = 0
    per_traj: Counter = Counter()
    for gfile in files:
        out_rows = []
        for row in (json.loads(l) for l in gfile.read_text(encoding="utf-8").splitlines() if l):
            sc = (row.get("plan") or {}).get("structured_context") or {}
            ev = sc.get("event") or {}
            if ev.get("event_id") == "education_child_stage_entry":
                params = ev.get("params") or {}
                new = _norm(params.get("new_stage"))
                updates = (sc.get("event_memory_updates") or []) + (sc.get("session_memory_updates") or [])
                changed = _fix_updates(updates, new)
                # Keep the event params consistent with the corrected transition.
                if _norm(params.get("previous_stage")) != _predecessor(new):
                    params["previous_stage"] = _predecessor(new)
                    changed += 1
                if changed:
                    fixed_sessions += 1
                    per_traj[row["trajectory_id"]] += 1
            out_rows.append(row)
        dest = output_dir / gfile.name
        with dest.open("w", encoding="utf-8") as handle:
            for row in out_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"fixed {fixed_sessions} education sessions (memory + params set to predecessor(new)->new)")
    print("  by trajectory:", dict(sorted(per_traj.items())))
    print(f"\noutput -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
