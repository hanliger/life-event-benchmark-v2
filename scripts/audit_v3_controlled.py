#!/usr/bin/env python
"""Audit controlled-run trajectory/session/checkpoint invariants."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import read_jsonl
from fin_life_benchmark.gold.prefix_gold_exporter import serialize_memory_state
from fin_life_benchmark.validation.audits import write_report


def audit_v3(
    trajectories: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    stage2_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    def fail(code: str, trajectory_id: str, detail: str) -> None:
        issues.append({"code": code, "trajectory_id": trajectory_id, "detail": detail})

    trajectories_by_id = {row["trajectory_id"]: row for row in trajectories}
    sessions_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    checkpoints_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sessions:
        sessions_by_id[row["trajectory_id"]].append(row)
    for row in checkpoints:
        checkpoints_by_id[row["trajectory_id"]].append(row)

    semantic_counts: dict[str, int] = defaultdict(int)
    multi_property_trajectories = 0
    max_properties_listed = 0
    for trajectory_id, trajectory in trajectories_by_id.items():
        instances = trajectory["life_event_instances"]
        occurred = [row for row in instances if row.get("occurred_month") is not None]
        if len(occurred) != 20:
            fail("occurred_target", trajectory_id, f"expected 20, found {len(occurred)}")

        arrivals = sorted(
            row["occurred_month"]
            for row in occurred
            if row["event_id"] in {"relationship_childbirth", "relationship_adoption"}
        )
        for left, right in zip(arrivals, arrivals[1:]):
            if right - left < 12:
                fail("child_arrival_gap", trajectory_id, f"occurred months {left} and {right}")

        known_properties: dict[str, str] = {
            prop["property_id"]: prop["address"]
            for prop in trajectory["initial_persona_state"]["life_state"].get("properties", [])
        }
        owned_property_ids = set(known_properties)
        known_children = {
            child["child_id"]
            for child in trajectory["initial_persona_state"]["life_state"].get("children", [])
        }
        purchase_ids: set[str] = set()
        for instance in sorted(occurred, key=lambda row: (row["occurred_month"], row.get("occurred_transition_order") or 0)):
            event_id = instance["event_id"]
            params = instance.get("params") or {}
            if instance.get("generation_source") == "forced":
                if not instance.get("causal_bundle_id") or instance.get("bundle_event_index") is None:
                    fail("subgraph_metadata", trajectory_id, instance["event_instance_id"])
            if event_id in {"relationship_childbirth", "relationship_adoption"}:
                child_id = params.get("child_id")
                if not child_id or child_id in known_children:
                    fail("child_arrival_identity", trajectory_id, instance["event_instance_id"])
                else:
                    known_children.add(child_id)
            elif event_id == "career_reinstatement":
                previous = params.get("previous_employer")
                if not previous or previous == "previous_employer" or params.get("new_employer") != previous:
                    fail("reinstatement_employer", trajectory_id, instance["event_instance_id"])
            elif event_id == "relationship_family_death" and params.get("deceased_relation") == "child":
                child_id = params.get("deceased_child_id")
                if child_id not in known_children:
                    fail("child_death_identity", trajectory_id, instance["event_instance_id"])
                else:
                    known_children.remove(child_id)
            elif event_id == "education_child_stage_entry":
                required = {"child_id", "child_age_months", "previous_stage", "new_stage"}
                missing = sorted(required - set(params))
                if missing:
                    fail("child_education_identity", trajectory_id, f"{instance['event_instance_id']}: {missing}")
                elif params.get("child_id") not in known_children:
                    fail("child_education_unknown_child", trajectory_id, instance["event_instance_id"])
            elif event_id == "housing_move":
                if params.get("new_residence_status") != "wolse" and params.get("new_payee") is not None:
                    fail("non_rent_payee", trajectory_id, instance["event_instance_id"])
            elif event_id == "housing_home_purchase":
                property_id = params.get("property_id")
                address = params.get("property_address")
                if not property_id or not address or property_id in known_properties:
                    fail("property_purchase_identity", trajectory_id, instance["event_instance_id"])
                else:
                    known_properties[property_id] = address
                    owned_property_ids.add(property_id)
                    purchase_ids.add(property_id)
            elif event_id == "housing_home_sale":
                property_id = params.get("sold_property_id")
                if property_id not in owned_property_ids:
                    fail("property_sale_identity", trajectory_id, instance["event_instance_id"])
                elif params.get("sold_property_address") != known_properties[property_id]:
                    fail("property_sale_address", trajectory_id, instance["event_instance_id"])
                else:
                    owned_property_ids.remove(property_id)

        final_properties = trajectory["final_persona_state"]["life_state"].get("properties", [])
        final_ids = {prop["property_id"] for prop in final_properties}
        max_properties_listed = max(max_properties_listed, len(final_properties))
        if len(final_properties) > 1:
            multi_property_trajectories += 1
        if purchase_ids - final_ids:
            fail("property_inventory", trajectory_id, f"missing {sorted(purchase_ids - final_ids)}")

        for step in trajectory.get("timeline_steps", []):
            orders = [transition.get("transition_order", 0) for transition in step.get("transitions", [])]
            if orders != list(range(1, len(orders) + 1)):
                fail("transition_order", trajectory_id, f"month {step['month_index']}: {orders}")

        trajectory_sessions = sorted(sessions_by_id.get(trajectory_id, []), key=lambda row: row["session_id"])
        if len(trajectory_sessions) != 300:
            fail("session_count", trajectory_id, f"expected 300, found {len(trajectory_sessions)}")
        windows: dict[int, list[dict[str, Any]]] = defaultdict(list)
        instance_windows: dict[str, set[int]] = defaultdict(set)
        for session in trajectory_sessions:
            windows[int(session.get("window_index") or 0)].append(session)
            if session.get("linked_event_instance_id"):
                instance_windows[session["linked_event_instance_id"]].add(int(session.get("window_index") or 0))
        if sorted(windows) != list(range(1, 21)):
            fail("window_indices", trajectory_id, str(sorted(windows)))
        window_anchors: set[str] = set()
        for window_index, window in windows.items():
            if len(window) != 15:
                fail("window_size", trajectory_id, f"window {window_index}: {len(window)}")
            anchors = {row.get("window_event_instance_id") for row in window}
            occurred_evidence = [
                row for row in window
                if row.get("session_type") == "occurred_evidence"
                and row.get("event_status_after_session") == "occurred"
            ]
            if len(anchors) != 1 or None in anchors:
                fail("window_anchor", trajectory_id, f"window {window_index}: {sorted(str(x) for x in anchors)}")
            elif len(occurred_evidence) != 1 or occurred_evidence[0].get("linked_event_instance_id") not in anchors:
                fail("window_occurred_quota", trajectory_id, f"window {window_index}: {len(occurred_evidence)}")
            else:
                window_anchors.update(anchors)
        split = sorted(instance_id for instance_id, indexes in instance_windows.items() if len(indexes) != 1)
        if split:
            fail("split_event_bundle", trajectory_id, str(split))
        occurred_ids = {row["event_instance_id"] for row in occurred}
        if window_anchors != occurred_ids:
            fail("window_anchor_coverage", trajectory_id, "anchors differ from occurred instances")

        trajectory_checkpoints = sorted(
            checkpoints_by_id.get(trajectory_id, []),
            key=lambda row: row.get("checkpoint_session_count", 0),
        )
        if len(trajectory_checkpoints) != 20:
            fail("checkpoint_count", trajectory_id, f"expected 20, found {len(trajectory_checkpoints)}")
        for index, checkpoint in enumerate(trajectory_checkpoints, start=1):
            if checkpoint.get("checkpoint_session_count") != index * 15:
                fail("checkpoint_stride", trajectory_id, f"checkpoint {index}")
            if checkpoint.get("occurred_event_count") != index:
                fail("checkpoint_occurred_count", trajectory_id, f"checkpoint {index}")

        semantic_counts["occurred"] += len(occurred)
        semantic_counts["cancelled"] += sum(row.get("status") == "cancelled" for row in instances)
        semantic_counts["open_weak_or_upcoming"] += sum(
            row.get("status") in {"weak_signal", "upcoming"} for row in instances
        )

    true_initial_mismatches = 0
    for item in stage2_items or []:
        trajectory = trajectories_by_id.get(item.get("trajectory_id"))
        if trajectory is None:
            true_initial_mismatches += 1
            continue
        expected = serialize_memory_state(trajectory["initial_financial_memory_state"])
        for path, cell in ((item.get("metadata") or {}).get("initial_memory") or {}).items():
            if cell != expected.get(path):
                true_initial_mismatches += 1

    return {
        "passed": not issues,
        "trajectory_count": len(trajectories),
        "session_count": len(sessions),
        "checkpoint_count": len(checkpoints),
        "window_size": 15,
        "target_occurred_events_per_trajectory": 20,
        "status_totals": dict(semantic_counts),
        "multi_property_trajectories": multi_property_trajectories,
        "max_properties_listed_in_one_trajectory": max_properties_listed,
        "issue_count": len(issues),
        "stage2_true_initial_memory_mismatches": true_initial_mismatches,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--checkpoints", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-version", default="v3")
    parser.add_argument("--stage2-items", default=None)
    args = parser.parse_args()

    trajectories = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(Path(args.trajectories_dir).glob("traj_*.json"))]
    sessions = [row for path in sorted(Path(args.sessions_dir).glob("sessions_traj_*.jsonl")) for row in read_jsonl(path)]
    checkpoints = list(read_jsonl(Path(args.checkpoints)))
    stage2_items = list(read_jsonl(Path(args.stage2_items))) if args.stage2_items else None
    report = audit_v3(trajectories, sessions, checkpoints, stage2_items)
    if report["stage2_true_initial_memory_mismatches"]:
        report["passed"] = False
        report["issue_count"] += report["stage2_true_initial_memory_mismatches"]
    output_dir = Path(args.output_dir)
    write_report(
        report,
        output_dir / f"{args.run_version}_controlled_audit.json",
        f"{args.run_version.upper()} Controlled Run Audit",
        output_dir / f"{args.run_version}_controlled_audit.md",
    )
    print(json.dumps({key: report[key] for key in ("passed", "trajectory_count", "session_count", "checkpoint_count", "issue_count")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
