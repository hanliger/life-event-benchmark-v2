#!/usr/bin/env python3
"""Build the focused Stage 2 GCA@15 result report from frozen raw answers."""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "experiment" / "src"))

from financial_memory_experiment.metrics import (  # noqa: E402
    _gca_components,
    _sum_gca_counts,
    summarize_predictions,
)
from financial_memory_experiment.paths import ExperimentPaths  # noqa: E402


CHECKPOINTS = tuple(range(15, 301, 15))
BANDS = {
    "early": tuple(range(15, 91, 15)),
    "middle": tuple(range(105, 196, 15)),
    "late": tuple(range(210, 301, 15)),
}
RETENTION_BUCKETS = (
    "0",
    "1-15",
    "16-30",
    "31-60",
    "61-120",
    "121-180",
    "181-240",
    "241+",
)


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
        "stage2_2_combined/0731_1550",
    ),
    ModelSpec(
        "fc_claude_opus_4_8",
        "Claude Opus 4.8",
        "Claude",
        "large",
        "stage2_2_combined/0731_1550",
    ),
    ModelSpec(
        "fc_gemini_3_1_pro",
        "Gemini 3.1 Pro",
        "Gemini",
        "large",
        "stage2_2_combined/0731_1550",
    ),
    ModelSpec(
        "fc_gpt_5_6_terra",
        "GPT 5.6 Terra",
        "GPT",
        "small",
        "stage2_2_combined/0731_1914",
    ),
    ModelSpec(
        "fc_gpt_5_6_luna",
        "GPT 5.6 Luna",
        "GPT",
        "small",
        "stage2_2_combined/0731_1914",
    ),
    ModelSpec(
        "fc_claude_sonnet_4_6",
        "Claude Sonnet 4.6",
        "Claude",
        "small",
        "stage2_2_combined/0731_1914",
    ),
    ModelSpec(
        "fc_gemini_3_5_flash",
        "Gemini 3.5 Flash",
        "Gemini",
        "small",
        "stage2_2_combined/0731_1914",
    ),
    ModelSpec(
        "fc_openrouter_llama_4_maverick",
        "Llama 4 Maverick",
        "Llama",
        "open-weight",
        "stage2_2/0801_0514",
    ),
    ModelSpec(
        "fc_openrouter_gpt_oss_120b",
        "GPT-OSS 120B",
        "OpenAI",
        "open-weight",
        "stage2_2/0801_0514_02",
    ),
    ModelSpec(
        "fc_openrouter_qwen_3_5_122b_a10b",
        "Qwen 3.5 122B A10B",
        "Qwen",
        "open-weight",
        "stage2_2/0801_0514_03",
    ),
    ModelSpec(
        "fc_openrouter_qwen_3_6_35b_a3b_fp8",
        "Qwen 3.6 35B A3B",
        "Qwen",
        "open-weight",
        "stage2_2/0801_0514_04",
    ),
)


def _band_gca(gca15: dict[str, Any], checkpoints: tuple[int, ...]) -> float:
    counts = _sum_gca_counts(
        gca15["by_checkpoint"][str(checkpoint)]["counts"]
        for checkpoint in checkpoints
    )
    return float(_gca_components(counts)["score"])


def _base_row(
    *,
    method_id: str,
    display_name: str,
    provider_family: str,
    model_scale: str,
    source_run: str,
    state: dict[str, Any],
    score: float,
    ci95: tuple[float | None, float | None],
    parse_errors: int,
    schema_failures: int,
    items: int,
    baseline_gca: float,
    baseline_final: float,
) -> dict[str, Any]:
    gca15 = state["gca15"]
    metrics = state["metrics"]
    retention = state["retention_after_update"]
    row: dict[str, Any] = {
        "report_schema_version": "stage2_gca15_report-v2",
        "model_scale": model_scale,
        "provider_family": provider_family,
        "model_display_name": display_name,
        "method_id": method_id,
        "source_run_dir": source_run,
        "items": items,
        "headline_metric": "GCA@15",
        "gca15": score,
        "gca15_ci95_low": ci95[0],
        "gca15_ci95_high": ci95[1],
        "gca15_initial_copy_lift": score - baseline_gca,
        "gca15_value_precision": gca15["value_precision"],
        "gca15_value_recall": gca15["value_recall"],
        "gca15_label_precision": gca15["label_precision"],
        "gca15_label_recall": gca15["label_recall"],
        "gca15_correct": gca15["counts"]["correct"],
        "gca15_wrong": gca15["counts"]["wrong"],
        "gca15_overshot": gca15["counts"]["overshot"],
        "gca15_missed": gca15["counts"]["missed"],
        "final_state_accuracy": metrics["final_state_accuracy"],
        "final_state_initial_copy_lift": (
            metrics["final_state_accuracy"] - baseline_final
        ),
        "retention_after_update": retention["mean_over_observed_lags"],
        "evidence_hit_rate": metrics["evidence_hit_rate"],
        "exact_state_match": metrics["exact_state_match"],
        "parse_success_rate": 1 - parse_errors / items,
        "schema_success_rate": 1 - schema_failures / items,
        "gca15_early": _band_gca(gca15, BANDS["early"]),
        "gca15_middle": _band_gca(gca15, BANDS["middle"]),
        "gca15_late": _band_gca(gca15, BANDS["late"]),
    }
    row["gca15_late_minus_early"] = (
        row["gca15_late"] - row["gca15_early"]
    )
    for checkpoint in CHECKPOINTS:
        row[f"gca15_cp{checkpoint:03d}"] = gca15["by_checkpoint"][
            str(checkpoint)
        ]["score"]
    for bucket in RETENTION_BUCKETS:
        row[f"retention_{bucket.replace('-', '_').replace('+', '_plus')}"] = (
            retention["by_lag_bucket"].get(bucket)
        )
    return row


def build_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    runs_root = REPO_ROOT / "experiment" / "runs"
    prediction_paths = []
    for spec in MODEL_SPECS:
        raw_root = runs_root / spec.source_run / "raw" / spec.method_id
        paths = sorted(raw_root.glob("traj_*.jsonl"))
        if len(paths) != 20:
            raise ValueError(
                f"expected 20 final trajectories for {spec.method_id}, "
                f"found {len(paths)}"
            )
        prediction_paths.extend(paths)
    report = summarize_predictions(
        ExperimentPaths.discover(), prediction_paths, allow_partial=True
    )
    baseline = report["stage2_2_initial_copy_baseline"]
    baseline_gca = float(baseline["gca15"]["score"])
    baseline_final = float(baseline["metrics"]["final_state_accuracy"])
    rows = []
    for spec in MODEL_SPECS:
        stage = report["methods"][spec.method_id]["stage2_2_reconstruct"]
        state = stage["state_reconstruction"]
        rows.append(
            _base_row(
                method_id=spec.method_id,
                display_name=spec.display_name,
                provider_family=spec.provider_family,
                model_scale=spec.model_scale,
                source_run=f"experiment/runs/{spec.source_run}",
                state=state,
                score=float(stage["score"]),
                ci95=(stage["ci95"][0], stage["ci95"][1]),
                parse_errors=int(stage["parse_errors"]),
                schema_failures=int(stage["final_schema_failures"]),
                items=int(stage["items"]),
                baseline_gca=baseline_gca,
                baseline_final=baseline_final,
            )
        )
    return rows, baseline


def _pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{100 * value:.{digits}f}"


def _signed_pp(value: float) -> str:
    return f"{100 * value:+.2f}"


def build_markdown(
    rows: list[dict[str, Any]], baseline: dict[str, Any]
) -> str:
    ranked = sorted(rows, key=lambda row: float(row["gca15"]), reverse=True)
    baseline_gca = float(baseline["gca15"]["score"])
    baseline_final = float(baseline["metrics"]["final_state_accuracy"])
    lines = [
        "# Stage 2 결과 — GCA@15 상태 변화 평가",
        "",
        "## 평가 원칙",
        "",
        "대표 지표는 Aksu & Chen (2024)의 Granular Change Accuracy를 "
        "15-session checkpoint 구조에 대응한 `GCA@15`다. trajectory를 dialogue, "
        "checkpoint를 turn, 34개 financial-memory path를 slot label, 정규화된 "
        "`(value, status)`를 strict slot value로 사용한다. 모델에 제공된 `S000`은 "
        "평가하지 않는 seed다.",
        "",
        "인접 checkpoint의 state delta를 논문의 Algorithm 1에 따라 "
        "`C/W/M/O`로 세고, VP/VR/LP/LR 및 support-weighted harmonic mean은 "
        "공식 구현을 그대로 사용한다. 95% CI는 trajectory-cluster bootstrap이다. "
        "Evidence ID는 GCA value에서 제외하고 별도의 Evidence Hit으로 보고한다.",
        "",
        "이 과업은 34개 path를 항상 출력하는 fixed schema이므로 정상 출력에서는 "
        "slot-label 누락/초과인 M/O가 드물고 GCA의 변별력은 주로 C/W에서 나온다. "
        "구성요소와 원수는 동반 CSV에 공개한다.",
        "",
        "- 논문: <https://aclanthology.org/2024.lrec-main.699/>",
        "- 공식 구현: <https://github.com/cuthalionn/Granular_Change_Accuracy>",
        "",
        "## 핵심 결과",
        "",
        "| Rank | Model | GCA@15 [95% CI] | vs Initial Copy | Retention | "
        "Final State | Final lift | Evidence Hit | Exact Snapshot | Schema Valid |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| — | Initial Copy | {_pct(baseline_gca)} | — | "
            f"{_pct(baseline['retention_after_update']['mean_over_observed_lags'])} "
            "| "
            f"{_pct(baseline_final)} | — | "
            f"{_pct(baseline['metrics']['evidence_hit_rate'])} | "
            f"{_pct(baseline['metrics']['exact_state_match'])} | 100.00 |"
        ),
    ]
    for rank, row in enumerate(ranked, 1):
        ci = (
            f"[{_pct(row['gca15_ci95_low'])}, "
            f"{_pct(row['gca15_ci95_high'])}]"
        )
        lines.append(
            f"| {rank} | {row['model_display_name']} | "
            f"{_pct(row['gca15'])} {ci} | "
            f"{_signed_pp(row['gca15_initial_copy_lift'])} pp | "
            f"{_pct(row['retention_after_update'])} | "
            f"{_pct(row['final_state_accuracy'])} | "
            f"{_signed_pp(row['final_state_initial_copy_lift'])} pp | "
            f"{_pct(row['evidence_hit_rate'])} | "
            f"{_pct(row['exact_state_match'])} | "
            f"{_pct(row['schema_success_rate'])} |"
        )

    lines.extend(
        [
            "",
            "## Checkpoint 구간 추세",
            "",
            "구간 점수는 해당 checkpoint transition의 C/W/M/O를 합친 뒤 GCA를 "
            "다시 계산했다. checkpoint 위치별 event 구성도 달라지므로 이 표는 "
            "context-length 효과만을 뜻하지 않는다. 장기 기억 저하는 다음 retention "
            "표와 함께 해석한다.",
            "",
            "| Model | cp15–90 | cp105–195 | cp210–300 | Late − Early |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in ranked:
        lines.append(
            f"| {row['model_display_name']} | {_pct(row['gca15_early'])} | "
            f"{_pct(row['gca15_middle'])} | {_pct(row['gca15_late'])} | "
            f"{_signed_pp(row['gca15_late_minus_early'])} pp |"
        )

    lines.extend(
        [
            "",
            "## Update 이후 retention",
            "",
            "각 update event가 최신 근거로 유효한 동안 affected path의 strict "
            "`(value, status)` 정확도를 lag별로 계산한다.",
            "",
            "| Model | 0 | 1–15 | 16–30 | 31–60 | 61–120 | 121–180 | 181–240 | 241+ |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in ranked:
        values = [
            _pct(row[f"retention_{bucket.replace('-', '_').replace('+', '_plus')}"])
            for bucket in RETENTION_BUCKETS
        ]
        lines.append(
            f"| {row['model_display_name']} | " + " | ".join(values) + " |"
        )

    top = ranked[0]
    second = ranked[1]
    top_ci_overlaps = [
        row["model_display_name"]
        for row in ranked[1:]
        if float(row["gca15_ci95_low"])
        <= float(top["gca15_ci95_high"])
        and float(row["gca15_ci95_high"])
        >= float(top["gca15_ci95_low"])
    ]
    late_early_deltas = [
        float(row["gca15_late_minus_early"]) for row in rows
    ]
    lines.extend(
        [
            "",
            "## 해석",
            "",
            f"1. `{top['model_display_name']}`가 GCA@15 {_pct(top['gca15'])}로 "
            f"1위이고, `{second['model_display_name']}`가 "
            f"{_pct(second['gca15'])}로 뒤를 잇는다.",
            "2. 1위 모델과 95% CI가 겹치는 모델은 "
            + (", ".join(top_ci_overlaps) if top_ci_overlaps else "없다")
            + ". CI가 겹치는 모델 간 순위 차이는 통계적으로 확정하지 않는다.",
            f"3. Initial-copy는 Final State Accuracy가 {_pct(baseline_final)}지만 "
            f"GCA@15는 {_pct(baseline_gca)}다. 전체-state slot accuracy의 "
            "unchanged-path 부풀림이 GCA에서 크게 줄어든다.",
            f"4. {sum(delta < 0 for delta in late_early_deltas)}/{len(rows)}개 "
            "모델에서 late 구간 GCA가 early보다 낮고, Late-Early 변화 범위는 "
            f"{100 * min(late_early_deltas):+.2f}~"
            f"{100 * max(late_early_deltas):+.2f} pp다. "
            "checkpoint별 event 구성 차이가 섞이므로, 이를 context-length 효과로만 "
            "해석하지 않고 lag별 Retention과 함께 본다.",
            "5. GCA@15는 transition 적용 능력, Retention은 적용된 update의 장기 "
            "보존 능력을 측정한다. 두 지표를 분리함으로써 동일 오류의 반복 계수와 "
            "실제 memory decay를 구분한다.",
            "6. Exact Snapshot은 표준 DST의 Joint Goal Accuracy에 대응하는 엄격한 "
            "sanity check이며, 낮은 값 자체를 대표 성능으로 사용하지 않는다.",
            "",
            "## Headline에서 제외한 지표",
            "",
            "`Dynamic-path Final Accuracy`, checkpoint `Correct-change F1`, "
            "path-macro F1, Event Exact Update, Value/Status Accuracy는 오류 분석용 "
            "artifact에는 남기되 핵심 결과 표에서는 제외했다. 서로 강하게 중복되거나 "
            "고정 schema·희소 support에 민감해 독립적인 대표 결론을 추가하지 못하기 "
            "때문이다.",
            "",
            "전체 checkpoint별 GCA, GCA 구성요소와 C/W/M/O, parse/schema "
            "신뢰성, provenance는 동반 CSV에 보존한다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    rows, baseline = build_rows()
    output_dir = REPO_ROOT / "experiment" / "docs" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "stage2_gca15_results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    markdown_path = output_dir / "stage2_gca15_results.md"
    markdown_path.write_text(build_markdown(rows, baseline), encoding="utf-8")
    print(markdown_path)
    print(csv_path)


if __name__ == "__main__":
    main()
