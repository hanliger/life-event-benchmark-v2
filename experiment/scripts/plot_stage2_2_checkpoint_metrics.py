#!/usr/bin/env python3
"""Generate Stage 2.2 checkpoint comparison figures from canonical run outputs."""

from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


CHECKPOINTS = (60, 120, 180, 240, 300)
BASE_PLAN_ID = "f84f98315cc1fd165734bc601808e2d10bf3ad959ef36d51ec76c9ccdfef18cd"
GEMINI_RETRY_PLAN_ID = (
    "feac9e9cf1e1b7a53c8339b517db8f16618295bde1516dcbe04108cea8d23667"
)

MODEL_SPECS = (
    {
        "label": "Claude Opus 5",
        "method_id": "fc_claude_opus_5",
        "color": "#2672B8",
        "dash": "",
        "marker": "circle",
    },
    {
        "label": "Gemini 3.1 Pro Preview",
        "method_id": "fc_gemini_3_1_pro",
        "color": "#D97706",
        "dash": "10 6",
        "marker": "square",
    },
    {
        "label": "GPT-5.6 Sol",
        "method_id": "fc_gpt_5_6_sol",
        "color": "#4D7C0F",
        "dash": "12 5 3 5",
        "marker": "triangle",
    },
)

METRICS = (
    (
        "dynamic_path_final_state_accuracy",
        "Dynamic-path Final State Accuracy by Checkpoint",
        "dynamic_path_final_state_accuracy_by_checkpoint",
    ),
    (
        "correct_change_f1",
        "Correct-change F1 by Checkpoint",
        "correct_change_f1_by_checkpoint",
    ),
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Run output not found: {path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _validated_rows(rows: list[dict[str, Any]], method_id: str) -> dict[int, dict[str, Any]]:
    selected: dict[int, dict[str, Any]] = {}
    for row in rows:
        checkpoint = row.get("query_checkpoint")
        if checkpoint not in CHECKPOINTS:
            continue
        if row.get("method_id") != method_id:
            raise ValueError(
                f"Expected method_id={method_id}, found {row.get('method_id')}"
            )
        if row.get("trajectory_id") != "traj_010":
            raise ValueError("Figure inputs must contain only trajectory_id=traj_010")
        if row.get("parse_error") is not None:
            raise ValueError(
                f"{method_id} checkpoint {checkpoint} has parse_error="
                f"{row.get('parse_error')}"
            )
        if checkpoint in selected:
            raise ValueError(f"Duplicate {method_id} checkpoint: {checkpoint}")
        selected[checkpoint] = row
    return selected


def load_scores(
    repo_root: Path,
    *,
    plan_id: str,
    gemini_retry_plan_id: str | None,
) -> dict[str, dict[int, dict[str, float]]]:
    run_root = repo_root / "experiment" / "runs" / "paid_smoke"
    base = run_root / plan_id
    retry = run_root / gemini_retry_plan_id if gemini_retry_plan_id else None
    scores: dict[str, dict[int, dict[str, float]]] = {}

    for spec in MODEL_SPECS:
        method_id = str(spec["method_id"])
        base_path = base / f"{method_id}__canonical.jsonl"
        if method_id == "fc_gemini_3_1_pro" and retry is not None:
            # The original checkpoint-300 response was truncated at 12k output
            # tokens. Keep checkpoints 60--240 and replace only checkpoint 300
            # with the successful 20k-token confirmation run.
            original_rows = [
                row
                for row in _read_jsonl(base_path)
                if row.get("query_checkpoint") != 300
            ]
            retry_path = retry / f"{method_id}__canonical.jsonl"
            rows = original_rows + _read_jsonl(retry_path)
        else:
            rows = _read_jsonl(base_path)

        validated = _validated_rows(rows, method_id)
        missing = sorted(set(CHECKPOINTS) - set(validated))
        if missing:
            raise ValueError(f"{method_id} is missing checkpoints: {missing}")

        scores[method_id] = {}
        for checkpoint in CHECKPOINTS:
            metrics = validated[checkpoint].get("metrics", {})
            scores[method_id][checkpoint] = {}
            for metric_key, _, _ in METRICS:
                value = metrics.get(metric_key)
                if not isinstance(value, (int, float)):
                    raise ValueError(
                        f"{method_id} checkpoint {checkpoint} has no numeric "
                        f"{metric_key}"
                    )
                scores[method_id][checkpoint][metric_key] = float(value) * 100.0

    return scores


def _marker_svg(marker: str, x: float, y: float, color: str) -> str:
    common = f'fill="{color}" stroke="#FFFFFF" stroke-width="2"'
    if marker == "circle":
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" {common}/>'
    if marker == "square":
        return (
            f'<rect x="{x - 6:.1f}" y="{y - 6:.1f}" width="12" height="12" '
            f'rx="1" {common}/>'
        )
    if marker == "triangle":
        points = f"{x:.1f},{y - 7:.1f} {x - 7:.1f},{y + 6:.1f} {x + 7:.1f},{y + 6:.1f}"
        return f'<polygon points="{points}" {common}/>'
    raise ValueError(f"Unsupported marker: {marker}")


def build_svg(
    scores: dict[str, dict[int, dict[str, float]]],
    metric_key: str,
    title: str,
    subtitle: str,
) -> str:
    width, height = 1000, 650
    left, right, top, bottom = 105, 955, 160, 545
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
        "</style>",
        '<rect width="1000" height="650" fill="#FFFFFF"/>',
        f'<text x="500" y="42" text-anchor="middle" class="title">{html.escape(title)}</text>',
        (
            '<text x="500" y="70" text-anchor="middle" class="subtitle">'
            f"{html.escape(subtitle)}"
            "</text>"
        ),
    ]

    legend_x = (205, 445, 725)
    for x, spec in zip(legend_x, MODEL_SPECS, strict=True):
        dash = (
            f' stroke-dasharray="{spec["dash"]}"'
            if spec["dash"]
            else ""
        )
        parts.append(
            f'<line x1="{x}" y1="112" x2="{x + 38}" y2="112" '
            f'stroke="{spec["color"]}" stroke-width="3"{dash}/>'
        )
        parts.append(
            _marker_svg(
                str(spec["marker"]), x + 19, 112, str(spec["color"])
            )
        )
        parts.append(
            f'<text x="{x + 48}" y="117" class="legend">'
            f'{html.escape(str(spec["label"]))}</text>'
        )

    for tick in range(0, 101, 20):
        y = y_pos(float(tick))
        parts.extend(
            [
                (
                    f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" '
                    'stroke="#DDE3EA" stroke-width="1"/>'
                ),
                (
                    f'<text x="{left - 14}" y="{y + 5:.1f}" text-anchor="end" '
                    f'class="tick">{tick}%</text>'
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
                    f'<text x="{x:.1f}" y="{bottom + 28}" text-anchor="middle" '
                    f'class="tick">{checkpoint}</text>'
                ),
            ]
        )

    for spec in MODEL_SPECS:
        method_id = str(spec["method_id"])
        points = [
            (x_pos(index), y_pos(scores[method_id][checkpoint][metric_key]))
            for index, checkpoint in enumerate(CHECKPOINTS)
        ]
        point_text = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        dash = (
            f' stroke-dasharray="{spec["dash"]}"'
            if spec["dash"]
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
                    str(spec["marker"]), x, y, str(spec["color"])
                )
            )

    parts.extend(
        [
            (
                '<text x="530" y="618" text-anchor="middle" class="axis-label">'
                "Dialogue checkpoint (sessions)</text>"
            ),
            (
                '<text x="28" y="353" text-anchor="middle" class="axis-label" '
                'transform="rotate(-90 28 353)">Score (%)</text>'
            ),
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def write_source_csv(
    scores: dict[str, dict[int, dict[str, float]]],
    output_dir: Path,
    *,
    plan_id: str,
) -> Path:
    path = output_dir / "checkpoint_metric_values.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "model",
                "plan_sha256",
                "input_condition",
                "trajectory_id",
                "checkpoint",
                "dynamic_path_final_state_accuracy_pct",
                "correct_change_f1_pct",
            ]
        )
        for spec in MODEL_SPECS:
            method_id = str(spec["method_id"])
            for checkpoint in CHECKPOINTS:
                writer.writerow(
                    [
                        spec["label"],
                        plan_id,
                        "Full Context",
                        "traj_010",
                        checkpoint,
                        f"{scores[method_id][checkpoint]['dynamic_path_final_state_accuracy']:.6f}",
                        f"{scores[method_id][checkpoint]['correct_change_f1']:.6f}",
                    ]
                )
    return path


def render(
    svg_path: Path,
    output_format: str,
    *,
    png_width: int = 2000,
) -> Path:
    converter = shutil.which("rsvg-convert")
    if converter is None:
        raise RuntimeError(
            "rsvg-convert is required for PNG/PDF export; SVG was still generated"
        )
    output_path = svg_path.with_suffix(f".{output_format}")
    command = [converter, "--format", output_format]
    if output_format == "png":
        command.extend(["--width", str(png_width)])
    command.extend(["--output", str(output_path), str(svg_path)])
    subprocess.run(command, check=True)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root (default: inferred from this script)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Figure directory (default: "
            "experiment/docs/figures/stage2_2_traj010)"
        ),
    )
    parser.add_argument(
        "--plan-id",
        default=BASE_PLAN_ID,
        help="Paid-smoke plan SHA used as the figure source.",
    )
    parser.add_argument(
        "--gemini-retry-plan-id",
        help=(
            "Optional plan SHA supplying only Gemini checkpoint 300. "
            "The historical default run uses its recorded retry automatically."
        ),
    )
    parser.add_argument(
        "--version-label",
        help="Reader-facing version label included in chart subtitles.",
    )
    parser.add_argument(
        "--formats",
        default="svg,png,pdf",
        help="Comma-separated output formats: svg,png,pdf",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else repo_root
        / "experiment"
        / "docs"
        / "figures"
        / "stage2_2_traj010"
    )
    formats = {item.strip().lower() for item in args.formats.split(",")}
    unsupported = formats - {"svg", "png", "pdf"}
    if unsupported:
        raise ValueError(f"Unsupported formats: {sorted(unsupported)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    gemini_retry_plan_id = args.gemini_retry_plan_id
    if args.plan_id == BASE_PLAN_ID and gemini_retry_plan_id is None:
        gemini_retry_plan_id = GEMINI_RETRY_PLAN_ID
    version_label = args.version_label or f"plan {args.plan_id[:12]}"
    subtitle = (
        f"Full Context · traj_010 · {version_label} · "
        "5 anchor checkpoints · n = 1 trajectory"
    )
    scores = load_scores(
        repo_root,
        plan_id=args.plan_id,
        gemini_retry_plan_id=gemini_retry_plan_id,
    )
    generated: list[Path] = [
        write_source_csv(scores, output_dir, plan_id=args.plan_id)
    ]
    for metric_key, title, stem in METRICS:
        svg_path = output_dir / f"{stem}.svg"
        svg_path.write_text(
            build_svg(scores, metric_key, title, subtitle), encoding="utf-8"
        )
        generated.append(svg_path)
        for output_format in ("png", "pdf"):
            if output_format in formats:
                generated.append(render(svg_path, output_format))

    for path in generated:
        print(path.relative_to(repo_root))


if __name__ == "__main__":
    main()
