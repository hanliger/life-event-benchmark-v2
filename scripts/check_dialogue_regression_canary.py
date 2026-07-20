#!/usr/bin/env python
"""Apply semantic/safety gates to a regression micro-canary audit."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


ZERO_CODES = (
    "direct_event_disclosure",
    "high_risk_missing_required_slot",
    "high_risk_false_completion",
    "high_risk_missing_confirmation",
    "high_risk_unplanned_slot_value",
    "high_risk_action_resolution_mismatch",
    "insufficient_event_evidence",
    "subtype_not_disambiguated",
    "stale_old_current_confusion",
    "unsupported_bank_policy_claim",
    "bank_policy_contradiction",
    "duplicate_opening_over_limit",
    "lifecycle_exact_phrase_overconcentration",
    "lifecycle_phrase_family_overconcentration",
    "evidence_placement_overconcentration",
    "event_strategy_overconcentration",
    "hard_negative_template_overconcentration",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    audit = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    counts = Counter(audit.get("violation_counts") or {})
    summary = audit.get("summary") or {}
    failures = [
        {"gate": code, "actual": counts[code], "threshold": 0}
        for code in ZERO_CODES
        if counts[code]
    ]
    if int(summary.get("generation_failure_count") or 0):
        failures.append({"gate": "generation_failure_count", "actual": summary["generation_failure_count"], "threshold": 0})
    unrecovered = max(
        int(summary.get("missing_session_count") or 0),
        int(summary.get("sessions_failing_after_repairs") or 0),
    )
    if unrecovered:
        failures.append(
            {"gate": "unrecovered_failure_count", "actual": unrecovered, "threshold": 0}
        )
    success_rate = float(summary.get("success_rate") or 0)
    if success_rate < 1.0:
        failures.append(
            {"gate": "final_success_rate", "actual": success_rate, "threshold": 1.0}
        )
    repair_rate = float(summary.get("repair_session_rate") or 0)
    if repair_rate > 0.10:
        failures.append({"gate": "repair_session_rate", "actual": repair_rate, "threshold": 0.10})
    decision = {
        "decision": "FAIL" if failures else "PASS",
        "hard_gate_failures": failures,
        "repair_session_rate": repair_rate,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "regression_canary_decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"regression canary decision: {decision['decision']}")
    return 0 if decision["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
