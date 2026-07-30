"""Shared synthetic corpus for RQ1 tests.

30 sessions (two 15-session checkpoints) for one persona, with:
- ev001 career_employment: weak S003 -> upcoming S007 -> occurred S012,
  consequence S014 (checkpoint 15 sees it occurred)
- ev002 career_employment (repeated event_id): weak S018 -> occurred S026
- ev003 housing_move: weak S020 -> upcoming S028 (still upcoming at 30)
- hard negative S010 (near-miss housing_move), routine elsewhere
plus a 20-session timeless filler bank compatible with
scripts.mask_lifecycle_experiment donor selection.
"""

from __future__ import annotations

from typing import Any

from fin_life_benchmark.dialogue.counterfactual_fillers import (
    SAFE_FILLER_TASK_TEMPLATE_IDS,
)
from fin_life_benchmark.fsm.models import EventInstance
from fin_life_benchmark.memory.models import FinancialMemoryState
from fin_life_benchmark.persona.models import NormalizedPersona
from fin_life_benchmark.trajectory.models import PersonaState, Trajectory

TRAJ_ID = "traj_901"
PERSONA_ID = "p_rq1_test"


def _turns(tag: str) -> list[dict[str, str]]:
    turns = []
    for i in range(4):
        turns.append({"speaker": "user", "text": f"{tag} 고객 발화 {i + 1}"})
        turns.append({"speaker": "assistant", "text": f"{tag} 상담원 안내 {i + 1}"})
    return turns


def _session(
    number: int,
    session_type: str = "routine_financial",
    *,
    linked: str | None = None,
    status_after: str = "no_event",
    cues: list[dict[str, Any]] | None = None,
    plan_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sid = f"S{number:03d}"
    plan: dict[str, Any] = {"session_id": sid}
    if session_type == "routine_financial":
        plan["task_template_id"] = "routine_deposit_rate"
    if plan_extra:
        plan.update(plan_extra)
    if cues is None:
        cues = (
            [{"turn_index": 0, "cue_type": "task_intent", "cue_text": ""}]
            if session_type == "routine_financial"
            else []
        )
    return {
        "session_id": sid,
        "trajectory_id": TRAJ_ID,
        "persona_id": PERSONA_ID,
        "month_index": (number - 1) // 3,
        "age": 30 + ((number - 1) // 36),
        "transition_order": 0,
        "plan": plan,
        "session_type": session_type,
        "linked_event_instance_id": linked,
        "window_event_instance_id": None,
        "event_status_after_session": status_after,
        # turn text must stay free of canonical ids / private field values
        "turns": _turns(f"주제{number:03d}"),
        "cue_annotations": cues,
        "financial_task": "입출금 내역 조회",
        "mapped_action": "FA-01",
        "action_resolution": {"mode": "information_only"},
    }


def build_sessions() -> list[dict[str, Any]]:
    special: dict[int, dict[str, Any]] = {}
    special[3] = _session(
        3, "weak_signal_evidence", linked=f"{TRAJ_ID}_ev001", status_after="weak_signal"
    )
    special[7] = _session(
        7, "upcoming_evidence", linked=f"{TRAJ_ID}_ev001", status_after="upcoming"
    )
    special[10] = _session(
        10,
        "hard_negative",
        plan_extra={
            "hard_negative_type": "sibling_event_negative",
            "near_miss_event_id": "housing_move",
            "near_miss_explanation": "이사 암시지만 상태 변화 없음",
        },
        cues=[],
    )
    special[12] = _session(
        12,
        "occurred_evidence",
        linked=f"{TRAJ_ID}_ev001",
        status_after="occurred",
        cues=[
            {
                "turn_index": 2,
                "cue_type": "memory_fact",
                "cue_text": "새 직장",
                "linked_memory_path": "employment.employer",
                "linked_memory_operation": "update",
                "linked_memory_value": "테스트정보시스템",
            }
        ],
    )
    special[14] = _session(
        14, "consequence_session", linked=f"{TRAJ_ID}_ev001", status_after="occurred"
    )
    special[18] = _session(
        18, "weak_signal_evidence", linked=f"{TRAJ_ID}_ev002", status_after="weak_signal"
    )
    special[20] = _session(
        20, "weak_signal_evidence", linked=f"{TRAJ_ID}_ev003", status_after="weak_signal"
    )
    special[26] = _session(
        26, "occurred_evidence", linked=f"{TRAJ_ID}_ev002", status_after="occurred"
    )
    special[28] = _session(
        28, "upcoming_evidence", linked=f"{TRAJ_ID}_ev003", status_after="upcoming"
    )
    return [special.get(n, _session(n)) for n in range(1, 31)]


def build_trajectory() -> Trajectory:
    def instance(num: int, event_id: str, label: str, domain: str, status: str) -> EventInstance:
        return EventInstance(
            event_instance_id=f"{TRAJ_ID}_ev{num:03d}",
            event_id=event_id,
            label_ko=label,
            domain=domain,
            status=status,
        )

    return Trajectory(
        trajectory_id=TRAJ_ID,
        locale="ko_KR",
        seed=0,
        horizon_months=12,
        persona=NormalizedPersona(
            persona_id=PERSONA_ID,
            persona_source_id="src",
            locale="ko_KR",
            age=30,
        ),
        initial_persona_state=PersonaState(month_index=0, age=30),
        initial_financial_memory_state=FinancialMemoryState(),
        initial_standing_actions=[],
        life_event_instances=[
            instance(1, "career_employment", "취업", "employment", "occurred"),
            instance(2, "career_employment", "취업", "employment", "occurred"),
            instance(3, "housing_move", "이사", "housing", "upcoming"),
        ],
    )


def build_filler_bank(count: int = 20) -> list[dict[str, Any]]:
    templates = sorted(SAFE_FILLER_TASK_TEMPLATE_IDS)
    bank = []
    for i in range(count):
        fid = f"CF{i + 1:03d}"
        bank.append(
            {
                "filler_id": fid,
                "session_id": fid,
                "trajectory_id": TRAJ_ID,
                "persona_id": PERSONA_ID,
                "source_kind": "synthetic_reserve",
                "month_index": None,
                "session_type": "routine_financial",
                "linked_event_instance_id": None,
                "event_status_after_session": "no_event",
                "mapped_action": "FA-01",
                "financial_task": "최근 거래 내역 조회",
                "turns": _turns(f"{fid}:filler"),
                "cue_annotations": [
                    {"turn_index": 0, "cue_type": "task_intent", "cue_text": ""}
                ],
                "action_resolution": {"mode": "information_only"},
                "plan": {"task_template_id": templates[i % len(templates)]},
            }
        )
    return bank


TAXONOMY = [
    {"event_id": "career_employment", "label_ko": "취업"},
    {"event_id": "housing_move", "label_ko": "이사"},
    {"event_id": "relationship_marriage", "label_ko": "결혼"},
]
