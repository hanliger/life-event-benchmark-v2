from __future__ import annotations

from financial_memory_experiment.config import load_experiment_config
from financial_memory_experiment.methods import method_ids
from financial_memory_experiment.prompts import parse_answer


def test_exactly_seven_methods_and_short_output_cap():
    cfg = load_experiment_config()
    assert len(method_ids()) == 7
    assert len(set(method_ids())) == 7
    assert cfg["models"]["final_answer_max_tokens"] == 4096
    assert cfg["models"]["reasoning_policy"] == "vendor_default"
    assert cfg["dataset"]["expected"]["stage3_items"] == 123


def test_parser_does_not_repair_invalid_output():
    item = {"stage": "stage2_memory_mcq"}
    assert parse_answer(item, "<answer>B</answer>") == "B"
    assert parse_answer(item, "정답은 B입니다") == ""
