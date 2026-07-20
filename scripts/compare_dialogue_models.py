#!/usr/bin/env python
"""Compare model results pairwise on identical dialogue plan IDs."""

from __future__ import annotations

import argparse
import json
from datetime import date
from itertools import combinations
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import RepoPaths, load_yaml, read_jsonl, write_jsonl


def _valid_price(model: str, provider: str, pricing: list[dict]) -> dict | None:
    today = date.today()
    for item in pricing:
        if item.get("model") != model or item.get("provider") != provider:
            continue
        start = date.fromisoformat(item["effective_from"])
        end = date.fromisoformat(item["effective_until"]) if item.get("effective_until") else None
        if start <= today and (end is None or today <= end):
            return item
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    root = Path(args.result_root)
    models = {}
    paired_failures = []
    review = []
    pricing = load_yaml(RepoPaths.default().generation / "model_pricing.yaml").get("pricing", [])
    id_sets = {}
    for model_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        manifest_path = model_dir / "generation_manifest.json"
        audit_path = model_dir / "audit/dialogue_generation_audit.json"
        if not manifest_path.exists() or not audit_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        sessions = [record for path in (model_dir / "sessions").glob("sessions_*.jsonl") for record in read_jsonl(path)]
        frozen_plans_dir = Path(manifest["plans_directory"])
        frozen_plans = [record for path in frozen_plans_dir.glob("plans_*.jsonl") for record in read_jsonl(path)]
        id_sets[model_dir.name] = {item["session_id"] for item in frozen_plans}
        efficiency = audit["efficiency"]
        price = _valid_price(manifest["model"], manifest["provider"], pricing)
        estimated_cost = None
        if price:
            uncached = max(0, efficiency["input_tokens"] - efficiency["cached_tokens"])
            estimated_cost = (
                uncached * price["input_per_million"]
                + efficiency["cached_tokens"] * (price.get("cached_input_per_million") or price["input_per_million"])
                + efficiency["output_tokens"] * price["output_per_million"]
            ) / 1_000_000
        models[model_dir.name] = {
            "provider": manifest["provider"], "model": manifest["model"],
            "summary": audit["summary"], "violation_counts": audit["violation_counts"],
            "quality": audit["quality"], "efficiency": efficiency,
            "estimated_cost": estimated_cost,
        }
        failed = {item["session_id"] for item in audit.get("violations", [])} | set(audit.get("missing_session_ids", []))
        for session_id in sorted(failed):
            paired_failures.append({"model_profile": model_dir.name, "session_id": session_id})
        review.extend({"model_profile": model_dir.name, "session_id": item["session_id"], "turns": item.get("turns")} for item in sessions[:5])
    pairwise = []
    for left, right in combinations(sorted(id_sets), 2):
        if id_sets[left] != id_sets[right]:
            raise SystemExit(f"model comparison requires identical plan IDs: {left} != {right}")
        pairwise.append({"left": left, "right": right, "paired_plan_count": len(id_sets[left])})
    report = {"models": models, "pairwise": pairwise, "ranking_policy": ["zero safety/grounding failures", "unrecovered failure rate", "plan-fidelity violations", "manual Korean naturalness", "repair rate", "cost", "latency"]}
    output_dir = Path(args.output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "model_comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Dialogue model comparison", "", "No winner is selected automatically.", ""]
    for name, item in models.items():
        lines.extend([f"## {name}", "", f"- model: {item['model']}", f"- success rate: {item['summary']['success_rate']}", f"- repair rate: {item['summary']['repair_session_rate']}", f"- estimated cost: {item['estimated_cost'] if item['estimated_cost'] is not None else 'unknown'}", ""])
    (output_dir / "model_comparison.md").write_text("\n".join(lines), encoding="utf-8")
    write_jsonl(output_dir / "paired_failures.jsonl", paired_failures)
    write_jsonl(output_dir / "manual_review_sample.jsonl", review)
    print(f"compared {len(models)} model profiles -> {output_dir}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
