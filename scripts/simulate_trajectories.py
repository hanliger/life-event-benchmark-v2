#!/usr/bin/env python
"""Simulate life-state trajectories for normalized personas.

Example:
  python scripts/simulate_trajectories.py \
    --personas data/personas/normalized/personas_ko_KR.jsonl \
    --initial-states data/generated/trajectories/initial_states.jsonl \
    --locale ko_KR \
    --num-trajectories 10 \
    --start-age-policy persona_age \
    --target-occurred-events 20 \
    --output-dir data/generated/trajectories \
    --seed 42
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from tqdm import tqdm

from fin_life_benchmark.actions.models import StandingAction
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, load_yaml, read_jsonl
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.memory.models import FinancialMemoryState
from fin_life_benchmark.persona.models import NormalizedPersona
from fin_life_benchmark.trajectory.episode_bridge import episode_scripted_events
from fin_life_benchmark.trajectory.models import Trajectory
from fin_life_benchmark.trajectory.simulator import ForcedEvent, TrajectorySimulator
from fin_life_benchmark.trajectory.subgraph_bridge import fixed_child_education_events, subgraph_scripted_events


def merge_forced_events(*event_lists: list[ForcedEvent] | None) -> list[ForcedEvent]:
    """Merge forced events, preferring parameterized events on same event/month."""
    merged: dict[tuple[str, int], ForcedEvent] = {}
    for events in event_lists:
        for event in events or []:
            key = (event[0], event[1])
            existing = merged.get(key)
            if existing is None or (len(event) > 2 and len(existing) == 2):
                merged[key] = event
    return sorted(merged.values(), key=lambda item: item[1])


def _format_age_month(start_age: int, month_index: int) -> str:
    elapsed_years = month_index // 12
    elapsed_months = month_index % 12
    return f"{start_age + elapsed_years}세 +{elapsed_months}개월"


def _persona_summary(persona: NormalizedPersona) -> str:
    return (
        f"{persona.persona_id} "
        f"({persona.age}세, "
        f"직업상태={persona.occupation_state.employment_status}, "
        f"혼인={persona.household.marital_status}, "
        f"주거={persona.housing.residence_status}, "
        f"자녀={len(persona.household.children_ages)}명)"
    )


def write_trajectory_summary_md(
    trajectories: list[Trajectory],
    output_path: Path,
    *,
    target_occurred_events: int | None = None,
) -> None:
    """Write a Markdown summary of each trajectory's occurred life events."""
    lines: list[str] = [
        "# Trajectory Life Event Summary",
        "",
        f"- Trajectories: {len(trajectories)}",
    ]
    if target_occurred_events is not None:
        lines.append(f"- Target occurred life events per trajectory: {target_occurred_events}")
    lines.append("")

    for index, trajectory in enumerate(trajectories, start=1):
        persona = trajectory.persona
        occurred = sorted(
            (
                instance
                for instance in trajectory.life_event_instances
                if instance.occurred_month is not None
            ),
            key=lambda instance: (instance.occurred_month or 0, instance.event_instance_id),
        )
        final_age = trajectory.final_persona_state.age if trajectory.final_persona_state else "unknown"

        lines.extend(
            [
                f"## {index}. {trajectory.trajectory_id}",
                "",
                f"- Persona: {_persona_summary(persona)}",
                f"- Horizon: {trajectory.horizon_months}개월 ({persona.age}세 → {final_age}세)",
                f"- Occurred life events: {len(occurred)}",
            ]
        )
        if target_occurred_events is not None and len(occurred) != target_occurred_events:
            lines.append(f"- Warning: target {target_occurred_events}개와 실제 {len(occurred)}개가 다릅니다.")

        lines.extend(
            [
                "",
                "| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |",
                "|---:|---|---:|---|---|---|---|",
            ]
        )
        if occurred:
            for event_index, instance in enumerate(occurred, start=1):
                occurred_month = int(instance.occurred_month or 0)
                params = json.dumps(
                    instance.params,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).replace("|", "\\|")
                lines.append(
                    "| "
                    f"{event_index} | "
                    f"{_format_age_month(persona.age, occurred_month)} | "
                    f"{occurred_month} | "
                    f"{instance.label_ko} | "
                    f"`{instance.event_id}` | "
                    f"`{params}` | "
                    f"`{instance.event_instance_id}` |"
                )
        else:
            lines.append("| - | - | - | 발생한 life event 없음 | - | - | - |")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personas", required=True)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--num-trajectories", type=int, default=10)
    parser.add_argument("--start-age-policy", choices=["persona_age"], default="persona_age")
    parser.add_argument(
        "--horizon-years",
        type=int,
        default=10,
        help="fixed horizon for legacy/non-target runs; target runs use --safety-max-age",
    )
    parser.add_argument(
        "--target-occurred-events",
        type=int,
        default=20,
        help="stop each trajectory at this many occurred instances (default: 20)",
    )
    parser.add_argument(
        "--safety-max-age",
        type=int,
        default=None,
        help="optional internal age boundary for target runs; omitted means use registry candidate exhaustion",
    )
    parser.add_argument(
        "--age-warning-threshold",
        type=int,
        default=80,
        help="print a warning when a trajectory reaches this final age",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--initial-states",
        default=None,
        help="JSONL produced by generate_initial_states.py; defaults to "
        "<output-dir>/initial_states.jsonl",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--mode",
        choices=["subgraph", "hazard", "episode"],
        default="subgraph",
        help="subgraph (default): persona-conditioned life-course arcs sampled "
        "by per-event hazard and used as the trajectory backbone; hazard: "
        "independent per-event probabilistic sampling; "
        "episode: hazard sampling plus forced life_generator episodes for "
        "coverage",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="in episode mode, bias episode selection toward impact-producing "
        "events (grows the rare post_occurred class)",
    )
    parser.add_argument("--episode-count", type=int, default=6)
    parser.add_argument(
        "--summary-md",
        default=None,
        help="Markdown summary output path; defaults to <output-dir>/trajectory_summary.md",
    )
    parser.add_argument(
        "--no-summary-md",
        action="store_true",
        help="do not write the generated trajectory Markdown summary",
    )
    args = parser.parse_args()

    paths = RepoPaths.default()
    locale = load_locale(args.locale, paths)
    templates = load_life_event_templates(paths)
    sim_config = load_yaml(paths.generation / "simulation.yaml")
    simulator = TrajectorySimulator(templates, locale, sim_config)

    personas = [NormalizedPersona.model_validate(row) for row in read_jsonl(Path(args.personas))]
    if not personas:
        raise SystemExit("no personas found — run normalize_personas.py first")
    persona_ids = [persona.persona_id for persona in personas]
    if len(persona_ids) != len(set(persona_ids)):
        raise SystemExit("duplicate persona_id found in personas input")
    if args.num_trajectories > len(personas):
        raise SystemExit(
            f"requested {args.num_trajectories} trajectories from {len(personas)} personas; "
            "persona reuse is not allowed"
        )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    initial_states_path = Path(args.initial_states) if args.initial_states else output_dir / "initial_states.jsonl"
    if not initial_states_path.exists():
        raise SystemExit(
            f"initial states not found: {initial_states_path} — "
            "run generate_initial_states.py first or pass --initial-states"
        )
    initial_states_by_persona: dict[str, tuple[FinancialMemoryState, list[StandingAction]]] = {}
    for record in read_jsonl(initial_states_path):
        persona_id = record.get("persona_id")
        if not persona_id:
            raise SystemExit(f"initial state record missing persona_id: {initial_states_path}")
        if persona_id in initial_states_by_persona:
            raise SystemExit(f"duplicate initial state for persona_id={persona_id}: {initial_states_path}")
        initial_states_by_persona[persona_id] = (
            FinancialMemoryState.model_validate(record["initial_financial_memory_state"]),
            [StandingAction.model_validate(action) for action in record["initial_standing_actions"]],
        )
    requested_persona_ids = set(persona_ids[: args.num_trajectories])
    missing_initial = sorted(requested_persona_ids - set(initial_states_by_persona))
    if missing_initial:
        raise SystemExit(f"missing initial states for persona_ids={missing_initial}")
    if args.num_trajectories == len(personas):
        extra_initial = sorted(set(initial_states_by_persona) - set(persona_ids))
        if extra_initial:
            raise SystemExit(f"initial states contain unknown persona_ids={extra_initial}")

    written = 0
    generated_trajectories: list[Trajectory] = []
    age_warnings: list[tuple[str, int, int]] = []
    target_shortfalls: list[tuple[str, int]] = []
    for i in tqdm(range(args.num_trajectories), desc="simulate"):
        persona = personas[i]
        seed = args.seed + i
        trajectory_id = f"traj_{i + 1:03d}"
        try:
            memory, actions = initial_states_by_persona[persona.persona_id]
        except KeyError as exc:
            raise SystemExit(
                f"no initial state for persona_id={persona.persona_id} in {initial_states_path}"
            ) from exc
        forced_events: list[ForcedEvent] | None = None
        subgraph_forced_events: list[ForcedEvent] | None = None
        active_simulator = simulator
        simulation_horizon_years = args.horizon_years
        horizon_months = args.horizon_years * 12
        planning_max_age = None
        if args.target_occurred_events is not None:
            # The registry's active age guards end at 90. Use a generous
            # internal planning buffer so this is not a user-facing horizon;
            # actual storage ends at the target event or the final plan node.
            planning_age_ceiling = args.safety_max_age or 120
            horizon_months = max(1, (planning_age_ceiling - persona.age) * 12)
            planning_max_age = args.safety_max_age
        if args.mode == "episode":
            forced_events = episode_scripted_events(
                seed=seed,
                horizon_months=horizon_months,
                start_age=persona.age,
                coverage=args.coverage,
                episode_count=args.episode_count,
                paths=paths,
            )
        elif args.mode == "subgraph":
            subgraph_forced_events = subgraph_scripted_events(
                persona=persona,
                seed=seed,
                horizon_months=horizon_months,
                episode_count=args.episode_count,
                target_event_count=args.target_occurred_events,
                max_age=planning_max_age,
                templates=templates,
                paths=paths,
            )
            # Subgraph events form the causal backbone. The normal guarded
            # hazard remains active between them so a single trajectory can
            # reach its target without a separate coverage trajectory.
            forced_events = subgraph_forced_events
            active_simulator = simulator
        forced_events = merge_forced_events(
            forced_events,
            fixed_child_education_events(persona, horizon_months),
        )
        if args.target_occurred_events is not None and forced_events:
            last_planned_month = max(event[1] for event in forced_events)
            simulation_horizon_years = max(
                args.horizon_years,
                last_planned_month // 12 + 3,
            )
        trajectory = active_simulator.simulate(
            persona=persona,
            initial_memory=memory,
            initial_actions=actions,
            horizon_years=simulation_horizon_years,
            seed=seed,
            trajectory_id=trajectory_id,
            forced_events=forced_events,
            target_occurred_events=args.target_occurred_events,
        )
        out_path = output_dir / f"{trajectory_id}.json"
        out_path.write_text(
            json.dumps(trajectory.model_dump(mode="json"), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        occurred_count = sum(
            1 for instance in trajectory.life_event_instances if instance.occurred_month is not None
        )
        final_age = trajectory.final_persona_state.age
        if args.target_occurred_events is not None and occurred_count != args.target_occurred_events:
            target_shortfalls.append((trajectory_id, occurred_count))
        if final_age >= args.age_warning_threshold:
            age_warnings.append((trajectory_id, persona.age, final_age))
        generated_trajectories.append(trajectory)
        written += 1

    print(f"wrote {written} trajectories -> {output_dir}")
    if not args.no_summary_md:
        default_summary = (
            output_dir.parent / "reports" / "trajectory_summary.md"
            if output_dir.name == "trajectories"
            else output_dir / "trajectory_summary.md"
        )
        summary_path = Path(args.summary_md) if args.summary_md else default_summary
        write_trajectory_summary_md(
            generated_trajectories,
            summary_path,
            target_occurred_events=args.target_occurred_events,
        )
        print(f"wrote trajectory summary -> {summary_path}")
    if target_shortfalls:
        raise SystemExit(f"target occurred count not reached: {target_shortfalls}")
    if age_warnings:
        print(f"WARNING: final age threshold reached: {age_warnings}")
    if not target_shortfalls and not age_warnings:
        print("target/age checks: all trajectories passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
