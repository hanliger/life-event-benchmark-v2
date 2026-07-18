#!/usr/bin/env python
"""Validate a trajectory run and write its manifest and audit report."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from fin_life_benchmark.fsm.event_lifecycle import (
    apply_occurred_to_life_state,
    validate_event_params,
)
from fin_life_benchmark.fsm.life_state_machine import LifeStateMachine
from fin_life_benchmark.fsm.models import EventStatus
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, load_yaml, read_jsonl
from fin_life_benchmark.trajectory.models import Trajectory
from simulate_trajectories import write_trajectory_summary_md


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _git_version(repo: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        return result.stdout.strip()

    return {
        "commit": run("rev-parse", "HEAD") or None,
        "dirty": bool(run("status", "--porcelain")),
    }


def _age_distribution(trajectories: list[Trajectory]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for trajectory in trajectories:
        decade = trajectory.persona.age // 10 * 10
        counts[f"{decade}s"] += 1
    return dict(sorted(counts.items()))


def _audit_trajectory(
    trajectory: Trajectory,
    templates: dict,
    target_occurred: int,
) -> list[str]:
    errors: list[str] = []
    instances = {item.event_instance_id: item for item in trajectory.life_event_instances}
    active_ids = set(templates)
    occurred = [item for item in instances.values() if item.occurred_month is not None]
    if len(occurred) != target_occurred:
        errors.append(f"occurred_count={len(occurred)} expected={target_occurred}")

    for instance in instances.values():
        if instance.event_id not in active_ids:
            errors.append(f"unknown event_id={instance.event_id}")
            continue
        template = templates[instance.event_id]
        if instance.memory_delta_template_id != (template.memory_delta_template_id or template.event_id):
            errors.append(f"{instance.event_instance_id}: memory template mismatch")
        if instance.action_impact_template_id != (template.action_impact_template_id or template.event_id):
            errors.append(f"{instance.event_instance_id}: action template mismatch")
        months = [item.month_index for item in instance.status_history]
        if months != sorted(months):
            errors.append(f"{instance.event_instance_id}: non-monotonic status history")

    state = trajectory.initial_persona_state.life_state.model_copy(deep=True)
    previous_month = 0
    fsm = LifeStateMachine(templates)
    for step in sorted(trajectory.timeline_steps, key=lambda item: item.month_index):
        for month in range(previous_month + 1, step.month_index + 1):
            if month % 12 == 0:
                state.tick_year()
        previous_month = step.month_index
        for transition in step.transitions:
            if transition.to_status != EventStatus.OCCURRED.value:
                continue
            instance = instances[transition.event_instance_id]
            template = templates[instance.event_id]
            try:
                validate_event_params(template, state, instance.params)
            except ValueError as exc:
                errors.append(f"{instance.event_instance_id}: {exc}")
            if not fsm.occurrence_guards_pass(template, state, step.age):
                errors.append(f"{instance.event_instance_id}: occurrence guard failed")
            apply_occurred_to_life_state(
                instance.event_id,
                state,
                instance.params,
                event_instance_id=instance.event_instance_id,
                month_index=step.month_index,
            )

        snapshot = trajectory.state_snapshots.get(str(step.month_index))
        if snapshot is not None:
            if snapshot.life_state.model_dump(mode="json") != state.model_dump(mode="json"):
                errors.append(f"month={step.month_index}: LifeState snapshot mismatch")

    instance_ids = set(instances)
    for step in trajectory.timeline_steps:
        for update in step.memory_updates:
            if update.source_event_instance_id not in instance_ids:
                errors.append(f"month={step.month_index}: orphan memory update")
        for impact in step.action_impacts:
            if impact.source_event_instance_id not in instance_ids:
                errors.append(f"month={step.month_index}: orphan action impact")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--master-seed", type=int, default=42)
    parser.add_argument("--target-occurred-events", type=int, default=20)
    parser.add_argument("--run-version", default="v2")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    personas_path = run_dir / "inputs" / "personas.jsonl"
    initial_states_path = run_dir / "inputs" / "initial_states.jsonl"
    trajectories_dir = run_dir / "trajectories"
    registry_path = RepoPaths.default().registries / "life_events.yaml"
    required = [personas_path, initial_states_path, registry_path]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"missing required files: {missing}")

    persona_rows = list(read_jsonl(personas_path))
    initial_rows = list(read_jsonl(initial_states_path))
    persona_ids = [row["persona_id"] for row in persona_rows]
    initial_ids = [row["persona_id"] for row in initial_rows]
    if len(persona_ids) != len(set(persona_ids)):
        raise SystemExit("duplicate persona_id in personas.jsonl")
    if len(initial_ids) != len(set(initial_ids)):
        raise SystemExit("duplicate persona_id in initial_states.jsonl")
    if set(persona_ids) != set(initial_ids):
        raise SystemExit("personas and initial states are not a 1:1 persona_id match")

    files = sorted(trajectories_dir.glob("traj_*.json"))
    trajectories = [
        Trajectory.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in files
    ]
    templates = load_life_event_templates()
    simulation_config = load_yaml(RepoPaths.default().generation / "simulation.yaml")
    audit_rows: list[dict[str, Any]] = []
    all_errors: list[str] = []
    event_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    param_counts: dict[str, Counter[str]] = {}
    seen_personas: list[str] = []

    for index, (path, trajectory) in enumerate(zip(files, trajectories, strict=True), start=1):
        expected_id = f"traj_{index:03d}"
        errors = _audit_trajectory(trajectory, templates, args.target_occurred_events)
        if path.stem != expected_id or trajectory.trajectory_id != expected_id:
            errors.append(f"expected trajectory_id={expected_id}")
        seen_personas.append(trajectory.persona.persona_id)
        occurred = [i for i in trajectory.life_event_instances if i.occurred_month is not None]
        event_counts.update(i.event_id for i in occurred)
        source_counts.update(i.generation_source for i in occurred)
        for instance in occurred:
            for name, value in instance.params.items():
                param_counts.setdefault(f"{instance.event_id}.{name}", Counter())[repr(value)] += 1
        audit_rows.append({
            "trajectory_id": trajectory.trajectory_id,
            "persona_id": trajectory.persona.persona_id,
            "seed": trajectory.seed,
            "start_age": trajectory.persona.age,
            "final_age": trajectory.final_persona_state.age if trajectory.final_persona_state else None,
            "occurred_events": len(occurred),
            "sha256": _sha256(path),
            "errors": errors,
        })
        all_errors.extend(f"{trajectory.trajectory_id}: {error}" for error in errors)

    if seen_personas != persona_ids[: len(trajectories)]:
        all_errors.append("trajectory persona order does not match personas.jsonl")
    if len(seen_personas) != len(set(seen_personas)):
        all_errors.append("a persona was reused by multiple trajectories")

    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    summary_path = reports_dir / "trajectory_summary.md"
    write_trajectory_summary_md(
        trajectories,
        summary_path,
        target_occurred_events=args.target_occurred_events,
    )
    preferred = simulation_config.get("preferred_horizon_months") or [120, 240]
    horizons = [trajectory.horizon_months for trajectory in trajectories]
    horizon_distribution = {
        "preferred_months": preferred,
        "minimum": min(horizons) if horizons else None,
        "maximum": max(horizons) if horizons else None,
        "mean": (sum(horizons) / len(horizons)) if horizons else None,
        "below_preferred": sum(months < preferred[0] for months in horizons),
        "within_preferred": sum(preferred[0] <= months <= preferred[1] for months in horizons),
        "above_preferred": sum(months > preferred[1] for months in horizons),
    }
    audit = {
        "passed": not all_errors,
        "trajectory_count": len(trajectories),
        "active_event_count": len(templates),
        "target_occurred_events": args.target_occurred_events,
        "event_distribution": dict(sorted(event_counts.items())),
        "generation_source_distribution": dict(sorted(source_counts.items())),
        "horizon_distribution": horizon_distribution,
        "parameter_distribution": {
            key: dict(sorted(counter.items())) for key, counter in sorted(param_counts.items())
        },
        "trajectories": audit_rows,
        "errors": all_errors,
    }
    (reports_dir / "trajectory_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    repo = RepoPaths.default().root
    manifest = {
        "run_version": args.run_version,
        "locale": args.locale,
        "master_seed": args.master_seed,
        "trajectory_seed_range": [args.master_seed, args.master_seed + len(trajectories) - 1],
        "persona_count": len(persona_rows),
        "age_distribution": _age_distribution(trajectories),
        "target_occurred_events_per_trajectory": args.target_occurred_events,
        "inputs": {
            "personas": {"path": "inputs/personas.jsonl", "sha256": _sha256(personas_path)},
            "initial_states": {"path": "inputs/initial_states.jsonl", "sha256": _sha256(initial_states_path)},
            "life_event_registry": {"path": str(registry_path.relative_to(repo)), "sha256": _sha256(registry_path)},
        },
        "generation": {
            "mode": "subgraph_with_guarded_hazard",
            "one_trajectory_per_persona": True,
            "sampling_policy": {
                "global_hazard_scale": simulation_config.get("global_hazard_scale"),
                "preferred_horizon_months": preferred,
            },
            "code_version": _git_version(repo),
        },
        "trajectories": [
            {
                "trajectory_id": row["trajectory_id"],
                "persona_id": row["persona_id"],
                "seed": row["seed"],
                "occurred_events": row["occurred_events"],
                "sha256": row["sha256"],
            }
            for row in audit_rows
        ],
        "audit": {"path": "reports/trajectory_audit.json", "passed": not all_errors},
        "summary": {"path": "reports/trajectory_summary.md"},
    }
    sessions_dir = run_dir / "dialogues" / "sessions"
    prefix_all_path = run_dir / "gold" / "prefix_gold_all_sessions.jsonl"
    checkpoints_path = run_dir / "gold" / "prefix_gold_checkpoints_15.jsonl"
    if sessions_dir.exists() and prefix_all_path.exists() and checkpoints_path.exists():
        session_files = sorted(sessions_dir.glob("sessions_traj_*.jsonl"))
        manifest["controlled_outputs"] = {
            "window_size_sessions": 15,
            "sessions": {
                "path": "dialogues/sessions",
                "count": sum(_line_count(path) for path in session_files),
            },
            "auxiliary_prefix_gold": {
                "path": "gold/prefix_gold_all_sessions.jsonl",
                "count": _line_count(prefix_all_path),
                "sha256": _sha256(prefix_all_path),
            },
            "main_checkpoint_gold": {
                "path": "gold/prefix_gold_checkpoints_15.jsonl",
                "count": _line_count(checkpoints_path),
                "sha256": _sha256(checkpoints_path),
            },
            "stage1_items": {"path": "benchmark_items/stage1_event_status.jsonl"},
            "stage2_items": {"path": "benchmark_items/stage2_memory_mcq.jsonl"},
            "audit": {"path": "reports/v3_controlled_audit.json"},
        }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if all_errors:
        raise SystemExit(f"trajectory audit failed with {len(all_errors)} error(s)")
    print(f"trajectory audit passed: {len(trajectories)} trajectories -> {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
