#!/usr/bin/env python
"""Freeze a deterministic semantic-regression plan subset for one trajectory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore # noqa: F401

from fin_life_benchmark.io import read_jsonl, write_jsonl

HIGH_RISK = {"FA-07", "FA-08", "FA-09", "FA-10"}
EVIDENCE_TYPES = {
    "weak_signal_evidence",
    "upcoming_evidence",
    "occurred_evidence",
    "cancellation_evidence",
}
POLICY_HINTS = ("공동", "명의", "수수료", "계좌 개설", "목적계좌")


def select_regression_plans(
    plans: list[dict], repaired_ids: set[str] | None = None
) -> tuple[list[dict], dict[str, list[str]]]:
    repaired_ids = repaired_ids or set()
    reasons: dict[str, set[str]] = {}

    def add(session_id: str, reason: str) -> None:
        reasons.setdefault(session_id, set()).add(reason)

    hard_seen: set[tuple[str, str]] = set()
    for plan in plans:
        session_id = str(plan["session_id"])
        session_type = plan.get("session_type")
        if session_type in EVIDENCE_TYPES:
            add(session_id, "evidence")
        if plan.get("mapped_action") in HIGH_RISK:
            add(session_id, "high_risk")
        if session_type == "stale_recall_session":
            add(session_id, "stale_recall")
        if session_type == "cancellation_evidence":
            add(session_id, "cancellation")
        if session_id in repaired_ids:
            add(session_id, "previously_repaired")
        if session_type != "hard_negative" and any(
            hint in str(plan.get("financial_task") or "") for hint in POLICY_HINTS
        ):
            add(session_id, "bank_policy_surface")
        if session_type == "hard_negative":
            key = (
                str(plan.get("hard_negative_type")),
                str(plan.get("hard_negative_surface_variant_id")),
            )
            if key not in hard_seen:
                add(session_id, "hard_negative_variant")
                if any(
                    hint in str(plan.get("financial_task") or "")
                    for hint in POLICY_HINTS
                ):
                    add(session_id, "bank_policy_surface")
                hard_seen.add(key)
    selected = [plan for plan in plans if str(plan["session_id"]) in reasons]
    return selected, {
        key: sorted(value) for key, value in sorted(reasons.items())
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectory-id", default="traj_001")
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--sessions-dir")
    parser.add_argument("--previously-repaired-ids-file")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    source = Path(args.plans_dir) / f"plans_{args.trajectory_id}.jsonl"
    plans = list(read_jsonl(source))
    repaired: set[str] = set()
    if args.sessions_dir:
        session_path = Path(args.sessions_dir) / f"sessions_{args.trajectory_id}.jsonl"
        if session_path.exists():
            repaired.update(
                str(item["session_id"])
                for item in read_jsonl(session_path)
                if int((item.get("generation_metadata") or {}).get("repair_count") or 0)
            )
    if args.previously_repaired_ids_file:
        repaired.update(
            line.strip()
            for line in Path(args.previously_repaired_ids_file)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    selected, reasons = select_regression_plans(plans, repaired)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"plans_{args.trajectory_id}.jsonl"
    write_jsonl(output_path, selected)
    index = {
        "trajectory_id": args.trajectory_id,
        "source_plan": str(source.resolve()),
        "source_plan_count": len(plans),
        "selected_plan_count": len(selected),
        "session_ids": [item["session_id"] for item in selected],
        "selection_reasons": reasons,
    }
    (output_dir / "regression_canary_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"regression canary: {len(selected)} frozen plans -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
