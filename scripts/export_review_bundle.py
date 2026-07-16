#!/usr/bin/env python
"""Render generated trajectories into a human-readable markdown review bundle.

Reads the SAME standard ``traj_*.json`` files that ``generate_dialogue_sessions.py``
consumes (so no separate machine format is produced — the JSON is dialogue-ready
as-is), and emits one markdown section per persona:

  persona summary -> initial state (LifeState + key memory cells + standing
  actions, taken from the trajectory's OWN embedded month-0 state so it is
  guaranteed consistent with the trajectory) -> the realized life-course arc
  (occurred events in age order).

Example:
  python scripts/export_review_bundle.py \
    --trajectories-dir data/generated/trajectories \
    --output data/generated/review/subgraph_bundle_20.md \
    --max-trajectories 20
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.fsm.models import EventStatus
from fin_life_benchmark.memory.models import CellStatus, FinancialMemoryState
from fin_life_benchmark.trajectory.models import Trajectory

# Memory paths worth surfacing for review, in display order.
_MEMORY_PATHS = [
    "household.marital_status",
    "household.children",
    "household.dependents",
    "employment.employment_status",
    "employment.occupation",
    "employment.employer",
    "employment.salary_day",
    "employment.income_stability",
    "housing.residence_status",
    "housing.contract_type",
    "housing.mortgage_status",
    "housing.rent_amount",
    "financial_products.loans",
    "financial_products.pension_or_irp",
    "financial_products.savings_accounts",
]


def _fmt_memory(memory: FinancialMemoryState) -> list[str]:
    lines: list[str] = []
    for path in _MEMORY_PATHS:
        cell = memory.latest(path)
        if cell is None or cell.status != CellStatus.CURRENT or cell.value in (None, [], ""):
            continue
        lines.append(f"  - `{path}` = {cell.value}")
    return lines


def _fmt_actions(actions) -> list[str]:
    if not actions:
        return ["  - (없음)"]
    lines = []
    for a in actions:
        amount = f", {a.amount:,}원" if a.amount else ""
        day = f", {a.trigger_day}일" if a.trigger_day else ""
        lines.append(f"  - {a.label} (`{a.type}`{amount}{day})")
    return lines


def _occurred_arc(traj: Trajectory) -> list[str]:
    start_age = traj.initial_persona_state.age
    rows: list[tuple[int, int, str, str, str]] = []
    for inst in traj.life_event_instances:
        if inst.status != EventStatus.OCCURRED or inst.occurred_month is None:
            continue
        month = inst.occurred_month
        age = start_age + month // 12
        rows.append((age, month, inst.label_ko, inst.domain, inst.event_id))
    rows.sort(key=lambda r: (r[1], r[4]))
    if not rows:
        return ["  - (occurred 이벤트 없음)"]
    return [f"  - {age}세 (m{month:>3}) **{label}** [{domain}] `{eid}`" for age, month, label, domain, eid in rows]


def _render(traj: Trajectory) -> str:
    p = traj.persona
    ls = traj.initial_persona_state.life_state
    fs = traj.final_persona_state.life_state if traj.final_persona_state else ls

    out: list[str] = []
    out.append(
        f"## {p.persona_id} — {p.age}세 / {p.sex or '?'} / "
        f"{p.household.marital_status} / {p.occupation_state.employment_status} / "
        f"{p.housing.residence_status} / 자녀{len(p.household.children_ages)}"
    )
    out.append(f"- trajectory: `{traj.trajectory_id}` (seed {traj.seed}, {traj.horizon_months}개월)")
    if p.occupation_state.occupation:
        out.append(f"- 직업: {p.occupation_state.occupation}")
    if p.persona_text:
        text = p.persona_text.strip().replace("\n", " ")
        out.append(f"- persona: {text[:200]}{'…' if len(text) > 200 else ''}")

    out.append("")
    out.append("**Initial state (month 0)**")
    out.append(
        f"- LifeState: 혼인={ls.marital_status}, 고용={ls.employment_status}, "
        f"주거={ls.residence_status}, 자녀나이={ls.children_ages}, 부양={ls.dependents_count}, "
        f"자가={ls.home_owned}, 은퇴준비={ls.retirement_prepared}"
    )
    out.append("- 재무 memory (current):")
    out.extend(_fmt_memory(traj.initial_financial_memory_state))
    out.append("- standing actions:")
    out.extend(_fmt_actions(traj.initial_standing_actions))

    out.append("")
    out.append("**Trajectory (occurred arc, 나이순)**")
    out.extend(_occurred_arc(traj))

    out.append("")
    out.append(
        f"**Final state**: 혼인={fs.marital_status}, 고용={fs.employment_status}, "
        f"주거={fs.residence_status}, 자녀나이={fs.children_ages}, 자가={fs.home_owned}"
    )
    out.append("\n---\n")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-trajectories", type=int, default=None)
    args = parser.parse_args()

    files = sorted(Path(args.trajectories_dir).glob("traj_*.json"))
    if args.max_trajectories is not None:
        files = files[: args.max_trajectories]
    if not files:
        raise SystemExit(f"no traj_*.json under {args.trajectories_dir} — run simulate_trajectories.py first")

    sections = [f"# Subgraph trajectory 검토 번들 ({len(files)}명)\n"]
    for f in files:
        traj = Trajectory.model_validate(json.loads(f.read_text(encoding="utf-8")))
        sections.append(_render(traj))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"wrote review bundle for {len(files)} trajectories -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
