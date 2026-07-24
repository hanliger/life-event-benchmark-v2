from __future__ import annotations

from pathlib import Path

from fin_life_benchmark.dialogue.counterfactual_fillers import (
    FILLERS_PER_PERSONA,
    SAFE_FILLER_TASK_TEMPLATE_IDS,
    audit_filler_bank,
    build_filler_plans,
    make_filler,
    validate_filler,
)
from fin_life_benchmark.trajectory.models import Trajectory


def _trajectory() -> Trajectory:
    path = Path("tests/fixtures/trajectories/traj_001.json")
    return Trajectory.model_validate_json(path.read_text(encoding="utf-8"))


def _turns(task: str) -> list[dict[str, str]]:
    return [
        {"speaker": "user", "text": f"{task} 확인 방법이 궁금해요"},
        {"speaker": "assistant", "text": "앱의 조회 메뉴에서 해당 항목을 선택하시면 됩니다."},
        {"speaker": "user", "text": "조회 기간도 제가 고를 수 있나요"},
        {"speaker": "assistant", "text": "기간 선택 메뉴에서 원하는 범위를 직접 지정할 수 있어요."},
        {"speaker": "user", "text": "결과는 화면에서만 확인하면 될 것 같아요"},
        {"speaker": "assistant", "text": "필터를 적용한 뒤 화면에서 항목을 살펴보시면 됩니다."},
        {"speaker": "user", "text": "알겠어요, 방법만 확인할게요"},
        {"speaker": "assistant", "text": "네, 변경 없이 조회 절차만 이용하시면 됩니다."},
    ]


def test_build_filler_plans_is_twenty_balanced_style_only_plans() -> None:
    trajectory = _trajectory()
    plans = build_filler_plans(trajectory)

    assert len(plans) == FILLERS_PER_PERSONA
    assert len({plan.filler_id for plan in plans}) == FILLERS_PER_PERSONA
    assert {plan.task_template_id for plan in plans} == set(
        SAFE_FILLER_TASK_TEMPLATE_IDS
    )
    assert all(
        sum(item.task_template_id == plan.task_template_id for item in plans) == 2
        for plan in plans
    )
    assert all(plan.persona_id == trajectory.persona.persona_id for plan in plans)
    assert all(not hasattr(plan, "month_index") for plan in plans)


def test_valid_filler_is_timeless_and_has_only_task_intent_cue() -> None:
    plan = build_filler_plans(_trajectory())[0]
    filler = make_filler(plan, _turns(plan.financial_task), {"model": "test"})

    assert validate_filler(filler, plan) == []
    assert filler.month_index is None
    assert filler.source_kind == "synthetic_reserve"
    assert filler.cue_annotations[0].cue_type == "task_intent"
    assert filler.cue_annotations[0].linked_memory_path is None


def test_validator_blocks_invented_personal_result_and_lifecycle_fact() -> None:
    plan = build_filler_plans(_trajectory())[0]
    turns = _turns(plan.financial_task)
    turns[1]["text"] = "조회 결과 현재 계좌가 두 개 있고 직장 급여도 확인됐어요."
    filler = make_filler(plan, turns, {"model": "test"})

    codes = {item["code"] for item in validate_filler(filler, plan)}
    assert "lifecycle_leak" in codes
    assert "invented_personal_result" in codes


def test_bank_audit_requires_all_plans_without_duplicate_dialogues() -> None:
    plans = build_filler_plans(_trajectory())
    fillers = [
        make_filler(
            plan,
            _turns(plan.financial_task + plan.surface_variant_id),
            {"model": "test"},
        )
        for plan in plans
    ]

    report = audit_filler_bank(plans, fillers)

    assert report["decision"] == "PASS"
    assert report["actual_fillers"] == FILLERS_PER_PERSONA
    assert report["violations"] == []
