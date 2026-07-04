"""Visualization and text summaries for generated life paths."""

from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path

from .models import EpisodeTemplate, GeneratedLifePath, TimelineEvent
from .rules import event_registry
from .sampler import sample_life_path
from .templates import EPISODE_TEMPLATES


LANES = {
    "relationship": 120,
    "career": 220,
    "residence": 320,
    "accident": 420,
}

EPISODE_PALETTE = (
    "#2563eb",
    "#16a34a",
    "#9333ea",
    "#dc2626",
    "#0891b2",
    "#ca8a04",
    "#db2777",
    "#475569",
    "#65a30d",
    "#7c3aed",
)


def write_visualizations(
    *,
    output_dir: Path,
    seed: int = 42,
    episode_count: int = 6,
    path: GeneratedLifePath | None = None,
    sample_stem: str | None = None,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_dir = output_dir / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)

    core_subgraphs_md = output_dir / "core_subgraphs.md"
    core_subgraphs_md.write_text(core_subgraphs_to_markdown(), encoding="utf-8")

    generated = path or sample_life_path(seed=seed, episode_count=episode_count)
    stem = sample_stem or f"sample_seed_{generated.seed}"
    sample_html = sample_dir / f"{stem}.html"
    sample_svg = sample_dir / f"{stem}.svg"
    sample_dot = sample_dir / f"{stem}.dot"
    sample_png = sample_dir / f"{stem}.png"
    sample_html.write_text(sample_to_html(generated), encoding="utf-8")
    sample_svg.write_text(sample_to_svg(generated), encoding="utf-8")
    sample_dot.write_text(sample_to_dot(generated), encoding="utf-8")
    _render_png(sample_svg, sample_png)

    index_path = output_dir / "index.html"
    _write_index(output_dir, core_subgraphs_md)
    return {
        "index": index_path,
        "core_subgraphs_md": core_subgraphs_md,
        "sample_page": sample_html,
        "sample_svg": sample_svg,
        "sample_dot": sample_dot,
        "sample_png": sample_png,
    }


def write_multiple_sample_visualizations(
    *,
    output_dir: Path,
    seed: int = 42,
    episode_count: int = 6,
    sample_count: int = 5,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    core_subgraphs_md = output_dir / "core_subgraphs.md"
    core_subgraphs_md.write_text(core_subgraphs_to_markdown(), encoding="utf-8")

    sample_pages: list[Path] = []
    sample_pngs: list[Path] = []
    for offset in range(sample_count):
        generated = sample_life_path(seed=seed + offset, episode_count=episode_count)
        paths = write_visualizations(
            output_dir=output_dir,
            seed=generated.seed,
            episode_count=episode_count,
            path=generated,
            sample_stem=f"sample_seed_{generated.seed}",
        )
        sample_pages.append(paths["sample_page"])
        sample_pngs.append(paths["sample_png"])

    index_path = output_dir / "index.html"
    _write_index(output_dir, core_subgraphs_md)
    return {
        "index": index_path,
        "core_subgraphs_md": core_subgraphs_md,
        "sample_pages": sample_pages,
        "sample_pngs": sample_pngs,
    }


def core_subgraphs_to_markdown() -> str:
    registry = event_registry()
    lines = [
        "# Core Subgraphs",
        "",
        "각 core_subgraph는 한 사람의 life path에 삽입될 수 있는 핵심 event-order motif다.",
        "시간 구간은 이전 event 이후 몇 년 뒤에 다음 event가 발생하는지를 뜻한다.",
        "",
    ]
    for template in EPISODE_TEMPLATES:
        lines.extend(
            [
                f"## {template.name}",
                "",
                f"- id: `{template.id}`",
                f"- domain: `{template.domain}`",
                f"- kind: `{template.kind}`",
                f"- start age: `{template.start_age_range[0]}-{template.start_age_range[1]}`",
                f"- sampling weight: `{template.sampling_weight}`",
            ]
        )
        if template.notes:
            lines.append(f"- note: {template.notes}")
        lines.extend(["", "```text"])
        for index, event_id in enumerate(template.event_ids):
            event = registry[event_id]
            actor = template.actors[index] if template.actors else "self"
            child_age = _child_age_label(template, event_id)
            actions = ",".join(event.actions) if event.actions else "-"
            suffix = f" [{actor}]"
            if child_age:
                suffix += f" child_age={child_age}"
            suffix += f" actions={actions}"
            if index == 0:
                lines.append(f"{event.name}{suffix}")
            else:
                min_gap, max_gap = template.gap_ranges[index - 1]
                gap = f"+{min_gap}y" if min_gap == max_gap else f"+{min_gap}-{max_gap}y"
                lines.append(f"  -> {event.name}{suffix} ({gap})")
        lines.extend(["```", ""])
    return "\n".join(lines)


def sample_to_svg(path: GeneratedLifePath) -> str:
    events = list(path.events)
    if not events:
        return '<svg viewBox="0 0 900 180"><text x="24" y="40">No events</text></svg>\n'

    episode_ids = _episode_ids(path)
    min_age = min(event.age for event in events)
    max_age = max(event.age for event in events)
    age_span = max(1, max_age - min_age)
    scale = max(28, min(54, 900 // age_span))
    left = 230
    right = 50
    top = 200
    lane_gap = 124
    axis_y = top + len(episode_ids) * lane_gap + 28
    width = max(980, left + right + (max_age - min_age + 2) * scale)
    height = axis_y + 72
    colors = _episode_colors(path)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="sample life path timeline">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#f8fafc" />',
        '<text x="24" y="36" font-size="20" font-weight="700">Sample Life Path Timeline</text>',
        '<text x="24" y="58" font-size="12" fill="#475569">x-axis: age in years, y-axis: sampled core_subgraph, edge color: sampled core_subgraph</text>',
    ]

    lanes = _episode_lanes(path, top, lane_gap)
    for episode_id in episode_ids:
        y = lanes[episode_id]
        color = colors.get(episode_id, "#64748b")
        label = _episode_label(episode_id)
        parts.append(f'<line x1="{left}" y1="{y}" x2="{width - right}" y2="{y}" stroke="#cbd5e1" />')
        parts.append(f'<circle cx="{left - 18}" cy="{y}" r="5" fill="{color}" />')
        parts.append(f'<text x="24" y="{y - 4}" font-size="13" font-weight="700">{html.escape(label)}</text>')
        parts.append(f'<text x="24" y="{y + 14}" font-size="10" fill="#64748b">{html.escape(episode_id)}</text>')

    first_tick = min_age - (min_age % 5)
    for age in range(first_tick, max_age + 6, 5):
        x = _x_for_age(age, min_age, left, scale)
        parts.append(f'<line x1="{x}" y1="82" x2="{x}" y2="{axis_y - 10}" stroke="#e2e8f0" />')
        parts.append(f'<text x="{x}" y="{axis_y + 15}" text-anchor="middle" font-size="11" fill="#475569">{age}</text>')

    positions = _layout_points(events, min_age, left, scale, lanes)
    label_offsets = _label_offsets(events, positions)
    subtitle_visibility = _subtitle_visibility(events, positions)

    for episode_id in path.selected_episode_ids:
        episode_events = [event for event in events if event.episode_id == episode_id]
        color = colors.get(episode_id, "#64748b")
        for prev, curr in zip(episode_events, episode_events[1:]):
            parts.append(_svg_edge(positions[id(prev)], positions[id(curr)], color))

    for event in events:
        x, y = positions[id(event)]
        color = colors.get(event.episode_id, "#64748b")
        subtitle = f"age {event.age}"
        if event.child_age is not None:
            subtitle += f" / child {event.child_age}"
        parts.append(_svg_node(x, y, event.name, subtitle, color, label_offsets[id(event)], subtitle_visibility[id(event)]))

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def sample_to_html(path: GeneratedLifePath) -> str:
    rows = "\n".join(
        f"<tr><td>{event.age}</td><td>{html.escape(event.actor)}</td><td>{'' if event.child_age is None else event.child_age}</td><td>{html.escape(event.domain)}</td><td>{html.escape(event.name)}</td><td><code>{html.escape(event.episode_id)}</code></td></tr>"
        for event in path.events
    )
    rejections = "\n".join(
        f"<tr><td><code>{html.escape(item.episode_id)}</code></td><td>{html.escape(item.reason)}</td><td>{html.escape(item.conflicted_event_id or '')}</td><td>{'' if item.age is None else item.age}</td></tr>"
        for item in path.rejections
    )
    return _html_page(
        title=f"Sample life path seed={path.seed}",
        body=f"""
        <p><a href="../index.html">index</a> · <a href="../core_subgraphs.md">core_subgraphs.md</a></p>
        {sample_to_svg(path)}
        <h2>Timeline</h2>
        <table><thead><tr><th>Age</th><th>Actor</th><th>Child Age</th><th>Event Domain</th><th>Event</th><th>Core Subgraph</th></tr></thead><tbody>{rows}</tbody></table>
        <h2>Rejected Core Subgraphs</h2>
        <table><thead><tr><th>Core Subgraph</th><th>Reason</th><th>Event</th><th>Age</th></tr></thead><tbody>{rejections}</tbody></table>
        """,
    )


def sample_to_dot(path: GeneratedLifePath) -> str:
    events = list(path.events)
    if not events:
        return "digraph sample_life_path {}\n"
    min_age = min(event.age for event in events)
    colors = _episode_colors(path)
    episode_order = {episode_id: index for index, episode_id in enumerate(_episode_ids(path))}
    lines = [
        "digraph sample_life_path {",
        "  graph [layout=neato, overlap=false, splines=true, outputorder=edgesfirst];",
        "  node [shape=plain, fontname=\"Noto Sans CJK KR\"];",
        "  edge [penwidth=2.0];",
    ]
    for index, event in enumerate(events):
        x, y = _dot_point(event, min_age, episode_order)
        color = colors.get(event.episode_id, "#64748b")
        label = f"{event.name}\\n{event.age}"
        lines.append(f'  "event_{index}" [label="{label}", pos="{x},{y}!", fontcolor="{color}"];')
    event_index = {id(event): index for index, event in enumerate(events)}
    for episode_id in path.selected_episode_ids:
        episode_events = [event for event in events if event.episode_id == episode_id]
        color = colors.get(episode_id, "#64748b")
        for prev, curr in zip(episode_events, episode_events[1:]):
            lines.append(f'  "event_{event_index[id(prev)]}" -> "event_{event_index[id(curr)]}" [color="{color}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _child_age_label(template: EpisodeTemplate, event_id: str) -> str:
    for candidate_id, (min_age, max_age) in template.child_age_by_event:
        if candidate_id == event_id:
            return str(min_age) if min_age == max_age else f"{min_age}-{max_age}"
    return ""


def _episode_colors(path: GeneratedLifePath) -> dict[str, str]:
    ids = _episode_ids(path)
    return {episode_id: EPISODE_PALETTE[index % len(EPISODE_PALETTE)] for index, episode_id in enumerate(ids)}


def _episode_ids(path: GeneratedLifePath) -> list[str]:
    ids = []
    for event in path.events:
        if event.episode_id not in ids:
            ids.append(event.episode_id)
    return ids


def _episode_lanes(path: GeneratedLifePath, top: int, lane_gap: int) -> dict[str, int]:
    return {episode_id: top + index * lane_gap for index, episode_id in enumerate(_episode_ids(path))}


def _episode_label(episode_id: str) -> str:
    templates = {template.id: template for template in EPISODE_TEMPLATES}
    source_id = episode_id.split("#", 1)[0]
    label = templates[source_id].name if source_id in templates else episode_id
    if "#" in episode_id:
        return f"{label} ({episode_id.rsplit('#', 1)[1]})"
    return label


def _x_for_age(age: int, min_age: int, left: int, scale: int) -> int:
    return left + (age - min_age + 1) * scale


def _layout_points(
    events: list[TimelineEvent],
    min_age: int,
    left: int,
    scale: int,
    lanes: dict[str, int],
) -> dict[int, tuple[int, int]]:
    positions: dict[int, tuple[int, int]] = {}
    for event in sorted(events, key=lambda item: (item.episode_id, item.age, item.name)):
        positions[id(event)] = (_x_for_age(event.age, min_age, left, scale), lanes[event.episode_id])
    return positions


def _label_offsets(events: list[TimelineEvent], positions: dict[int, tuple[int, int]]) -> dict[int, int]:
    offsets: dict[int, int] = {}
    by_lane: dict[tuple[str, int], list[TimelineEvent]] = {}
    for event in events:
        _, y = positions[id(event)]
        by_lane.setdefault((event.episode_id, y), []).append(event)

    offset_cycle = (-22, -48, -74, -100)
    for lane_events in by_lane.values():
        occupied: list[tuple[int, int, int]] = []
        for event in sorted(lane_events, key=lambda item: (item.age, item.name)):
            x, _ = positions[id(event)]
            width = _label_width(event.name)
            chosen = offset_cycle[0]
            for offset in offset_cycle:
                if all(
                    abs(x - other_x) >= (width + other_width) // 2 + 10 or abs(offset - other_offset) >= 24
                    for other_x, other_offset, other_width in occupied
                ):
                    chosen = offset
                    break
            occupied.append((x, chosen, width))
            offsets[id(event)] = chosen
    return offsets


def _subtitle_visibility(events: list[TimelineEvent], positions: dict[int, tuple[int, int]]) -> dict[int, bool]:
    visibility: dict[int, bool] = {}
    seen: set[tuple[str, int, int]] = set()
    for event in sorted(events, key=lambda item: (item.episode_id, item.age, item.name)):
        _, y = positions[id(event)]
        key = (event.episode_id, y, event.age)
        visibility[id(event)] = key not in seen
        seen.add(key)
    return visibility


def _dot_point(event: TimelineEvent, min_age: int, episode_order: dict[str, int]) -> tuple[float, float]:
    return ((event.age - min_age + 1) * 0.9, -episode_order[event.episode_id] * 0.8)


def _svg_node(
    x: int,
    y: int,
    title: str,
    subtitle: str,
    color: str,
    label_offset: int,
    show_subtitle: bool,
) -> str:
    title_y = y + label_offset
    subtitle_y = title_y + 16
    subtitle_text = (
        f'<text x="{x}" y="{subtitle_y}" text-anchor="middle" font-size="11" fill="#475569">{html.escape(subtitle)}</text>'
        if show_subtitle
        else ""
    )
    return f"""
    <g>
      <circle cx="{x}" cy="{y}" r="4" fill="{color}" />
      <text x="{x}" y="{title_y}" text-anchor="middle" font-size="14" font-weight="700" fill="#111827">{html.escape(title)}</text>
      {subtitle_text}
    </g>
    """


def _label_width(title: str) -> int:
    return max(70, min(150, 24 + len(title) * 14))


def _svg_edge(start: tuple[int, int], end: tuple[int, int], color: str) -> str:
    x1, y1 = start
    x2, y2 = end
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2.2" opacity="0.76" />'


def _html_page(*, title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; background: #f8fafc; color: #111827; }}
    svg {{ width: 100%; height: auto; border: 1px solid #cbd5e1; border-radius: 8px; background: white; }}
    table {{ width: 100%; border-collapse: collapse; background: white; margin-top: 16px; border: 1px solid #cbd5e1; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }}
    th {{ background: #f1f5f9; }}
    code {{ font-size: 12px; }}
  </style>
</head>
<body>{body}</body>
</html>
"""


def _write_index(output_dir: Path, core_subgraphs_md: Path) -> None:
    sample_dir = output_dir / "samples"
    sample_pages = sorted(sample_dir.glob("sample_seed_*.html")) if sample_dir.exists() else []
    (output_dir / "index.html").write_text(_index_html(core_subgraphs_md, sample_pages), encoding="utf-8")


def _index_html(core_subgraphs_md: Path, sample_pages: list[Path]) -> str:
    rows = "\n".join(
        f'<li><a href="samples/{html.escape(path.name)}">{html.escape(path.stem)}</a></li>'
        for path in sample_pages
    )
    return _html_page(
        title="life_generator index",
        body=f"""
        <h1>life_generator</h1>
        <p>Age-based plausible core_subgraph sampler and sampled life path timeline.</p>
        <h2>Core Subgraph Library</h2>
        <p><a href="{html.escape(core_subgraphs_md.name)}">core_subgraphs.md</a></p>
        <h2>Samples</h2>
        <ul>{rows}</ul>
        """,
    )


def _render_png(svg_path: Path, png_path: Path) -> None:
    renderer = shutil.which("rsvg-convert")
    if not renderer:
        raise RuntimeError("rsvg-convert is required to render timeline PNG files from SVG")
    subprocess.run([renderer, str(svg_path), "-o", str(png_path)], check=True)
