#!/usr/bin/env python
"""Merge regenerated sessions back into the published dialogues/ and gold/ split.

scripts/regenerate_flagged_sessions.py emits one whole Session per file. The
published dataset stores that session as two rows -- the answer-free dialogue and
the gold labels -- so this splits each regenerated session along the same seam
and writes it over the matching row, leaving every other row byte-identical.

Field ownership is taken from the published rows themselves rather than
hard-coded, so a schema change cannot silently drop a field: whatever key the
existing row has, the regenerated session supplies if it carries it. Keys the
session does not carry (persona_id, session_date, month_index, ...) keep their
published values.

Example:
  python scripts/merge_regenerated_sessions.py \
      --dialogues-dir data/runs/hf_full/final_dialogues \
      --gold-dir     data/runs/hf_full/gold_contract_patched \
      --regenerated-dir data/runs/hf_full/regenerated_contracts \
      --output-root  data/runs/hf_full/merged
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

# Identity and timeline keys belong to the published row, never to a regenerated
# session: rewriting them would repoint the row at a different session or undo
# the calendar dates.
PROTECTED = frozenset(
    {
        "persona_id",
        "session_id",
        "trajectory_id",
        "session_date",
        "month_index",
        "age",
        "transition_order",
        "window_index",
        "position_in_window",
    }
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


def _merge_row(row: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Overwrite the row's own keys from the session, preserving key order."""
    merged = dict(row)
    for key in row:
        if key in PROTECTED or key not in session:
            continue
        merged[key] = session[key]
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dialogues-dir", required=True)
    parser.add_argument("--gold-dir", required=True)
    parser.add_argument("--regenerated-dir", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()

    regenerated: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(Path(args.regenerated_dir).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        session = payload.get("session") or payload
        key = (session["trajectory_id"], session["session_id"])
        if key in regenerated:
            raise SystemExit(f"duplicate regenerated session: {key}")
        # The generator reports the model on the session; the dialogue row keeps
        # it in flat model/provider fields.
        metadata = session.get("generation_metadata") or {}
        session.setdefault("model", metadata.get("model"))
        session.setdefault("provider", metadata.get("provider"))
        regenerated[key] = session

    output_root = Path(args.output_root)
    applied: Counter = Counter()
    for label, source in (("dialogues", args.dialogues_dir), ("gold", args.gold_dir)):
        for path in sorted(Path(source).glob("traj_*.jsonl")):
            rows = _read_jsonl(path)
            out_rows = []
            for row in rows:
                session = regenerated.get((row["trajectory_id"], row["session_id"]))
                if session is None:
                    out_rows.append(row)
                    continue
                out_rows.append(_merge_row(row, session))
                applied[label] += 1
            _write_jsonl(output_root / label / path.name, out_rows)

    missing = len(regenerated) - applied["dialogues"]
    if missing:
        raise SystemExit(
            f"{missing} regenerated session(s) matched no dialogue row -- "
            "wrong --dialogues-dir?"
        )
    print(f"merged {len(regenerated)} regenerated sessions")
    for label, count in applied.most_common():
        print(f"  {count:5d}  rows rewritten in {label}/")
    print(f"output -> {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
