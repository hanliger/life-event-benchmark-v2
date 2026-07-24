#!/usr/bin/env python
"""Rule-based repair for ungrounded provided_slots in generated sessions.

The planner grounds execution slots (e.g. ``amount``) from the persona's
structured context *before* the dialogue exists, so a persona-level constant
(such as an existing standing transfer's amount) can be stamped onto a session
whose dialogue never mentions it. This pass reconciles each session's execution
contract against the dialogue that was actually generated: a grounded slot is
kept only when its value is visible in the user turns (the same predicate the
``provided_slot_not_grounded_in_dialogue`` validator uses); otherwise it moves
to ``missing_slots`` and the action is downgraded to
``pending_required_information`` when a required execution slot is lost.

It reads joined ``sessions_*.jsonl`` (turns + plan + action_resolution, as
produced by scripts/fetch_dialogue_data.py) and writes corrected copies:

    python scripts/reconcile_provided_slots.py \
        --sessions-dir data/runs/<RUN_ID>/dialogues/sessions \
        --output-dir   data/runs/<RUN_ID>/dialogues/sessions_reconciled

Pass --in-place to overwrite the input files instead of writing a new dir.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.validation.dialogue_validator import reconcile_provided_slots


def _reconcile_session(session: dict) -> tuple[dict, list[str]]:
    plan = session.get("plan") or {}
    contract = plan.get("action_execution_contract") or {}
    resolution = session.get("action_resolution") or {}
    turns = session.get("turns") or []
    new_contract, new_resolution, dropped = reconcile_provided_slots(
        contract, resolution, turns
    )
    if not dropped:
        return session, []
    # Write the reconciled contract/resolution back, leaving everything else
    # (turns, cues, persona, gold labels) untouched.
    if "plan" in session:
        session = {**session, "plan": {**plan, "action_execution_contract": new_contract}}
    session["action_resolution"] = new_resolution
    return session, dropped


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="where to write corrected sessions_*.jsonl (default: alongside, with _reconciled suffix)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="overwrite the input files instead of writing a separate output dir",
    )
    args = parser.parse_args()

    sessions_dir = Path(args.sessions_dir)
    files = sorted(sessions_dir.glob("sessions_*.jsonl"))
    if not files:
        raise SystemExit(f"no sessions_*.jsonl under {sessions_dir}")

    if args.in_place:
        output_dir = sessions_dir
    else:
        output_dir = Path(args.output_dir or f"{sessions_dir}_reconciled")
        output_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    changed = 0
    dropped_by_slot: Counter = Counter()
    dropped_by_mode: Counter = Counter()
    for path in files:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        out_rows = []
        for session in rows:
            total += 1
            prior_mode = ((session.get("plan") or {}).get("action_execution_contract") or {}).get("action_mode")
            new_session, dropped = _reconcile_session(session)
            if dropped:
                changed += 1
                for slot in dropped:
                    dropped_by_slot[slot] += 1
                dropped_by_mode[prior_mode] += 1
            out_rows.append(new_session)
        dest = output_dir / path.name
        with dest.open("w", encoding="utf-8") as handle:
            for row in out_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"reconciled {changed}/{total} sessions across {len(files)} file(s)")
    print("dropped slots by name:")
    for slot, n in dropped_by_slot.most_common():
        print(f"  {n:5d}  {slot}")
    print("changed sessions by prior action_mode:")
    for mode, n in dropped_by_mode.most_common():
        print(f"  {n:5d}  {mode}")
    print(f"output -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
