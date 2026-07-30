#!/usr/bin/env python3
"""Plot per-trajectory and macro-average Stage 2.2 checkpoint metrics."""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from statistics import mean
from typing import Any

from plot_stage2_2_checkpoint_metrics import (
    CHECKPOINTS,
    METRICS,
    MODEL_SPECS,
    render,
)


TRAJECTORY_SPECS = (
    {
        "trajectory_id": "traj_002",
        "color": "#2672B8",
        "dash": "",
        "marker": "circle",
    },
    {
        "trajectory_id": "traj_003",
        "color": "#D97706",
        "dash": "9 5",
        "marker": "square",
    },
    {
        "trajectory_id": "traj_010",
        "color": "#4D7C0F",
        "dash": "12 4 3 4",
        "marker": "triangle",
    },
    {
        "trajectory_id": "macro_average",
        "color": "#17202A",
        "dash": "",
        "marker": "diamond",
    },
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_scores(
    repo_root: Path,
    plans: dict[str, str],
) -> tuple[
    dict[str, dict[str, dict[int, dict[str, float]]]],
    set[tuple[str, str, int]],
]:
    run_root = repo_root / "experiment" / "runs" / "paid_smoke"
    scores: dict[str, dict[str, dict[int, dict[str, float]]]] = {}
    parse_failures: set[tuple[str, str, int]] = set()
    for trajectory_id, plan in plans.items():
        scores[trajectory_id] = {}
        for model in MODEL_SPECS:
            method_id = str(model["method_id"])
            path = run_root / plan / f"{method_id}__canonical.jsonl"
            rows = _read_jsonl(path)
            selected = {
                int(row["query_checkpoint"]): row
                for row in rows
                if row["trajectory_id"] == trajectory_id
            }
            if set(selected) != set(CHECKPOINTS):
                raise ValueError(
                    f"{trajectory_id}/{method_id}: incomplete checkpoints"
                )
            scores[trajectory_id][method_id] = {}
            for checkpoint in CHECKPOINTS:
                row = selected[checkpoint]
                if row.get("parse_error") is not None:
                    parse_failures.add(
                        (trajectory_id, method_id, checkpoint)
                    )
                metrics = row.get("metrics") or {}
                scores[trajectory_id][method_id][checkpoint] = {}
                for metric_key, _, _ in METRICS:
                    value = metrics.get(metric_key)
                    if not isinstance(value, (int, float)):
                        raise ValueError(
                            f"{trajectory_id}/{method_id}/{checkpoint}: "
                            f"missing {metric_key}"
                        )
                    scores[trajectory_id][method_id][checkpoint][
                        metric_key
                    ] = float(value) * 100.0

    scores["macro_average"] = {}
    for model in MODEL_SPECS:
        method_id = str(model["method_id"])
        scores["macro_average"][method_id] = {}
        for checkpoint in CHECKPOINTS:
            scores["macro_average"][method_id][checkpoint] = {}
            for metric_key, _, _ in METRICS:
                scores["macro_average"][method_id][checkpoint][
                    metric_key
                ] = mean(
                    scores[trajectory_id][method_id][checkpoint][metric_key]
                    for trajectory_id in plans
                )
    return scores, parse_failures


def _marker_svg(
    marker: str,
    x: float,
    y: float,
    color: str,
    *,
    size: float = 5.5,
) -> str:
    common = f'fill="#FFFFFF" stroke="{color}" stroke-width="2.5"'
    if marker == "circle":
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{size}" {common}/>'
    if marker == "square":
        return (
            f'<rect x="{x - size:.1f}" y="{y - size:.1f}" '
            f'width="{size * 2:.1f}" height="{size * 2:.1f}" '
            f'rx="1" {common}/>'
        )
    if marker == "triangle":
        points = (
            f"{x:.1f},{y - size - 1:.1f} "
            f"{x - size - 1:.1f},{y + size:.1f} "
            f"{x + size + 1:.1f},{y + size:.1f}"
        )
        return f'<polygon points="{points}" {common}/>'
    if marker == "diamond":
        points = (
            f"{x:.1f},{y - size - 1:.1f} "
            f"{x - size - 1:.1f},{y:.1f} "
            f"{x:.1f},{y + size + 1:.1f} "
            f"{x + size + 1:.1f},{y:.1f}"
        )
        return f'<polygon points="{points}" {common}/>'
    raise ValueError(f"unsupported marker: {marker}")


def build_svg(
    scores: dict[str, dict[str, dict[int, dict[str, float]]]],
    parse_failures: set[tuple[str, str, int]],
    metric_key: str,
    title: str,
) -> str:
    width, height = 1500, 700
    top, bottom = 205, 590
    panel_bounds = ((100, 460), (570, 930), (1040, 1400))
    plot_height = bottom - top

    def y_pos(score: float) -> float:
        return bottom - score / 100.0 * plot_height

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{html.escape(title)}">'
        ),
        "<style>",
        "text { font-family: Arial, 'DejaVu Sans', sans-serif; fill: #17202A; }",
        ".title { font-size: 28px; font-weight: 700; }",
        ".subtitle { font-size: 15px; fill: #5B6573; }",
        ".panel-title { font-size: 18px; font-weight: 700; }",
        ".axis-label { font-size: 16px; font-weight: 600; }",
        ".tick { font-size: 13px; fill: #4B5563; }",
        ".legend { font-size: 14px; font-weight: 600; }",
        ".failure { font-size: 12px; font-weight: 700; fill: #B42318; }",
        "</style>",
        '<rect width="1500" height="700" fill="#FFFFFF"/>',
        (
            f'<text x="750" y="42" text-anchor="middle" class="title">'
            f"{html.escape(title)}</text>"
        ),
        (
            '<text x="750" y="70" text-anchor="middle" class="subtitle">'
            "Low reasoning · Full Context · 5 anchor checkpoints · "
            "trajectory-macro average</text>"
        ),
    ]

    legend_x = (255, 520, 785, 1065)
    for x, spec in zip(legend_x, TRAJECTORY_SPECS, strict=True):
        dash = (
            f' stroke-dasharray="{spec["dash"]}"'
            if spec["dash"]
            else ""
        )
        width_px = 4 if spec["trajectory_id"] == "macro_average" else 2.7
        parts.append(
            f'<line x1="{x}" y1="120" x2="{x + 46}" y2="120" '
            f'stroke="{spec["color"]}" stroke-width="{width_px}"{dash}/>'
        )
        parts.append(
            _marker_svg(
                str(spec["marker"]),
                x + 23,
                120,
                str(spec["color"]),
                size=6 if spec["trajectory_id"] == "macro_average" else 5,
            )
        )
        label = (
            "3-trajectory average"
            if spec["trajectory_id"] == "macro_average"
            else str(spec["trajectory_id"])
        )
        parts.append(
            f'<text x="{x + 56}" y="125" class="legend">'
            f"{html.escape(label)}</text>"
        )

    for panel_index, (left, right) in enumerate(panel_bounds):
        method_id = str(MODEL_SPECS[panel_index]["method_id"])
        model_label = str(MODEL_SPECS[panel_index]["label"])
        plot_width = right - left

        def x_pos(index: int) -> float:
            return left + index * plot_width / (len(CHECKPOINTS) - 1)

        parts.append(
            f'<text x="{(left + right) / 2:.1f}" y="177" '
            f'text-anchor="middle" class="panel-title">'
            f"{html.escape(model_label)}</text>"
        )
        for tick in range(0, 101, 20):
            y = y_pos(float(tick))
            parts.append(
                f'<line x1="{left}" y1="{y:.1f}" x2="{right}" '
                f'y2="{y:.1f}" stroke="#DDE3EA" stroke-width="1"/>'
            )
            if panel_index == 0:
                parts.append(
                    f'<text x="{left - 12}" y="{y + 5:.1f}" '
                    f'text-anchor="end" class="tick">{tick}%</text>'
                )
        parts.extend(
            [
                (
                    f'<line x1="{left}" y1="{top}" x2="{left}" '
                    f'y2="{bottom}" stroke="#7B8794" stroke-width="1.2"/>'
                ),
                (
                    f'<line x1="{left}" y1="{bottom}" x2="{right}" '
                    f'y2="{bottom}" stroke="#7B8794" stroke-width="1.2"/>'
                ),
            ]
        )
        for index, checkpoint in enumerate(CHECKPOINTS):
            x = x_pos(index)
            parts.extend(
                [
                    (
                        f'<line x1="{x:.1f}" y1="{bottom}" x2="{x:.1f}" '
                        f'y2="{bottom + 5}" stroke="#7B8794" '
                        'stroke-width="1"/>'
                    ),
                    (
                        f'<text x="{x:.1f}" y="{bottom + 25}" '
                        f'text-anchor="middle" class="tick">{checkpoint}</text>'
                    ),
                ]
            )

        for spec in TRAJECTORY_SPECS:
            trajectory_id = str(spec["trajectory_id"])
            points = [
                (
                    x_pos(index),
                    y_pos(
                        scores[trajectory_id][method_id][checkpoint][
                            metric_key
                        ]
                    ),
                )
                for index, checkpoint in enumerate(CHECKPOINTS)
            ]
            point_text = " ".join(
                f"{x:.1f},{y:.1f}" for x, y in points
            )
            dash = (
                f' stroke-dasharray="{spec["dash"]}"'
                if spec["dash"]
                else ""
            )
            width_px = 4 if trajectory_id == "macro_average" else 2.7
            parts.append(
                f'<polyline points="{point_text}" fill="none" '
                f'stroke="{spec["color"]}" stroke-width="{width_px}" '
                f'stroke-linejoin="round" stroke-linecap="round"{dash}/>'
            )
            for x, y in points:
                parts.append(
                    _marker_svg(
                        str(spec["marker"]),
                        x,
                        y,
                        str(spec["color"]),
                        size=6 if trajectory_id == "macro_average" else 5,
                    )
                )

        for trajectory_id, failed_method, checkpoint in parse_failures:
            if failed_method != method_id:
                continue
            x = x_pos(CHECKPOINTS.index(checkpoint))
            y = y_pos(
                scores[trajectory_id][method_id][checkpoint][metric_key]
            )
            parts.extend(
                [
                    (
                        f'<line x1="{x - 7:.1f}" y1="{y - 7:.1f}" '
                        f'x2="{x + 7:.1f}" y2="{y + 7:.1f}" '
                        'stroke="#B42318" stroke-width="3"/>'
                    ),
                    (
                        f'<line x1="{x - 7:.1f}" y1="{y + 7:.1f}" '
                        f'x2="{x + 7:.1f}" y2="{y - 7:.1f}" '
                        'stroke="#B42318" stroke-width="3"/>'
                    ),
                    (
                        f'<text x="{x:.1f}" y="{y - 14:.1f}" '
                        'text-anchor="middle" class="failure">'
                        "parse failure</text>"
                    ),
                ]
            )

    parts.extend(
        [
            (
                '<text x="750" y="672" text-anchor="middle" '
                'class="axis-label">Dialogue checkpoint (sessions)</text>'
            ),
            (
                '<text x="25" y="400" text-anchor="middle" '
                'class="axis-label" transform="rotate(-90 25 400)">'
                "Score (%)</text>"
            ),
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def write_source_csv(
    scores: dict[str, dict[str, dict[int, dict[str, float]]]],
    parse_failures: set[tuple[str, str, int]],
    plans: dict[str, str],
    output_dir: Path,
) -> Path:
    path = output_dir / "checkpoint_metric_values.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "model",
                "trajectory_id",
                "plan_sha256",
                "checkpoint",
                "dynamic_path_final_state_accuracy_pct",
                "correct_change_f1_pct",
                "parse_error",
            ]
        )
        for model in MODEL_SPECS:
            method_id = str(model["method_id"])
            for spec in TRAJECTORY_SPECS:
                trajectory_id = str(spec["trajectory_id"])
                plan = plans.get(trajectory_id, "trajectory_macro_average")
                for checkpoint in CHECKPOINTS:
                    values = scores[trajectory_id][method_id][checkpoint]
                    dynamic_accuracy = values[
                        "dynamic_path_final_state_accuracy"
                    ]
                    parse_error = any(
                        failure
                        == (trajectory_id, method_id, checkpoint)
                        for failure in parse_failures
                    )
                    writer.writerow(
                        [
                            model["label"],
                            trajectory_id,
                            plan,
                            checkpoint,
                            f"{dynamic_accuracy:.6f}",
                            f"{values['correct_change_f1']:.6f}",
                            "invalid_json_or_missing_state"
                            if parse_error
                            else "",
                        ]
                    )
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traj-002-plan", required=True)
    parser.add_argument("--traj-003-plan", required=True)
    parser.add_argument("--traj-010-plan", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plans = {
        "traj_002": args.traj_002_plan,
        "traj_003": args.traj_003_plan,
        "traj_010": args.traj_010_plan,
    }
    scores, parse_failures = load_scores(repo_root, plans)
    generated = [
        write_source_csv(
            scores,
            parse_failures,
            plans,
            output_dir,
        )
    ]
    for metric_key, title, stem in METRICS:
        svg_path = output_dir / f"{stem}.svg"
        svg_path.write_text(
            build_svg(scores, parse_failures, metric_key, title),
            encoding="utf-8",
        )
        generated.extend(
            [
                svg_path,
                render(svg_path, "png"),
                render(svg_path, "pdf"),
            ]
        )
    for path in generated:
        print(path.relative_to(repo_root))


if __name__ == "__main__":
    main()
