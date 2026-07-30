"""Anthropic adaptive-thinking provider tests.

No network: a fake SDK client records the exact request kwargs and returns
stand-in response objects. Covers the request shape for Opus 4.8, the streaming
path, text-only answer extraction, thinking-token normalization, and that
non-thinking Anthropic behavior is unchanged.
"""

from __future__ import annotations

import pytest

from fin_life_benchmark.llm.client import (
    ANTHROPIC_EFFORT_VALUES,
    EmptyLLMResponseError,
    LLMClient,
    TruncatedLLMResponseError,
    anthropic_thinking_tokens,
)


class _Block:
    def __init__(self, type_: str, text: str = "", thinking: str = ""):
        self.type = type_
        self.text = text
        self.thinking = thinking


class _Usage:
    def __init__(self, input_tokens=1000, output_tokens=300, thinking_tokens=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0
        if thinking_tokens is not None:
            self.output_tokens_details = type(
                "Details", (), {"thinking_tokens": thinking_tokens}
            )()


class _Response:
    def __init__(self, blocks, usage, stop_reason="end_turn"):
        self.content = blocks
        self.usage = usage
        self.stop_reason = stop_reason
        self.stop_sequence = None


class _Stream:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._response


class _Messages:
    """Records kwargs for whichever path the client takes.

    Mirrors an SDK build new enough to take ``output_config`` as a named
    parameter.
    """

    def __init__(self, response):
        self.response = response
        self.create_kwargs = None
        self.stream_kwargs = None

    _UNSET = object()

    def create(self, *, output_config=_UNSET, **kwargs):
        # only record what the caller actually sent, not the stub's default
        if output_config is not self._UNSET:
            kwargs["output_config"] = output_config
        self.create_kwargs = kwargs
        return self.response

    def stream(self, *, output_config=_UNSET, **kwargs):
        if output_config is not self._UNSET:
            kwargs["output_config"] = output_config
        self.stream_kwargs = kwargs
        return _Stream(self.response)


class _LegacyMessages(_Messages):
    """An older SDK build: no ``output_config`` parameter, only ``extra_body``.

    Calling it with ``output_config=`` raises TypeError before any HTTP request,
    which is exactly the failure the transport probe exists to avoid.
    """

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.response

    def stream(self, **kwargs):
        self.stream_kwargs = kwargs
        return _Stream(self.response)


def _client(
    model="claude-opus-4-8",
    *,
    thinking_mode=None,
    reasoning_effort=None,
    blocks=None,
    usage=None,
    stop_reason="end_turn",
    messages_cls=_Messages,
    monkeypatch=None,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = LLMClient(
        provider="anthropic",
        model=model,
        temperature=0.0,
        max_tokens=65536,
        reasoning_effort=reasoning_effort,
        thinking_mode=thinking_mode,
    )
    response = _Response(
        blocks if blocks is not None else [_Block("text", '{"pairs": []}')],
        usage if usage is not None else _Usage(thinking_tokens=4321),
        stop_reason=stop_reason,
    )
    messages = messages_cls(response)
    client._client = type("FakeSDK", (), {"messages": messages})()
    return client, messages


# ---------------------------------------------------------------------------
# request shape


def test_opus_48_adaptive_request_uses_adaptive_thinking_and_xhigh_effort(monkeypatch):
    client, messages = _client(
        thinking_mode="adaptive", reasoning_effort="xhigh", monkeypatch=monkeypatch
    )
    client.generate(system="sys", user="user")

    kwargs = messages.stream_kwargs
    assert kwargs is not None, "adaptive thinking must take the streaming path"
    assert messages.create_kwargs is None
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "xhigh"}
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["max_tokens"] == 65536


def test_opus_48_adaptive_request_never_sends_legacy_budget_tokens(monkeypatch):
    client, messages = _client(
        thinking_mode="adaptive", reasoning_effort="xhigh", monkeypatch=monkeypatch
    )
    client.generate(system="sys", user="user")

    thinking = messages.stream_kwargs["thinking"]
    assert "budget_tokens" not in thinking
    assert thinking.get("type") != "enabled"


def test_adaptive_request_omits_temperature_and_records_why(monkeypatch):
    client, messages = _client(
        thinking_mode="adaptive", reasoning_effort="xhigh", monkeypatch=monkeypatch
    )
    client.generate(system="sys", user="user")

    assert "temperature" not in messages.stream_kwargs
    meta = client.last_response_metadata
    assert meta["temperature_requested"] == 0.0
    assert meta["temperature_applied"] is None
    assert meta["temperature_omission_reason"] == "adaptive_thinking_provider_contract"


def test_adaptive_is_not_accepted_as_an_effort_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(ValueError, match="unsupported reasoning_effort"):
        LLMClient(provider="anthropic", model="claude-opus-4-8", reasoning_effort="adaptive")


def test_unknown_thinking_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(ValueError, match="unsupported thinking_mode"):
        LLMClient(
            provider="anthropic", model="claude-opus-4-8", thinking_mode="enabled"
        )


def test_effort_rides_in_extra_body_on_an_older_sdk(monkeypatch):
    """An SDK without an output_config parameter must not raise TypeError."""

    client, messages = _client(
        thinking_mode="adaptive",
        reasoning_effort="xhigh",
        messages_cls=_LegacyMessages,
        monkeypatch=monkeypatch,
    )
    client.generate(system="sys", user="user")

    kwargs = messages.stream_kwargs
    assert "output_config" not in kwargs
    assert kwargs["extra_body"] == {"output_config": {"effort": "xhigh"}}
    # same wire bytes, so the applied-effort record is unchanged
    meta = client.last_response_metadata
    assert meta["reasoning_effort_applied"] == "xhigh"
    assert meta["reasoning_effort_transport"] == "extra_body"


def test_effort_uses_the_named_parameter_when_the_sdk_supports_it(monkeypatch):
    client, messages = _client(
        thinking_mode="adaptive", reasoning_effort="xhigh", monkeypatch=monkeypatch
    )
    client.generate(system="sys", user="user")

    assert messages.stream_kwargs["output_config"] == {"effort": "xhigh"}
    assert "extra_body" not in messages.stream_kwargs
    meta = client.last_response_metadata
    assert meta["reasoning_effort_transport"] == "named_parameter"


def test_no_effort_requested_sends_no_output_config(monkeypatch):
    client, messages = _client(
        thinking_mode="adaptive", messages_cls=_LegacyMessages, monkeypatch=monkeypatch
    )
    client.generate(system="sys", user="user")

    assert "extra_body" not in messages.stream_kwargs
    meta = client.last_response_metadata
    assert meta["reasoning_effort_applied"] is None
    assert meta["reasoning_effort_transport"] is None


def test_supported_effort_values():
    assert ANTHROPIC_EFFORT_VALUES == {"low", "medium", "high", "xhigh", "max"}
    assert "adaptive" not in ANTHROPIC_EFFORT_VALUES


# ---------------------------------------------------------------------------
# response handling


def test_only_text_blocks_reach_the_parser(monkeypatch):
    client, _ = _client(
        thinking_mode="adaptive",
        reasoning_effort="xhigh",
        blocks=[
            _Block("thinking", thinking="private chain of thought"),
            _Block("text", '{"pairs": '),
            _Block("text", "[]}"),
        ],
        monkeypatch=monkeypatch,
    )
    text = client.generate(system="sys", user="user")
    assert text == '{"pairs": []}'
    assert "private chain of thought" not in text


def test_thinking_block_content_is_not_persisted(monkeypatch):
    secret = "PRIVATE-REASONING-DO-NOT-STORE"
    client, _ = _client(
        thinking_mode="adaptive",
        reasoning_effort="xhigh",
        blocks=[_Block("thinking", thinking=secret), _Block("text", '{"pairs": []}')],
        monkeypatch=monkeypatch,
    )
    client.generate(system="sys", user="user")

    meta = client.last_response_metadata
    assert secret not in repr(meta)
    assert meta["thinking_block_present"] is True
    # only safe counters and flags
    assert meta["thinking_mode_applied"] == "adaptive"
    assert meta["streaming_used"] is True


def test_thinking_tokens_are_read_from_output_tokens_details(monkeypatch):
    client, _ = _client(
        thinking_mode="adaptive",
        reasoning_effort="xhigh",
        usage=_Usage(output_tokens=300, thinking_tokens=12345),
        monkeypatch=monkeypatch,
    )
    client.generate(system="sys", user="user")
    meta = client.last_response_metadata
    assert meta["thinking_tokens"] == 12345
    assert meta["thinking_tokens_source"] == "output_tokens_details"
    # never inferred by subtracting visible text tokens
    assert meta["output_tokens"] == 300


def test_absent_thinking_count_becomes_null_not_zero(monkeypatch):
    client, _ = _client(
        thinking_mode="adaptive",
        reasoning_effort="xhigh",
        usage=_Usage(thinking_tokens=None),
        monkeypatch=monkeypatch,
    )
    client.generate(system="sys", user="user")
    meta = client.last_response_metadata
    assert meta["thinking_tokens"] is None
    assert meta["thinking_tokens_source"] == "unavailable"


def test_thinking_tokens_helper_handles_dict_shaped_usage():
    assert anthropic_thinking_tokens(
        {"output_tokens_details": {"thinking_tokens": 7}}
    ) == (7, "output_tokens_details")
    assert anthropic_thinking_tokens({"output_tokens_details": {}}) == (
        None,
        "unavailable",
    )
    assert anthropic_thinking_tokens({}) == (None, "unavailable")
    assert anthropic_thinking_tokens(None) == (None, "unavailable")


def test_truncation_indicator_follows_stop_reason(monkeypatch):
    client, _ = _client(
        thinking_mode="adaptive",
        reasoning_effort="xhigh",
        stop_reason="max_tokens",
        monkeypatch=monkeypatch,
    )
    client.generate(system="sys", user="user")
    assert client.last_response_metadata["truncated"] is True
    assert client.last_response_metadata["stop_reason"] == "max_tokens"


def test_thinking_only_response_at_max_tokens_is_not_retried(monkeypatch):
    """The whole budget went to thinking; retrying burns it again for nothing."""

    client, messages = _client(
        thinking_mode="adaptive",
        reasoning_effort="xhigh",
        blocks=[_Block("thinking", thinking="...")],  # no text block at all
        stop_reason="max_tokens",
        monkeypatch=monkeypatch,
    )
    call_count = 0
    inner = messages.stream

    def counting_stream(**kwargs):
        nonlocal call_count
        call_count += 1
        return inner(**kwargs)

    messages.stream = counting_stream

    with pytest.raises(TruncatedLLMResponseError, match="output budget"):
        client.generate(system="sys", user="user")
    assert call_count == 1, "a deterministic truncation must not be retried"
    meta = client.last_response_metadata
    assert meta["truncated"] is True
    assert meta["content_block_types"] == ["thinking"]


def test_empty_response_without_truncation_is_still_retried(monkeypatch):
    """A blank response that did not hit the cap may be transient."""

    client, messages = _client(
        blocks=[_Block("text", "")],
        stop_reason="end_turn",
        monkeypatch=monkeypatch,
    )
    call_count = 0
    inner = messages.create

    def counting_create(**kwargs):
        nonlocal call_count
        call_count += 1
        return inner(**kwargs)

    messages.create = counting_create

    with pytest.raises(EmptyLLMResponseError):
        client.generate(system="sys", user="user")
    assert call_count == 3, "transient empties keep the existing retry behavior"


# ---------------------------------------------------------------------------
# unchanged non-thinking behavior


def test_non_thinking_anthropic_call_is_unchanged(monkeypatch):
    client, messages = _client(monkeypatch=monkeypatch)
    client.generate(system="sys", user="user")

    assert messages.create_kwargs is not None, "must stay on the non-streaming path"
    assert messages.stream_kwargs is None
    assert "thinking" not in messages.create_kwargs
    assert "output_config" not in messages.create_kwargs
    meta = client.last_response_metadata
    assert meta["thinking_mode_requested"] is None
    assert meta["thinking_mode_applied"] is None
    assert meta["streaming_used"] is False


def test_older_anthropic_model_keeps_temperature_and_never_gets_adaptive(monkeypatch):
    """Opus 4.6 accepts temperature and is not an adaptive-thinking model."""

    client, messages = _client(
        model="claude-opus-4-6", thinking_mode="adaptive", monkeypatch=monkeypatch
    )
    client.generate(system="sys", user="user")

    assert messages.create_kwargs is not None
    assert "thinking" not in messages.create_kwargs
    assert messages.create_kwargs["temperature"] == 0.0
    meta = client.last_response_metadata
    assert meta["thinking_mode_applied"] is None
    assert meta["temperature_applied"] == 0.0
    assert meta["temperature_omission_reason"] is None
