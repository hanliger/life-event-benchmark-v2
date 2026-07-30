#!/usr/bin/env python
"""Tabulate an RQ1 occurred-pair checkpoint ladder across models.

Reads the per-item prediction JSONLs a ladder run produced -- one file per model
-- and prints the strict-F1 trend by checkpoint, plus the component diagnostics
that say *how* a score moved: whether the model found the right sessions
(``session_only``), the right labels (``event_only``), and how many pairs it
predicted against how many were gold.

Reads the prediction rows rather than the report aggregate on purpose. A run
whose inference gate excluded an item still writes that item's row, so the
ladder stays readable when the aggregate is empty.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.io.jsonl import read_jsonl

STRICT = "strict_occurred_event_evidence_"


def _rows(path: Path) -> list[dict[str, Any]]:
    return sorted(read_jsonl(path), key=lambda r: int(r["checkpoint_session_count"]))


def _label(rows: list[dict[str, Any]], path: Path) -> str:
    return f"{rows[0]['provider']}/{rows[0]['model']}" if rows else path.stem


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", nargs="+", help="prediction JSONL per model")
    parser.add_argument("--metric", default="f1", choices=("f1", "precision", "recall"))
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    by_model: dict[str, list[dict[str, Any]]] = {}
    for raw in args.predictions:
        path = Path(raw)
        rows = _rows(path)
        by_model[_label(rows, path)] = rows

    checkpoints = sorted(
        {int(r["checkpoint_session_count"]) for rows in by_model.values() for r in rows}
    )
    names = list(by_model)

    width = max((len(n) for n in names), default=10)
    header = "| cp  | gold | " + " | ".join(n.ljust(width) for n in names) + " |"
    print(f"\nstrict occurred-pair {args.metric} by checkpoint\n")
    print(header)
    print("| --- | ---- | " + " | ".join("-" * width for _ in names) + " |")

    summary: dict[str, Any] = {"metric": args.metric, "checkpoints": {}}
    for cp in checkpoints:
        cells, per_model = [], {}
        gold = ""
        for name in names:
            row = next(
                (r for r in by_model[name] if r["checkpoint_session_count"] == cp), None
            )
            if row is None:
                cells.append("-".ljust(width))
                continue
            metrics = row["metrics"]
            value = metrics.get(f"{STRICT}{args.metric}")
            gold = str(metrics.get("gold_pair_count", ""))
            flag = ""
            if row.get("inference_configuration_error"):
                flag = " !"          # excluded from the run's own aggregate
            elif row.get("call_error") or row.get("parse_error"):
                flag = " ?"
            cells.append(
                (f"{value:.4f}{flag}" if value is not None else "none").ljust(width)
            )
            per_model[name] = {
                "f1": metrics.get(f"{STRICT}f1"),
                "precision": metrics.get(f"{STRICT}precision"),
                "recall": metrics.get(f"{STRICT}recall"),
                "session_only": metrics.get(
                    "diagnostic_evidence_session_only_f1"
                ),
                "event_only": metrics.get("diagnostic_event_id_only_f1"),
                "predicted_pair_count": metrics.get("predicted_pair_count"),
                "gold_pair_count": metrics.get("gold_pair_count"),
                "visible_session_count": row.get("visible_session_count"),
                "inference_configuration_error": row.get(
                    "inference_configuration_error"
                ),
                "inference_metadata_gap": row.get("inference_metadata_gap"),
            }
        print(f"| {cp:<3} | {gold:<4} | " + " | ".join(cells) + " |")
        summary["checkpoints"][cp] = per_model

    print("\nmean over the ladder:")
    for name in names:
        values = [
            r["metrics"].get(f"{STRICT}{args.metric}")
            for r in by_model[name]
            if r["metrics"].get(f"{STRICT}{args.metric}") is not None
        ]
        mean = sum(values) / len(values) if values else None
        print(
            f"  {name.ljust(width)}  {'none' if mean is None else f'{mean:.4f}'}"
            f"  ({len(values)} rungs)"
        )
        summary.setdefault("ladder_mean", {})[name] = mean

    print("\n! = excluded from that run's aggregate by the inference gate; "
          "? = call or parse error")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nsummary -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
