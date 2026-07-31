#!/usr/bin/env python3
"""Build the canonical Stage 1/2 large+small model comparison."""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CHECKPOINTS = tuple(range(15, 301, 15))
BANDS = {
    "early": (15, 90),
    "middle": (105, 195),
    "late": (210, 300),
}


@dataclass(frozen=True)
class ModelSpec:
    method_id: str
    display_name: str
    provider_family: str
    model_scale: str
    stage1_run: str
    stage2_run: str


MODEL_SPECS = (
    ModelSpec(
        "fc_gpt_5_6_sol",
        "GPT 5.6 Sol",
        "GPT",
        "large",
        "stage1_api3_combined/0731_1551",
        "stage2_2_combined/0731_1550",
    ),
    ModelSpec(
        "fc_claude_opus_4_8",
        "Claude Opus 4.8",
        "Claude",
        "large",
        "stage1_api3_combined/0731_1551",
        "stage2_2_combined/0731_1550",
    ),
    ModelSpec(
        "fc_gemini_3_1_pro",
        "Gemini 3.1 Pro",
        "Gemini",
        "large",
        "stage1_api3_combined/0731_1551",
        "stage2_2_combined/0731_1550",
    ),
    ModelSpec(
        "fc_gpt_5_6_terra",
        "GPT 5.6 Terra",
        "GPT",
        "small",
        "stage1_small4/0731_1101",
        "stage2_2_combined/0731_1914",
    ),
    ModelSpec(
        "fc_gpt_5_6_luna",
        "GPT 5.6 Luna",
        "GPT",
        "small",
        "stage1_small4/0731_1101",
        "stage2_2_combined/0731_1914",
    ),
    ModelSpec(
        "fc_claude_sonnet_4_6",
        "Claude Sonnet 4.6",
        "Claude",
        "small",
        "stage1_small4/0731_1101",
        "stage2_2_combined/0731_1914",
    ),
    ModelSpec(
        "fc_gemini_3_5_flash",
        "Gemini 3.5 Flash",
        "Gemini",
        "small",
        "stage1_small4/0731_1101",
        "stage2_2_combined/0731_1914",
    ),
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def as_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def as_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot average an empty sequence")
    return statistics.fmean(values)


def percentile(values: list[float], proportion: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(proportion * len(ordered)) - 1)
    return ordered[index]


def pearson_r(xs: list[float], ys: list[float]) -> float:
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_scale = math.sqrt(sum((x - x_mean) ** 2 for x in xs))
    y_scale = math.sqrt(sum((y - y_mean) ** 2 for y in ys))
    if x_scale == 0 or y_scale == 0:
        return 0.0
    return numerator / (x_scale * y_scale)


def metric_root(runs_root: Path, relative_run: str) -> Path:
    root = runs_root / relative_run / "metrics"
    required = (
        "main_results.csv",
        "checkpoint_metrics.csv",
        "parse_reliability.csv",
        "cost_latency.csv",
    )
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        raise FileNotFoundError(f"{root}: missing {missing}")
    return root


def one_stage_row(
    *,
    runs_root: Path,
    spec: ModelSpec,
    stage: str,
) -> dict[str, Any]:
    relative_run = spec.stage1_run if stage == "stage1" else spec.stage2_run
    root = metric_root(runs_root, relative_run)
    main_rows = [
        row
        for row in read_csv(root / "main_results.csv")
        if row["method_id"] == spec.method_id
    ]
    if len(main_rows) != 1:
        raise ValueError(
            f"expected one main result for {stage}/{spec.method_id}, "
            f"found {len(main_rows)}"
        )
    main = main_rows[0]
    checkpoint_rows = [
        row
        for row in read_csv(root / "checkpoint_metrics.csv")
        if row["method_id"] == spec.method_id
    ]
    if len(checkpoint_rows) != 400:
        raise ValueError(
            f"expected 400 checkpoints for {stage}/{spec.method_id}, "
            f"found {len(checkpoint_rows)}"
        )
    checkpoint_metric = (
        "strict_occurred_event_evidence_f1"
        if stage == "stage1"
        else "final_state_accuracy"
    )
    checkpoint_means: dict[int, float] = {}
    for checkpoint in CHECKPOINTS:
        values = [
            float(row[checkpoint_metric])
            for row in checkpoint_rows
            if int(row["checkpoint"]) == checkpoint
        ]
        if len(values) != 20:
            raise ValueError(
                f"expected 20 trajectories at {stage}/{spec.method_id}/"
                f"cp{checkpoint}, found {len(values)}"
            )
        checkpoint_means[checkpoint] = mean(values)

    reliability = [
        row
        for row in read_csv(root / "parse_reliability.csv")
        if row["method_id"] == spec.method_id
    ]
    if len(reliability) != 400:
        raise ValueError(
            f"expected 400 reliability rows for {stage}/{spec.method_id}, "
            f"found {len(reliability)}"
        )

    cost_rows = [
        row
        for row in read_csv(root / "cost_latency.csv")
        if row["method_id"] == spec.method_id
    ]
    if len(cost_rows) != 400:
        raise ValueError(
            f"expected 400 cost rows for {stage}/{spec.method_id}, "
            f"found {len(cost_rows)}"
        )
    latencies = [
        float(row["latency_seconds"])
        for row in cost_rows
        if row.get("latency_seconds") not in (None, "")
    ]
    input_tokens = [
        int(row["input_tokens"])
        for row in cost_rows
        if row.get("input_tokens") not in (None, "")
    ]
    output_tokens = [
        int(row["output_tokens"])
        for row in cost_rows
        if row.get("output_tokens") not in (None, "")
    ]
    recorded_cost = sum(
        float(row["estimated_cost_usd"])
        for row in cost_rows
        if row.get("estimated_cost_usd") not in (None, "")
    )

    row: dict[str, Any] = {
        "report_schema_version": "cross-stage-model-summary-v1",
        "stage": stage,
        "stage_label": (
            "Stage 1 occurred-event/evidence pairs"
            if stage == "stage1"
            else "Stage 2 state reconstruction"
        ),
        "model_scale": spec.model_scale,
        "provider_family": spec.provider_family,
        "model_display_name": spec.display_name,
        "method_id": spec.method_id,
        "source_run_dir": f"experiment/runs/{relative_run}",
        "items": int(main["items"]),
        "headline_metric": checkpoint_metric,
        "headline_score": float(main["score"]),
        "ci95_low": float(main["ci95_low"]),
        "ci95_high": float(main["ci95_high"]),
        "aggregation": main["aggregation"],
        "parse_errors": int(main["parse_errors"]),
        "parse_error_rate": int(main["parse_errors"]) / int(main["items"]),
        "first_attempt_parse_errors": sum(
            value.lower() == "true"
            for value in (item["first_attempt_parse_error"] for item in reliability)
        ),
        "first_attempt_schema_failures": sum(
            value.lower() == "true"
            for value in (
                item["first_attempt_schema_failure"] for item in reliability
            )
        ),
        "final_parse_errors": sum(
            value.lower() == "true"
            for value in (item["final_parse_error"] for item in reliability)
        ),
        "final_schema_failures": sum(
            value.lower() == "true"
            for value in (item["final_schema_failure"] for item in reliability)
        ),
        "total_retry_count": sum(int(item["retry_count"]) for item in reliability),
        "early_score_cp015_090": mean(
            [
                score
                for checkpoint, score in checkpoint_means.items()
                if BANDS["early"][0] <= checkpoint <= BANDS["early"][1]
            ]
        ),
        "middle_score_cp105_195": mean(
            [
                score
                for checkpoint, score in checkpoint_means.items()
                if BANDS["middle"][0] <= checkpoint <= BANDS["middle"][1]
            ]
        ),
        "late_score_cp210_300": mean(
            [
                score
                for checkpoint, score in checkpoint_means.items()
                if BANDS["late"][0] <= checkpoint <= BANDS["late"][1]
            ]
        ),
        "cp300_minus_cp015": checkpoint_means[300] - checkpoint_means[15],
        "checkpoint_pearson_r": pearson_r(
            [float(checkpoint) for checkpoint in CHECKPOINTS],
            [checkpoint_means[checkpoint] for checkpoint in CHECKPOINTS],
        ),
        "dynamic_path_final_state_accuracy": as_float(
            main.get("dynamic_path_final_state_accuracy")
        ),
        "correct_change_f1": as_float(main.get("correct_change_f1")),
        "path_macro_correct_change_f1": as_float(
            main.get("path_macro_correct_change_f1")
        ),
        "event_macro_update_accuracy": as_float(
            main.get("event_macro_update_accuracy")
        ),
        "event_exact_update_accuracy": as_float(
            main.get("event_exact_update_accuracy")
        ),
        "retention_mean_over_observed_lags": as_float(
            main.get("retention_mean_over_observed_lags")
        ),
        "recorded_cost_usd": recorded_cost,
        "recorded_cost_per_item_usd": recorded_cost / int(main["items"]),
        "latency_observations": len(latencies),
        "mean_latency_seconds": mean(latencies) if latencies else None,
        "p50_latency_seconds": percentile(latencies, 0.50),
        "p95_latency_seconds": percentile(latencies, 0.95),
        "max_latency_seconds": max(latencies) if latencies else None,
        "usage_observations": min(len(input_tokens), len(output_tokens)),
        "total_input_tokens": sum(input_tokens),
        "total_output_tokens": sum(output_tokens),
        "mean_output_tokens": mean([float(value) for value in output_tokens])
        if output_tokens
        else None,
    }
    for checkpoint in CHECKPOINTS:
        row[f"cp{checkpoint:03d}_score"] = checkpoint_means[checkpoint]
    return row


def format_float(value: Any, digits: int = 3) -> str:
    if value is None or value == "":
        return "—"
    return f"{float(value):.{digits}f}"


def markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]


def build_markdown(rows: list[dict[str, Any]]) -> str:
    by_stage = {
        stage: [row for row in rows if row["stage"] == stage]
        for stage in ("stage1", "stage2")
    }
    lines = [
        "# Stage 1/2 대형·소형 모델 통합 결과",
        "",
        "## 범위",
        "",
        "이 보고서는 API 모델 7종의 canonical 완료 run을 통합한다. 각 "
        "model-stage 조합은 20 trajectories x 20 checkpoints = 400 predictions로 "
        "구성된다. 대형 모델은 GPT 5.6 Sol, Claude Opus 4.8, Gemini 3.1 Pro이고, "
        "소형 모델은 GPT 5.6 Terra/Luna, Claude Sonnet 4.6, Gemini 3.5 Flash이다.",
        "",
        "Stage 1 headline은 strict occurred-event/evidence-pair F1, Stage 2 "
        "headline은 final-state accuracy이다. 초반·중반·후반 구간은 각각 "
        "cp15-90, cp105-195, cp210-300이다.",
        "",
        "## Canonical 원본",
        "",
    ]
    sources = []
    seen_sources: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["stage"], row["source_run_dir"])
        if key in seen_sources:
            continue
        seen_sources.add(key)
        models = ", ".join(
            candidate["model_display_name"]
            for candidate in rows
            if candidate["stage"] == row["stage"]
            and candidate["source_run_dir"] == row["source_run_dir"]
        )
        sources.append(
            [row["stage"].title(), f"`{row['source_run_dir']}`", models]
        )
    lines.extend(markdown_table(["Stage", "Run", "선택 모델"], sources))

    lines.extend(["", "## 전체 결과", ""])
    overall_rows = []
    for stage in ("stage1", "stage2"):
        for row in sorted(
            by_stage[stage], key=lambda item: item["headline_score"], reverse=True
        ):
            overall_rows.append(
                [
                    stage.title(),
                    row["model_scale"],
                    row["model_display_name"],
                    f"{row['headline_score']:.3f} "
                    f"[{row['ci95_low']:.3f}, {row['ci95_high']:.3f}]",
                    f"{row['early_score_cp015_090']:.3f} -> "
                    f"{row['middle_score_cp105_195']:.3f} -> "
                    f"{row['late_score_cp210_300']:.3f}",
                    format_float(row["cp300_minus_cp015"]),
                    f"{row['parse_errors']} / {row['final_schema_failures']}",
                    f"${row['recorded_cost_usd']:.2f}",
                    format_float(row["mean_latency_seconds"], 2),
                ]
            )
    lines.extend(
        markdown_table(
            [
                "Stage",
                "크기",
                "모델",
                "Headline [95% CI]",
                "초반 -> 중반 -> 후반",
                "cp300-cp15",
                "Parse / validation",
                "기록 비용",
                "평균 지연 (s)",
            ],
            overall_rows,
        )
    )

    lines.extend(["", "## Stage 2 세부 지표", ""])
    stage2_rows = []
    for row in sorted(
        by_stage["stage2"], key=lambda item: item["headline_score"], reverse=True
    ):
        stage2_rows.append(
            [
                row["model_scale"],
                row["model_display_name"],
                format_float(row["headline_score"]),
                format_float(row["dynamic_path_final_state_accuracy"]),
                format_float(row["correct_change_f1"]),
                format_float(row["path_macro_correct_change_f1"]),
                format_float(row["event_macro_update_accuracy"]),
                format_float(row["event_exact_update_accuracy"]),
                format_float(row["retention_mean_over_observed_lags"]),
            ]
        )
    lines.extend(
        markdown_table(
            [
                "크기",
                "모델",
                "상태 정확도",
                "동적 경로 정확도",
                "변화 F1",
                "경로-macro 변화 F1",
                "이벤트 update",
                "정확 update",
                "유지율",
            ],
            stage2_rows,
        )
    )

    large_means = {
        stage: mean(
            [
                row["headline_score"]
                for row in by_stage[stage]
                if row["model_scale"] == "large"
            ]
        )
        for stage in by_stage
    }
    small_means = {
        stage: mean(
            [
                row["headline_score"]
                for row in by_stage[stage]
                if row["model_scale"] == "small"
            ]
        )
        for stage in by_stage
    }
    lines.extend(
        [
            "",
            "## 모델 크기별 비교",
            "",
            *markdown_table(
                ["Stage", "대형 평균", "소형 평균", "대형-소형"],
                [
                    [
                        stage.title(),
                        format_float(large_means[stage]),
                        format_float(small_means[stage]),
                        format_float(large_means[stage] - small_means[stage]),
                    ]
                    for stage in ("stage1", "stage2")
                ],
            ),
            "",
            "이는 서로 다른 모델 수(대형 3종, 소형 4종)의 기술통계 평균이며, "
            "모델 크기 효과에 대한 paired 추정치는 아니다.",
        ]
    )

    lines.extend(["", "## 전체 checkpoint 곡선", ""])
    model_order = [spec.display_name for spec in MODEL_SPECS]
    for stage in ("stage1", "stage2"):
        lines.extend([f"### {stage.title()}", ""])
        cp_rows = []
        stage_lookup = {
            row["model_display_name"]: row for row in by_stage[stage]
        }
        for checkpoint in CHECKPOINTS:
            cp_rows.append(
                [str(checkpoint)]
                + [
                    format_float(
                        stage_lookup[model][f"cp{checkpoint:03d}_score"]
                    )
                    for model in model_order
                ]
            )
        lines.extend(markdown_table(["CP", *model_order], cp_rows))
        lines.append("")

    rankings = {
        stage: sorted(
            by_stage[stage], key=lambda item: item["headline_score"], reverse=True
        )
        for stage in by_stage
    }
    lookup = {
        (row["stage"], row["method_id"]): row
        for row in rows
    }
    lines.extend(
        [
            "## 주요 관찰",
            "",
            "1. Stage 1 순위: "
            + " > ".join(
                f"{row['model_display_name']} ({row['headline_score']:.3f})"
                for row in rankings["stage1"]
            )
            + ".",
            "2. Stage 2 순위: "
            + " > ".join(
                f"{row['model_display_name']} ({row['headline_score']:.3f})"
                for row in rankings["stage2"]
            )
            + ".",
            "3. Stage 2는 7개 모델 모두 초반에서 후반으로 갈수록 하락한다. "
            "전체 곡선에는 국소 변동이 있지만 checkpoint 상관계수는 모두 음수다.",
            "4. Stage 1에는 보편적인 길이 추세가 없다. Gemini 3.5 Flash와 "
            "GPT 5.6 Luna는 약화되지만, Claude Sonnet 4.6과 GPT 5.6 Terra는 "
            "이벤트가 누적될수록 개선된다.",
            "5. 모델 family의 상대 성능은 과업 의존적이다. Stage 1에서는 "
            "Claude Sonnet이 Opus보다 높지만 Stage 2에서는 Opus가 Sonnet보다 "
            f"{lookup[('stage2', 'fc_claude_opus_4_8')]['headline_score'] - lookup[('stage2', 'fc_claude_sonnet_4_6')]['headline_score']:.3f} "
            "높다. Gemini Pro의 Flash 대비 우위는 Stage 2에서 크게 좁아진다.",
            "6. GPT 5.6 Luna는 Stage 1에서 Sol과 Terra보다 낮지만 Stage 2에서는 "
            "최고 소형 모델이자 전체 2위다.",
            "",
            "## 신뢰성 및 비용 주의사항",
            "",
            "- Parse 및 validation 실패는 headline 결과에 포함되며 제외하지 않았다. "
            "표의 validation 수는 parse 실패뿐 아니라 부분적으로 채점 가능한 invalid "
            "record/cell도 포함하므로 parse 수보다 클 수 있다.",
            "- Stage 2 Claude Sonnet 4.6 `traj_009/cp_300`은 반복적인 빈 API "
            "응답 이후 명시적인 empty prediction 오답으로 처리했다.",
            "- 기록 비용은 usage가 반환된 행을 기준으로 한다. 반복 Sonnet 빈 응답처럼 "
            "usage metadata가 없는 provider 실패 호출은 빠져 있으므로 실제 지출은 더 높다.",
            "- Stage 1과 Stage 2 headline은 서로 다른 과업을 측정하므로 같은 척도처럼 "
            "직접 차감해서는 안 된다.",
            "- 정확한 checkpoint 값, token 합계, latency percentile, retry 횟수 및 "
            "source-run provenance는 동반 CSV에 포함했다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    experiment_root = repo_root / "experiment"
    runs_root = experiment_root / "runs"
    output_dir = experiment_root / "docs" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        one_stage_row(runs_root=runs_root, spec=spec, stage=stage)
        for stage in ("stage1", "stage2")
        for spec in MODEL_SPECS
    ]
    csv_path = output_dir / "stage1_stage2_all_models.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    markdown_path = output_dir / "stage1_stage2_all_models.md"
    markdown_path.write_text(build_markdown(rows), encoding="utf-8")
    print(markdown_path)
    print(csv_path)


if __name__ == "__main__":
    main()
