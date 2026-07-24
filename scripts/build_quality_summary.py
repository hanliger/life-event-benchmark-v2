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


def _jsonl_files(directory: Path) -> list[Path]:
    trajectory_files = sorted(directory.glob("traj_*.jsonl"))
    if trajectory_files:
        return trajectory_files
    return sorted(directory.glob("sessions_*.jsonl"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
    for path in traj_files:
        trajectory = json.loads(path.read_text(encoding="utf-8"))
        for instance in trajectory["life_event_instances"]:
            by_label[instance["label_ko"]] += 1
            by_status[instance["status"]] += 1
        for step in trajectory["timeline_steps"]:
            for update in step["memory_updates"]:
                by_operation[update["operation"]] += 1
            for impact in step["action_impacts"]:
                by_decision[impact["expected_decision"]] += 1
                by_risk[impact["risk"]] += 1
                if (
                    impact["risk"] == "high"
                    and impact["funds_movement"]
                    and impact["expected_decision"]
                    not in {"ask_confirmation", "reject", "pause"}
                    and not impact["must_not_execute"]
                ):
                    high_risk_wo_confirmation += 1

    n_sessions = sum(
        1
        for path in _jsonl_files(Path(args.sessions_dir))
        for _ in read_jsonl(path)
    )

    lines = [
        "# Trajectory Quality Report",
        "",
        f"- trajectories: {len(traj_files)}",
        f"- sessions: {n_sessions}",
        f"- high-risk decisions without confirmation in gold (**must be 0**): {high_risk_wo_confirmation}",
        "",
        "## Event instances by label",
        *(f"- {key}: {value}" for key, value in by_label.most_common()),
        "",
        "## Event instances by final status",
        *(f"- {key}: {value}" for key, value in by_status.most_common()),
        "",
        "## Memory updates by operation",
        *(f"- {key}: {value}" for key, value in by_operation.most_common()),
        "",
        "## Action impacts by expected decision",
        *(f"- {key}: {value}" for key, value in by_decision.most_common()),
        "",
        "## Action impacts by risk",
        *(f"- {key}: {value}" for key, value in by_risk.most_common()),
    ]
    (out / "trajectory_quality_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    # ---------------- benchmark item report ----------------
    stage_counts: dict[str, int] = {}
    mcq_error_types: Counter = Counter()
    mcq_with_stale = 0
    target_event_counts: Counter = Counter()
    target_path_counts: Counter = Counter()
    canonical_target_counts: Counter = Counter()
    mcq_answer_pos: Counter = Counter()
    option_counts: Counter = Counter()

    for path in sorted(Path(args.items_dir).glob("stage*.jsonl")):
        items = list(read_jsonl(path))
        stage_counts[path.name] = len(items)
        if "mcq" not in path.name or path.name.endswith(".filtered.jsonl"):
            continue
        for item in items:
            metadata = item.get("metadata") or {}
            target_event_counts[
                metadata.get("target_event_id")
                or metadata.get("target_event_label")
                or "?"
            ] += 1
            target_path_counts[metadata.get("memory_path") or "?"] += 1
            canonical_target_counts[metadata.get("canonical_target_id") or "?"] += 1
            options = item.get("options") or []
            option_counts[len(options)] += 1
            correct_option = (item.get("gold") or {}).get("correct_option", "?")
            mcq_answer_pos[correct_option] += 1
            if any(
                option.get("error_type") == "stale_memory_carryover"
                for option in options
            ):
                mcq_with_stale += 1
            for option in options:
                if option.get("error_type"):
                    mcq_error_types[option["error_type"]] += 1

    prefixes = list(read_jsonl(Path(args.prefix_gold)))
    reused_targets = sum(
        count for target, count in canonical_target_counts.items()
        if target != "?" and count > 1
    )
    distinct_targets = sum(
        1 for target in canonical_target_counts if target != "?"
    )

    lines = [
        "# Benchmark Item Report",
        "",
        f"- prefix gold records: {len(prefixes)}",
        "",
        "## Items per stage file",
        *(f"- {key}: {value}" for key, value in stage_counts.items()),
        "",
        "## Stage 2 target event distribution",
        *(f"- {key}: {value}" for key, value in target_event_counts.most_common()),
        "",
        "## Stage 2 memory path distribution",
        *(f"- {key}: {value}" for key, value in target_path_counts.most_common()),
        "",
        "## Stage 2 canonical target reuse",
        f"- distinct canonical targets: {distinct_targets}",
        f"- reused canonical target occurrences: {reused_targets}",
        "",
        "## Stage 2 option count",
        *(f"- {key}: {value}" for key, value in sorted(option_counts.items())),
        "",
        "## Stage 2 correct-option position (should be spread across A-D)",
        *(f"- {key}: {value}" for key, value in sorted(mcq_answer_pos.items())),
        "",
        "## Stage 2 distractor error types",
        *(f"- {key}: {value}" for key, value in mcq_error_types.most_common()),
        "",
        f"- Items with stale-memory distractor: {mcq_with_stale}",
        "",
        "Constraint check: high-risk decisions without confirmation in gold "
        f"= {high_risk_wo_confirmation} (must be 0)",
    ]
    (out / "benchmark_item_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print(
        f"reports -> {out / 'trajectory_quality_report.md'}, "
        f"{out / 'benchmark_item_report.md'}"
    )
    if high_risk_wo_confirmation:
        print("ERROR: high-risk gold decisions without confirmation found")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
