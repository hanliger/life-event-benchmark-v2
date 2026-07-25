#!/usr/bin/env python
"""Assign a calendar date to every dialogue session (deterministic, in-place-able).

Sessions carry ``month_index`` but no calendar date, so temporal-reasoning items
("which happened first", "how many months apart", "what year") cannot be built.
This stamps a single ``session_date`` (``YYYY-MM-DD``) onto each dialogue row and
the matching gold row.

Calendar placement is *end-aligned*: every trajectory's last session lands in
``--end-month`` (default 2026-06), so each trajectory's start month is
``end_month - max(month_index)``. That keeps all dates in the past and gives every
trajectory the same "now", which trajectory-spanning questions rely on. Because
``age == first_age + month_index // 12`` holds exactly in this corpus, the start
month also fixes each persona's birthday month -- end-alignment makes those
differ per trajectory instead of putting everyone in January.

Day-of-month is spread deterministically across 1..28 (never 29-31, so no month
is short of slots) with a seeded jitter, strictly increasing in session order
within a calendar month. Sessions whose dialogue already refers back to a day in
the current month ("이번 달 10일에 처음 급여 들어왔어요") are pushed on or after
that day, so the date never contradicts the transcript.

Example:
  python scripts/assign_session_dates.py \
      --dialogues-dir data/runs/hf_full/final_dialogues \
      --gold-dir     data/runs/hf_full/final_gold \
      --output-root  data/runs/hf_full/dated \
      --manifest     data/runs/hf_full/session_dates.manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

# Only 1..28 so every calendar month offers the same number of slots.
LAST_USABLE_DAY = 28

# A user pointing back at a day inside the current month dates the session: the
# visit cannot precede the deposit it describes. Past-tense verbs only -- "매달
# 21일에 나가요" is a schedule, not an event, and constrains nothing.
_SAME_MONTH_PAST_DAY = re.compile(
    r"(?:이번\s*달|이달)\s*([12]?[0-9]|3[01])\s*일(?:에|부터)?"
    r"[^.?!]{0,25}?(?:들어왔|나갔|냈|빠졌|이체됐|입금됐|처리됐|되었|했어|받았)"
    r"|([12]?[0-9]|3[01])\s*일에\s*(?:처음\s*)?[^.?!]{0,20}?"
    r"(?:들어왔|나갔|냈|빠졌|입금됐)"
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


def _parse_month(text: str) -> int:
    """``"2026-06"`` -> months since year 0, so month arithmetic is plain ints."""
    year, month = text.split("-")
    return int(year) * 12 + (int(month) - 1)


def _format_date(month_ordinal: int, day: int) -> str:
    year, month = divmod(month_ordinal, 12)
    return f"{year:04d}-{month + 1:02d}-{day:02d}"


def _jitter(trajectory_id: str, month_index: int, position: int, span: int) -> int:
    """Deterministic offset in ``[0, span)`` -- stable across runs and platforms.

    Uses a digest rather than ``hash()``, which is salted per process.
    """
    if span <= 1:
        return 0
    digest = hashlib.sha256(
        f"{trajectory_id}:{month_index}:{position}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:4], "big") % span


def _referenced_day(turns: list[dict[str, Any]]) -> int | None:
    """Earliest day-of-month the user says something already happened on."""
    text = " ".join(
        str(turn.get("text", ""))
        for turn in turns
        if turn.get("speaker") == "user"
    )
    days = [
        int(group)
        for match in _SAME_MONTH_PAST_DAY.finditer(text)
        for group in match.groups()
        if group
    ]
    days = [day for day in days if 1 <= day <= LAST_USABLE_DAY]
    return max(days) if days else None


def _assign_days(
    trajectory_id: str,
    month_index: int,
    constraints: list[int | None],
) -> tuple[list[int], int, int]:
    """Non-decreasing days in 1..28 for the sessions sharing one calendar month.

    ``constraints[i]`` is the earliest allowed day for session ``i`` (or None).
    Days strictly increase whenever the slots allow it, so session order is
    readable from the date. When they do not -- a month holding 17 sessions where
    one is pinned to "the 15th or later" cannot fit 17 distinct days -- sessions
    tie on a day rather than break either the ordering or the transcript. Two
    bank visits on one day is ordinary; a date that contradicts what the customer
    said is not.

    Returns ``(days, shifted, tied)``: how many sessions moved off their spread
    slot to honour a constraint, and how many share a day with the previous one.
    """
    count = len(constraints)
    if count > LAST_USABLE_DAY:
        raise ValueError(
            f"{trajectory_id} month_index={month_index} has {count} sessions, "
            f"more than the {LAST_USABLE_DAY} available day slots"
        )
    # Disjoint, ascending windows: the spread alone is already strictly increasing.
    spread: list[int] = []
    for position in range(count):
        low = position * LAST_USABLE_DAY // count + 1
        high = (position + 1) * LAST_USABLE_DAY // count
        spread.append(low + _jitter(trajectory_id, month_index, position, high - low + 1))

    days: list[int] = []
    shifted = tied = 0
    previous = 0
    for position, base in enumerate(spread):
        floor = max(constraints[position] or 1, base)
        # Leave room for the sessions still to be placed, so an early constraint
        # cannot starve the tail into a pile-up at day 28.
        cap = LAST_USABLE_DAY - (count - 1 - position)
        day = max(floor, previous + 1)
        if day > cap:
            # No distinct slot left: keep the constraint and the ordering, drop
            # only the strictness.
            day = min(max(floor, previous), LAST_USABLE_DAY)
            if day == previous:
                tied += 1
        if day != base:
            shifted += 1
        days.append(day)
        previous = day
    return days, shifted, tied


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dialogues-dir", required=True)
    parser.add_argument("--gold-dir", required=True)
    parser.add_argument(
        "--output-root",
        default=None,
        help="write dated copies to <root>/dialogues and <root>/gold "
        "(default: --in-place required)",
    )
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--manifest", default=None)
    parser.add_argument(
        "--end-month",
        default="2026-06",
        help="calendar month of every trajectory's LAST session (YYYY-MM)",
    )
    args = parser.parse_args()

    if not args.in_place and not args.output_root:
        raise SystemExit("pass --output-root or --in-place")

    dialogues_dir = Path(args.dialogues_dir)
    gold_dir = Path(args.gold_dir)
    dialogue_files = sorted(dialogues_dir.glob("traj_*.jsonl"))
    if not dialogue_files:
        raise SystemExit(f"no traj_*.jsonl under {dialogues_dir}")

    end_month = _parse_month(args.end_month)
    manifest: dict[str, Any] = {
        "end_month": args.end_month,
        "last_usable_day": LAST_USABLE_DAY,
        "trajectories": {},
    }
    totals = Counter()

    for dialogue_path in dialogue_files:
        trajectory_id = dialogue_path.stem
        rows = _read_jsonl(dialogue_path)
        gold_path = gold_dir / f"{trajectory_id}.jsonl"
        gold_rows = _read_jsonl(gold_path)
        gold_by_sid = {row["session_id"]: row for row in gold_rows}
        missing = [r["session_id"] for r in rows if r["session_id"] not in gold_by_sid]
        if missing:
            raise SystemExit(
                f"{trajectory_id}: gold missing {len(missing)} session(s), "
                f"e.g. {missing[:3]}"
            )

        month_indices = [row["month_index"] for row in rows]
        if month_indices != sorted(month_indices):
            raise SystemExit(f"{trajectory_id}: month_index is not sorted")
        start_month = end_month - max(month_indices)

        by_month: dict[int, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            by_month[row["month_index"]].append(index)

        dates: dict[str, str] = {}
        for month_index, indices in sorted(by_month.items()):
            constraints = [_referenced_day(rows[i].get("turns") or []) for i in indices]
            days, shifted, tied = _assign_days(trajectory_id, month_index, constraints)
            totals["shifted"] += shifted
            totals["tied"] += tied
            totals["constrained"] += sum(1 for c in constraints if c)
            for index, day in zip(indices, days):
                dates[rows[index]["session_id"]] = _format_date(
                    start_month + month_index, day
                )

        dated_rows = [
            _with_date(row, dates[row["session_id"]], after="month_index")
            for row in rows
        ]
        dated_gold = [
            _with_date(row, dates[row["session_id"]], after="session_id")
            if row["session_id"] in dates
            else row
            for row in gold_rows
        ]
        totals["sessions"] += len(dated_rows)

        if args.in_place:
            _write_jsonl(dialogue_path, dated_rows)
            _write_jsonl(gold_path, dated_gold)
        else:
            root = Path(args.output_root)
            _write_jsonl(root / "dialogues" / f"{trajectory_id}.jsonl", dated_rows)
            _write_jsonl(root / "gold" / f"{trajectory_id}.jsonl", dated_gold)

        manifest["trajectories"][trajectory_id] = {
            "start_date": dates[rows[0]["session_id"]],
            "end_date": dates[rows[-1]["session_id"]],
            "months": max(month_indices),
            "sessions": len(rows),
        }

    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"dated {totals['sessions']} sessions across {len(dialogue_files)} trajectories")
    print(f"transcript day-constraints honoured: {totals['constrained']}")
    print(f"sessions moved off their spread slot: {totals['shifted']}")
    print(f"sessions sharing a day with the previous one: {totals['tied']}")
    for trajectory_id, info in manifest["trajectories"].items():
        print(f"  {trajectory_id}: {info['start_date']} .. {info['end_date']} "
              f"({info['months']}개월, {info['sessions']} sessions)")
    if args.manifest:
        print(f"manifest -> {args.manifest}")
    return 0


def _with_date(row: dict[str, Any], date: str, *, after: str) -> dict[str, Any]:
    """Copy ``row`` with ``session_date`` inserted right after ``after``.

    Key order is cosmetic for JSONL readers but keeps published diffs readable.
    """
    out: dict[str, Any] = {}
    inserted = False
    for key, value in row.items():
        out[key] = value
        if key == after:
            out["session_date"] = date
            inserted = True
    if not inserted:
        out["session_date"] = date
    return out


if __name__ == "__main__":
    raise SystemExit(main())
