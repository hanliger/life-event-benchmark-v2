"""The evaluated model only receives true initial memory and visible turns."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.evaluate_benchmark_items import (
    _build_stage2_prompt,
    _visible_sessions,
)
from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.benchmark.mcq_input import (
    Stage2Checkpoint,
    Stage2Target,
    _join_records,
)
from scripts.export_public_benchmark import public_item, public_session


def test_stage2_prompt_excludes_internal_session_metadata():
    session = {
        "trajectory_id": "traj_001",
        "session_id": "S001",
        "session_date": "2020-01-15",
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
            "target_date_start": "2020-01-01",
            "target_date_end": "2020-01-15",
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
    assert "[상담일: 2020년 1월 15일]" in prompt
    assert "평가 대상 기간: 2020년 1월 1일~2020년 1월 15일" in prompt
    assert "S001" not in prompt
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


def test_stage1_prompt_uses_dates_without_session_ids():
    from scripts.evaluate_benchmark_items import _build_stage1_event_identification_prompt

    session = {
        "trajectory_id": "traj_001",
        "session_id": "S015",
        "session_date": "2020-01-15",
        "turns": [{"speaker": "user", "text": "상담 내용"}],
    }
    item = {
        "question": "2020년 1월 1일~2020년 1월 15일 기간에 마지막으로 실제 발생한 Life Event는 무엇인가?",
        "metadata": {
            "target_date_start": "2020-01-01",
            "target_date_end": "2020-01-15",
            "candidate_events": [{"event_id": "career_employment", "label_ko": "취업"}],
        },
    }

    prompt = _build_stage1_event_identification_prompt(item, [session])

    assert "[상담일: 2020년 1월 15일]" in prompt
    assert "평가 대상 기간: 2020년 1월 1일~2020년 1월 15일" in prompt
    assert "S015" not in prompt
    assert "마지막으로 실제 발생한" in prompt


def test_mcq_input_accepts_canonical_joined_sessions(tmp_path):
    row = {
        "trajectory_id": "traj_001",
        "persona_id": "p_001",
        "session_id": "S001",
        "session_date": "2020-01-01",
        "turns": [{"speaker": "user", "text": "상담"}],
        "plan": {"session_id": "S001", "window_index": 1, "position_in_window": 1},
    }
    (tmp_path / "sessions_traj_001.jsonl").write_text(
        __import__("json").dumps(row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    joined = _join_records(tmp_path, None, "traj_001")

    assert joined[0][0]["turns"] == row["turns"]
    assert joined[0][1]["session_date"] == "2020-01-01"


def _make_stage2_target(target_id: str, event_id: str, checkpoint: int, after: str):
    return Stage2Target(
        canonical_target_id=target_id,
        trajectory_id="traj_001",
        target_event_instance_id=f"{target_id}_event",
        target_event_id=event_id,
        target_event_label=event_id,
        memory_path="employment.employer",
        operation="update",
        first_visible_checkpoint=checkpoint,
        evidence_sessions=(f"S{checkpoint:03d}",),
        evidence_turns=(f"S{checkpoint:03d}:1",),
        before_state={"value": "이전 값", "status": "current", "pending_proposal": None},
        after_state={"value": after, "status": "current", "pending_proposal": None},
        option_pool_type="entity",
        option_pool=("가나", "나나", "다나", after),
    )


def test_stage2_repeats_all_eligible_targets_with_stable_shuffled_options():
    first = _make_stage2_target("target_a", "career_employment", 15, "새 직장 A")
    second = _make_stage2_target("target_b", "career_job_change", 30, "새 직장 B")
    checkpoints = [
        Stage2Checkpoint(
            trajectory_id="traj_001",
            prefix_id="traj_001_pfx015",
            checkpoint_session_count=15,
            visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 16)),
            targets=(first,),
            target_date_start="2020-01-01",
            target_date_end="2020-01-15",
        ),
        Stage2Checkpoint(
            trajectory_id="traj_001",
            prefix_id="traj_001_pfx030",
            checkpoint_session_count=30,
            visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 31)),
            targets=(first, second),
            target_date_start="2020-02-01",
            target_date_end="2020-02-15",
        ),
    ]

    builder = ItemBuilder(seed=42, shuffle_options=True)
    items = builder.build_stage2(checkpoints)
    repeated = [
        item for item in items
        if item.gold["canonical_target_id"] == "target_a"
    ]

    assert len(items) == 3
    assert len(repeated) == 2
    assert repeated[0].question == repeated[1].question
    assert {
        option.text for option in repeated[0].options
    } == {
        option.text for option in repeated[1].options
    }
    assert repeated[0].gold["answer_value"] == repeated[1].gold["answer_value"]
    assert repeated[0].metadata["options_shuffled"] is True
    assert [option.option_id for option in repeated[0].options] == list("ABCD")
    assert len(repeated[0].visible_sessions) == 15
    assert len(repeated[1].visible_sessions) == 30
