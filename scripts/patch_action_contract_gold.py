#!/usr/bin/env python
"""Apply the cancellation/lookup contract subtypes to frozen gold, surgically.

Re-planning the whole corpus is not an option: the frozen plans were produced by
an older registry state and differ from any current-code re-plan in 578 sessions,
almost all of them hard negatives whose dialogues were generated from those very
plans. This touches only the sessions the contract change is about.

Two kinds of session change:

  contract-only  the task template keeps its identity but now resolves to a
                 cancel/cancel_reservation/inquiry subtype, so only
                 ``plan.action_execution_contract`` is recomputed.
  retasked       the task template itself is wrong for this persona --
                 dependent_end_transfer_stop assigned where no support transfer
                 exists -- so task_template_id, financial_task,
                 task_user_goal_instruction and the contract all come from the
                 replanned plan. Every other plan field (cues, evidence
                 dimensions, structured context) is identical by construction,
                 because the sibling task carries the same cue template and
                 memory paths; the script asserts that rather than trusting it.

``action_resolution`` is deliberately left alone -- it describes what the
dialogue did, and these sessions are regenerated afterwards.

Example:
  python scripts/patch_action_contract_gold.py \
      --gold-dir data/runs/hf_full/final_gold \
      --replanned-dir data/runs/<REPLAN>/plans \
      --output-dir data/runs/hf_full/gold_contract_patched \
      --targets-out data/runs/hf_full/regen_targets_contracts.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths
from fin_life_benchmark.locale import load_locale

# Fields the sibling task legitimately changes. Anything else differing means the
# replan drifted for an unrelated reason and must not be spliced in.
RETASK_FIELDS = (
    "task_template_id",
    "financial_task",
    "task_user_goal_instruction",
    "action_execution_contract",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--gold-dir", required=True)
    parser.add_argument("--replanned-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--targets-out", default=None)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument(
        "--retask",
        action="append",
        default=[],
        metavar="FROM=TO",
        help="accept this task-template swap from the replan (repeatable). Any "
        "other difference is pre-existing planner drift and is ignored.",
    )
    parser.add_argument(
        "--targets",
        default=None,
        help="JSON list of [trajectory_id, session_id, ...] to recompute, instead "
        "of every session whose task declares a contract subtype. Required when "
        "the sessions will be regenerated: recomputing a contract re-grounds "
        "slots the reconcile pass pruned for not appearing in the dialogue, so it "
        "is only safe where a fresh dialogue follows.",
    )
    args = parser.parse_args()
    retask_pairs = dict(item.split("=", 1) for item in args.retask)
    explicit: set[tuple[str, str]] | None = None
    if args.targets:
        explicit = {
            (row[0], row[1])
            for row in json.loads(Path(args.targets).read_text(encoding="utf-8"))
        }

    paths = RepoPaths.default()
    planner = EvidencePlanner(
        load_life_event_templates(paths), load_locale(args.locale, paths), paths
    )
    subtypes = planner.high_risk_contract_registry.get("task_subtypes") or {}

    replanned: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(Path(args.replanned_dir).glob("plans_traj_*.jsonl")):
        for row in _read_jsonl(path):
            replanned[(row["trajectory_id"], row["session_id"])] = row

    gold_dir = Path(args.gold_dir)
    output_dir = Path(args.output_dir)
    changed: Counter = Counter()
    targets: list[list[str]] = []

    for gold_path in sorted(gold_dir.glob("traj_*.jsonl")):
        rows = _read_jsonl(gold_path)
        out_rows = []
        for row in rows:
            plan = row.get("plan") or {}
            key = (row["trajectory_id"], row["session_id"])
            new_plan = replanned.get(key) or {}
            frozen_task = plan.get("task_template_id")
            replanned_task = new_plan.get("task_template_id")
            # Only the swaps asked for. The frozen plans predate the current
            # registry, so the replan also differs on ~578 unrelated sessions
            # (mostly hard negatives) whose dialogues were built from the frozen
            # version -- those must stay exactly as published.
            retasked = (
                replanned_task is not None
                and retask_pairs.get(frozen_task) == replanned_task
            )

            selected = key in explicit if explicit is not None else frozen_task in subtypes
            if not retasked and not selected:
                out_rows.append(row)
                continue

            if retasked:
                # Only the sibling swap may differ; anything else is drift.
                drifted = [
                    field
                    for field in set(plan) | set(new_plan)
                    if field not in RETASK_FIELDS and plan.get(field) != new_plan.get(field)
                ]
                if drifted:
                    raise SystemExit(
                        f"{key}: replanned plan differs beyond the retask fields "
                        f"({drifted[:6]}); refusing to splice"
                    )
                patched_plan = {
                    **plan,
                    **{field: new_plan[field] for field in RETASK_FIELDS},
                }
                changed["retasked"] += 1
            else:
                # Same task, new subtype: recompute the contract from the frozen
                # plan so nothing else can leak in from the replan.
                contract = planner._action_execution_contract(
                    DialogueGenerationPlan.model_validate(plan)
                ).model_dump(mode="json")
                if contract == plan.get("action_execution_contract"):
                    out_rows.append(row)
                    continue
                patched_plan = {**plan, "action_execution_contract": contract}
                changed["contract_only"] += 1

            out_rows.append({**row, "plan": patched_plan})
            targets.append([row["trajectory_id"], row["session_id"], "contract_subtype"])

        _write_jsonl(output_dir / gold_path.name, out_rows)

    if args.targets_out:
        Path(args.targets_out).write_text(
            json.dumps(targets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"patched {sum(changed.values())} sessions")
    for kind, count in changed.most_common():
        print(f"  {count:5d}  {kind}")
    print(f"output -> {output_dir}")
    if args.targets_out:
        print(f"regeneration targets -> {args.targets_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
