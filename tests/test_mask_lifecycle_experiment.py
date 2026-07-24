from __future__ import annotations

import copy

import pytest

from scripts.mask_lifecycle_experiment import (
    _is_neutral_filler,
    _neutralize,
    _pick_filler,
)


def _session(
    session_id: str,
    *,
    month: int = 1,
    persona_id: str = "persona-1",
    task_template_id: str = "routine_recent_transactions",
    cue_types: tuple[str, ...] = ("task_intent",),
    session_type: str = "routine_financial",
    linked_event_instance_id: str | None = None,
) -> dict:
    return {
        "session_id": session_id,
        "trajectory_id": "traj_001",
        "persona_id": persona_id,
        "month_index": month,
        "session_type": session_type,
        "window_event_instance_id": None,
        "linked_event_instance_id": linked_event_instance_id,
        "event_status_after_session": "no_event",
        "turns": [
            {"speaker": "user", "text": f"{session_id} user {i}"}
            if i % 2 == 0
            else {"speaker": "assistant", "text": f"{session_id} assistant {i}"}
            for i in range(8)
        ],
        "cue_annotations": [
            {"turn_index": 0, "cue_type": cue_type}
            for cue_type in cue_types
        ],
        "financial_task": "최근 거래내역 확인",
        "mapped_action": "FA-00",
        "action_resolution": {"mode": "information_only"},
        "quality_self_check": {"turn_count_ok": True},
        "generation_metadata": {"model": "test"},
        "plan": {"task_template_id": task_template_id},
    }


def test_neutral_filler_requires_safe_template_and_task_intent_only() -> None:
    assert _is_neutral_filler(_session("S001"))
    assert not _is_neutral_filler(
        _session("S002", task_template_id="routine_loan_repayment_simulation")
    )
    assert not _is_neutral_filler(
        _session("S003", cue_types=("task_intent", "event_signal"))
    )
    assert not _is_neutral_filler(
        _session("S004", linked_event_instance_id="traj_001_ev001")
    )


def test_pick_filler_uses_nearest_unseen_distinct_same_persona_session() -> None:
    pool = [
        _session("S010", month=10),
        _session("S011", month=11),
        _session("S020", month=20),
        _session("S021", month=12, persona_id="persona-2"),
    ]
    prefix_ids = {"S010"}
    used: set[str] = set()
    slot = _session("S005", month=9)

    first = _pick_filler(pool, prefix_ids, used, slot)
    second = _pick_filler(pool, prefix_ids, used, slot)

    assert first["session_id"] == "S011"
    assert second["session_id"] == "S020"
    assert used == {"S011", "S020"}


def test_pick_filler_does_not_fall_back_to_visible_dialogue() -> None:
    slot = _session("S005")
    with pytest.raises(ValueError, match="no unused unseen neutral filler"):
        _pick_filler([_session("S010")], {"S010"}, set(), slot)


def test_pick_filler_supports_timeless_reserve_and_is_deterministic() -> None:
    pool = [
        {**_session(f"CF{index:03d}"), "month_index": None}
        for index in range(1, 7)
    ]
    slot = _session("S005", month=60)

    first = _pick_filler(pool, set(), set(), slot)
    repeated = _pick_filler(pool, set(), set(), slot)

    assert first["session_id"] == repeated["session_id"]
    assert first["month_index"] is None


def test_neutralize_preserves_slot_identity_and_removes_hidden_plans() -> None:
    slot = _session(
        "S005",
        month=3,
        session_type="occurred_evidence",
        linked_event_instance_id="traj_001_ev001",
    )
    slot["window_event_instance_id"] = "traj_001_ev001"
    slot["event_status_after_session"] = "occurred"
    slot["cue_annotations"] = [{"cue_type": "memory_fact"}]
    filler = _session("S020", month=20)
    original = copy.deepcopy(slot)

    masked = _neutralize(slot, filler)

    assert (masked["session_id"], masked["month_index"], masked["persona_id"]) == (
        "S005",
        3,
        "persona-1",
    )
    assert masked["turns"] == filler["turns"]
    assert masked["session_type"] == "routine_financial"
    assert masked["linked_event_instance_id"] is None
    assert masked["window_event_instance_id"] is None
    assert masked["event_status_after_session"] == "no_event"
    assert masked["cue_annotations"] == []
    assert masked["plan"] is None
    assert slot == original


def test_neutralize_rejects_cross_persona_content() -> None:
    with pytest.raises(ValueError, match="same persona"):
        _neutralize(_session("S001"), _session("S002", persona_id="persona-2"))
