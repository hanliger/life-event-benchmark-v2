#!/usr/bin/env python
"""Deterministically sample a stratified model bake-off plan set."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.sample_dialogue_plans in tests
    from scripts import _bootstrap  # type: ignore # noqa: F401

from fin_life_benchmark.io import read_jsonl, write_jsonl

TARGETS = {
    "occurred_evidence": 10,
    "weak_signal_evidence": 6,
    "upcoming_evidence": 6,
    "cancellation_evidence": 5,
    "consequence_session": 5,
    "stale_recall_session": 5,
    "hard_negative": 7,
    "routine_financial": 4,
}
HIGH_RISK = {"FA-07", "FA-08", "FA-09", "FA-10"}


def _session_number(record: dict) -> int:
    return int(str(record["session_id"]).lstrip("S"))


def sample_plans(records: list[dict], total: int, seed: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    selected: list[dict] = []
    reasons: list[dict] = []
    selected_ids: set[str] = set()
    domains: set[str] = set()
    risks: set[str] = set()
    update_modes: set[str] = set()

    def choose(candidates: list[dict], reason: str) -> bool:
        candidates = [item for item in candidates if item["session_id"] not in selected_ids]
        if not candidates:
            return False
        rng.shuffle(candidates)

        def score(item: dict) -> tuple[int, int, str]:
            event = (item.get("structured_context") or {}).get("event") or {}
            domain = event.get("domain") or "no_event"
            risk = "high" if item.get("mapped_action") in HIGH_RISK else "low"
            update_mode = "with_update" if item.get("session_update_paths") else "no_update"
            number = _session_number(item)
            adjacent = any(abs(number - _session_number(chosen)) == 1 for chosen in selected)
            diversity = int(domain not in domains) + int(risk not in risks) + int(update_mode not in update_modes)
            return diversity, int(not adjacent), item["session_id"]

        item = max(candidates, key=score)
        selected.append(item)
        selected_ids.add(item["session_id"])
        event = (item.get("structured_context") or {}).get("event") or {}
        domains.add(event.get("domain") or "no_event")
        risks.add("high" if item.get("mapped_action") in HIGH_RISK else "low")
        update_modes.add("with_update" if item.get("session_update_paths") else "no_update")
        reasons.append({
            "session_id": item["session_id"],
            "reason": reason,
            "session_type": item.get("session_type"),
            "domain": event.get("domain"),
            "risk": "high" if item.get("mapped_action") in HIGH_RISK else "low",
            "has_session_update": bool(item.get("session_update_paths")),
        })
        return True

    # Explicit feature guarantees before filling the per-type quotas.
    choose([item for item in records if item.get("stale_memory_pairs")], "required_stale_pair")
    choose([
        item for item in records
        if item.get("session_type") == "cancellation_evidence"
        and any(update.get("operation") == "clear_pending" for update in (item.get("structured_context") or {}).get("session_memory_updates", []))
    ], "required_cancelled_pending")
    choose([item for item in records if item.get("session_update_paths")], "required_session_update")
    choose([item for item in records if not item.get("session_update_paths")], "required_no_update")

    selected_counts = Counter(item.get("session_type") for item in selected)
    for session_type, target in TARGETS.items():
        desired = round(target * total / 48)
        while selected_counts[session_type] < desired and len(selected) < total:
            if not choose(
                [item for item in records if item.get("session_type") == session_type],
                f"stratum:{session_type}",
            ):
                break
            selected_counts[session_type] += 1
    while len(selected) < total:
        if not choose(records, "redistributed_unavailable_quota"):
            break
    selected.sort(key=_session_number)
    reason_by_id = {item["session_id"]: item for item in reasons}
    return selected, [reason_by_id[item["session_id"]] for item in selected]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--trajectory-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--total", type=int, default=48)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    source = Path(args.plans_dir) / f"plans_{args.trajectory_id}.jsonl"
    if not source.exists():
        raise SystemExit(f"missing plan file: {source}")
    records = list(read_jsonl(source))
    selected, reasons = sample_plans(records, args.total, args.seed)
    if len(selected) != args.total:
        raise SystemExit(f"requested {args.total} plans but selected {len(selected)}")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / source.name
    write_jsonl(output, selected)
    manifest = {
        "trajectory_id": args.trajectory_id,
        "seed": args.seed,
        "requested_total": args.total,
        "selected_total": len(selected),
        "source_plan_file": str(source.resolve()),
        "selected_session_ids": [item["session_id"] for item in selected],
        "session_type_counts": dict(sorted(Counter(item["session_type"] for item in selected).items())),
        "selections": reasons,
    }
    (output_dir / "sampling_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"sampled {len(selected)} plans -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
