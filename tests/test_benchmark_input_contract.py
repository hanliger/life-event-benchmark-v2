"""The evaluated model only receives true initial memory and visible turns."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.evaluate_benchmark_items import (
    _build_stage2_prompt,
    _visible_sessions,
)
from scripts.export_public_benchmark import public_item, public_session


def test_stage2_prompt_excludes_internal_session_metadata():
    session = {
        "trajectory_id": "traj_001",
        "session_id": "S001",
        "turns": [
            {"speaker": "user", "text": "급여일은 25일이에요."},
            {"speaker": "assistant", "text": "확인했습니다."},
        ],
        "plan": {"secret_gold": "DO_NOT_LEAK"},
        "structured_context": {"future_answer": "DO_NOT_LEAK_EITHER"},
        "cue_annotations": [{"linked_memory_value": 25}],
    }
    item = {
        "question": "현재 급여일은?",
        "options": [{"option_id": "A", "text": "25일"}],
        "metadata": {
            "initial_memory": {
                "employment.salary_day": {
                    "value": 10,
                    "status": "current",
                    "historical_values": [],
                }
            }
        },
    }

    prompt = _build_stage2_prompt(item, [session])

    assert "급여일은 25일이에요" in prompt
    assert "employment.salary_day" in prompt
    assert "DO_NOT_LEAK" not in prompt
    assert "structured_context" not in prompt
    assert "cue_annotations" not in prompt


def test_session_lookup_is_trajectory_scoped():
    sessions = {
        ("traj_001", "S001"): {"trajectory_id": "traj_001", "session_id": "S001"},
        ("traj_002", "S001"): {"trajectory_id": "traj_002", "session_id": "S001"},
    }
    item = {"trajectory_id": "traj_001", "visible_sessions": ["S001"]}

    assert _visible_sessions(item, sessions)[0]["trajectory_id"] == "traj_001"


def test_public_exports_strip_private_annotations_and_gold():
    session = {
        "session_id": "S001",
        "trajectory_id": "traj_001",
        "turns": [{"speaker": "user", "text": "안녕하세요"}],
        "plan": {"structured_context": {"answer": 25}},
        "cue_annotations": [{"linked_memory_value": 25}],
    }
    item = {
        "item_id": "item_1",
        "stage": "stage2_memory_mcq",
        "trajectory_id": "traj_001",
        "prefix_id": "pfx1",
        "visible_sessions": ["S001"],
        "question": "질문",
        "options": [{"option_id": "A", "text": "보기", "correct": True, "error_type": None}],
        "gold": {"correct_option": "A"},
        "metadata": {"initial_memory": {}},
    }

    safe_session = public_session(session)
    safe_item = public_item(item)

    assert set(safe_session) == {"session_id", "trajectory_id", "turns"}
    assert "gold" not in safe_item
    assert set(safe_item["options"][0]) == {"option_id", "text"}


def test_visible_sessions_rejects_incomplete_context():
    item = {
        "trajectory_id": "traj_001",
        "visible_sessions": ["S001", "S002"],
    }
    sessions = {
        ("traj_001", "S001"): {"trajectory_id": "traj_001", "session_id": "S001"},
    }
    try:
        _visible_sessions(item, sessions)
    except ValueError as exc:
        assert "S002" in str(exc)
    else:
        raise AssertionError("missing visible sessions must fail loudly")
