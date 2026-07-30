#!/usr/bin/env python3
"""Plot medium-versus-low Stage 2.2 checkpoint metrics."""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path

from plot_stage2_2_checkpoint_metrics import (
    CHECKPOINTS,
    METRICS,
    MODEL_SPECS,
    load_scores,
    render,
)


POLICIES = (
    {
        "key": "medium",
        "label": "Medium",
        "dash": "",
        "filled": True,
    },
    {
        "key": "low",
        "label": "Low",
        "dash": "9 6",
        "filled": False,
    },
)


def _marker_svg(
    marker: str,
    x: float,
    y: float,
    color: str,
    *,
    filled: bool,
) -> str:
    fill = color if filled else "#FFFFFF"
    common = f'fill="{fill}" stroke="{color}" stroke-width="2.4"'
    if marker == "circle":
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" {common}/>'
    if marker == "square":
        return (
            f'<rect x="{x - 6:.1f}" y="{y - 6:.1f}" width="12" '
            f'height="12" rx="1" {common}/>'
        )
    if marker == "triangle":
        points = (
            f"{x:.1f},{y - 7:.1f} {x - 7:.1f},{y + 6:.1f} "
            f"{x + 7:.1f},{y + 6:.1f}"
        )
        return f'<polygon points="{points}" {common}/>'
    raise ValueError(f"Unsupported marker: {marker}")


def build_svg(
    score_sets: dict[str, dict[str, dict[int, dict[str, float]]]],
    metric_key: str,
    title: str,
    *,
    medium_plan: str,
    low_plan: str,
) -> str:
    width, height = 1000, 700
    left, right, top, bottom = 105, 955, 210, 590
    plot_width, plot_height = right - left, bottom - top

    def x_pos(index: int) -> float:
        return left + index * plot_width / (len(CHECKPOINTS) - 1)

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
        ".title { font-size: 26px; font-weight: 700; }",
        ".subtitle { font-size: 15px; fill: #5B6573; }",
        ".axis-label { font-size: 16px; font-weight: 600; }",
        ".tick { font-size: 14px; fill: #4B5563; }",
        ".legend { font-size: 14px; font-weight: 600; }",
        ".legend-label { font-size: 13px; fill: #4B5563; }",
        "</style>",
        '<rect width="1000" height="700" fill="#FFFFFF"/>',
        (
            f'<text x="500" y="42" text-anchor="middle" class="title">'
            f"{html.escape(title)}</text>"
        ),
        (
            '<text x="500" y="70" text-anchor="middle" class="subtitle">'
            "Full Context · traj_010 · 5 anchor checkpoints · "
            "n = 1 trajectory</text>"
        ),
    ]

    legend_x = (205, 445, 725)
    for x, spec in zip(legend_x, MODEL_SPECS, strict=True):
        parts.append(
            f'<line x1="{x}" y1="108" x2="{x + 38}" y2="108" '
            f'stroke="{spec["color"]}" stroke-width="3"/>'
        )
        parts.append(
            _marker_svg(
                str(spec["marker"]),
                x + 19,
                108,
                str(spec["color"]),
                filled=True,
            )
        )
        parts.append(
            f'<text x="{x + 48}" y="113" class="legend">'
            f'{html.escape(str(spec["label"]))}</text>'
        )

    policy_x = (275, 585)
    for x, policy, plan in zip(
        policy_x,
        POLICIES,
        (medium_plan, low_plan),
        strict=True,
    ):
        dash = (
            f' stroke-dasharray="{policy["dash"]}"'
            if policy["dash"]
            else ""
        )
        parts.append(
            f'<line x1="{x}" y1="155" x2="{x + 44}" y2="155" '
            f'stroke="#34495E" stroke-width="3"{dash}/>'
        )
        parts.append(
            _marker_svg(
                "circle",
                x + 22,
                155,
                "#34495E",
                filled=bool(policy["filled"]),
            )
        )
        parts.append(
            f'<text x="{x + 54}" y="153" class="legend">'
            f'{policy["label"]}</text>'
        )
        parts.append(
            f'<text x="{x + 54}" y="171" class="legend-label">'
            f'plan {plan[:12]}</text>'
        )

    for tick in range(0, 101, 20):
        y = y_pos(float(tick))
        parts.extend(
            [
                (
                    f'<line x1="{left}" y1="{y:.1f}" x2="{right}" '
                    f'y2="{y:.1f}" stroke="#DDE3EA" stroke-width="1"/>'
                ),
                (
                    f'<text x="{left - 14}" y="{y + 5:.1f}" '
                    f'text-anchor="end" class="tick">{tick}%</text>'
                ),
            ]
        )

    parts.extend(
        [
            (
                f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" '
                'stroke="#7B8794" stroke-width="1.3"/>'
            ),
            (
                f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" '
                'stroke="#7B8794" stroke-width="1.3"/>'
            ),
        ]
    )
    for index, checkpoint in enumerate(CHECKPOINTS):
        x = x_pos(index)
        parts.extend(
            [
                (
                    f'<line x1="{x:.1f}" y1="{bottom}" x2="{x:.1f}" '
                    f'y2="{bottom + 6}" stroke="#7B8794" stroke-width="1"/>'
                ),
                (
                    f'<text x="{x:.1f}" y="{bottom + 28}" '
                    f'text-anchor="middle" class="tick">{checkpoint}</text>'
                ),
            ]
        )

    for spec in MODEL_SPECS:
        method_id = str(spec["method_id"])
        for policy in POLICIES:
            policy_key = str(policy["key"])
            points = [
                (
                    x_pos(index),
                    y_pos(
                        score_sets[policy_key][method_id][checkpoint][
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
                f' stroke-dasharray="{policy["dash"]}"'
                if policy["dash"]
                else ""
            )
            parts.append(
                f'<polyline points="{point_text}" fill="none" '
                f'stroke="{spec["color"]}" stroke-width="3" '
                f'stroke-linejoin="round" stroke-linecap="round"{dash}/>'
            )
            for x, y in points:
                parts.append(
                    _marker_svg(
                        str(spec["marker"]),
                        x,
                        y,
                        str(spec["color"]),
                        filled=bool(policy["filled"]),
                    )
                )

    parts.extend(
        [
            (
                '<text x="530" y="668" text-anchor="middle" '
                'class="axis-label">Dialogue checkpoint (sessions)</text>'
            ),
            (
                '<text x="28" y="400" text-anchor="middle" '
                'class="axis-label" transform="rotate(-90 28 400)">'
                "Score (%)</text>"
            ),
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def write_source_csv(
    score_sets: dict[str, dict[str, dict[int, dict[str, float]]]],
    output_dir: Path,
    *,
    medium_plan: str,
    low_plan: str,
) -> Path:
    path = output_dir / "checkpoint_metric_values.csv"
    plans = {"medium": medium_plan, "low": low_plan}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "model",
                "reasoning_level",
                "plan_sha256",
                "trajectory_id",
                "checkpoint",
                "dynamic_path_final_state_accuracy_pct",
                "correct_change_f1_pct",
            ]
        )
        for spec in MODEL_SPECS:
            method_id = str(spec["method_id"])
            for policy in POLICIES:
                key = str(policy["key"])
                for checkpoint in CHECKPOINTS:
                    values = score_sets[key][method_id][checkpoint]
                    dynamic_accuracy = values[
                        "dynamic_path_final_state_accuracy"
                    ]
                    writer.writerow(
                        [
                            spec["label"],
                            policy["label"],
                            plans[key],
                            "traj_010",
                            checkpoint,
                            f"{dynamic_accuracy:.6f}",
                            f"{values['correct_change_f1']:.6f}",
                        ]
                    )
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--medium-plan", required=True)
    parser.add_argument("--low-plan", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    score_sets = {
        "medium": load_scores(
            repo_root,
            plan_id=args.medium_plan,
            gemini_retry_plan_id=None,
        ),
        "low": load_scores(
            repo_root,
            plan_id=args.low_plan,
            gemini_retry_plan_id=None,
        ),
    }
    generated = [
        write_source_csv(
            score_sets,
            output_dir,
            medium_plan=args.medium_plan,
            low_plan=args.low_plan,
        )
    ]
    for metric_key, title, stem in METRICS:
        svg_path = output_dir / f"{stem}.svg"
        svg_path.write_text(
            build_svg(
                score_sets,
                metric_key,
                title,
                medium_plan=args.medium_plan,
                low_plan=args.low_plan,
            ),
            encoding="utf-8",
        )
        generated.append(svg_path)
        generated.append(render(svg_path, "png"))
        generated.append(render(svg_path, "pdf"))
    for path in generated:
        print(path.relative_to(repo_root))


if __name__ == "__main__":
    main()
