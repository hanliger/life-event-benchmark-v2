"""Bridge life_generator episodes into forced trajectory events.

life_generator produces curated, order-consistent life-course paths (marriage →
childbirth → school milestones, rental → homeownership → sale, employment →
education → job change, …). We reuse those paths to *guarantee* that specific
occurred events appear in a trajectory, instead of waiting for the hazard
sampler to produce rare (occurred event × impacted standing action) pairs.

A life_generator event id is mapped to a benchmark event id via the
``life_generator_node_ids`` cross-reference in life_events.yaml, and its
person-age is converted to a trajectory month offset. The resulting
(benchmark_event_id, start_month) list is fed to
``TrajectorySimulator.simulate(..., forced_events=...)``.
"""

from __future__ import annotations

import random

from ..io import RepoPaths, load_yaml


def build_reverse_map(paths: RepoPaths | None = None) -> dict[str, str]:
    """life_generator node id -> benchmark event id (first mapping wins)."""
    paths = paths or RepoPaths.default()
    events = load_yaml(paths.registries / "life_events.yaml")
    reverse: dict[str, str] = {}
    for event_id, spec in events.items():
        if not isinstance(spec, dict):
            continue
        for node_id in spec.get("life_generator_node_ids") or []:
            reverse.setdefault(node_id, event_id)
    return reverse


def impact_event_ids(paths: RepoPaths | None = None) -> set[str]:
    """Benchmark events that impact a standing action on occurrence
    (the post_occurred sources)."""
    paths = paths or RepoPaths.default()
    impacts = load_yaml(paths.registries / "event_to_action_impact.yaml")
    return {
        event_id
        for event_id, spec in impacts.items()
        if isinstance(spec, dict) and (spec.get("on_occurred") or {}).get("action_impacts")
    }


def coverage_template_ids(paths: RepoPaths | None = None) -> list[str]:
    """life_generator template ids whose events map to impact-producing
    benchmark events — the templates worth forcing for post_occurred coverage."""
    from life_generator.templates import EPISODE_TEMPLATES

    paths = paths or RepoPaths.default()
    reverse = build_reverse_map(paths)
    targets = impact_event_ids(paths)
    selected: list[str] = []
    for template in EPISODE_TEMPLATES:
        mapped = {reverse.get(node) for node in template.event_ids}
        if mapped & targets:
            selected.append(template.id)
    return selected


def templates_for_event(paths: RepoPaths | None = None) -> dict[str, list[str]]:
    """benchmark event id -> life_generator template ids that contain it."""
    from life_generator.templates import EPISODE_TEMPLATES

    paths = paths or RepoPaths.default()
    reverse = build_reverse_map(paths)
    out: dict[str, list[str]] = {}
    for template in EPISODE_TEMPLATES:
        for node in template.event_ids:
            event_id = reverse.get(node)
            if event_id is not None:
                out.setdefault(event_id, [])
                if template.id not in out[event_id]:
                    out[event_id].append(template.id)
    return out


def impact_pairs(paths: RepoPaths | None = None) -> list[tuple[str, dict]]:
    """(event_id, selector) for every on_occurred action impact.

    selector has {label: <action_type>} and/or {linked_memory_path: <path>};
    the coverage driver uses it to find a persona that already owns a matching
    standing action."""
    paths = paths or RepoPaths.default()
    impacts = load_yaml(paths.registries / "event_to_action_impact.yaml")
    pairs: list[tuple[str, dict]] = []
    for event_id, spec in impacts.items():
        if not isinstance(spec, dict):
            continue
        for impact in (spec.get("on_occurred") or {}).get("action_impacts") or []:
            pairs.append((event_id, impact.get("selector") or {}))
    return pairs


def scripted_events_from_path(
    path,
    start_age: int,
    horizon_months: int,
    reverse_map: dict[str, str],
    rng: random.Random,
    compress: bool = False,
) -> list[tuple[str, int]]:
    """Convert a life_generator GeneratedLifePath into (benchmark_event_id,
    start_month) pairs anchored inside [0, horizon_months).

    Episodes can span more years than the horizon (e.g. jeonse→purchase→sale
    over ~28 years). With ``compress=True`` the episode timeline is linearly
    scaled to fit [0, horizon_months) so every mapped event lands inside the
    horizon (ordering preserved, inter-event gaps shortened). Without it,
    events past the horizon are dropped. Coverage generation uses compress=True
    so the target event is guaranteed to occur."""
    events = list(path.events)
    if not events:
        return []
    base_age = min(e.age for e in events)
    raw: list[tuple[str, int]] = []
    for event in events:
        benchmark_id = reverse_map.get(event.event_id)
        if benchmark_id is None:
            continue  # life_generator node with no benchmark counterpart
        raw.append((benchmark_id, (event.age - base_age) * 12))

    if not raw:
        return []

    span = max(m for _, m in raw)
    scripted: list[tuple[str, int]] = []
    if compress and span >= horizon_months:
        scale = (horizon_months - 2) / span  # leave a tick of headroom
        for benchmark_id, month in raw:
            scripted.append((benchmark_id, max(0, int(month * scale))))
    else:
        for benchmark_id, month in raw:
            m = month + rng.randint(0, 11)
            if 0 <= m < horizon_months:
                scripted.append((benchmark_id, m))
    scripted.sort(key=lambda item: item[1])
    return scripted


def episode_scripted_events(
    seed: int,
    horizon_months: int,
    start_age: int,
    *,
    template_ids: list[str] | None = None,
    episode_count: int = 6,
    coverage: bool = False,
    compress: bool | None = None,
    paths: RepoPaths | None = None,
) -> list[tuple[str, int]]:
    """Sample a life_generator path and return forced (event_id, month) pairs.

    coverage=True biases template selection toward impact-producing episodes.
    compress defaults to True when explicit template_ids are given (coverage
    generation, where the target event must land inside the horizon)."""
    from life_generator.sampler import sample_life_path

    paths = paths or RepoPaths.default()
    reverse = build_reverse_map(paths)
    rng = random.Random(f"episode_bridge:{seed}")

    if template_ids is None and coverage:
        candidates = coverage_template_ids(paths)
        if candidates:
            k = min(len(candidates), max(2, episode_count // 2))
            template_ids = rng.sample(candidates, k)

    if compress is None:
        compress = template_ids is not None

    path = sample_life_path(seed=seed, episode_count=episode_count, template_ids=template_ids)
    return scripted_events_from_path(path, start_age, horizon_months, reverse, rng, compress=compress)
