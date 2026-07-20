#!/usr/bin/env python
"""Apply configured hard gates to exactly one 300-session dialogue canary."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.check_dialogue_canary in tests
    from scripts import _bootstrap  # type: ignore # noqa: F401

from fin_life_benchmark.io import RepoPaths, load_yaml, read_jsonl


def evaluate_canary(audit: dict, plans: list[dict], gates: dict, warnings_cfg: dict) -> dict:
    summary = audit["summary"]
    violations = Counter(audit.get("violation_counts") or {})
    quality = audit.get("quality") or {}
    planned = max(1, int(summary.get("planned_session_count") or 0))
    failure_ids = set(audit.get("missing_session_ids") or [])
    failure_ids.update(
        str(record["session_id"])
        for record in (audit.get("error_records") or [])
        if record.get("session_id")
    )
    unrecovered_failures = (
        len(failure_ids)
        if "missing_session_ids" in audit or "error_records" in audit
        else max(
            int(summary.get("missing_session_count") or 0),
            int(summary.get("sessions_failing_after_repairs") or 0),
        )
    )
    actual = {
        "final_success_rate_min": float(summary.get("success_rate") or 0),
        "unrecovered_failure_count_max": unrecovered_failures,
        "repair_session_rate_max": float(summary.get("repair_session_rate") or 0),
        "event_label_leakage_max": violations["event_label_leakage"],
        "internal_metadata_leakage_max": violations["internal_metadata_leakage"] + violations["fa_code_leakage"],
        "unsafe_high_risk_execution_max": violations["high_risk_auto_execution"],
        "memory_fact_mismatch_max": sum(violations[key] for key in ("missing_memory_fact_grounding", "memory_fact_path_mismatch", "memory_fact_operation_mismatch", "memory_fact_value_mismatch")),
        "cancelled_commit_max": violations["cancelled_value_committed"],
        "weak_signal_overcommit_max": violations["weak_signal_overcommitted"],
        "hard_negative_update_max": violations["hard_negative_unintended_update"],
        "missing_required_cue_rate_max": (violations["missing_required_cue"] + violations["required_cue_not_in_user_turn"]) / planned,
        "repeated_utterance_session_rate_max": float(quality.get("repeated_utterance_session_rate") or 0),
        "near_duplicate_session_rate_max": float(quality.get("near_duplicate_session_rate") or 0),
        "offline_banking_violation_max": violations["offline_banking_context"],
        "turn_contract_violation_max": (
            violations["turn_contract_violation"]
            + violations["user_turn_contract_violation"]
        ),
        "opening_evidence_coupling_max": violations[
            "opening_evidence_not_coupled"
        ],
    }
    failures = []
    for key, threshold in gates.items():
        value = actual.get(key, 0)
        passed = value >= threshold if key.endswith("_min") else value <= threshold
        if not passed:
            failures.append({"gate": key, "actual": value, "threshold": threshold})
    warnings = []
    warning_rate = float(warnings_cfg.get("repair_session_rate", 1))
    if actual["repair_session_rate_max"] > warning_rate:
        warnings.append({"warning": "repair_session_rate", "actual": actual["repair_session_rate_max"], "threshold": warning_rate})
    if warnings_cfg.get("filler_same_month_density"):
        filler_counts = Counter(plan["month_index"] for plan in plans if plan.get("filler_allowed_month_range") is not None)
        if filler_counts and max(filler_counts.values()) > 5:
            warnings.append({"warning": "filler_same_month_density", "max": max(filler_counts.values())})
    if warnings_cfg.get("task_template_overconcentration"):
        task_counts = Counter(plan.get("task_template_id") for plan in plans if plan.get("task_template_id"))
        if task_counts and max(task_counts.values()) / planned > 0.20:
            warnings.append({"warning": "task_template_overconcentration", "rate": max(task_counts.values()) / planned})
    decision = "FAIL" if failures else "REVIEW_REQUIRED" if warnings else "PASS"
    return {"decision": decision, "hard_gate_failures": failures, "warnings": warnings, "gate_actuals": actual}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectory-id", required=True)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    plan_path = Path(args.plans_dir) / f"plans_{args.trajectory_id}.jsonl"
    plans = list(read_jsonl(plan_path))
    if len(plans) != 300 or {plan["trajectory_id"] for plan in plans} != {args.trajectory_id}:
        raise SystemExit("canary check requires exactly one trajectory's 300 plans")
    sessions_path = Path(args.sessions_dir) / f"sessions_{args.trajectory_id}.jsonl"
    sessions = list(read_jsonl(sessions_path)) if sessions_path.exists() else []
    manifest_path = Path(args.sessions_dir).parent / "generation_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"missing generation manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("trajectory_ids") != [args.trajectory_id]:
        raise SystemExit("canary manifest must contain exactly the requested trajectory")
    audit_path = Path(args.audit_dir) / "dialogue_generation_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    cfg = load_yaml(RepoPaths.default().generation / "dialogue.yaml")["canary"]
    decision = evaluate_canary(audit, plans, cfg["gates"], cfg.get("warnings", {}))
    if len(sessions) != int(cfg.get("required_sessions", 300)):
        decision["hard_gate_failures"].append({"gate": "required_sessions", "actual": len(sessions), "threshold": cfg.get("required_sessions", 300)})
        decision["decision"] = "FAIL"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    combined = {"trajectory_id": args.trajectory_id, "manifest": manifest, "decision": decision, "generation_audit": audit}
    (output_dir / "canary_audit.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "canary_decision.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Dialogue canary decision", "", f"- decision: **{decision['decision']}**", f"- trajectory: {args.trajectory_id}", f"- sessions: {len(sessions)}", "", "## Hard gate failures", ""]
    lines.extend(f"- {item}" for item in decision["hard_gate_failures"] or ["none"])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in decision["warnings"] or ["none"])
    (output_dir / "canary_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"canary decision: {decision['decision']}")
    return {"PASS": 0, "FAIL": 1, "REVIEW_REQUIRED": 2}[decision["decision"]]


if __name__ == "__main__":
    raise SystemExit(main())
