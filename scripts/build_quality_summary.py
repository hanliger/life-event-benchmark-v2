#!/usr/bin/env python
"""Aggregate metrics into trajectory_quality_report.md and
benchmark_item_report.md.

Example:
  python scripts/build_quality_summary.py \
    --trajectories-dir data/generated/trajectories \
    --sessions-dir data/generated/sessions \
    --prefix-gold data/generated/gold/prefix_gold.jsonl \
    --items-dir data/generated/benchmark_items \
    --output-dir data/generated/quality_reports
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import read_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--prefix-gold", required=True)
    parser.add_argument("--items-dir", required=True)
    parser.add_argument("--output-dir", default="data/generated/quality_reports")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ---------------- trajectory report ----------------
    traj_files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    by_label: Counter = Counter()
    by_status: Counter = Counter()
    by_operation: Counter = Counter()
    by_decision: Counter = Counter()
    by_risk: Counter = Counter()
    high_risk_wo_confirmation = 0
    for f in traj_files:
        traj = json.loads(f.read_text(encoding="utf-8"))
        for instance in traj["life_event_instances"]:
            by_label[instance["label_ko"]] += 1
            by_status[instance["status"]] += 1
        for step in traj["timeline_steps"]:
            for u in step["memory_updates"]:
                by_operation[u["operation"]] += 1
            for i in step["action_impacts"]:
                by_decision[i["expected_decision"]] += 1
                by_risk[i["risk"]] += 1
                if i["risk"] == "high" and i["funds_movement"] and i["expected_decision"] not in {
                    "ask_confirmation", "reject", "pause",
                } and not i["must_not_execute"]:
                    high_risk_wo_confirmation += 1

    # sessions per trajectory
    n_sessions = sum(1 for p in sorted(Path(args.sessions_dir).glob("sessions_*.jsonl")) for _ in read_jsonl(p))

    lines = [
        "# Trajectory Quality Report",
        "",
        f"- trajectories: {len(traj_files)}",
        f"- sessions: {n_sessions}",
        f"- high-risk decisions without confirmation in gold (**must be 0**): {high_risk_wo_confirmation}",
        "",
        "## Event instances by label",
        *(f"- {k}: {v}" for k, v in by_label.most_common()),
        "",
        "## Event instances by final status",
        *(f"- {k}: {v}" for k, v in by_status.most_common()),
        "",
        "## Memory updates by operation",
        *(f"- {k}: {v}" for k, v in by_operation.most_common()),
        "",
        "## Action impacts by expected decision",
        *(f"- {k}: {v}" for k, v in by_decision.most_common()),
        "",
        "## Action impacts by risk",
        *(f"- {k}: {v}" for k, v in by_risk.most_common()),
    ]
    (out / "trajectory_quality_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------------- benchmark item report ----------------
    stage_counts = {}
    mcq_error_types: Counter = Counter()
    mcq_with_stale = 0
    mcq_hop_type: Counter = Counter()
    mcq_answer_pos: Counter = Counter()
    for path in sorted(Path(args.items_dir).glob("stage*.jsonl")):
        items = list(read_jsonl(path))
        stage_counts[path.name] = len(items)
        if "mcq" in path.name and not path.name.endswith(".filtered.jsonl"):
            for item in items:
                meta = item.get("metadata") or {}
                if meta.get("has_stale_distractor"):
                    mcq_with_stale += 1
                mcq_hop_type[meta.get("hop_type") or meta.get("context", "?")] += 1
                gold = item.get("gold") or {}
                mcq_answer_pos[gold.get("correct_option", "?")] += 1
                for opt in item.get("options", []):
                    if opt.get("error_type"):
                        mcq_error_types[opt["error_type"]] += 1
                        if opt["error_type"] == "stale_memory_carryover":
                            mcq_with_stale += 1

    prefixes = list(read_jsonl(Path(args.prefix_gold)))
    lines = [
        "# Benchmark Item Report",
        "",
        f"- prefix gold records: {len(prefixes)}",
        "",
        "## Items per stage file",
        *(f"- {k}: {v}" for k, v in stage_counts.items()),
        "",
        "## MCQ hop/context distribution",
        *(f"- {k}: {v}" for k, v in mcq_hop_type.most_common()),
        "",
        "## MCQ correct-option position (should be spread across A–E)",
        *(f"- {k}: {v}" for k, v in sorted(mcq_answer_pos.items())),
        "",
        "## MCQ distractor error types",
        *(f"- {k}: {v}" for k, v in mcq_error_types.most_common()),
        "",
        f"- MCQ stale-memory distractor occurrences: {mcq_with_stale}",
        "",
        "Constraint check: high-risk decisions without confirmation in gold "
        f"= {high_risk_wo_confirmation} (must be 0)",
    ]
    (out / "benchmark_item_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"reports -> {out}/trajectory_quality_report.md, {out}/benchmark_item_report.md")
    if high_risk_wo_confirmation:
        print("ERROR: high-risk gold decisions without confirmation found")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
