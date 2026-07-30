from __future__ import annotations

from types import SimpleNamespace

import pytest

from financial_memory_experiment.methods.readers import ProviderReader


class _Endpoint:
    def __init__(self, response):
        self.response = response
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return self.response


class _GeminiModels:
    def __init__(self):
        self.request = None

    def generate_content(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(text="{}", usage_metadata=None)


def _reader(provider, client, settings):
    reader = ProviderReader.__new__(ProviderReader)
    reader.provider = provider
    reader.model = f"{provider}-model"
    reader.max_tokens = 4096
    reader.timeout_seconds = 120.0
    reader.generation_settings = settings
    reader.client = client
    return reader


def test_anthropic_reader_sends_medium_adaptive_thinking():
    endpoint = _Endpoint(
        SimpleNamespace(
            content=[SimpleNamespace(type="text", text="{}")],
            usage=None,
        )
    )
    reader = _reader(
        "anthropic",
        SimpleNamespace(messages=endpoint),
        {
            "thinking": {"type": "adaptive", "display": "omitted"},
            "output_config": {"effort": "medium"},
        },
    )

    _, metadata = reader.generate(system="system", user="user", max_tokens=20000)

    assert endpoint.request == {
        "model": "anthropic-model",
        "max_tokens": 20000,
        "messages": [{"role": "user", "content": "user"}],
        "system": "system",
        "thinking": {"type": "adaptive", "display": "omitted"},
        "output_config": {"effort": "medium"},
    }
    assert metadata["generation_settings"] == {
        "thinking": {"type": "adaptive", "display": "omitted"},
        "output_config": {"effort": "medium"},
    }


def test_gemini_reader_sends_medium_thinking_without_summary():
    models = _GeminiModels()
    reader = _reader(
        "google",
        SimpleNamespace(models=models),
        {
            "thinking_config": {
                "thinking_level": "medium",
                "include_thoughts": False,
            },
            "temperature": 1.0,
        },
    )

    reader.generate(system="system", user="user", max_tokens=20000)

    assert models.request == {
        "model": "google-model",
        "contents": "user",
        "config": {
            "system_instruction": "system",
            "max_output_tokens": 20000,
            "thinking_config": {
                "thinking_level": "medium",
                "include_thoughts": False,
            },
            "temperature": 1.0,
        },
    }


def test_openai_reader_sends_medium_standard_current_turn_reasoning():
    endpoint = _Endpoint(SimpleNamespace(output_text="{}", usage=None))
    reader = _reader(
        "openai",
        SimpleNamespace(responses=endpoint),
        {
            "reasoning": {
                "effort": "medium",
                "mode": "standard",
                "context": "current_turn",
            },
            "text": {"verbosity": "medium"},
            "store": False,
            "truncation": "disabled",
        },
    )

    reader.generate(system="system", user="user", max_tokens=20000)

    assert endpoint.request == {
        "model": "openai-model",
        "instructions": "system",
        "input": "user",
        "max_output_tokens": 20000,
        "reasoning": {
            "effort": "medium",
            "mode": "standard",
            "context": "current_turn",
        },
        "text": {"verbosity": "medium"},
        "store": False,
        "truncation": "disabled",
    }


def test_provider_reader_rejects_generation_setting_override():
    reader = ProviderReader.__new__(ProviderReader)
    with pytest.raises(ValueError, match="cannot override"):
        ProviderReader.__init__(
            reader,
            "openai",
            "gpt-5.6-sol",
            generation_settings={"model": "wrong-model"},
        )
