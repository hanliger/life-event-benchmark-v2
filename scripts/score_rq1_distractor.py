#!/usr/bin/env python
"""Paired analysis of RQ1 distractor runs (full vs mask_distractor vs sham).

Consumes the three prediction files produced by evaluate_rq1.py over the
same cases.jsonl and reports, per paired case:

    distractor_cost       = score(mask_distractor) - score(full)
    replacement_artifact  = score(full) - score(sham)

plus near-miss hallucination, hard-negative evidence attribution, false
occurred rates, status/evidence deltas and non-target ledger invariance.
Uncertainty: paired bootstrap CIs clustered by trajectory and a clustered
sign-flip permutation p-value. Natural and counterfactual scores are never
combined into one headline number.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.benchmark.rq1_metrics import (
    clustered_bootstrap_ci,
    clustered_sign_flip_pvalue,
    item_metrics,
    summarize_grouped,
)
from fin_life_benchmark.benchmark.rq1_models import (
    RQ1GoldEventInstance,
    RQ1PredictedEvent,
)
from fin_life_benchmark.io.jsonl import read_jsonl

SCORE_KEYS = (
    "full_ledger_event_f1",
    "ordered_occurred_event_f1",
    "status_macro_f1",
    "core_evidence_precision",
)


def _load_predictions(path: Path) -> dict[str, dict[str, Any]]:
    rows = {}
    for row in read_jsonl(path):
        rows[row["item_id"]] = row
    return rows


def _case_metrics(case: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    gold = case["gold"]
    ledger = [
        RQ1GoldEventInstance.model_validate(e)
        for e in gold.get("full_observed_ledger", [])
    ]
    occurred = [
        RQ1GoldEventInstance.model_validate(e)
        for e in gold.get("occurred_trajectory", [])
    ]
    events = [
        RQ1PredictedEvent.model_validate(e)
        for e in (row.get("prediction") or {}).get("events", [])
    ]
    return item_metrics(ledger, occurred, events)


def _instance_outcomes(metrics: dict[str, Any]) -> dict[str, tuple[bool, str]]:
    return {
        rec["event_instance_id"]: (rec["matched"], rec["pred_status"])
        for rec in metrics["instance_records"]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--full", required=True, help="predictions for condition=full")
    parser.add_argument("--masked", required=True, help="predictions for condition=mask_distractor")
    parser.add_argument("--sham", required=True, help="predictions for condition=sham")
    parser.add_argument("--report", required=True)
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--n-perm", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    cases = {c["case_id"]: c for c in read_jsonl(Path(args.cases))}
    preds = {
        "full": _load_predictions(Path(args.full)),
        "mask_distractor": _load_predictions(Path(args.masked)),
        "sham": _load_predictions(Path(args.sham)),
    }
    paired_ids = sorted(
        cid
        for cid in cases
        if all(cid in preds[cond] for cond in preds)
    )
    if not paired_ids:
        raise SystemExit("no cases present in all three prediction files")

    cluster_of = {cid: cases[cid]["trajectory_id"] for cid in paired_ids}
    type_of = {cid: cases[cid].get("hard_negative_type", "") for cid in paired_ids}
    near_miss_of = {cid: cases[cid].get("near_miss_event_id", "") for cid in paired_ids}

    scores: dict[str, dict[str, dict[str, float | None]]] = {
        cond: {key: {} for key in SCORE_KEYS} for cond in preds
    }
    flags: dict[str, dict[str, dict[str, Any]]] = {cond: {} for cond in preds}
    outcomes: dict[str, dict[str, dict[str, tuple[bool, str]]]] = {
        cond: {} for cond in preds
    }
    for cid in paired_ids:
        case = cases[cid]
        for cond, rows in preds.items():
            metrics = _case_metrics(case, rows[cid])
            for key in SCORE_KEYS:
                scores[cond][key][cid] = metrics.get(key)
            flags[cond][cid] = rows[cid].get("distractor") or {}
            outcomes[cond][cid] = _instance_outcomes(metrics)

    report: dict[str, Any] = {
        "n_paired_cases": len(paired_ids),
        "n_trajectories": len(set(cluster_of.values())),
        "condition_means": {
            cond: {
                key: (
                    sum(v for v in vals.values() if v is not None)
                    / max(1, sum(1 for v in vals.values() if v is not None))
                    if any(v is not None for v in vals.values())
                    else None
                )
                for key, vals in per_key.items()
            }
            for cond, per_key in scores.items()
        },
    }

    def paired_block(key: str, cond_a: str, cond_b: str) -> dict[str, Any]:
        diffs = {
            cid: scores[cond_a][key][cid] - scores[cond_b][key][cid]
            for cid in paired_ids
            if scores[cond_a][key][cid] is not None
            and scores[cond_b][key][cid] is not None
        }
        return {
            **clustered_bootstrap_ci(
                diffs, cluster_of, n_boot=args.n_boot, seed=args.seed
            ),
            "permutation_p_value": clustered_sign_flip_pvalue(
                diffs, cluster_of, n_perm=args.n_perm, seed=args.seed
            ),
            "by_trajectory": summarize_grouped(diffs, cluster_of),
            "by_hard_negative_type": summarize_grouped(diffs, type_of),
            "by_near_miss_event_id": summarize_grouped(diffs, near_miss_of),
        }

    report["distractor_cost"] = paired_block(
        "full_ledger_event_f1", "mask_distractor", "full"
    )
    report["replacement_artifact"] = paired_block(
        "full_ledger_event_f1", "full", "sham"
    )
    report["status_macro_f1_change"] = paired_block(
        "status_macro_f1", "mask_distractor", "full"
    )
    report["core_evidence_precision_change"] = paired_block(
        "core_evidence_precision", "mask_distractor", "full"
    )

    for cond in preds:
        rows = flags[cond]
        n = len(rows)
        report.setdefault("rates", {})[cond] = {
            "near_miss_event_hallucination_rate": (
                sum(1 for f in rows.values() if f.get("near_miss_hallucinated")) / n
            ),
            "hard_negative_evidence_attribution_rate": (
                sum(1 for f in rows.values() if f.get("hard_negative_cited")) / n
            ),
            "false_occurred_rate": (
                sum(1 for f in rows.values() if (f.get("false_occurred_count") or 0) > 0)
                / n
            ),
            "false_occurred_mean_count": (
                sum(f.get("false_occurred_count") or 0 for f in rows.values()) / n
            ),
        }

    # non-target ledger invariance: gold instances are all non-target (the
    # hard negative is not an event); measure prediction agreement per case
    # between full and mask_distractor.
    agreements = []
    for cid in paired_ids:
        full_out = outcomes["full"][cid]
        masked_out = outcomes["mask_distractor"][cid]
        keys = set(full_out) | set(masked_out)
        if not keys:
            continue
        agree = sum(
            1 for k in keys if full_out.get(k) == masked_out.get(k)
        )
        agreements.append(agree / len(keys))
    report["non_target_ledger_invariance"] = (
        sum(agreements) / len(agreements) if agreements else None
    )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    cost = report["distractor_cost"]
    print(
        f"rq1 distractor: {len(paired_ids)} paired cases; "
        f"distractor_cost mean={cost['mean']:.4f} "
        f"CI=[{cost['ci_low']:.4f},{cost['ci_high']:.4f}] "
        f"p={cost['permutation_p_value']:.4f}"
    )
    print(f"report -> {report_path}")


if __name__ == "__main__":
    main()
