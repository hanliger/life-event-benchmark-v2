#!/usr/bin/env python
"""Audit the ``session_date`` stamps added by scripts/assign_session_dates.py.

Checks every invariant a temporal-reasoning item could rely on:

  order          dates never go backwards within a trajectory
  month_offset   months between the first date and each date == month_index
  age            date year/month is consistent with the row's ``age``
                 (``age == first_age + month_index // 12`` in this corpus)
  transcript     a session whose dialogue says something happened on day N of the
                 current month is dated on or after day N
  gold_parity    the gold row for each session carries the identical date
  untouched      every pre-existing field is byte-identical to the baseline
  no_future      no date past --today

Example:
  python scripts/audit_session_dates.py \
      --dialogues-dir data/runs/hf_full/dated/dialogues \
      --gold-dir     data/runs/hf_full/dated/gold \
      --baseline-dialogues-dir data/runs/hf_full/final_dialogues \
      --baseline-gold-dir      data/runs/hf_full/final_gold
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from assign_session_dates import _referenced_day  # noqa: E402


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _month_ordinal(value: str) -> int:
    year, month, _ = value.split("-")
    return int(year) * 12 + (int(month) - 1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dialogues-dir", required=True)
    parser.add_argument("--gold-dir", required=True)
    parser.add_argument("--baseline-dialogues-dir", default=None)
    parser.add_argument("--baseline-gold-dir", default=None)
    parser.add_argument("--today", default="2026-07-25")
    args = parser.parse_args()

    dialogues_dir = Path(args.dialogues_dir)
    gold_dir = Path(args.gold_dir)
    today = date.fromisoformat(args.today)

    failures: Counter = Counter()
    examples: dict[str, list[str]] = {}
    sessions = 0

    def fail(check: str, detail: str) -> None:
        failures[check] += 1
        examples.setdefault(check, [])
        if len(examples[check]) < 3:
            examples[check].append(detail)

    for dialogue_path in sorted(dialogues_dir.glob("traj_*.jsonl")):
        trajectory_id = dialogue_path.stem
        rows = _read_jsonl(dialogue_path)
        gold_by_sid = {
            row["session_id"]: row
            for row in _read_jsonl(gold_dir / f"{trajectory_id}.jsonl")
        }
        first_month = _month_ordinal(rows[0]["session_date"])
        first_age = rows[0]["age"]
        previous = None

        for row in rows:
            sessions += 1
            sid = f"{trajectory_id}/{row['session_id']}"
            value = row.get("session_date")
            if not isinstance(value, str):
                fail("present", f"{sid}: session_date missing")
                continue
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                fail("parse", f"{sid}: {value!r}")
                continue

            if previous is not None and parsed < previous:
                fail("order", f"{sid}: {value} < {previous.isoformat()}")
            previous = parsed

            offset = _month_ordinal(value) - first_month
            if offset != row["month_index"]:
                fail("month_offset", f"{sid}: offset {offset} != month_index {row['month_index']}")

            if row["age"] != first_age + row["month_index"] // 12:
                fail("age", f"{sid}: age {row['age']}")

            referenced = _referenced_day(row.get("turns") or [])
            if referenced is not None and parsed.day < referenced:
                fail("transcript", f"{sid}: dated day {parsed.day} < referenced {referenced}")

            gold = gold_by_sid.get(row["session_id"])
            if gold is None:
                fail("gold_parity", f"{sid}: no gold row")
            elif gold.get("session_date") != value:
                fail("gold_parity", f"{sid}: gold {gold.get('session_date')!r} != {value!r}")

            if parsed > today:
                fail("no_future", f"{sid}: {value}")

    if args.baseline_dialogues_dir and args.baseline_gold_dir:
        pairs = [
            (dialogues_dir, Path(args.baseline_dialogues_dir)),
            (gold_dir, Path(args.baseline_gold_dir)),
        ]
        for current_dir, baseline_dir in pairs:
            for path in sorted(current_dir.glob("traj_*.jsonl")):
                base_rows = _read_jsonl(baseline_dir / path.name)
                new_rows = _read_jsonl(path)
                if len(base_rows) != len(new_rows):
                    fail("untouched", f"{path.name}: {len(base_rows)} -> {len(new_rows)} rows")
                    continue
                for base, new in zip(base_rows, new_rows):
                    stripped = {k: v for k, v in new.items() if k != "session_date"}
                    if stripped != base:
                        changed = sorted(
                            k for k in set(base) | set(stripped)
                            if base.get(k) != stripped.get(k)
                        )
                        fail("untouched", f"{path.name}/{new.get('session_id')}: {changed}")

    print(f"audited {sessions} sessions")
    if not failures:
        print("ALL CHECKS PASS")
        return 0
    print("FAILURES:")
    for check, count in failures.most_common():
        print(f"  {count:6d}  {check}")
        for detail in examples[check]:
            print(f"            {detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
