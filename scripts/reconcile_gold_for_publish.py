#!/usr/bin/env python
"""Produce reconciled gold/ files for re-publishing to the HF dataset.

The frozen corpus was generated before grounded slots were reconciled against
the realized dialogue, so ~4% of high-risk sessions carry an ungrounded
provided_slots.amount (a persona-constant stamped onto an unrelated dialogue).
This rewrites each gold row's ``plan.action_execution_contract`` and
``action_resolution`` with the same rule-based reconciliation the generator now
applies (see fin_life_benchmark.validation.reconcile_provided_slots), keying
slot visibility off the matching dialogue turns. Every other gold field and the
row/column schema are preserved verbatim, so the output is a drop-in replacement
for the dataset's gold/ config.

    python scripts/reconcile_gold_for_publish.py \
        --gold-dir      <hf_cache>/gold \
        --dialogues-dir <hf_cache>/dialogues \
        --output-dir    data/runs/<RUN_ID>/gold_reconciled

Then review the diff and upload the output dir to the dataset's gold/ path with
`huggingface-cli upload` (see the printed hint). Uploading overwrites the frozen
labels other consumers depend on, so it is intentionally left as a manual step.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import load_yaml
from fin_life_benchmark.io.paths import RepoPaths
from fin_life_benchmark.validation.dialogue_validator import (
    event_slot_candidates,
    reconcile_provided_slots,
    standing_action_amounts,
)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _slot_aliases() -> dict[str, list[str]]:
    registry = load_yaml(
        RepoPaths.default().registries / "high_risk_action_contracts.yaml"
    )
    return registry.get("slot_aliases") or {}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--gold-dir", required=True, help="dir of gold/traj_*.jsonl")
    parser.add_argument("--dialogues-dir", required=True, help="dir of dialogues/traj_*.jsonl (for turns)")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    gold_dir = Path(args.gold_dir)
    dialogues_dir = Path(args.dialogues_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    gold_files = sorted(gold_dir.glob("*.jsonl"))
    if not gold_files:
        raise SystemExit(f"no *.jsonl under {gold_dir}")

    slot_aliases = _slot_aliases()
    total = 0
    changed = 0
    dropped_by_slot: Counter = Counter()
    regrounded_by_slot: Counter = Counter()
    for gfile in gold_files:
        traj = gfile.stem
        turns_by_sid = {
            row["session_id"]: row.get("turns") or []
            for row in _read_jsonl(dialogues_dir / f"{traj}.jsonl")
        }
        out_rows = []
        for row in _read_jsonl(gfile):
            total += 1
            plan = row.get("plan") or {}
            contract = plan.get("action_execution_contract") or {}
            resolution = row.get("action_resolution") or {}
            turns = turns_by_sid.get(row["session_id"], [])
            new_contract, new_resolution, slots = reconcile_provided_slots(
                contract,
                resolution,
                turns,
                slot_candidates=event_slot_candidates(plan, slot_aliases),
                reference_values=standing_action_amounts(plan),
            )
            if slots:
                changed += 1
                after = new_contract.get("grounded_slots") or {}
                for slot in slots:
                    if slot in after:
                        regrounded_by_slot[slot] += 1
                    else:
                        dropped_by_slot[slot] += 1
                # Preserve every other gold field and key order; only the two
                # reconciled fields change.
                row = {
                    **row,
                    "plan": {**plan, "action_execution_contract": new_contract},
                    "action_resolution": new_resolution,
                }
            out_rows.append(row)
        dest = output_dir / gfile.name
        with dest.open("w", encoding="utf-8") as handle:
            for row in out_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"reconciled {changed}/{total} gold rows across {len(gold_files)} file(s)")
    print("re-grounded slots (value corrected from event params):")
    for slot, n in regrounded_by_slot.most_common():
        print(f"  {n:5d}  {slot}")
    print("dropped slots (moved to missing_slots):")
    for slot, n in dropped_by_slot.most_common():
        print(f"  {n:5d}  {slot}")
    print(f"output -> {output_dir}")
    print(
        "\nto publish (overwrites frozen gold labels):\n"
        f"  huggingface-cli upload --repo-type dataset <REPO_ID> {output_dir} gold"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
