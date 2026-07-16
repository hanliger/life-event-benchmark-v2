#!/usr/bin/env python
"""Export only the occurred-event order from trajectory JSON files.

Example:
  python scripts/export_event_order_bundle.py \
    --trajectories-dir data/generated/trajectories \
    --output data/generated/review/subgraph_event_order_20.md \
    --max-trajectories 20
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.fsm.models import EventStatus
from fin_life_benchmark.trajectory.models import Trajectory


def _render(traj: Trajectory) -> str:
    rows = []
    for instance in traj.life_event_instances:
        if instance.status != EventStatus.OCCURRED or instance.occurred_month is None:
            continue
        age = traj.initial_persona_state.age + instance.occurred_month // 12
        rows.append((instance.occurred_month, instance.event_id, age, instance.label_ko))
    rows.sort(key=lambda row: (row[0], row[1]))

    lines = [
        f"## {traj.trajectory_id} — {traj.persona.persona_id} — {traj.persona.age}세",
        "",
    ]
    if not rows:
        lines.append("- occurred event 없음")
    else:
        for index, (month, event_id, age, label) in enumerate(rows, start=1):
            lines.append(f"{index}. m{month:03d} / {age}세 — {label} (`{event_id}`)")
    lines.extend(["", "---", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-trajectories", type=int, default=None)
    args = parser.parse_args()

    files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    if args.max_trajectories is not None:
        files = files[: args.max_trajectories]
    if not files:
        raise SystemExit("no traj_*.json found")

    trajectories = [
        Trajectory.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in files
    ]
    sections = [
        f"# Subgraph trajectory 사건 순서 ({len(trajectories)}명)",
        "",
        "> occurred 사건만 표시합니다. cancelled/미발생 event는 제외합니다.",
        "",
    ]
    sections.extend(_render(trajectory) for trajectory in trajectories)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(sections), encoding="utf-8")
    print(f"wrote event-order bundle for {len(trajectories)} trajectories -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
