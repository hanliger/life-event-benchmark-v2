"""LLM client provider-specific request shaping."""

from types import SimpleNamespace

from tenacity import wait_none

from fin_life_benchmark.llm.client import LLMClient


class FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=SimpleNamespace(input_tokens=10, output_tokens=2),
        )


class FlakyMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content=[],
                stop_reason="max_tokens",
                stop_sequence=None,
                usage=SimpleNamespace(input_tokens=10, output_tokens=0),
            )
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok after retry")],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=SimpleNamespace(input_tokens=10, output_tokens=3),
        )


def _anthropic_client(model: str) -> tuple[LLMClient, FakeMessages]:
    client = LLMClient(provider="mock", model=model, temperature=0.7, max_tokens=128)
    client.provider = "anthropic"
    messages = FakeMessages()
    client._client = SimpleNamespace(messages=messages)
    return client, messages


def test_anthropic_claude_5_omits_temperature():
    client, messages = _anthropic_client("claude-sonnet-5")

    assert client.generate("system", "prompt") == "ok"

    assert "temperature" not in messages.kwargs
    assert messages.kwargs["model"] == "claude-sonnet-5"
    assert client.last_response_metadata["stop_reason"] == "end_turn"
    assert client.last_response_metadata["content_block_types"] == ["text"]
    assert client.last_response_metadata["usage"] == {"input_tokens": 10, "output_tokens": 2}


def test_anthropic_older_models_keep_temperature():
    client, messages = _anthropic_client("claude-opus-4-8")

    assert client.generate("system", "prompt") == "ok"

    assert messages.kwargs["temperature"] == 0.7


def test_anthropic_empty_text_retries_provider_call():
    client = LLMClient(provider="mock", model="claude-sonnet-5", temperature=0.7, max_tokens=128)
    client.provider = "anthropic"
    messages = FlakyMessages()
    client._client = SimpleNamespace(messages=messages)

    retry_without_sleep = client.generate.retry_with(wait=wait_none())

    assert retry_without_sleep(client, "system", "prompt") == "ok after retry"
    assert messages.calls == 2
    assert client.last_response_metadata["stop_reason"] == "end_turn"
