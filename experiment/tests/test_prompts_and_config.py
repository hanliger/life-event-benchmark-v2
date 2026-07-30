from __future__ import annotations

import pytest

from financial_memory_experiment.config import load_experiment_config
from financial_memory_experiment.methods import method_ids
from financial_memory_experiment.methods.registry import (
    comparison_contract,
    generation_settings_for_policy,
)
from financial_memory_experiment.paths import ExperimentPaths
from financial_memory_experiment.prompts import build_query, gold_answer, parse_answer


def test_exactly_eight_methods_and_short_output_cap():
    cfg = load_experiment_config()
    assert len(method_ids()) == 8
    assert len(set(method_ids())) == 8
    assert len(cfg["methods"]) == 7
    assert cfg["analysis_methods"] == ["oracle_rel_gpt_5_6_sol"]
    assert cfg["models"]["final_answer_max_tokens"] == 4096
    assert cfg["stage2_2_reconstruct"]["smoke"]["max_output_tokens"] == 20000
    assert cfg["models"]["reasoning_policy"] == "deployment_realistic_low"
    assert cfg["models"]["sampling_policy"] == "provider_default"
    assert cfg["models"]["generation_settings"] == {
        "anthropic": {
            "thinking": {"type": "adaptive", "display": "omitted"},
            "output_config": {"effort": "low"},
        },
        "google": {
            "thinking_config": {
                "thinking_level": "low",
                "include_thoughts": False,
            },
        },
        "openai": {
            "reasoning": {
                "effort": "low",
                "mode": "standard",
                "context": "current_turn",
            },
            "text": {"verbosity": "medium"},
            "store": False,
            "truncation": "disabled",
        },
    }
    low = cfg["models"]["generation_settings"]
    assert generation_settings_for_policy(
        cfg["models"], "deployment_realistic_low"
    ) == low
    assert low["anthropic"]["output_config"]["effort"] == "low"
    assert low["google"]["thinking_config"]["thinking_level"] == "low"
    assert low["openai"]["reasoning"]["effort"] == "low"
    assert all(
        "temperature" not in provider_settings
        for provider_settings in low.values()
    )
    medium = cfg["models"]["generation_profiles"][
        "deployment_realistic_medium"
    ]
    assert medium["anthropic"]["output_config"]["effort"] == "medium"
    assert medium["google"]["thinking_config"]["thinking_level"] == "medium"
    assert medium["openai"]["reasoning"]["effort"] == "medium"
    assert cfg["dataset"]["expected"]["stage3_items"] == 123
    assert comparison_contract() == {
        "embedding_model": "gemini-embedding-2",
        "embedding_dimensions": 768,
        "top_k": 10,
        "methods": [
            "dense_ge2_gemini_3_1_pro",
            "mem0_gemini_3_1_pro",
            "letta_gemini_3_1_pro",
        ],
    }


def test_parser_does_not_repair_invalid_output():
    item = {"stage": "stage2_memory_value", "metadata": {"answer_type": "mcq"}}
    assert parse_answer(item, "<answer>B</answer>") == "B"
    assert parse_answer(item, "<answer>G</answer>") == "G"
    assert parse_answer(item, "정답은 B입니다") == ""


def test_stage2_free_response_is_normalized_with_the_core_contract():
    item = {
        "stage": "stage2_memory_value",
        "metadata": {"answer_type": "free_response", "normalizer": "krw"},
        "gold": {"normalized_answer": "3000000"},
    }

    assert parse_answer(item, "<answer>300만원</answer>") == "3000000"
    assert gold_answer(item) == "3000000"
    query = build_query(
        {**item, "question": "기준일의 지출액은?", "options": []},
        [],
    )
    assert "[선택지]" not in query
    assert "<answer>값</answer>" in query


def test_comparison_contract_rejects_dimension_drift(tmp_path):
    root = tmp_path / "experiment"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "experiment.yaml").write_text(
        """
benchmark:
  top_k_main: 10
models:
  gemini_embedding: gemini-embedding-2
  embedding_dimensions: 1536
""",
        encoding="utf-8",
    )
    paths = ExperimentPaths(root=root, repo_root=tmp_path)
    with pytest.raises(ValueError, match="768-dimensional"):
        comparison_contract(paths)


def test_unknown_reasoning_policy_is_rejected():
    cfg = load_experiment_config()
    with pytest.raises(ValueError, match="unknown reasoning policy"):
        generation_settings_for_policy(cfg["models"], "maximum_magic")
