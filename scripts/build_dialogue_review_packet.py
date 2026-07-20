#!/usr/bin/env python
"""Build an evaluator-only human review packet for one canary trajectory."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import read_jsonl, write_jsonl

HIGH_RISK = {"FA-07", "FA-08", "FA-09", "FA-10"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectory-id", required=True)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--target", type=int, default=40)
    args = parser.parse_args()
    plans = {item["session_id"]: item for item in read_jsonl(Path(args.plans_dir) / f"plans_{args.trajectory_id}.jsonl")}
    sessions = {item["session_id"]: item for item in read_jsonl(Path(args.sessions_dir) / f"sessions_{args.trajectory_id}.jsonl")}
    audit = json.loads((Path(args.audit_dir) / "dialogue_generation_audit.json").read_text(encoding="utf-8"))
    violations: dict[str, list[dict]] = defaultdict(list)
    for item in audit.get("violations", []):
        violations[item["session_id"]].append(item)
    rng = random.Random(args.seed)
    selected: list[str] = []

    def add(candidates, limit=None):
        values = [session_id for session_id in candidates if session_id in sessions and session_id not in selected]
        rng.shuffle(values)
        selected.extend(values if limit is None else values[:limit])

    add([sid for sid, session in sessions.items() if violations[sid] or (session.get("generation_metadata") or {}).get("repair_count")], None)
    add([sid for sid, plan in plans.items() if plan.get("session_type") == "cancellation_evidence"], 5)
    add([sid for sid, plan in plans.items() if plan.get("session_type") == "stale_recall_session"], 5)
    occurred_by_domain: dict[str, list[str]] = defaultdict(list)
    for sid, plan in plans.items():
        if plan.get("session_type") == "occurred_evidence":
            occurred_by_domain[((plan.get("structured_context") or {}).get("event") or {}).get("domain", "unknown")].append(sid)
    occurred = []
    for domain in sorted(occurred_by_domain):
        occurred.append(rng.choice(occurred_by_domain[domain]))
    occurred.extend(sid for sid, plan in plans.items() if plan.get("session_type") == "occurred_evidence")
    add(occurred, 8)
    add([sid for sid, plan in plans.items() if plan.get("session_type") in {"weak_signal_evidence", "upcoming_evidence"}], 5)
    add([sid for sid, plan in plans.items() if plan.get("session_type") == "hard_negative"], 5)
    add([sid for sid, plan in plans.items() if plan.get("mapped_action") in HIGH_RISK], 5)
    add([sid for sid, plan in plans.items() if plan.get("session_type") == "routine_financial"], max(0, args.target - len(selected)))

    records = []
    for sid in selected:
        plan, session = plans[sid], sessions[sid]
        metadata = session.get("generation_metadata") or {}
        event = (plan.get("structured_context") or {}).get("event") or {}
        records.append({
            "evaluator_only": {
                "trajectory_id": args.trajectory_id,
                "session_id": sid,
                "session_type": plan.get("session_type"),
                "lifecycle_status": plan.get("event_status_after_session"),
                "event_id": event.get("event_id"),
                "financial_task": plan.get("financial_task"),
                "planned_cues": plan.get("planned_cues") or [],
                "expected_memory_updates": (plan.get("structured_context") or {}).get("session_memory_updates") or [],
                "validator_results": violations[sid],
                "repair_count": metadata.get("repair_count", 0),
                "provider": metadata.get("provider"),
                "model": metadata.get("model"),
                "token_usage": metadata.get("usage") or {},
                "latency_ms": metadata.get("request_duration_ms"),
                "automatic_flags": {
                    "direct_disclosure_patterns": [
                        item
                        for item in violations[sid]
                        if item.get("code")
                        in {
                            "direct_event_disclosure",
                            "near_direct_event_disclosure",
                            "forbidden_event_paraphrase",
                        }
                    ],
                    "lifecycle_phrase_family": plan.get(
                        "lifecycle_surface_family"
                    ),
                    "evidence_dimensions_planned": [
                        item.get("dimension_id")
                        for item in plan.get("evidence_dimensions") or []
                    ],
                    "evidence_dimensions_realized": sorted(
                        {
                            item.get("evidence_dimension_id")
                            for item in session.get("cue_annotations") or []
                            if item.get("evidence_dimension_id")
                        }
                    ),
                    "evidence_dimensions_missing": sorted(
                        set(
                            item.get("dimension_id")
                            for item in plan.get("evidence_dimensions") or []
                            if item.get("required", True)
                        )
                        - {
                            item.get("evidence_dimension_id")
                            for item in session.get("cue_annotations") or []
                            if item.get("evidence_dimension_id")
                        }
                    ),
                    "evidence_dimension_violations": [
                        item
                        for item in violations[sid]
                        if item.get("code")
                        in {
                            "required_evidence_not_realized",
                            "insufficient_event_evidence",
                            "missing_required_evidence_role",
                            "subtype_not_disambiguated",
                        }
                    ],
                    "high_risk_contract": plan.get(
                        "action_execution_contract"
                    ),
                    "high_risk_slots": {
                        "required": (
                            plan.get("action_execution_contract") or {}
                        ).get("required_slots") or [],
                        "grounded": (
                            plan.get("action_execution_contract") or {}
                        ).get("grounded_slots") or {},
                        "plan_missing": (
                            plan.get("action_execution_contract") or {}
                        ).get("missing_slots") or [],
                        "provided": (session.get("action_resolution") or {}).get(
                            "provided_slots"
                        ) or {},
                        "resolution_missing": (
                            session.get("action_resolution") or {}
                        ).get("missing_slots") or [],
                    },
                    "action_resolution": session.get("action_resolution"),
                    "policy_violations": [
                        item
                        for item in violations[sid]
                        if item.get("code")
                        in {
                            "unsupported_bank_policy_claim",
                            "bank_policy_contradiction",
                        }
                    ],
                    "semantic_template_concentration_group": {
                        "placement_strategy": plan.get(
                            "evidence_placement_strategy"
                        ),
                        "lifecycle_surface_variant_id": plan.get(
                            "lifecycle_surface_variant_id"
                        ),
                        "hard_negative_surface_variant_id": plan.get(
                            "hard_negative_surface_variant_id"
                        ),
                    },
                },
            },
            "generated_dialogue": session.get("turns") or [],
            "cue_annotations": session.get("cue_annotations") or [],
            "reviewer": {
                "natural_korean_dialogue": None,
                "event_task_alignment": None,
                "lifecycle_calibration": None,
                "memory_grounding": None,
                "assistant_semantic_leakage": None,
                "high_risk_safety": None,
                "event_implicit_but_recoverable": None,
                "comments": "",
            },
        })
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "sampled_sessions.jsonl", records)
    index = {"trajectory_id": args.trajectory_id, "seed": args.seed, "count": len(records), "session_ids": selected}
    (output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Canary dialogue human-review packet", "", "> Evaluator-only metadata below must not be copied into benchmark-visible dialogue.", ""]
    for record in records:
        meta = record["evaluator_only"]
        lines.extend([
            f"## {meta['session_id']} — {meta['session_type']}", "",
            f"- evaluator event: `{meta['event_id']}`", f"- lifecycle: `{meta['lifecycle_status']}`",
            f"- task: {meta['financial_task']}", f"- repairs: {meta['repair_count']}", "", "### Dialogue", "",
        ])
        lines.extend(f"- **{turn['speaker']}**: {turn['text']}" for turn in record["generated_dialogue"])
        lines.extend(["", "### Reviewer fields", "", "- natural Korean dialogue: [ ] pass [ ] fail", "- event-task alignment: [ ] pass [ ] fail", "- lifecycle calibration: [ ] pass [ ] fail", "- memory grounding: [ ] pass [ ] fail", "- assistant semantic leakage: [ ] pass [ ] fail", "- high-risk safety: [ ] pass [ ] fail", "- event implicit but recoverable: [ ] pass [ ] fail", "- comments:", ""])
    (output_dir / "review_packet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"review packet: {len(records)} sessions -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
