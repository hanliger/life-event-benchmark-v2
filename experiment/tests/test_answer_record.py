from __future__ import annotations

from financial_memory_experiment.answer_record import build_answer_record
from financial_memory_experiment.stage1 import STAGE1


STAGE1_ITEM = {
    "item_id": "traj_001_w01_s1_event",
    "stage": STAGE1,
    "trajectory_id": "traj_001",
    "question": "...",
    "gold": {
        "event_id": "E003",
        "event_label": "이사",
        "event_instance_id": "traj_001::evt_7",
    },
    "metadata": {
        "candidate_events": [
            {"event_id": "E001", "label_ko": "이직"},
            {"event_id": "E003", "label_ko": "이사"},
        ]
    },
}


def test_record_lifts_answer_into_gold_schema():
    record = build_answer_record(
        STAGE1_ITEM, prediction="E003", raw_answer="<answer>E003</answer>"
    )
    assert record["stage"] == STAGE1
    assert record["prediction"] == {
        "event_id": "E003",
        "event_label": "이사",
        "event_instance_id": None,
    }
    assert record["gold"]["event_label"] == "이사"
    assert record["diff"] == []
    assert record["prediction_in_candidate_set"] is True
    assert record["raw_answer"] == "<answer>E003</answer>"
    # Gold provenance the model cannot produce is excluded from the diff.
    assert "event_instance_id" in record["unpredictable_gold_fields"]


def test_record_diffs_wrong_answer_on_id_and_label():
    record = build_answer_record(
        STAGE1_ITEM, prediction="E001", raw_answer="<answer>E001</answer>"
    )
    assert record["diff"] == [
        {"field": "event_id", "prediction": "E001", "gold": "E003"},
        {"field": "event_label", "prediction": "이직", "gold": "이사"},
    ]


def test_record_flags_an_id_outside_the_candidate_set():
    record = build_answer_record(
        STAGE1_ITEM, prediction="E999", raw_answer="<answer>E999</answer>"
    )
    assert record["prediction_in_candidate_set"] is False
    assert record["prediction"]["event_label"] is None


def test_record_handles_an_unparsable_answer():
    record = build_answer_record(
        STAGE1_ITEM, prediction="", raw_answer="태그 없이 설명만 했다"
    )
    assert record["prediction"]["event_id"] == ""
    assert record["prediction_in_candidate_set"] is False
    assert record["diff"][0]["field"] == "event_id"
    assert record["raw_answer"] == "태그 없이 설명만 했다"


def test_other_stages_get_no_record():
    # stage2_2_reconstruct is covered by state_pairs; the MCQ task is legacy.
    for stage in (
        "stage2_memory_value",
        "stage2_2_reconstruct",
        "masking_lifecycle",
    ):
        assert (
            build_answer_record(
                {**STAGE1_ITEM, "stage": stage},
                prediction="E003",
                raw_answer="x",
            )
            is None
        )
