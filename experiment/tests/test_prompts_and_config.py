from __future__ import annotations

import pytest

from financial_memory_experiment.config import load_experiment_config
from financial_memory_experiment.methods import method_ids
from financial_memory_experiment.methods import registry
from financial_memory_experiment.methods.registry import (
    comparison_contract,
    generation_settings_for_policy,
)
from financial_memory_experiment.paths import ExperimentPaths
from financial_memory_experiment.prompts import build_query, gold_answer, parse_answer


def test_nine_stage2_methods_and_three_stage1_models():
    cfg = load_experiment_config()
    assert len(method_ids()) == 9
    assert len(set(method_ids())) == 9
    assert cfg["methods"] == [
        "fc_claude_opus_4_8",
        "bm25_claude_opus_4_8",
        "dense_ge2_claude_opus_4_8",
        "mem0_claude_opus_4_8",
        "letta_claude_opus_4_8",
        "fc_openrouter_llama_4_maverick",
        "fc_openrouter_gpt_oss_120b",
        "fc_openrouter_qwen_3_5_122b_a10b",
        "fc_openrouter_qwen_3_6_35b_a3b_fp8",
    ]
    assert cfg["analysis_methods"] == []
    profiles = cfg["stage1_occurred_event_evidence_pairs"][
        "execution_profiles"
    ]
    assert profiles["api3"] == {
        "methods": [
            "fc_gpt_5_6_sol",
            "fc_claude_opus_4_8",
            "fc_gemini_3_1_pro",
        ],
        "request_timeout_seconds": 600,
        "parse_retries": 0,
    }
    assert profiles["method9"]["methods"] == method_ids()
    assert profiles["method9"]["request_timeout_seconds"] == 300
    assert profiles["method9"]["parse_retries"] == 1
    assert cfg["models"]["claude_opus_4_8"] == "claude-opus-4-8"
    assert cfg["models"]["claude_opus_4_8_request_timeout_seconds"] == 300
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
    assert comparison_contract() == {
        "embedding_model": "gemini-embedding-2",
        "embedding_dimensions": 768,
        "retrieval_top_k_per_group": 5,
        "retrieval_max_evidence": 20,
        "methods": [
            "bm25_claude_opus_4_8",
            "dense_ge2_claude_opus_4_8",
            "mem0_claude_opus_4_8",
            "letta_claude_opus_4_8",
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
    (root / "configs" / "methods.yaml").write_text(
        """
stage2_2_retrieval:
  top_k_per_group: 5
  max_evidence_sessions: 20
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


def test_opus_4_8_method_uses_pinned_model_and_low_settings(monkeypatch):
    captured = []

    def fake_reader(
        provider,
        model,
        mock,
        max_tokens,
        timeout_seconds,
        generation_settings,
    ):
        captured.append(
            (provider, model, timeout_seconds, generation_settings)
        )
        return registry.MockReader()

    monkeypatch.setattr(registry, "_reader", fake_reader)
    method = registry.create_method(
        "fc_claude_opus_4_8",
        trajectory_id="traj_002",
        mock=True,
        reasoning_policy="deployment_realistic_low",
    )

    assert method.method_id == "fc_claude_opus_4_8"
    assert captured[-1] == (
        "anthropic",
        "claude-opus-4-8",
        300.0,
        {
            "thinking": {"type": "adaptive", "display": "omitted"},
            "output_config": {"effort": "low"},
        },
    )


def test_stage1_gpt_uses_frozen_chat_completions_settings(monkeypatch):
    captured = []

    def fake_reader(
        provider,
        model,
        mock,
        max_tokens,
        timeout_seconds,
        generation_settings,
        *,
        api_surface=None,
    ):
        captured.append(
            (
                provider,
                model,
                timeout_seconds,
                generation_settings,
                api_surface,
            )
        )
        return registry.MockReader()

    monkeypatch.setattr(registry, "_reader", fake_reader)
    registry.create_method(
        "fc_gpt_5_6_sol",
        trajectory_id="traj_010",
        mock=True,
        reasoning_policy="deployment_realistic_low",
        stage="stage1_occurred_event_evidence_pairs",
    )

    assert captured[-1] == (
        "openai",
        "gpt-5.6-sol",
        120.0,
        {
            "reasoning_effort": "low",
            "verbosity": "medium",
            "store": False,
        },
        "chat_completions",
    )


def test_openrouter_qwen_3_6_is_provider_locked_to_fp8(
    monkeypatch,
):
    captured = []

    def fake_reader(
        provider,
        model,
        mock,
        max_tokens,
        timeout_seconds,
        generation_settings,
    ):
        captured.append((provider, model, generation_settings))
        return registry.MockReader()

    monkeypatch.setattr(registry, "_reader", fake_reader)
    monkeypatch.setenv(
        "STAGE2_2_OPENROUTER_PROVIDER_LOCK",
        '{"fc_openrouter_qwen_3_6_35b_a3b_fp8":"Provider X"}',
    )
    registry.create_method(
        "fc_openrouter_qwen_3_6_35b_a3b_fp8",
        trajectory_id="traj_001",
        mock=True,
    )
    provider, model, settings = captured[-1]
    assert provider == "openrouter"
    assert model == "qwen/qwen3.6-35b-a3b"
    assert settings["provider"]["order"] == ["Provider X"]
    assert settings["provider"]["only"] == ["Provider X"]
    assert settings["provider"]["quantizations"] == ["fp8"]
    assert settings["provider"]["allow_fallbacks"] is False
    assert settings["provider"]["data_collection"] == "deny"
    assert settings["provider"]["zdr"] is True
