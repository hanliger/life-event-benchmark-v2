"""Memory state, delta engine, and impact engine semantics."""

import random

import pytest

from fin_life_benchmark.actions.impact_engine import ImpactEngine
from fin_life_benchmark.actions.models import ActionStatus, StandingAction
from fin_life_benchmark.fsm.models import EventInstance, EventStatus
from fin_life_benchmark.memory.delta_engine import DeltaEngine
from fin_life_benchmark.memory.models import CellStatus, FinancialMemoryState, MemoryOperation, MemoryUpdate


def _memory_with(path: str, value):
    memory = FinancialMemoryState()
    memory.set_initial(path, value)
    return memory


def test_update_archives_old_value():
    memory = _memory_with("employment.salary_day", 25)
    update = memory.apply(MemoryUpdate(path="employment.salary_day", operation=MemoryOperation.UPDATE, new_value=10, month_index=5))
    assert update.old_value == 25
    hist = memory.history("employment.salary_day")
    assert hist[0].status == CellStatus.HISTORICAL and hist[0].valid_until == 5
    assert memory.current_value("employment.salary_day") == 10
    assert memory.historical_values("employment.salary_day") == [25]


def test_clear_pending_cancels_pending_cells():
    memory = _memory_with("household.marital_status", "single")
    memory.apply(MemoryUpdate(path="household.marital_status", operation=MemoryOperation.SET_PENDING, new_value="married", month_index=3))
    memory.apply(MemoryUpdate(path="household.marital_status", operation=MemoryOperation.CLEAR_PENDING, month_index=4))
    statuses = [c.status for c in memory.history("household.marital_status")]
    assert CellStatus.CANCELLED in statuses
    assert memory.current_value("household.marital_status") is None  # last cell cancelled
    # first (single) cell remains untouched historical record
    assert memory.history("household.marital_status")[0].value == "single"


def test_delta_engine_rejects_commit_on_weak_signal():
    engine = DeltaEngine()
    # sanity: registry must not declare committed updates on weak_signal
    for event_id, spec in engine.registry.items():
        weak = spec.get("on_weak_signal") or {}
        for item in (weak.get("memory_updates") or []) + (weak.get("pending_memory") or []):
            assert item["operation"] in {"set_pending", "needs_verification", "no_update"}, event_id


def test_delta_engine_job_change_occurred():
    engine = DeltaEngine()
    memory = _memory_with("employment.employer", "구직장")
    memory.set_initial("employment.salary_day", 25)
    memory.set_initial("employment.salary_account", "main_checking")
    memory.set_initial("employment.income_stability", "stable")
    instance = EventInstance(
        event_instance_id="t_ev001", event_id="career_job_change", label_ko="이직/전근",
        domain="employment", params={"new_employer": "새직장", "new_salary_day": 10},
    )
    updates = engine.apply_transition(memory, instance, EventStatus.OCCURRED, 12, random.Random(0))
    assert memory.current_value("employment.employer") == "새직장"
    assert memory.historical_values("employment.employer") == ["구직장"]
    salary_cell = memory.latest("employment.salary_day")
    assert salary_cell.status == CellStatus.NEEDS_VERIFICATION
    assert any(u.operation == MemoryOperation.UPDATE for u in updates)


def test_delta_engine_skips_noop_update():
    engine = DeltaEngine()
    engine.registry = {
        "test_event": {
            "on_occurred": {
                "memory_updates": [
                    {"path": "employment.employer", "operation": "update", "value_from": "literal:같은직장"}
                ]
            }
        }
    }
    memory = _memory_with("employment.employer", "같은직장")
    instance = EventInstance(
        event_instance_id="t_ev_noop", event_id="test_event", label_ko="테스트", domain="employment",
    )

    updates = engine.apply_transition(memory, instance, EventStatus.OCCURRED, 12, random.Random(0))

    assert updates == []
    assert len(memory.history("employment.employer")) == 1
    assert memory.current_value("employment.employer") == "같은직장"


def test_delta_engine_skips_repeated_needs_verification():
    engine = DeltaEngine()
    engine.registry = {
        "test_event": {
            "on_occurred": {
                "memory_updates": [
                    {"path": "employment.salary_day", "operation": "needs_verification"}
                ]
            }
        }
    }
    memory = _memory_with("employment.salary_day", 25)
    memory.apply(MemoryUpdate(path="employment.salary_day", operation=MemoryOperation.NEEDS_VERIFICATION, month_index=3))
    instance = EventInstance(
        event_instance_id="t_ev_repeat", event_id="test_event", label_ko="테스트", domain="employment",
    )

    updates = engine.apply_transition(memory, instance, EventStatus.OCCURRED, 12, random.Random(0))

    assert updates == []
    assert memory.latest("employment.salary_day").status == CellStatus.NEEDS_VERIFICATION


def test_impact_engine_never_executes_funds_moving_actions():
    engine = ImpactEngine()
    action = StandingAction(
        action_id="SO_x", type="salary_linked_savings", label="자동저축",
        status=ActionStatus.ACTIVE, funds_movement=True, risk="high",
        linked_memory_paths=["employment.salary_day"],
    )
    instance = EventInstance(
        event_instance_id="t_ev002", event_id="career_job_change", label_ko="이직/전근", domain="employment",
    )
    impacts = engine.apply_transition([action], instance, EventStatus.OCCURRED, 12)
    assert impacts, "job change must impact salary-linked action"
    for impact in impacts:
        assert impact.must_not_execute
        assert impact.expected_decision.value != "execute"
    assert action.validity_status == "needs_review"


def test_impact_engine_skips_actions_already_needing_review():
    engine = ImpactEngine()
    action = StandingAction(
        action_id="SO_x", type="salary_linked_savings", label="자동저축",
        status=ActionStatus.ACTIVE, funds_movement=True, risk="high",
        linked_memory_paths=["employment.salary_day"],
    )
    instance = EventInstance(
        event_instance_id="t_ev002", event_id="career_job_change", label_ko="이직/전근", domain="employment",
    )

    first = engine.apply_transition([action], instance, EventStatus.OCCURRED, 12)
    second = engine.apply_transition([action], instance, EventStatus.OCCURRED, 13)

    assert first
    assert second == []
    assert action.validity_status == "needs_review"


def test_registry_impacts_never_expect_execute():
    engine = ImpactEngine()
    for event_id, spec in engine.registry.items():
        for hook, body in spec.items():
            for impact in (body or {}).get("action_impacts") or []:
                assert impact.get("expected_decision") != "execute", f"{event_id}.{hook}"


@pytest.mark.parametrize("op", ["update", "archive", "mark_stale"])
def test_history_never_deleted(op):
    memory = _memory_with("housing.address", "옛주소")
    memory.apply(MemoryUpdate(path="housing.address", operation=MemoryOperation(op), new_value="새주소", month_index=1))
    assert memory.history("housing.address")[0].value == "옛주소"
