"""Offline regression tests for semantic dialogue contracts."""

from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest

from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan
from fin_life_benchmark.dialogue.generation_control import (
    require_human_review_pass,
    require_review_pass,
)
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.trajectory.models import Trajectory
from fin_life_benchmark.validation.dialogue_generation_audit import (
    audit_dialogue_generation,
)
from fin_life_benchmark.validation.dialogue_validator import (
    DialogueValidator,
    contains_contextual_event_label,
    event_slot_candidates,
    _numbers_in_text,
    reconcile_provided_slots,
    standing_action_amounts,
)
from scripts.sample_dialogue_regression_canary import select_regression_plans
from scripts.score_dialogue_review_packet import score_records
from scripts.generate_dialogue_sessions import main as generate_main


def _validator() -> DialogueValidator:
    return DialogueValidator(load_life_event_templates())


def _session(
    *,
    event_id: str = "relationship_childbirth",
    status: str = "occurred",
    mapped_action: str = "FA-01",
    user: str = "목적저축 설정을 확인해 주세요",
    assistant: str = "현재 설정을 확인해 드릴게요",
    dimensions: list[dict] | None = None,
    annotations: list[dict] | None = None,
    contract: dict | None = None,
    resolution: dict | None = None,
) -> dict:
    plan = {
        "financial_task": "목적저축 설정 확인",
        "target_memory_paths": [],
        "evidence_dimensions": dimensions or [],
        "evidence_placement_slots": [0],
        "forbidden_direct_event_patterns": [],
        "action_execution_contract": contract or {},
        "structured_context": {
            "event": {"event_id": event_id},
            "session_memory_updates": [],
            "dialogue_contract": {"evidence_deadline_user_turn": 3},
        },
    }
    return {
        "session_id": "S001",
        "trajectory_id": "traj_test",
        "session_type": f"{status}_evidence" if status != "cancelled" else "cancellation_evidence",
        "event_status_after_session": status,
        "mapped_action": mapped_action,
        "financial_task": plan["financial_task"],
        "turns": [
            {"speaker": "user", "text": user},
            {"speaker": "assistant", "text": assistant},
        ],
        "cue_annotations": annotations or [],
        "action_resolution": resolution or {},
        "plan": plan,
    }


@pytest.mark.parametrize(
    ("event_id", "text"),
    [
        ("relationship_childbirth", "막내 태어나서 목적저축을 보려고요"),
        ("relationship_childbirth", "애가 하나 더 생겨서 확인하려고요"),
        ("career_leave_of_absence", "회사를 잠깐 쉬고 있어서 점검하려고요"),
        ("housing_home_purchase", "집을 샀어요. 대출 설정을 볼게요"),
    ],
)
def test_direct_event_paraphrases_fail(event_id, text):
    codes = {
        item["code"] for item in _validator().validate_session(_session(event_id=event_id, user=text))
    }
    assert "direct_event_disclosure" in codes
    assert "forbidden_event_paraphrase" in codes


def test_indirect_state_and_financial_evidence_passes_disclosure():
    dimensions = [
        {"dimension_id": "child_count", "role": "entity_change", "required": True},
        {"dimension_id": "saving", "role": "financial_consequence", "required": True},
    ]
    user = "가족 금융 등록 대상이 한 명 늘어서 새 목적저축 설정을 확인해 주세요"
    annotations = [
        {"turn_index": 0, "cue_type": "task_intent", "evidence_text": "목적저축 설정", "evidence_dimension_id": None},
        {"turn_index": 0, "cue_type": "entity_change", "evidence_text": "등록 대상이 한 명 늘어서", "evidence_dimension_id": "child_count"},
        {"turn_index": 0, "cue_type": "financial_consequence", "evidence_text": "새 목적저축 설정", "evidence_dimension_id": "saving"},
    ]
    codes = {item["code"] for item in _validator().validate_session(_session(user=user, dimensions=dimensions, annotations=annotations))}
    assert "direct_event_disclosure" not in codes
    assert "insufficient_event_evidence" not in codes


@pytest.mark.parametrize("text", ["이사 계획", "이사할 예정", "이사했어요"])
def test_korean_event_label_suffixes_match(text):
    assert contains_contextual_event_label(text, "이사")


@pytest.mark.parametrize("text", ["특이사항 없습니다", "이상 거래 없음", "회사 사정입니다"])
def test_korean_event_label_substrings_do_not_match(text):
    assert not contains_contextual_event_label(text, "이사")


def test_forbidden_label_contract_uses_contextual_boundary_too():
    session = _session(user="계좌 특이사항 없습니다. 목적저축 설정을 확인해 주세요")
    session["plan"]["must_not_include_terms"] = ["이사"]
    codes = {item["code"] for item in _validator().validate_session(session)}
    assert "event_label_leakage" not in codes
    assert "forbidden_term" not in codes


def _high_risk_session(missing: list[str], completion: bool = True) -> dict:
    grounded = {"source_account": "주거래계좌"}
    contract = {
        "action_mode": "pending_required_information" if missing else "ready_for_confirmation",
        "required_slots": ["source_account", "amount", "recurrence_day", "explicit_confirmation"],
        "grounded_slots": grounded if missing else {**grounded, "amount": 100000, "recurrence_day": 10},
        "missing_slots": missing,
        "completion_allowed": not missing,
        "confirmation_required": True,
    }
    resolution = {
        "mode": "executed_after_confirmation" if completion else contract["action_mode"],
        "provided_slots": contract["grounded_slots"],
        "missing_slots": missing,
        "explicit_confirmation_turn_index": 0 if not missing else None,
        "completion_turn_index": 1 if completion else None,
    }
    return _session(
        mapped_action="FA-09",
        status="no_event",
        user="주거래계좌에서 100,000원을 매달 10일에 넣는 내용 확인했고 진행해 주세요" if not missing else "목적저축을 진행해 주세요",
        assistant="설정이 완료되었습니다" if completion else "필요한 정보를 확인한 뒤 진행할 수 있습니다",
        contract=contract,
        resolution=resolution,
    )


def test_high_risk_missing_amount_cannot_complete():
    codes = {item["code"] for item in _validator().validate_session(_high_risk_session(["amount", "recurrence_day"]))}
    assert "high_risk_missing_required_slot" in codes
    assert "high_risk_false_completion" in codes


def test_high_risk_all_slots_and_confirmation_may_complete():
    codes = {item["code"] for item in _validator().validate_session(_high_risk_session([]))}
    assert not {"high_risk_missing_required_slot", "high_risk_false_completion", "high_risk_missing_confirmation"}.intersection(codes)


def test_high_risk_generic_slot_value_is_unplanned():
    session = _high_risk_session([], completion=False)
    session["action_resolution"]["provided_slots"]["amount"] = "해당 금액"
    codes = {item["code"] for item in _validator().validate_session(session)}
    assert "high_risk_unplanned_slot_value" in codes


def test_high_risk_generic_words_do_not_realize_grounded_slots():
    session = _high_risk_session([])
    session["turns"][0]["text"] = "주거래계좌에서 해당 금액을 선택한 날짜에 넣는 내용에 동의해요"
    codes = {item["code"] for item in _validator().validate_session(session)}
    assert "high_risk_missing_required_slot" in codes
    assert "high_risk_false_completion" in codes


def test_bank_policy_neutral_passes_and_fee_waiver_fails():
    neutral = _session(user="공동 생활비 계좌 관리 방법을 앱에서 확인해 주세요")
    assert "unsupported_bank_policy_claim" not in {item["code"] for item in _validator().validate_session(neutral)}
    invented = _session(assistant="이 상품은 수수료가 없습니다")
    assert "unsupported_bank_policy_claim" in {item["code"] for item in _validator().validate_session(invented)}


def test_trajectory_joint_account_contradiction_fails():
    plans = []
    sessions = []
    for index, assistant in enumerate(("공동명의 계좌 등록이 가능합니다", "공동명의 계좌는 만들 수 없습니다"), start=1):
        plan = {"session_id": f"S{index:03d}", "session_type": "routine_financial", "event_status_after_session": "no_event", "financial_task": "계좌 확인", "structured_context": {"session_memory_updates": []}}
        session = {"session_id": plan["session_id"], "trajectory_id": "traj_test", "session_type": "routine_financial", "event_status_after_session": "no_event", "mapped_action": "FA-01", "financial_task": "계좌 확인", "turns": [{"speaker": "user", "text": "계좌를 확인해 주세요"}, {"speaker": "assistant", "text": assistant}], "cue_annotations": [], "plan": plan}
        plans.append(plan); sessions.append(session)
    report = audit_dialogue_generation(plans, sessions, [], load_life_event_templates(), 2, 8)
    assert report["violation_counts"]["bank_policy_contradiction"] >= 1


def test_exact_opening_repeated_three_times_fails_diversity():
    plans = []
    sessions = []
    for index in range(1, 4):
        sid = f"S{index:03d}"
        plan = {"session_id": sid, "session_type": "routine_financial", "event_status_after_session": "no_event", "financial_task": "잔액 확인", "structured_context": {"session_memory_updates": []}}
        session = {"session_id": sid, "trajectory_id": "traj_test", "session_type": "routine_financial", "event_status_after_session": "no_event", "mapped_action": "FA-01", "financial_task": "잔액 확인", "turns": [{"speaker": "user", "text": "잔액을 확인해 주세요"}, {"speaker": "assistant", "text": f"{index}번째 결과입니다"}], "cue_annotations": [], "plan": plan}
        plans.append(plan); sessions.append(session)
    report = audit_dialogue_generation(plans, sessions, [], load_life_event_templates(), 2, 8)
    assert report["violation_counts"]["duplicate_opening_over_limit"] == 1


def _lifecycle_audit_records(
    *, status: str, count: int, phrase: str, phrase_count: int
) -> tuple[list[dict], list[dict]]:
    plans: list[dict] = []
    sessions: list[dict] = []
    session_type = (
        "cancellation_evidence" if status == "cancelled" else f"{status}_evidence"
    )
    for index in range(1, count + 1):
        sid = f"S{index:03d}"
        plan = {
            "session_id": sid,
            "session_type": session_type,
            "event_status_after_session": status,
            "financial_task": "계좌 상태 확인",
            "evidence_dimensions": [],
            "structured_context": {"session_memory_updates": []},
        }
        user = f"계좌 상태를 {index}번째로 확인해 주세요"
        assistant = phrase if index <= phrase_count else f"서로 다른 안내 {index}입니다"
        session = {
            "session_id": sid,
            "trajectory_id": "traj_test",
            "session_type": session_type,
            "event_status_after_session": status,
            "mapped_action": "FA-01",
            "financial_task": "계좌 상태 확인",
            "turns": [
                {"speaker": "user", "text": user},
                {"speaker": "assistant", "text": assistant},
            ],
            "cue_annotations": [],
            "plan": plan,
        }
        plans.append(plan)
        sessions.append(session)
    return plans, sessions


def test_lifecycle_phrase_ratio_ignores_a_single_small_stratum_occurrence():
    plans, sessions = _lifecycle_audit_records(
        status="cancelled", count=6, phrase="없던 일이 됐습니다", phrase_count=1
    )

    report = audit_dialogue_generation(
        plans, sessions, [], load_life_event_templates(), 2, 8
    )

    assert report["violation_counts"].get(
        "lifecycle_exact_phrase_overconcentration", 0
    ) == 0


def test_lifecycle_phrase_ratio_counts_only_longest_nested_phrase():
    plans, sessions = _lifecycle_audit_records(
        status="occurred",
        count=20,
        phrase="이번에 실제로 반영됐습니다",
        phrase_count=4,
    )

    report = audit_dialogue_generation(
        plans, sessions, [], load_life_event_templates(), 2, 8
    )
    concentrations = report["surface_diversity"][
        "lifecycle_exact_phrase_concentrations"
    ]

    assert [item["phrase"] for item in concentrations] == ["이번에 실제로 반영"]


def test_lifecycle_phrase_gate_waits_for_complete_status_stratum():
    plans, sessions = _lifecycle_audit_records(
        status="occurred",
        count=20,
        phrase="이번에 실제로 반영됐습니다",
        phrase_count=4,
    )

    report = audit_dialogue_generation(
        plans, sessions[:10], [], load_life_event_templates(), 2, 8
    )

    assert report["violation_counts"].get(
        "lifecycle_exact_phrase_overconcentration", 0
    ) == 0
    assert report["surface_diversity"]["lifecycle_status_generation_coverage"] == {
        "occurred": {"planned": 20, "successful": 10, "complete": False}
    }


def test_partial_audit_rates_use_successful_session_denominator():
    plans, sessions = _lifecycle_audit_records(
        status="occurred", count=4, phrase="서로 다른 표현", phrase_count=0
    )
    sessions = sessions[:2]
    sessions[0]["generation_metadata"] = {"repair_count": 1}

    report = audit_dialogue_generation(
        plans, sessions, [], load_life_event_templates(), 2, 8
    )

    assert report["summary"]["repair_session_rate"] == 0.5
    assert report["summary"]["repair_session_rate_planned"] == 0.25


def test_planner_realization_is_deterministic_and_varied():
    paths = RepoPaths.default()
    trajectory = Trajectory.model_validate(json.loads((paths.root / "tests/fixtures/trajectories/traj_001.json").read_text(encoding="utf-8")))
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, load_locale("ko_KR", paths), paths)
    first = planner.build_plans(trajectory, seed=42)
    second = planner.build_plans(trajectory, seed=42)
    signature = lambda plans: [(item.session_id, item.evidence_realization_strategy, item.evidence_placement_strategy, item.lifecycle_surface_variant_id) for item in plans]
    assert signature(first) == signature(second)
    assert len({item.evidence_placement_strategy for item in first if item.evidence_dimensions}) >= 3
    linked_settings_review = next(
        item
        for item in first
        if item.task_template_id == "employment_end_linked_settings"
    )
    assert linked_settings_review.action_execution_contract.action_mode == "information_only"
    mortgage_execution = next(
        item
        for item in first
        if item.task_template_id == "home_purchase_mortgage_execute"
    )
    assert mortgage_execution.action_execution_contract.action_mode != "information_only"


def _review_record(**overrides):
    reviewer = {
        "natural_korean_dialogue": True,
        "event_task_alignment": True,
        "lifecycle_calibration": True,
        "memory_grounding": True,
        "assistant_semantic_leakage": True,
        "high_risk_safety": True,
        "event_implicit_but_recoverable": True,
        "comments": "checked",
    }
    reviewer.update(overrides)
    return {"evaluator_only": {"session_id": "S001"}, "reviewer": reviewer}


def test_human_review_null_and_critical_failure_block_pass():
    assert score_records([_review_record(comments=None)])["decision"] == "FAIL"
    assert score_records([_review_record(high_risk_safety=False)])["decision"] == "FAIL"
    assert score_records([_review_record()])["decision"] == "PASS"


def test_production_requires_human_review_pass_even_after_automated_gate(tmp_path):
    with pytest.raises(SystemExit, match="require-human-review-pass"):
        generate_main(
            [
                "--trajectories-dir",
                "tests/fixtures/trajectories",
                "--plans-dir",
                "tests/fixtures/plans",
                "--exclude-trajectory-id",
                "traj_001",
                "--canary-manifest",
                str(tmp_path / "manifest.json"),
                "--require-canary-pass",
                str(tmp_path / "automated.json"),
                "--confirm-multi-trajectory-generation",
                "--output-dir",
                str(tmp_path / "sessions"),
                "--execute",
            ]
        )


def test_review_pass_file_is_required(tmp_path):
    path = tmp_path / "review.json"
    path.write_text('{"decision":"FAIL"}', encoding="utf-8")
    # Both the neutral gate and the deprecated human alias reject a non-PASS
    # decision with the same producer-agnostic message.
    for gate in (require_review_pass, require_human_review_pass):
        with pytest.raises(ValueError, match="review decision"):
            gate(path)


def test_review_pass_accepts_pass_from_any_producer(tmp_path):
    # judge_review_decision.json and human_review_decision.json are
    # interchangeable at the gate: both are PASS decision files.
    for name, producer in (("judge_review_decision.json", "llm_judge"),
                           ("human_review_decision.json", "human")):
        path = tmp_path / name
        path.write_text(
            f'{{"decision":"PASS","producer":"{producer}"}}', encoding="utf-8"
        )
        require_review_pass(path)  # must not raise


def test_regression_sampler_selects_evidence_high_risk_stale_repaired_and_variants():
    plans = [
        {"session_id": "S001", "session_type": "occurred_evidence", "mapped_action": "FA-01", "financial_task": "조회"},
        {"session_id": "S002", "session_type": "routine_financial", "mapped_action": "FA-09", "financial_task": "저축"},
        {"session_id": "S003", "session_type": "stale_recall_session", "mapped_action": "FA-01", "financial_task": "이전 값"},
        {"session_id": "S004", "session_type": "hard_negative", "hard_negative_type": "existing_state_negative", "hard_negative_surface_variant_id": "v1", "mapped_action": "FA-01", "financial_task": "조회"},
        {"session_id": "S005", "session_type": "routine_financial", "mapped_action": "FA-01", "financial_task": "조회"},
    ]
    selected, reasons = select_regression_plans(plans, {"S005"})
    assert {item["session_id"] for item in selected} == {"S001", "S002", "S003", "S004", "S005"}
    assert "previously_repaired" in reasons["S005"]


def test_plan_serialization_round_trip_preserves_contracts():
    plan = DialogueGenerationPlan.model_validate_json(
        next(open("tests/fixtures/plans/plans_traj_001.jsonl", encoding="utf-8"))
    )
    assert DialogueGenerationPlan.model_validate(plan.model_dump(mode="json")) == plan


def _transfer_session(user_turns: list[str], assistant_turns: list[str], *, amount=None, day=None) -> dict:
    grounded = {}
    if amount is not None:
        grounded["amount"] = amount
    if day is not None:
        grounded["recurrence_day"] = day
    contract = {
        "action_mode": "pending_required_information",
        "required_slots": ["source_account", "amount", "recurrence_day", "explicit_confirmation"],
        "grounded_slots": grounded,
        "missing_slots": [],
        "completion_allowed": False,
        "confirmation_required": True,
    }
    turns = []
    for i in range(max(len(user_turns), len(assistant_turns))):
        if i < len(user_turns):
            turns.append({"speaker": "user", "text": user_turns[i]})
        if i < len(assistant_turns):
            turns.append({"speaker": "assistant", "text": assistant_turns[i]})
    sess = _session(mapped_action="FA-08", status="no_event", contract=contract,
                    resolution={"mode": "pending_required_information", "provided_slots": {}, "missing_slots": []})
    sess["turns"] = turns
    return sess


def test_assistant_premature_amount_disclosure_flagged():
    sess = _transfer_session(
        user_turns=["생활비 정기이체 설정하고 싶어요", "네 맞아요"],
        assistant_turns=["받는 분 알려주세요", "지금 나가는 30만원으로 진행할까요?"],
        amount=300000,
    )
    codes = {i["code"] for i in _validator().validate_session(sess)}
    assert "assistant_premature_slot_disclosure" in codes


def test_user_stated_amount_first_not_flagged():
    sess = _transfer_session(
        user_turns=["매달 30만원씩 생활비 이체 설정할게요", "네 맞아요"],
        assistant_turns=["30만원으로 준비할게요", "확인했습니다"],
        amount=300000,
    )
    codes = {i["code"] for i in _validator().validate_session(sess)}
    assert "assistant_premature_slot_disclosure" not in codes


def test_assistant_premature_day_disclosure_flagged():
    sess = _transfer_session(
        user_turns=["자동납부 설정하고 싶어요", "확인해볼게요"],
        assistant_turns=["매달 21일 납부 조건으로 진행할게요", "네"],
        day=21,
    )
    codes = {i["code"] for i in _validator().validate_session(sess)}
    assert "assistant_premature_slot_disclosure" in codes


def _calc_session(user_turns, assistant_turns, task="적금 만기금액 계산") -> dict:
    sess = _session(mapped_action="FA-01", status="no_event")
    sess["financial_task"] = task
    sess["plan"]["financial_task"] = task
    turns = []
    for i in range(max(len(user_turns), len(assistant_turns))):
        if i < len(user_turns):
            turns.append({"speaker": "user", "text": user_turns[i]})
        if i < len(assistant_turns):
            turns.append({"speaker": "assistant", "text": assistant_turns[i]})
    sess["turns"] = turns
    return sess


def test_calc_result_without_user_amount_flagged():
    sess = _calc_session(
        user_turns=["적금 만기금액 계산해줘", "매달 일정 금액을 2년간 넣을게요", "네 계산해주세요"],
        assistant_turns=["금액이랑 기간 알려주세요", "금리는요?", "말씀하신 조건으로 계산한 예상 만기금액을 화면에서 확인하실 수 있어요"],
    )
    codes = {i["code"] for i in _validator().validate_session(sess)}
    assert "calc_result_without_required_input" in codes


def test_calc_result_with_user_amount_not_flagged():
    sess = _calc_session(
        user_turns=["적금 만기금액 계산해줘", "매달 50만원을 2년간 넣을게요", "네 계산해주세요"],
        assistant_turns=["금액이랑 기간 알려주세요", "금리는요?", "말씀하신 조건으로 계산한 예상 만기금액을 화면에서 확인하실 수 있어요"],
    )
    codes = {i["code"] for i in _validator().validate_session(sess)}
    assert "calc_result_without_required_input" not in codes


def _pending_transfer_contract(amount: int = 200000) -> tuple[dict, dict]:
    contract = {
        "action_mode": "pending_required_information",
        "required_slots": ["source_account", "amount", "recurrence_day", "explicit_confirmation"],
        "grounded_slots": {"source_account": "main_checking", "amount": amount},
        "missing_slots": ["recurrence_day"],
        "completion_allowed": False,
        "confirmation_required": True,
    }
    resolution = {
        "mode": "pending_required_information",
        "provided_slots": dict(contract["grounded_slots"]),
        "missing_slots": ["recurrence_day"],
        "explicit_confirmation_turn_index": None,
        "completion_turn_index": None,
    }
    return contract, resolution


def test_reconcile_drops_amount_not_surfaced_in_dialogue():
    # Persona-constant amount (200,000) stamped onto a dialogue that only talks
    # about a 3,000,000 funeral transfer -> amount must move to missing_slots.
    contract, resolution = _pending_transfer_contract(200000)
    turns = [{"speaker": "user", "text": "장례비 300만원을 주거래계좌에서 보내야 해요"}]
    new_contract, new_resolution, dropped = reconcile_provided_slots(contract, resolution, turns)
    assert dropped == ["amount"]
    assert "amount" not in new_contract["grounded_slots"]
    assert "amount" not in new_resolution["provided_slots"]
    assert "amount" in new_contract["missing_slots"]
    # source_account is surfaced ("주거래계좌") and must be preserved.
    assert new_contract["grounded_slots"]["source_account"] == "main_checking"
    assert new_contract["action_mode"] == "pending_required_information"


def test_reconcile_keeps_amount_actually_stated_in_dialogue():
    contract, resolution = _pending_transfer_contract(200000)
    turns = [{"speaker": "user", "text": "주거래계좌에서 매달 20만원씩 넣고 싶어요"}]
    new_contract, new_resolution, dropped = reconcile_provided_slots(contract, resolution, turns)
    assert dropped == []
    assert new_contract["grounded_slots"]["amount"] == 200000
    assert new_resolution["provided_slots"]["amount"] == 200000


def test_reconcile_downgrades_ready_session_that_loses_a_required_slot():
    contract = {
        "action_mode": "ready_for_confirmation",
        "required_slots": ["source_account", "amount", "recurrence_day", "explicit_confirmation"],
        "grounded_slots": {"source_account": "main_checking", "amount": 500000, "recurrence_day": 10},
        "missing_slots": [],
        "completion_allowed": True,
        "confirmation_required": True,
    }
    resolution = {
        "mode": "executed_after_confirmation",
        "provided_slots": dict(contract["grounded_slots"]),
        "missing_slots": [],
        "explicit_confirmation_turn_index": 0,
        "completion_turn_index": 1,
    }
    # Dialogue surfaces the account and day but never the 500,000 amount.
    turns = [{"speaker": "user", "text": "주거래계좌에서 매달 10일에 넣어주세요"}]
    new_contract, new_resolution, dropped = reconcile_provided_slots(contract, resolution, turns)
    assert dropped == ["amount"]
    assert new_contract["action_mode"] == "pending_required_information"
    assert new_contract["completion_allowed"] is False
    assert new_resolution["mode"] == "pending_required_information"
    assert new_resolution["completion_turn_index"] is None
    assert new_resolution["explicit_confirmation_turn_index"] is None


def test_reconcile_is_noop_for_information_only():
    contract = {"action_mode": "information_only", "grounded_slots": {}, "missing_slots": []}
    resolution = {"mode": "information_only", "provided_slots": {}, "missing_slots": []}
    new_contract, new_resolution, dropped = reconcile_provided_slots(contract, resolution, [])
    assert dropped == []
    assert new_contract == contract
    assert new_resolution == resolution


def test_reconciled_session_passes_provided_slot_grounding_check():
    contract, resolution = _pending_transfer_contract(200000)
    session = _session(
        mapped_action="FA-09",
        status="no_event",
        user="장례비 300만원을 주거래계좌에서 보내야 해요",
        contract=contract,
        resolution=resolution,
    )
    codes = {item["code"] for item in _validator().validate_session(session)}
    assert "provided_slot_not_grounded_in_dialogue" in codes  # before reconcile

    new_contract, new_resolution, _ = reconcile_provided_slots(
        contract, resolution, session["turns"]
    )
    session["plan"]["action_execution_contract"] = new_contract
    session["action_resolution"] = new_resolution
    codes = {item["code"] for item in _validator().validate_session(session)}
    assert "provided_slot_not_grounded_in_dialogue" not in codes  # after reconcile


def test_reconcile_regrounds_amount_from_event_params():
    # The persona-constant 200,000 is not in the dialogue, but the event's own
    # rent (400,000) is -- so re-ground rather than drop to missing_slots.
    contract, resolution = _pending_transfer_contract(200000)
    turns = [{"speaker": "user", "text": "주거래계좌에서 월세 40만원 내고 있어요"}]
    new_contract, new_resolution, changed = reconcile_provided_slots(
        contract, resolution, turns, slot_candidates={"amount": [400000]}
    )
    assert changed == ["amount"]
    assert new_contract["grounded_slots"]["amount"] == 400000
    assert new_resolution["provided_slots"]["amount"] == 400000
    assert "amount" not in new_contract["missing_slots"]


def test_reconcile_prefers_event_param_over_a_stale_amount_also_spoken():
    # Customers narrate both sides of a change; the stale half is just as
    # visible, so literal visibility alone must not keep it.
    contract, resolution = _pending_transfer_contract(650000)
    turns = [
        {
            "speaker": "user",
            "text": "주거래계좌요. 예전엔 65만원 냈었고 지금은 40만원이에요",
        }
    ]
    new_contract, _, changed = reconcile_provided_slots(
        contract, resolution, turns, slot_candidates={"amount": [400000]}
    )
    assert changed == ["amount"]
    assert new_contract["grounded_slots"]["amount"] == 400000


def test_reconcile_keeps_amount_the_user_referred_to_without_saying_it():
    # "금액은 지금 나가는 정도로" points at an existing standing action; that is a
    # real dialogue reference, so the amount stays grounded.
    contract, resolution = _pending_transfer_contract(650000)
    turns = [
        {"speaker": "user", "text": "주거래계좌에서요. 금액은 지금 나가는 정도로 해주세요"}
    ]
    new_contract, new_resolution, changed = reconcile_provided_slots(
        contract, resolution, turns, reference_values={"650000"}
    )
    assert changed == []
    assert new_contract["grounded_slots"]["amount"] == 650000
    assert new_resolution["provided_slots"]["amount"] == 650000


def test_reconcile_ignores_a_bare_reference_unrelated_to_the_amount():
    # A cancellation session saying "주소는 원래 그대로예요" refers to something
    # else entirely and must not ground the persona-constant amount.
    contract, resolution = _pending_transfer_contract(650000)
    turns = [{"speaker": "user", "text": "주거래계좌요. 주소는 원래 그대로예요"}]
    _, new_resolution, changed = reconcile_provided_slots(
        contract, resolution, turns, reference_values={"650000"}
    )
    assert changed == ["amount"]
    assert "amount" not in new_resolution["provided_slots"]


def test_reconcile_grounds_a_required_slot_the_planner_never_filled():
    # The alias gap left `amount` in neither provided nor missing on personas
    # with no standing amount to borrow, while the value sat in the transcript.
    contract, resolution = _pending_transfer_contract(200000)
    contract["grounded_slots"] = {"source_account": "main_checking"}
    resolution["provided_slots"] = {"source_account": "main_checking"}
    turns = [{"speaker": "user", "text": "주거래계좌에서 월세 40만원 나가고 있어요"}]
    new_contract, new_resolution, changed = reconcile_provided_slots(
        contract, resolution, turns, slot_candidates={"amount": [400000]}
    )
    assert changed == ["amount"]
    assert new_contract["grounded_slots"]["amount"] == 400000
    assert new_resolution["provided_slots"]["amount"] == 400000


def test_event_slot_candidates_covers_every_money_carrying_event_param():
    # Each of these lives on a different life event; none may fall through to the
    # persona's standing-action amount (the root cause of the 201-session defect).
    aliases = {"amount": ["amount", "new_rent_amount", "monthly_edu_cost", "one_off_cost"]}
    for param, value in [
        ("new_rent_amount", 400000),
        ("monthly_edu_cost", 300000),
        ("one_off_cost", 5000000),
    ]:
        plan = {"structured_context": {"event": {"params": {param: value}}}}
        assert event_slot_candidates(plan, aliases) == {"amount": [value]}


def test_standing_action_amounts_reads_only_numeric_amounts():
    plan = {
        "structured_context": {
            "current_standing_actions": [
                {"action_id": "SO_rent", "amount": 650000},
                {"action_id": "SO_none", "amount": None},
                {"action_id": "SO_flag", "amount": True},
            ]
        }
    }
    assert standing_action_amounts(plan) == frozenset({"650000"})


def _contract_for(task_template_id: str, mapped_action: str, financial_task: str):
    paths = RepoPaths.default()
    planner = EvidencePlanner(
        load_life_event_templates(paths), load_locale("ko_KR", paths), paths
    )
    plan = DialogueGenerationPlan(
        session_id="S001",
        trajectory_id="traj_test",
        month_index=0,
        age=30,
        transition_order=0,
        window_index=1,
        position_in_window=1,
        window_event_instance_id="ev",
        session_type="occurred_evidence",
        event_status_after_session="occurred",
        mapped_action=mapped_action,
        financial_task=financial_task,
        task_template_id=task_template_id,
    )
    return planner._action_execution_contract(plan)


def test_cancelling_an_existing_arrangement_needs_only_its_identity():
    # Stopping a transfer must not demand a payee and an amount the customer has
    # no reason to restate -- only which arrangement, plus confirmation.
    contract = _contract_for(
        "dependent_end_transfer_stop", "FA-08", "기존 지원 정기이체 변경"
    )
    assert set(contract.required_slots) == {"target_action_ids", "explicit_confirmation"}
    assert "amount" not in contract.required_slots
    assert "destination_account" not in contract.required_slots


def test_cancelling_a_reservation_needs_nothing_but_confirmation():
    # The scheduled change was never created, so there is no arrangement to name.
    contract = _contract_for(
        "housing_move_jeonse_payment_cancel", "FA-08", "주거비 납부 변경 예약 취소"
    )
    assert contract.required_slots == ["explicit_confirmation"]
    assert contract.action_mode == "ready_for_confirmation"


def test_declared_lookup_stays_information_only_despite_the_noun_변경():
    # "주거 변경 비용 이체 한도 확인" is a pure lookup, but the noun "주거 변경"
    # trips any substring test for the verb 변경.
    contract = _contract_for(
        "housing_move_other_transfer_limit", "FA-07", "주거 변경 비용 이체 한도 확인"
    )
    assert contract.action_mode == "information_only"
    assert contract.required_slots == []


def test_undeclared_template_still_falls_back_to_the_task_string():
    # Templates that declare no subtype keep the old heuristic: this one really
    # does execute even though it ends in 확인.
    contract = _contract_for(
        "home_purchase_mortgage_execute", "FA-10", "주담대 실행과 상환계좌 확인"
    )
    assert contract.action_mode != "information_only"
    assert "amount_or_schedule" in contract.required_slots


def _dependent_end_task(active_action_types: list[str]) -> str:
    """Which occurred task the planner picks for a persona holding these actions."""
    paths = RepoPaths.default()
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, load_locale("ko_KR", paths), paths)
    actions = [SimpleNamespace(type=name) for name in active_action_types]
    task, _score, _reasons, _grounding = planner.select_task_template(
        templates["relationship_dependent_end"],
        "occurred",
        {},
        SimpleNamespace(),
        SimpleNamespace(latest=lambda path: None),
        [],
        [],
        [],
        actions,
        [],
        random.Random(0),
    )
    return task["task_template_id"]


def test_stop_task_requires_a_transfer_that_actually_exists():
    # Either support transfer alone is enough -- required_action_types could not
    # express this, being an AND gate.
    assert _dependent_end_task(["parent_support_transfer"]) == "dependent_end_transfer_stop"
    assert (
        _dependent_end_task(["spouse_living_expense_transfer"])
        == "dependent_end_transfer_stop"
    )


def test_persona_with_nothing_to_stop_gets_the_review_task_instead():
    # Previously the stop task was assigned anyway, so the dialogue asserted a
    # support transfer the trajectory never created.
    assert (
        _dependent_end_task(["rent_autopay", "pension_contribution"])
        == "dependent_end_registration_review"
    )
    assert _dependent_end_task([]) == "dependent_end_registration_review"


def test_the_two_dependent_end_tasks_are_mutually_exclusive():
    # Exactly one is valid per persona, so selection never falls to the rng
    # tie-break and existing assignments cannot drift.
    for actions in (["parent_support_transfer"], ["rent_autopay"], []):
        picked = {_dependent_end_task(actions) for _ in range(5)}
        assert len(picked) == 1


def test_review_task_executes_nothing():
    contract = _contract_for(
        "dependent_end_registration_review", "FA-08", "부양가족 등록 정보 확인"
    )
    assert contract.action_mode == "information_only"
    assert contract.required_slots == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("장례비로 오백만원 나갔어요", "5000000"),
        ("매달 이백만원 정도 상환돼요", "2000000"),
        ("오십만 원씩 보내고 있어요", "500000"),
        ("백만원 정도예요", "1000000"),
        ("천만원 들어왔어요", "10000000"),
        ("삼천오백만원이요", "35000000"),
        ("일억원 대출이요", "100000000"),
    ],
)
def test_hangul_numeral_amounts_are_read(text, expected):
    # Digits-only parsing read these sessions as stating no amount, so a
    # correctly grounded slot looked ungrounded and was dropped.
    assert expected in _numbers_in_text(text)


@pytest.mark.parametrize("text", ["십일 정도 걸려요", "이번 달에요", "삼일 뒤에", "백화점에서요"])
def test_hangul_words_that_are_not_amounts_stay_unread(text):
    assert _numbers_in_text(text) == []


def test_amount_stated_in_hangul_keeps_the_slot_grounded():
    contract, resolution = _pending_transfer_contract(5000000)
    turns = [{"speaker": "user", "text": "주거래계좌에서 장례비 오백만원 보냈어요"}]
    _, new_resolution, changed = reconcile_provided_slots(contract, resolution, turns)
    assert changed == []
    assert new_resolution["provided_slots"]["amount"] == 5000000


def test_a_zero_amount_is_not_grounded():
    # housing_move sets new_rent_amount: 0 when moving in with family; grounding
    # it would oblige the dialogue to have the customer say "0원".
    contract = _contract_for(
        "housing_move_family_contribution_prepare", "FA-08", "생활비 분담 정기이체 준비"
    )
    assert "amount" not in (contract.grounded_slots or {})
    assert "amount" in contract.missing_slots


def test_a_zero_event_amount_does_not_fall_through_to_the_persona():
    # Skipping the zero and continuing the search re-grounded the persona's old
    # rent autopay figure for a household that now pays no rent -- the exact
    # contamination that put one constant on every flagged session.
    paths = RepoPaths.default()
    planner = EvidencePlanner(
        load_life_event_templates(paths), load_locale("ko_KR", paths), paths
    )
    plan = DialogueGenerationPlan(
        session_id="S001",
        trajectory_id="traj_test",
        month_index=0,
        age=30,
        transition_order=0,
        window_index=1,
        position_in_window=1,
        window_event_instance_id="ev",
        session_type="occurred_evidence",
        event_status_after_session="occurred",
        mapped_action="FA-08",
        financial_task="생활비 분담 정기이체 준비",
        task_template_id="housing_move_family_contribution_prepare",
        structured_context={
            "event": {"params": {"new_rent_amount": 0}},
            "current_standing_actions": [
                {"action_id": "SO_rent", "type": "rent_autopay", "amount": 650000}
            ],
        },
    )
    contract = planner._action_execution_contract(plan)
    assert "amount" not in (contract.grounded_slots or {})
    assert "amount" in contract.missing_slots
