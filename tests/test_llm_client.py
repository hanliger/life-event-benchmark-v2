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
    # Opus 4.6 and earlier still accept temperature.
    client, messages = _anthropic_client("claude-opus-4-6")

    assert client.generate("system", "prompt") == "ok"

    assert messages.kwargs["temperature"] == 0.7


def test_anthropic_opus_47_48_omit_temperature():
    # Opus 4.7 / 4.8 reject temperature (HTTP 400) — the client must not send it.
    for model in ("claude-opus-4-7", "claude-opus-4-8"):
        client, messages = _anthropic_client(model)
        assert client.generate("system", "prompt") == "ok"
        assert "temperature" not in messages.kwargs, model


def test_anthropic_empty_text_retries_provider_call():
    client = LLMClient(provider="mock", model="claude-sonnet-5", temperature=0.7, max_tokens=128)
    client.provider = "anthropic"
    messages = FlakyMessages()
    client._client = SimpleNamespace(messages=messages)

    retry_without_sleep = client.generate.retry_with(wait=wait_none())

    assert retry_without_sleep(client, "system", "prompt") == "ok after retry"
    assert messages.calls == 2
    assert client.last_response_metadata["stop_reason"] == "end_turn"


# ---------------------------------------------------------------------------
# run-configuration knobs that must be honoured or visibly refused


def test_temperature_none_is_not_sent_and_is_recorded_as_such():
    """`--no-temperature` means "do not ask", not "ask for 0.0".

    The distinction matters because the frontier models refuse a requested
    temperature anyway; without separate reason codes an unspecified
    temperature and a refused one would look identical in the artifacts.
    """

    client = LLMClient(
        provider="mock", model="claude-opus-4-6", temperature=None, max_tokens=128
    )
    client.provider = "anthropic"
    messages = FakeMessages()
    client._client = SimpleNamespace(messages=messages)

    assert client.generate("system", "prompt") == "ok"
    assert "temperature" not in messages.kwargs
    meta = client.last_response_metadata
    assert meta["temperature_requested"] is None
    assert meta["temperature_applied"] is None
    assert meta["temperature_omission_reason"] == "not_requested_provider_default"


def test_a_requested_temperature_the_model_refuses_has_a_distinct_reason():
    client = LLMClient(
        provider="mock", model="claude-opus-5", temperature=0.0, max_tokens=128
    )
    client.provider = "anthropic"
    client._client = SimpleNamespace(messages=FakeMessages())

    client.generate("system", "prompt")
    meta = client.last_response_metadata
    assert meta["temperature_requested"] == 0.0
    assert meta["temperature_applied"] is None
    assert meta["temperature_omission_reason"] == "model_rejects_temperature"


def test_max_retries_zero_makes_the_first_failure_final():
    """A single-replicate protocol must not silently redraw.

    The module used to pin stop_after_attempt(3), which would override an
    `automatic_retries: 0` run configuration and report the second draw as the
    measurement.
    """

    client = LLMClient(
        provider="mock",
        model="claude-sonnet-5",
        temperature=0.7,
        max_tokens=128,
        max_retries=0,
    )
    client.provider = "anthropic"
    messages = FlakyMessages()
    client._client = SimpleNamespace(messages=messages)

    retry_without_sleep = client.generate.retry_with(wait=wait_none())
    try:
        retry_without_sleep(client, "system", "prompt")
    except Exception:
        pass
    assert messages.calls == 1, "a failure must not be retried when max_retries=0"


def test_default_max_retries_still_retries():
    client = LLMClient(
        provider="mock", model="claude-sonnet-5", temperature=0.7, max_tokens=128
    )
    client.provider = "anthropic"
    messages = FlakyMessages()
    client._client = SimpleNamespace(messages=messages)

    retry_without_sleep = client.generate.retry_with(wait=wait_none())
    assert retry_without_sleep(client, "system", "prompt") == "ok after retry"
    assert messages.calls == 2
