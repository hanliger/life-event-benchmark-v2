#!/usr/bin/env python
"""Export answer-free benchmark inputs from private run artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import read_jsonl, write_jsonl


def _public_memory(memory: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for path, raw_cell in memory.items():
        cell = raw_cell or {}
        pending = cell.get("pending_proposal")
        public[path] = {
            "value": cell.get("value"),
            "status": cell.get("status"),
            "historical_values": list(cell.get("historical_values") or []),
            "pending_proposal": (
                {
                    "value": pending.get("value"),
                    "valid_from": pending.get("valid_from"),
                }
                if isinstance(pending, dict)
                else None
            ),
        }
    return public


def public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session["session_id"],
        "trajectory_id": session["trajectory_id"],
        "turns": session.get("turns") or [],
    }


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    public_metadata: dict[str, Any] = {}
    if metadata.get("initial_memory"):
        public_metadata["initial_memory"] = _public_memory(metadata["initial_memory"])
    return {
        "item_id": item["item_id"],
        "stage": item["stage"],
        "trajectory_id": item["trajectory_id"],
        "prefix_id": item["prefix_id"],
        "visible_sessions": item.get("visible_sessions") or [],
        "question": item["question"],
        "options": [
            {"option_id": option["option_id"], "text": option["text"]}
            for option in (item.get("options") or [])
        ],
        "metadata": public_metadata,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--items-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    public_sessions_dir = output_dir / "dialogues" / "sessions"
    public_items_dir = output_dir / "benchmark_items"

    session_count = 0
    for path in sorted(Path(args.sessions_dir).glob("sessions_*.jsonl")):
        rows = [public_session(row) for row in read_jsonl(path)]
        session_count += write_jsonl(public_sessions_dir / path.name, rows)

    item_count = 0
    for path in sorted(Path(args.items_dir).glob("stage*.jsonl")):
        rows = [public_item(row) for row in read_jsonl(path)]
        item_count += write_jsonl(public_items_dir / path.name, rows)

    print(f"public benchmark: {session_count} sessions, {item_count} items -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
