#!/usr/bin/env python
"""Join the three condition predictions into the paired retraction report.

Separating the join from the calls is what makes the single reused ``full``
prediction and per-condition resumability work: each condition is evaluated (and
re-run) on its own, then scored together here.

    python scripts/score_rq1_pair_counterfactual.py \
      --cases <cases.jsonl> \
      --full <full.jsonl> --mask-terminal <mask_terminal.jsonl> \
      --mask-all <mask_all.jsonl> \
      --report <reports/<provider>__<model>.json>
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

from fin_life_benchmark.benchmark.rq1_pair_counterfactual import (
    CANARY_PROTOCOL_VERSION,
    MASKED_CONDITIONS,
    aggregate_paired_cases,
    paired_case_metrics,
)
from fin_life_benchmark.benchmark.rq1_pair_models import (
    RQ1_PAIR_METRICS_VERSION,
    RQ1_PAIR_PROTOCOL_VERSION,
    RQ1_PAIR_STAGE,
    RQ1PairPrediction,
    RQ1PredictedPair,
)
from fin_life_benchmark.io.jsonl import read_jsonl


def _prediction_from_row(row: dict[str, Any]) -> RQ1PairPrediction:
    return RQ1PairPrediction(
        valid_pairs=[
            RQ1PredictedPair(**pair) for pair in row.get("predicted_pairs") or []
        ],
        invalid_record_count=int(row.get("invalid_record_count") or 0),
        validation_errors=list(row.get("validation_errors") or []),
        parse_error=row.get("parse_error"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--full", required=True, help="full.jsonl (single shared call)")
    parser.add_argument("--mask-terminal", required=True)
    parser.add_argument("--mask-all", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument(
        "--allow-unscored",
        action="store_true",
        help="score anyway when a call was flagged as an inference configuration error",
    )
    args = parser.parse_args()

    cases = {case["case_id"]: case for case in read_jsonl(Path(args.cases))}
    if not cases:
        raise SystemExit(f"no cases in {args.cases}")

    full_rows = list(read_jsonl(Path(args.full)))
    if len(full_rows) != 1:
        raise SystemExit(
            f"{args.full}: expected exactly 1 shared full prediction, got {len(full_rows)}"
        )
    full_row = full_rows[0]
    full_prediction = _prediction_from_row(full_row)

    masked_rows: dict[str, dict[str, dict[str, Any]]] = {}
    for condition, path in (
        ("mask_terminal", args.mask_terminal),
        ("mask_all", args.mask_all),
    ):
        rows = list(read_jsonl(Path(path)))
        by_case = {row["case_id"]: row for row in rows}
        if len(by_case) != len(rows):
            raise SystemExit(f"{path}: duplicate case_id rows")
        masked_rows[condition] = by_case

    unscored: list[dict[str, Any]] = []
    for label, row in [("full", full_row)] + [
        (condition, row)
        for condition in MASKED_CONDITIONS
        for row in masked_rows[condition].values()
    ]:
        if not row.get("scored", True):
            unscored.append(
                {
                    "condition": label,
                    "case_id": row.get("case_id"),
                    "inference_configuration_error": row.get(
                        "inference_configuration_error"
                    ),
                    "call_error": row.get("call_error"),
                }
            )
    if unscored and not args.allow_unscored:
        raise SystemExit(
            "refusing to score: unscored calls present "
            f"(pass --allow-unscored to override): {json.dumps(unscored, ensure_ascii=False)}"
        )

    # every case must reference the same full context
    full_hash = full_row["context_prompt_sha256"]
    applies = set(full_row.get("applies_to_case_ids") or [])
    missing = set(cases) - applies
    if missing:
        raise SystemExit(
            f"full prediction does not cover cases: {sorted(missing)}"
        )

    paired: list[dict[str, Any]] = []
    for case_id, case in sorted(cases.items()):
        predictions = {"full": full_prediction}
        for condition in MASKED_CONDITIONS:
            row = masked_rows[condition].get(case_id)
            if row is None:
                raise SystemExit(f"{condition}: missing prediction for {case_id}")
            predictions[condition] = _prediction_from_row(row)
        metrics = paired_case_metrics(case, predictions)
        metrics["full_prediction_reuse"] = {
            "reused": True,
            "prediction_id": full_row["prediction_id"],
            "context_prompt_sha256": full_hash,
        }
        metrics["condition_context_hashes"] = {
            "full": full_hash,
            **{
                condition: masked_rows[condition][case_id]["context_prompt_sha256"]
                for condition in MASKED_CONDITIONS
            },
        }
        paired.append(metrics)

    report = {
        "stage": RQ1_PAIR_STAGE,
        "pair_protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "pair_metrics_version": RQ1_PAIR_METRICS_VERSION,
        "canary_protocol_version": CANARY_PROTOCOL_VERSION,
        "provider": full_row["provider"],
        "model": full_row["model"],
        "applied_provider_params": full_row.get("applied_provider_params"),
        "full_prediction": {
            "prediction_id": full_row["prediction_id"],
            "context_prompt_sha256": full_hash,
            "reused_across_cases": sorted(applies),
            "call_count_saved": max(0, len(cases) - 1),
            "thinking_tokens": full_row.get("provider_thinking_tokens"),
        },
        "unscored_calls": unscored,
        "summary": aggregate_paired_cases(paired),
        "cases": paired,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = report["summary"]
    print(
        f"paired canary {full_row['provider']}/{full_row['model']}: "
        f"{summary['case_count']} cases, "
        f"{summary['retraction_opportunities']} retraction opportunities"
    )
    for condition in MASKED_CONDITIONS:
        stats = summary[condition]
        print(
            f"  {condition:14} exact_persistence={stats['target_exact_pair_persistence']} "
            f"label_persistence={stats['target_label_persistence']} "
            f"retracted_correctly={stats['retracted_correctly']} "
            f"non_target_regressions={stats['non_target_regression_total']}"
        )
    print(f"  verdicts: {summary['verdicts']}")
    print(f"report -> {report_path}")


if __name__ == "__main__":
    main()
