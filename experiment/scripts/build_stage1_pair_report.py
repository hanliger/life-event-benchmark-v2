#!/usr/bin/env python3
"""Build the focused Stage 1 pair-reconstruction result report."""

from __future__ import annotations

import csv
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "experiment" / "src"))

from financial_memory_experiment.metrics import summarize_predictions  # noqa: E402
from financial_memory_experiment.paths import ExperimentPaths  # noqa: E402
from financial_memory_experiment.stage1 import STAGE1  # noqa: E402
from financial_memory_experiment.stage1_pairs import (  # noqa: E402
    HEADLINE_METRIC,
)
from financial_memory_experiment.util import read_jsonl  # noqa: E402


CHECKPOINTS = tuple(range(15, 301, 15))
BANDS = {
    "early": tuple(range(15, 91, 15)),
    "middle": tuple(range(105, 196, 15)),
    "late": tuple(range(210, 301, 15)),
}


@dataclass(frozen=True)
class ModelSpec:
    method_id: str
    display_name: str
    provider_family: str
    model_scale: str
    source_run: str


MODEL_SPECS = (
    ModelSpec(
        "fc_gpt_5_6_sol",
        "GPT 5.6 Sol",
        "GPT",
        "large",
        "stage1_api3_combined/0731_1551",
    ),
    ModelSpec(
        "fc_claude_opus_4_8",
        "Claude Opus 4.8",
        "Claude",
        "large",
        "stage1_api3_combined/0731_1551",
    ),
    ModelSpec(
        "fc_gemini_3_1_pro",
        "Gemini 3.1 Pro",
        "Gemini",
        "large",
        "stage1_api3_combined/0731_1551",
    ),
    ModelSpec(
        "fc_gpt_5_6_terra",
        "GPT 5.6 Terra",
        "GPT",
        "small",
        "stage1_small4/0731_1101",
    ),
    ModelSpec(
        "fc_gpt_5_6_luna",
        "GPT 5.6 Luna",
        "GPT",
        "small",
        "stage1_small4/0731_1101",
    ),
    ModelSpec(
        "fc_claude_sonnet_4_6",
        "Claude Sonnet 4.6",
        "Claude",
        "small",
        "stage1_small4/0731_1101",
    ),
    ModelSpec(
        "fc_gemini_3_5_flash",
        "Gemini 3.5 Flash",
        "Gemini",
        "small",
        "stage1_small4/0731_1101",
    ),
)


def _mean_metric(rows: list[dict[str, Any]], metric: str) -> float:
    return statistics.fmean(float(row["metrics"][metric]) for row in rows)


def _band_mean(
    by_checkpoint: dict[int, float], checkpoints: tuple[int, ...]
) -> float:
    return statistics.fmean(by_checkpoint[checkpoint] for checkpoint in checkpoints)


def build_rows() -> list[dict[str, Any]]:
    runs_root = REPO_ROOT / "experiment" / "runs"
    paths_by_method: dict[str, list[Path]] = {}
    prediction_paths: list[Path] = []
    for spec in MODEL_SPECS:
        raw_root = runs_root / spec.source_run / "raw" / spec.method_id
        paths = sorted(raw_root.glob("traj_*.jsonl"))
        if len(paths) != 20:
            raise ValueError(
                f"expected 20 final trajectories for {spec.method_id}, "
                f"found {len(paths)}"
            )
        paths_by_method[spec.method_id] = paths
        prediction_paths.extend(paths)

    report = summarize_predictions(
        ExperimentPaths.discover(), prediction_paths, allow_partial=True
    )
    rows = []
    for spec in MODEL_SPECS:
        raw_rows = [
            row
            for path in paths_by_method[spec.method_id]
            for row in read_jsonl(path)
        ]
        stage = report["methods"][spec.method_id][STAGE1]
        f1_by_checkpoint = {
            checkpoint: _mean_metric(
                [
                    row
                    for row in raw_rows
                    if int(row["query_checkpoint"]) == checkpoint
                ],
                HEADLINE_METRIC,
            )
            for checkpoint in CHECKPOINTS
        }
        exact_by_checkpoint = {
            checkpoint: _mean_metric(
                [
                    row
                    for row in raw_rows
                    if int(row["query_checkpoint"]) == checkpoint
                ],
                "exact_pair_multiset_match",
            )
            for checkpoint in CHECKPOINTS
        }
        row: dict[str, Any] = {
            "report_schema_version": "stage1_pair_report-v1",
            "model_scale": spec.model_scale,
            "provider_family": spec.provider_family,
            "model_display_name": spec.display_name,
            "method_id": spec.method_id,
            "source_run_dir": f"experiment/runs/{spec.source_run}",
            "items": len(raw_rows),
            "headline_metric": HEADLINE_METRIC,
            "strict_pair_f1": stage["score"],
            "strict_pair_f1_ci95_low": stage["ci95"][0],
            "strict_pair_f1_ci95_high": stage["ci95"][1],
            "exact_pair_set_match": stage["exact_pair_set_match"],
            "exact_pair_set_ci95_low": stage["exact_pair_set_ci95"][0],
            "exact_pair_set_ci95_high": stage["exact_pair_set_ci95"][1],
            "parse_success_rate": 1 - stage["parse_errors"] / len(raw_rows),
            "schema_success_rate": (
                1 - stage["final_schema_failures"] / len(raw_rows)
            ),
            "event_id_only_f1": _mean_metric(
                raw_rows, "diagnostic_event_id_only_f1"
            ),
            "evidence_session_only_f1": _mean_metric(
                raw_rows, "diagnostic_evidence_session_only_f1"
            ),
            "absolute_pair_count_error": _mean_metric(
                raw_rows, "absolute_pair_count_error"
            ),
            "strict_pair_f1_early": _band_mean(
                f1_by_checkpoint, BANDS["early"]
            ),
            "strict_pair_f1_middle": _band_mean(
                f1_by_checkpoint, BANDS["middle"]
            ),
            "strict_pair_f1_late": _band_mean(
                f1_by_checkpoint, BANDS["late"]
            ),
            "exact_pair_set_early": _band_mean(
                exact_by_checkpoint, BANDS["early"]
            ),
            "exact_pair_set_middle": _band_mean(
                exact_by_checkpoint, BANDS["middle"]
            ),
            "exact_pair_set_late": _band_mean(
                exact_by_checkpoint, BANDS["late"]
            ),
        }
        for checkpoint in CHECKPOINTS:
            row[f"strict_pair_f1_cp{checkpoint:03d}"] = f1_by_checkpoint[
                checkpoint
            ]
            row[f"exact_pair_set_cp{checkpoint:03d}"] = exact_by_checkpoint[
                checkpoint
            ]
        rows.append(row)
    return rows


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.2f}"


def build_markdown(rows: list[dict[str, Any]]) -> str:
    ranked = sorted(
        rows, key=lambda row: float(row["strict_pair_f1"]), reverse=True
    )
    lines = [
        "# Stage 1 결과 — 누적 event/evidence pair 복원",
        "",
        "## 평가 원칙",
        "",
        "대표 지표는 `Strict Pair F1`이다. 각 prediction atom은 life-event ID와 "
        "그 발생을 처음 확정하는 dialogue session ID가 모두 일치할 때만 true "
        "positive다. checkpoint별 trajectory macro를 계산한 뒤 cp15부터 cp300까지 "
        "동일 가중한다.",
        "",
        "`Exact Pair-Set Match`는 checkpoint까지 누적된 전체 pair multiset이 "
        "완전히 같을 때만 1이다. 부분 복원 능력은 Strict Pair F1, 완전한 event "
        "history 복원 성공률은 Exact Pair-Set Match로 해석한다. 두 지표의 95% CI는 "
        "trajectory-cluster bootstrap이다.",
        "",
        "## 핵심 결과",
        "",
        "| Rank | Model | Strict Pair F1 [95% CI] | Exact Pair-Set [95% CI] | Schema Valid |",
        "|---:|---|---:|---:|---:|",
    ]
    for rank, row in enumerate(ranked, 1):
        lines.append(
            f"| {rank} | {row['model_display_name']} | "
            f"{_pct(row['strict_pair_f1'])} "
            f"[{_pct(row['strict_pair_f1_ci95_low'])}, "
            f"{_pct(row['strict_pair_f1_ci95_high'])}] | "
            f"{_pct(row['exact_pair_set_match'])} "
            f"[{_pct(row['exact_pair_set_ci95_low'])}, "
            f"{_pct(row['exact_pair_set_ci95_high'])}] | "
            f"{_pct(row['schema_success_rate'])} |"
        )

    lines.extend(
        [
            "",
            "## Checkpoint 구간 추세",
            "",
            "Gold pair 수는 cp15의 1개에서 cp300의 20개까지 증가한다. 각 구간은 "
            "cp15–90, cp105–195, cp210–300이다.",
            "",
            "| Model | F1 early | F1 middle | F1 late | Exact early | Exact middle | Exact late |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in ranked:
        lines.append(
            f"| {row['model_display_name']} | "
            f"{_pct(row['strict_pair_f1_early'])} | "
            f"{_pct(row['strict_pair_f1_middle'])} | "
            f"{_pct(row['strict_pair_f1_late'])} | "
            f"{_pct(row['exact_pair_set_early'])} | "
            f"{_pct(row['exact_pair_set_middle'])} | "
            f"{_pct(row['exact_pair_set_late'])} |"
        )

    lines.extend(
        [
            "",
            "## 해석",
            "",
            "1. Gemini 3.1 Pro가 Strict Pair F1 74.82와 Exact Pair-Set "
            "11.25로 두 지표 모두 가장 높다.",
            "2. Exact Pair-Set은 누적 pair 중 하나만 틀려도 0이므로 F1보다 훨씬 "
            "엄격하다. cp300에서는 일곱 모델 모두 0이다.",
            "3. F1은 event/session pair 단위의 부분 복원 능력을, Exact Pair-Set은 "
            "checkpoint 전체의 무결한 복원 성공률을 보여준다. 모델 순위의 대표값은 "
            "F1으로 두고 Exact를 엄격한 성공 기준으로 함께 보고한다.",
            "4. Event-ID-only F1, evidence-session-only F1과 pair-count error는 "
            "오류 분석용으로 동반 CSV에 보존한다.",
            "",
            "전체 checkpoint별 Strict Pair F1과 Exact Pair-Set, 신뢰성, provenance는 "
            "동반 CSV에 포함한다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = build_rows()
    output_dir = REPO_ROOT / "experiment" / "docs" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "stage1_pair_results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    markdown_path = output_dir / "stage1_pair_results.md"
    markdown_path.write_text(build_markdown(rows), encoding="utf-8")
    print(markdown_path)
    print(csv_path)


if __name__ == "__main__":
    main()
