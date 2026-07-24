#!/usr/bin/env python
"""Audit lifecycle-masking ladders and recalculated counterfactual PrefixGold."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

LEVELS = ("full", "mask_terminal", "mask_upcoming", "mask_all")
STATUS_RANK = {
    "no_event": 0,
    "weak_signal": 1,
    "upcoming": 2,
    "occurred": 3,
    "cancelled": 3,
}


def _target_status(gold: dict[str, Any], event_id: str) -> dict[str, Any]:
    for event in gold.get("gold_life_events") or []:
        if event.get("event_instance_id") == event_id:
            return {
                "event_status": event.get("event_status"),
                "occurred": bool(event.get("occurred")),
                "update_allowed": bool(event.get("update_allowed")),
            }
    return {
        "event_status": "no_event",
        "occurred": False,
        "update_allowed": False,
    }


def _non_target_payload(gold: dict[str, Any], event_id: str) -> dict[str, Any]:
    # old_value is replay-derived state, not frozen evidence. Masking a target
    # update can legitimately change the old_value observed by a later,
    # otherwise unchanged event update on the same path.
    non_target_updates = []
    for item in gold.get("gold_memory_updates") or []:
        if item.get("source_event_instance_id") == event_id:
            continue
        normalized = dict(item)
        normalized.pop("old_value", None)
        non_target_updates.append(normalized)
    return {
        "events": sorted(
            (
                item
                for item in gold.get("gold_life_events") or []
                if item.get("event_instance_id") != event_id
            ),
            key=lambda item: item["event_instance_id"],
        ),
        "memory_updates": non_target_updates,
        "action_decisions": [
            item
            for item in gold.get("gold_action_decisions") or []
            if item.get("source_event_instance_id") != event_id
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Lifecycle masking audit",
        "",
        f"- Decision: **{report['decision']}**",
        f"- Events: {report['event_count']}",
        f"- Prefix-gold cases: {report['prefix_gold_case_count']}",
        f"- Exclusions: {report['exclusion_count']}",
        f"- Replacement assignments: {report['replacement_assignment_count']}",
        f"- Violations: {report['violation_count']}",
        "",
        "## Status distribution",
        "",
    ]
    for level in LEVELS:
        lines.append(
            f"- `{level}`: "
            + ", ".join(
                f"{status}={count}"
                for status, count in sorted(
                    report["status_distribution"].get(level, {}).items()
                )
            )
        )
    lines.extend(["", "## Violations", ""])
    if not report["violations"]:
        lines.append("None.")
    else:
        for item in report["violations"]:
            lines.append(
                f"- `{item.get('event_instance_id')}/{item.get('level')}` "
                f"**{item['code']}**: {item['detail']}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ladder", required=True)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--exclusions", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-events", type=int)
    args = parser.parse_args()

    ladder = json.loads(Path(args.ladder).read_text(encoding="utf-8"))
    exclusions = json.loads(Path(args.exclusions).read_text(encoding="utf-8"))
    ladder_by_event = {item["event_instance_id"]: item for item in ladder}
    violations: list[dict[str, Any]] = []
    status_distribution: dict[str, Counter[str]] = {
        level: Counter() for level in LEVELS
    }
    replacement_count = 0

    def add(event_id: str | None, level: str | None, code: str, detail: str) -> None:
        violations.append({
            "event_instance_id": event_id,
            "level": level,
            "code": code,
            "detail": detail,
        })

    if exclusions:
        add(None, None, "nonempty_exclusions", f"{len(exclusions)} event(s) excluded")
    if args.expected_events is not None and len(ladder) != args.expected_events:
        add(
            None,
            None,
            "event_count",
            f"expected {args.expected_events}, got {len(ladder)}",
        )
    if len(ladder_by_event) != len(ladder):
        add(None, None, "duplicate_event", "ladder contains duplicate event IDs")

    for result in ladder:
        event_id = result["event_instance_id"]
        levels = result.get("ladder") or []
        if [item.get("level") for item in levels] != list(LEVELS):
            add(event_id, None, "level_order", "expected the four canonical levels")
            continue
        statuses = [item["event_status"] for item in levels]
        for level in levels:
            status_distribution[level["level"]][level["event_status"]] += 1
            expected_update = (
                level["level"] == "full"
                and level["event_status"] == "occurred"
            )
            if bool(level["update_allowed"]) != expected_update:
                add(
                    event_id,
                    level["level"],
                    "update_allowed",
                    f"expected {expected_update}, got {level['update_allowed']}",
                )
            if level["level"] != "full" and level["occurred"]:
                add(event_id, level["level"], "masked_occurred", "masked level occurred")
        if any(
            STATUS_RANK[later] > STATUS_RANK[earlier]
            for earlier, later in zip(statuses, statuses[1:])
        ):
            add(event_id, None, "non_monotonic_status", str(statuses))

        mapping: dict[str, str] = {}
        masked_sets = []
        for level in levels:
            donor_ids = []
            masked_sets.append({
                filler["slot_session_id"] for filler in level.get("fillers") or []
            })
            for filler in level.get("fillers") or []:
                replacement_count += 1
                slot_id = filler["slot_session_id"]
                donor_id = filler["donor_session_id"]
                donor_ids.append(donor_id)
                if filler.get("donor_source_kind") != "synthetic_reserve":
                    add(event_id, level["level"], "donor_source", str(filler))
                if filler.get("donor_month_index") is not None:
                    add(event_id, level["level"], "timed_donor", str(filler))
                if filler.get("donor_already_visible"):
                    add(event_id, level["level"], "visible_donor", str(filler))
                if not filler.get("same_persona"):
                    add(event_id, level["level"], "cross_persona", str(filler))
                previous = mapping.setdefault(slot_id, donor_id)
                if previous != donor_id:
                    add(
                        event_id,
                        level["level"],
                        "level_inconsistent_donor",
                        f"{slot_id}: {previous} vs {donor_id}",
                    )
            if len(donor_ids) != len(set(donor_ids)):
                add(event_id, level["level"], "duplicate_donor", str(donor_ids))
        if not all(left <= right for left, right in zip(masked_sets, masked_sets[1:])):
            add(event_id, None, "non_nested_masks", str(masked_sets))

    prefix_case_count = 0
    current_event_id = None
    current_records: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()

    def audit_prefix_group(
        event_id: str | None,
        records: list[dict[str, Any]],
    ) -> None:
        if event_id is None:
            return
        result = ladder_by_event.get(event_id)
        if result is None:
            add(event_id, None, "orphan_prefix_gold", "event absent from ladder")
            return
        by_level = {record["level"]: record for record in records}
        if set(by_level) != set(LEVELS):
            add(event_id, None, "prefix_level_set", f"found {sorted(by_level)}")
            return
        full_gold = by_level["full"]["prefix_gold"]
        non_target_full = _non_target_payload(full_gold, event_id)
        ladder_levels = {item["level"]: item for item in result["ladder"]}
        for level in LEVELS:
            record = by_level[level]
            gold = record["prefix_gold"]
            expected = {
                key: ladder_levels[level][key]
                for key in ("event_status", "occurred", "update_allowed")
            }
            actual = _target_status(gold, event_id)
            if actual != expected:
                add(event_id, level, "prefix_target_status", f"{actual} != {expected}")
            if int(gold.get("checkpoint_session_count", -1)) != int(
                result["checkpoint_session_count"]
            ):
                add(event_id, level, "checkpoint_mismatch", str(gold.get("checkpoint_session_count")))
            if len(gold.get("visible_sessions") or []) != int(
                result["checkpoint_session_count"]
            ):
                add(event_id, level, "visible_session_count", str(len(gold.get("visible_sessions") or [])))
            if _non_target_payload(gold, event_id) != non_target_full:
                add(
                    event_id,
                    level,
                    "collateral_gold_drift",
                    "non-target event/evidence/new-memory/action gold differs from full",
                )

    with Path(args.prefix_gold).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            prefix_case_count += 1
            case_id = record.get("case_id")
            if case_id in seen_case_ids:
                add(record.get("event_instance_id"), record.get("level"), "duplicate_case_id", str(case_id))
            seen_case_ids.add(case_id)
            event_id = record.get("event_instance_id")
            if current_event_id is not None and event_id != current_event_id:
                audit_prefix_group(current_event_id, current_records)
                current_records = []
            current_event_id = event_id
            current_records.append(record)
    audit_prefix_group(current_event_id, current_records)

    expected_cases = len(ladder) * len(LEVELS)
    if prefix_case_count != expected_cases:
        add(None, None, "prefix_case_count", f"expected {expected_cases}, got {prefix_case_count}")

    report = {
        "decision": "PASS" if not violations else "FAIL",
        "event_count": len(ladder),
        "prefix_gold_case_count": prefix_case_count,
        "exclusion_count": len(exclusions),
        "replacement_assignment_count": replacement_count,
        "violation_count": len(violations),
        "status_distribution": {
            level: dict(sorted(counts.items()))
            for level, counts in status_distribution.items()
        },
        "violations": violations,
    }
    decision = {
        key: report[key]
        for key in (
            "decision",
            "event_count",
            "prefix_gold_case_count",
            "exclusion_count",
            "replacement_assignment_count",
            "violation_count",
        )
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "masking_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "masking_audit.md").write_text(_markdown(report), encoding="utf-8")
    (out_dir / "masking_decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, ensure_ascii=False))
    print(f"audit -> {out_dir}")
    return 0 if report["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
