"""Stage 3 Multi-hop items require two grounded memory facts."""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.evaluate_benchmark_items import _build_stage3_prompt
from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.benchmark.multihop import (
    MultiHopFact,
    Stage3MultiHopTarget,
    _amount_evidence_status,
    _fact_surface_error,
    _is_meaningful_representative,
    _representative_rank,
    _second_hop_shortcut,
    audit_stage3_multihop_items,
    build_stage3_multihop_targets,
)


def _fact(
    *,
    event: str,
    checkpoint: int,
    value,
    old_value,
    date: str,
    path: str = "employment.employment_status",
) -> MultiHopFact:
    return MultiHopFact(
        fact_id=f"{event}:{path}",
        trajectory_id="traj_001",
        prefix_id=f"traj_001_pfx{checkpoint:03d}",
        checkpoint_session_count=checkpoint,
        event_instance_id=event,
        event_id="career_test",
        event_label="테스트 사건",
        memory_path=path,
        operation="update",
        old_value=old_value,
        new_value=value,
        projected_value=value,
        evidence_sessions=(f"S{checkpoint:03d}",),
        evidence_turns=(f"S{checkpoint:03d}:0",),
        evidence_date=date,
    )


def _target(
    first: MultiHopFact,
    second: MultiHopFact,
    *,
    derivation: str = "state_sequence",
    answer=None,
    option_pool=("employed", "on_leave", "unemployed", "retired"),
) -> Stage3MultiHopTarget:
    return Stage3MultiHopTarget(
        canonical_target_id=f"traj_001:mh:{derivation}:test",
        trajectory_id="traj_001",
        derivation_type=derivation,
        memory_path=first.memory_path,
        question_label=(
            "일회성 지출 합계"
            if derivation == "expense_aggregation"
            else "고용 상태"
        ),
        value_selector=("amount_krw" if derivation == "expense_aggregation" else "value"),
        option_pool_type=("numeric" if derivation == "expense_aggregation" else "categorical"),
        option_pool=option_pool,
        hops=(first, second),
        answer_value=(
            answer
            if answer is not None
            else [copy.deepcopy(first.projected_value), copy.deepcopy(second.projected_value)]
        ),
        first_visible_checkpoint=second.checkpoint_session_count,
        prefix_id=second.prefix_id,
        visible_session_ids=(first.evidence_sessions[0], second.evidence_sessions[0]),
    )


def _session(fact: MultiHopFact) -> dict:
    if fact.memory_path == "employment.employment_status":
        text = {
            "employed": "현재 회사에 다니고 있습니다.",
            "on_leave": "현재 휴직 중입니다.",
            "unemployed": "현재는 무직입니다.",
            "self_employed": "현재 자영업을 하고 있습니다.",
            "retired": "현재는 은퇴한 상태입니다.",
        }.get(fact.new_value, f"확인 값은 {fact.new_value}입니다.")
    elif fact.memory_path == "employment.income_stability":
        text = {
            "stable": "수입이 꾸준하고 안정적입니다.",
            "variable": "수입이 달마다 달라 변동적입니다.",
            "reduced": "수입이 이전보다 줄었습니다.",
            "unstable": "수입이 들쭉날쭉하고 불안정합니다.",
        }.get(fact.new_value, f"확인 값은 {fact.new_value}입니다.")
    elif fact.memory_path == "employment.salary_day":
        text = f"급여일은 {fact.new_value}일입니다."
    elif fact.memory_path == "cashflow.recent_one_off_expense":
        text = f"일회성 지출로 {fact.new_value:,}원이 나갔습니다."
    elif fact.memory_path == "housing.rent_amount":
        text = (
            "월세는 이제 내지 않아요."
            if fact.new_value in {None, 0}
            else f"월세는 {fact.new_value:,}원입니다."
        )
    else:
        text = f"확인 값은 {fact.new_value}입니다."
    return {
        "trajectory_id": fact.trajectory_id,
        "session_id": fact.evidence_sessions[0],
        "session_date": fact.evidence_date,
        "linked_event_instance_id": fact.event_instance_id,
        "event_status_after_session": "occurred",
        "turns": [
            {"speaker": "user", "text": text},
            {"speaker": "assistant", "text": "확인했습니다."},
        ],
        "cue_annotations": [
            {
                "cue_type": "memory_fact",
                "turn_index": 0,
                "evidence_text": text,
                "linked_memory_path": fact.memory_path,
                "linked_memory_operation": fact.operation,
                "linked_memory_value": fact.new_value,
            }
        ],
    }


def _update(fact: MultiHopFact) -> dict:
    return {
        "path": fact.memory_path,
        "operation": fact.operation,
        "old_value": fact.old_value,
        "new_value": fact.new_value,
        "source_event_instance_id": fact.event_instance_id,
        "evidence_turns": list(fact.evidence_turns),
    }


def test_multihop_sequence_item_uses_two_dates_without_session_ids():
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value="unemployed",
        value="employed",
        date="2020-01-15",
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value="employed",
        value="on_leave",
        date="2021-03-20",
    )

    item = ItemBuilder(seed=42, shuffle_options=True).build_stage3_multihop(
        [_target(first, second)]
    )[0]

    assert item.stage == "stage3_multi_hop_mcq"
    assert item.reasoning_type == "multi_hop"
    assert item.gold["answer_value"] == ["employed", "on_leave"]
    assert item.gold["hop_count"] == 2
    assert "2020년 1월 15일" in item.question
    assert "2021년 3월 20일" in item.question
    assert "각 상담 시점에 확인된 고용 상태를 시간순으로 올바르게" in item.question
    assert "나열한 것은 무엇인가" in item.question
    assert re.search(r"\bS\d+\b", item.question) is None
    assert all("2020년 1월 15일" not in option.text for option in item.options)
    assert all("2021년 3월 20일" not in option.text for option in item.options)
    assert len(item.options) == 4
    assert len({option.text for option in item.options}) == 4
    assert {option.error_type for option in item.options} == {
        None,
        "reversed_hop_order",
        "wrong_first_hop",
        "wrong_second_hop",
    }
    assert not {
        "first_state_carryover",
        "second_state_overgeneralization",
    } & {option.error_type for option in item.options}
    assert sum(option.correct for option in item.options) == 1
    assert item.gold["correct_option"] == next(
        option.option_id for option in item.options if option.correct
    )


def test_multihop_expense_item_sums_both_hops():
    path = "cashflow.recent_one_off_expense"
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value=None,
        value=1_000_000,
        date="2020-01-15",
        path=path,
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value=1_000_000,
        value=3_000_000,
        date="2021-03-20",
        path=path,
    )
    target = _target(
        first,
        second,
        derivation="expense_aggregation",
        answer=4_000_000,
        option_pool=(),
    )

    item = ItemBuilder().build_stage3_multihop([target])[0]

    assert item.gold["answer_value"] == 4_000_000
    assert "합계" in item.question
    assert any(option.text == "4,000,000원" and option.correct for option in item.options)
    assert len({option.text for option in item.options}) == 4


def test_multihop_equal_expense_options_are_positive_and_non_redundant():
    path = "cashflow.recent_one_off_expense"
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value=None,
        value=1_000_000,
        date="2020-01-15",
        path=path,
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value=1_000_000,
        value=1_000_000,
        date="2021-03-20",
        path=path,
    )
    target = _target(
        first,
        second,
        derivation="expense_aggregation",
        answer=2_000_000,
        option_pool=(),
    )

    item = ItemBuilder().build_stage3_multihop([target])[0]

    assert {option.error_type for option in item.options} == {
        None,
        "first_hop_only",
        "underestimated_sum",
        "overestimated_sum",
    }
    assert all(option.text != "0원" for option in item.options)
    assert len({option.text for option in item.options}) == 4
    assert any(option.text == "2,000,000원" and option.correct for option in item.options)


def test_multihop_audit_requires_dialogue_and_endpoint_prefix_grounding():
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value="unemployed",
        value="employed",
        date="2020-01-15",
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value="employed",
        value="on_leave",
        date="2021-03-20",
    )
    item = ItemBuilder().build_stage3_multihop([_target(first, second)])[0]
    prefix = {
        "trajectory_id": "traj_001",
        "prefix_id": second.prefix_id,
        "visible_sessions": ["S015", "S030"],
        "gold_life_events": [],
        "gold_memory_updates": [_update(first), _update(second)],
        "gold_action_decisions": [],
        "gold_full_memory_state": {},
        "gold_full_action_state": {},
        "repeats_previous": False,
    }
    sessions = {"traj_001": [_session(first), _session(second)]}

    report = audit_stage3_multihop_items(
        [item.model_dump(mode="json")],
        [prefix],
        sessions,
    )
    assert report["passed"] is True

    broken = item.model_dump(mode="json")
    broken["gold"]["hops"][0]["new_value"] = "wrong"
    broken_report = audit_stage3_multihop_items([broken], [prefix], sessions)
    assert broken_report["passed"] is False
    assert any(
        "prefix_missing_hop" in error
        for error in broken_report["failures"][0]["errors"]
    )


def test_multihop_audit_enforces_one_representative_per_path():
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value="unemployed",
        value="employed",
        date="2020-01-15",
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value="employed",
        value="on_leave",
        date="2021-03-20",
    )
    item = ItemBuilder().build_stage3_multihop([_target(first, second)])[0]
    prefix = {
        "trajectory_id": "traj_001",
        "prefix_id": second.prefix_id,
        "visible_sessions": ["S015", "S030"],
        "gold_life_events": [],
        "gold_memory_updates": [_update(first), _update(second)],
        "gold_action_decisions": [],
        "gold_full_memory_state": {},
        "gold_full_action_state": {},
        "repeats_previous": False,
    }
    sessions = {"traj_001": [_session(first), _session(second)]}
    representatives = {
        "traj_001": {
            first.memory_path: item.gold["canonical_target_id"],
        }
    }

    report = audit_stage3_multihop_items(
        [item.model_dump(mode="json")],
        [prefix],
        sessions,
        expected_representatives=representatives,
    )
    assert report["passed"] is True
    assert report["selection_failures"] == []

    duplicate_report = audit_stage3_multihop_items(
        [item.model_dump(mode="json"), item.model_dump(mode="json")],
        [prefix],
        sessions,
        expected_representatives=representatives,
    )
    assert duplicate_report["passed"] is False
    assert any(
        failure["error"] == "representative_item_count"
        for failure in duplicate_report["selection_failures"]
    )


def test_representative_rank_prefers_direct_changed_pair():
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value="unemployed",
        value="employed",
        date="2020-01-15",
    )
    middle = _fact(
        event="ev002",
        checkpoint=30,
        old_value="employed",
        value="on_leave",
        date="2021-03-20",
    )
    last = _fact(
        event="ev003",
        checkpoint=45,
        old_value="on_leave",
        value="employed",
        date="2022-05-10",
    )
    updates = {(15, "ev001"), (30, "ev002"), (45, "ev003")}
    skipped = _target(first, last)
    direct = _target(middle, last)

    assert _representative_rank(direct, updates) < _representative_rank(
        skipped, updates
    )
    assert _is_meaningful_representative(direct) is True

    unchanged_last = _fact(
        event="ev004",
        checkpoint=60,
        old_value="employed",
        value="employed",
        date="2023-07-12",
    )
    assert _is_meaningful_representative(
        _target(last, unchanged_last)
    ) is False


def test_multihop_candidate_builder_rejects_wrong_dependent_direction():
    path = "household.dependents"
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value=0,
        value=1,
        date="2020-01-15",
        path=path,
    )
    second = MultiHopFact(
        **{
            **_fact(
                event="ev002",
                checkpoint=30,
                old_value=1,
                value=0,
                date="2021-03-20",
                path=path,
            ).__dict__,
            "event_id": "relationship_dependent_addition",
        }
    )
    first = MultiHopFact(**{**first.__dict__, "event_id": "relationship_dependent_addition"})
    sessions = {"traj_001": [_session(first), _session(second)]}
    prefix15 = {
        "trajectory_id": "traj_001",
        "prefix_id": first.prefix_id,
        "checkpoint_session_count": 15,
        "visible_sessions": ["S015"],
        "gold_life_events": [
            {
                "event_instance_id": first.event_instance_id,
                "event_id": first.event_id,
                "life_event_label": first.event_label,
                "occurred": True,
            }
        ],
        "gold_memory_updates": [_update(first)],
        "gold_action_decisions": [],
        "gold_full_memory_state": {},
        "gold_full_action_state": {},
        "repeats_previous": False,
    }
    prefix30 = {
        **copy.deepcopy(prefix15),
        "prefix_id": second.prefix_id,
        "checkpoint_session_count": 30,
        "visible_sessions": ["S015", "S030"],
        "gold_life_events": [
            *prefix15["gold_life_events"],
            {
                "event_instance_id": second.event_instance_id,
                "event_id": second.event_id,
                "life_event_label": second.event_label,
                "occurred": True,
            },
        ],
        "gold_memory_updates": [_update(first), _update(second)],
    }
    policy = {
        path: {
            "derivation_type": "count_sequence",
            "question_label": "부양가족 수",
            "value_selector": "value",
            "option_pool_type": "count",
            "option_pool": (0, 1, 2, 3, 4),
            "allow_same_value": True,
            "allow_null": False,
            "max_candidates": 1,
        }
    }

    result = build_stage3_multihop_targets(
        [prefix15, prefix30],
        sessions,
        policy,
    )

    assert result.targets == ()
    assert result.report["exclusion_counts"]["event_direction_mismatch"] == 1


def test_multihop_evaluation_prompt_hides_internal_annotations():
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value="unemployed",
        value="employed",
        date="2020-01-15",
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value="employed",
        value="on_leave",
        date="2021-03-20",
    )
    item = ItemBuilder().build_stage3_multihop([_target(first, second)])[0]
    sessions = [_session(first), _session(second)]
    sessions[0]["structured_context"] = {"answer": "DO_NOT_LEAK"}

    prompt = _build_stage3_prompt(item.model_dump(mode="json"), sessions)

    assert "두 상담일의 정보를 각각 찾아 연결하거나 계산" in prompt
    assert "DO_NOT_LEAK" not in prompt
    assert "cue_annotations" not in prompt
    assert "S015" not in prompt



def test_multihop_amount_surface_distinguishes_exact_and_lower_bound():
    assert _amount_evidence_status("약 300만원 정도 나갔어요.", 3_000_000) == "exact"
    assert _amount_evidence_status("오백만원 넘게 나갔어요.", 5_000_000) == "lower_bound"
    assert _amount_evidence_status("큰돈이 나갔어요.", 5_000_000) == "missing"


def test_multihop_fact_surface_rejects_unsupported_income_and_inexact_expense():
    income = _fact(
        event="ev001",
        checkpoint=15,
        old_value=None,
        value="stable",
        date="2020-01-15",
        path="employment.income_stability",
    )
    income_session = _session(income)
    income_session["turns"][0]["text"] = "첫 급여가 들어왔어요."
    assert (
        _fact_surface_error(income, {"S015": income_session})
        == "unsupported_income_stability"
    )

    expense = _fact(
        event="ev002",
        checkpoint=30,
        old_value=None,
        value=5_000_000,
        date="2021-03-20",
        path="cashflow.recent_one_off_expense",
    )
    expense_session = _session(expense)
    expense_session["turns"][0]["text"] = "오백만원 넘게 지출했어요."
    assert (
        _fact_surface_error(expense, {"S030": expense_session})
        == "inexact_expense_amount"
    )


def test_multihop_second_session_recall_is_rejected_as_shortcut():
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value="unemployed",
        value="employed",
        date="2020-01-15",
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value="employed",
        value="unemployed",
        date="2021-03-20",
    )
    second_session = _session(second)
    second_session["turns"][0]["text"] = "요즘 다니던 회사를 안 다니게 됐어요."

    assert _second_hop_shortcut(
        first,
        second,
        {"S015": _session(first), "S030": second_session},
    )


def test_multihop_candidate_builder_rejects_initial_memory_shortcut():
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value="unemployed",
        value="employed",
        date="2020-01-15",
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value="employed",
        value="on_leave",
        date="2021-03-20",
    )
    sessions = {"traj_001": [_session(first), _session(second)]}
    prefix15 = {
        "trajectory_id": "traj_001",
        "prefix_id": first.prefix_id,
        "checkpoint_session_count": 15,
        "visible_sessions": ["S015"],
        "gold_life_events": [
            {
                "event_instance_id": first.event_instance_id,
                "event_id": first.event_id,
                "life_event_label": first.event_label,
                "occurred": True,
            }
        ],
        "gold_memory_updates": [_update(first)],
        "gold_action_decisions": [],
        "gold_full_memory_state": {},
        "gold_full_action_state": {},
        "repeats_previous": False,
    }
    prefix30 = {
        **copy.deepcopy(prefix15),
        "prefix_id": second.prefix_id,
        "checkpoint_session_count": 30,
        "visible_sessions": ["S015", "S030"],
        "gold_life_events": [
            *prefix15["gold_life_events"],
            {
                "event_instance_id": second.event_instance_id,
                "event_id": second.event_id,
                "life_event_label": second.event_label,
                "occurred": True,
            },
        ],
        "gold_memory_updates": [_update(first), _update(second)],
    }
    policy = {
        first.memory_path: {
            "derivation_type": "state_sequence",
            "question_label": "고용 상태",
            "value_selector": "value",
            "option_pool_type": "categorical",
            "option_pool": ("employed", "on_leave", "unemployed", "retired"),
            "excluded_values": (),
            "allow_same_value": False,
            "allow_null": False,
            "max_candidates": 1,
        }
    }

    result = build_stage3_multihop_targets(
        [prefix15, prefix30],
        sessions,
        policy,
        initial_memory_by_traj={
            "traj_001": {
                first.memory_path: {
                    "value": "employed",
                    "status": "current",
                }
            }
        },
    )

    assert result.targets == ()
    assert result.report["exclusion_counts"]["initial_memory_shortcut"] == 1


def test_multihop_salary_day_options_include_day_unit():
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value=10,
        value=15,
        date="2020-01-15",
        path="employment.salary_day",
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value=15,
        value=25,
        date="2021-03-20",
        path="employment.salary_day",
    )
    target = _target(
        first,
        second,
        option_pool=(10, 15, 20, 25),
    )
    item = ItemBuilder().build_stage3_multihop([target])[0]

    assert all(re.search(r"\d+일", option.text) for option in item.options)


def test_multihop_fact_surface_rejects_property_address_without_residence_evidence():
    fact = _fact(
        event="ev001",
        checkpoint=15,
        old_value="서울 관악구",
        value="서울 송파구",
        date="2020-01-15",
        path="housing.address",
    )
    session = _session(fact)
    session["turns"][0]["text"] = (
        "송파구 쪽 부동산 등기를 마쳤고 담보대출이 실행됐어요."
    )

    assert (
        _fact_surface_error(fact, {"S015": session})
        == "unsupported_residential_address"
    )


def test_multihop_fact_surface_rejects_retired_without_retirement_evidence():
    fact = _fact(
        event="ev001",
        checkpoint=15,
        old_value="employed",
        value="retired",
        date="2020-01-15",
    )
    session = _session(fact)
    session["turns"][0]["text"] = "요즘 급여가 끊겨서 일을 나가지 않고 있어요."

    assert (
        _fact_surface_error(fact, {"S015": session})
        == "unsupported_employment_status"
    )


def test_multihop_candidate_builder_rejects_equivalent_no_rent_values():
    path = "housing.rent_amount"
    first = _fact(
        event="ev001",
        checkpoint=15,
        old_value=400_000,
        value=0,
        date="2020-01-15",
        path=path,
    )
    second = _fact(
        event="ev002",
        checkpoint=30,
        old_value=650_000,
        value=None,
        date="2021-03-20",
        path=path,
    )
    sessions = {"traj_001": [_session(first), _session(second)]}
    prefix15 = {
        "trajectory_id": "traj_001",
        "prefix_id": first.prefix_id,
        "checkpoint_session_count": 15,
        "visible_sessions": ["S015"],
        "gold_life_events": [
            {
                "event_instance_id": first.event_instance_id,
                "event_id": first.event_id,
                "life_event_label": first.event_label,
                "occurred": True,
            }
        ],
        "gold_memory_updates": [_update(first)],
        "gold_action_decisions": [],
        "gold_full_memory_state": {},
        "gold_full_action_state": {},
        "repeats_previous": False,
    }
    prefix30 = {
        **copy.deepcopy(prefix15),
        "prefix_id": second.prefix_id,
        "checkpoint_session_count": 30,
        "visible_sessions": ["S015", "S030"],
        "gold_life_events": [
            *prefix15["gold_life_events"],
            {
                "event_instance_id": second.event_instance_id,
                "event_id": second.event_id,
                "life_event_label": second.event_label,
                "occurred": True,
            },
        ],
        "gold_memory_updates": [_update(first), _update(second)],
    }
    policy = {
        path: {
            "derivation_type": "amount_comparison",
            "question_label": "월세 금액",
            "value_selector": "value",
            "option_pool_type": "numeric",
            "option_pool": (0, 400_000, 650_000, 800_000),
            "excluded_values": (),
            "allow_same_value": False,
            "allow_null": True,
            "max_candidates": 1,
        }
    }

    result = build_stage3_multihop_targets(
        [prefix15, prefix30],
        sessions,
        policy,
        initial_memory_by_traj={
            "traj_001": {path: {"value": 400_000, "status": "current"}}
        },
    )

    assert result.targets == ()
    assert (
        result.report["exclusion_counts"]["semantically_equivalent_values"]
        == 1
    )
