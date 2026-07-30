"""Provider-agnostic LLM client. Keys come from .env; never hard-code or
print secrets."""

from __future__ import annotations

import inspect
import os
import time
from typing import Any

from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)


def _anthropic_supports_temperature(model: str) -> bool:
    """Newer Claude generation models reject the temperature parameter.

    Opus 4.7 / 4.8, Sonnet 5, and the Fable/Mythos 5 family return HTTP 400 when
    ``temperature`` (or ``top_p`` / ``top_k``) is sent. Opus 4.6 and earlier
    still accept it.
    """
    lowered = model.lower()
    return not (
        lowered.startswith("claude-")
        and any(
            family in lowered
            for family in ("opus-4-7", "opus-4-8", "sonnet-5", "fable-5", "mythos-5", "opus-5")
        )
    )


def _openai_uses_max_completion_tokens(model: str) -> bool:
    """Newer OpenAI chat-compatible models require max_completion_tokens."""
    lowered = model.lower()
    return lowered.startswith(("gpt-5", "o1", "o3", "o4"))


def _openai_supports_temperature(model: str) -> bool:
    """Reasoning/frontier OpenAI models often reject non-default temperature."""
    lowered = model.lower()
    return not lowered.startswith(("gpt-5", "o1", "o3", "o4"))


def _openai_supports_reasoning_effort(model: str) -> bool:
    lowered = model.lower()
    return lowered.startswith(("gpt-5.6", "o1", "o3", "o4"))


class EmptyLLMResponseError(RuntimeError):
    """Raised when a provider returns no usable text after a successful call."""


class TruncatedLLMResponseError(EmptyLLMResponseError):
    """Raised when the output budget ran out before any answer text was emitted.

    Deliberately *not* retried: a response that generated blocks and stopped on
    ``max_tokens`` without any text block is deterministic, so a retry burns the
    whole budget again for the same outcome. Under adaptive thinking the fix is a
    larger ``max_tokens`` (thinking and answer text share one budget) or a lower
    effort level -- never another attempt at the same settings.

    An empty content list at ``max_tokens`` is *not* this: nothing was generated,
    which can be transient, so it stays on the retrying path.
    """


# Anthropic output_config effort levels. "adaptive" is deliberately absent: it
# names a thinking mode, not an effort.
ANTHROPIC_EFFORT_VALUES = frozenset({"low", "medium", "high", "xhigh", "max"})
# None keeps the historical non-thinking request shape untouched.
THINKING_MODES = frozenset({None, "adaptive"})


def _anthropic_uses_adaptive_thinking(model: str, thinking_mode: str | None) -> bool:
    """Adaptive thinking applies to the Opus 4.8+ generation only.

    Older Claude models take the legacy fixed-budget
    ``thinking={"type": "enabled", "budget_tokens": N}`` shape, which this client
    deliberately never sends to Opus 4.8.
    """

    if thinking_mode != "adaptive":
        return False
    lowered = model.lower()
    return lowered.startswith(("claude-opus-4-8", "claude-opus-5", "claude-fable-5"))


def _accepts_output_config(method: Any) -> bool:
    """Whether this SDK build takes ``output_config`` as a named parameter.

    ``output_config`` (the adaptive-thinking effort level) is GA on the API but
    was added to the Python SDK later than the pin in requirements.txt. On an
    older build the named argument raises TypeError *before* any HTTP call, so
    the effort level has to ride in ``extra_body`` instead -- same wire bytes,
    same request. Probing the signature keeps both SDK generations working, and
    the native path takes over automatically once the SDK is upgraded.
    """

    try:
        return "output_config" in inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False


def _usage_metadata(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "thoughts_token_count",
    )
    metadata: dict[str, int] = {}
    for field in fields:
        value = _attr_or_key(usage, field)
        if value is not None:
            metadata[field] = int(value)
    prompt_details = _attr_or_key(usage, "prompt_tokens_details")
    cached_tokens = _attr_or_key(prompt_details, "cached_tokens")
    if cached_tokens is not None:
        metadata["cached_tokens"] = int(cached_tokens)
    return metadata or None


def _attr_or_key(source: Any, name: str) -> Any:
    """Read ``name`` from an SDK object or a dict-shaped stand-in.

    Provider SDKs return attribute objects; tests and cached fixtures use plain
    dicts. Both shapes must resolve identically.
    """

    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def anthropic_thinking_tokens(usage: Any) -> tuple[int | None, str]:
    """Read Anthropic thinking usage from ``output_tokens_details``.

    Returns ``(tokens, source)``. When the field is absent the count is ``None``
    with source ``"unavailable"`` -- never 0, and never inferred by subtracting
    visible text tokens, which would silently invent a number.
    """

    details = _attr_or_key(usage, "output_tokens_details")
    value = _attr_or_key(details, "thinking_tokens")
    if value is None:
        return None, "unavailable"
    return int(value), "output_tokens_details"


def _gemini_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text)
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(str(part_text))
    return "".join(chunks)


class LLMClient:
    def __init__(
        self,
        provider: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        reasoning_effort: str | None = None,
        response_format: str = "prompt_json",
        response_schema: dict[str, Any] | None = None,
        cache_prompt: bool = False,
        thinking_mode: str | None = None,
    ):
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # When True, mark the (stable) system prompt with cache_control so a fixed
        # prefix reused across many calls (e.g. a judge rubric) bills at cache-read
        # rates. Anthropic only; no-op for prefixes below the model's cache minimum.
        self.cache_prompt = cache_prompt
        if reasoning_effort not in ANTHROPIC_EFFORT_VALUES | {None, "none"}:
            raise ValueError(f"unsupported reasoning_effort: {reasoning_effort}")
        if response_format not in {"prompt_json", "json_schema"}:
            raise ValueError(f"unsupported response_format: {response_format}")
        # "adaptive" is a thinking *mode*, never an effort level.
        if thinking_mode not in THINKING_MODES:
            raise ValueError(
                f"unsupported thinking_mode: {thinking_mode!r} "
                f"(expected one of {sorted(m for m in THINKING_MODES if m)})"
            )
        self.thinking_mode = thinking_mode
        self.reasoning_effort = reasoning_effort
        self.response_format = response_format
        self.response_schema = response_schema
        self._client = None
        self.last_response_metadata: dict[str, Any] = {}
        self._provider_attempts_since_success = 0
        self._request_started_at: float | None = None
        if provider == "openai":
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("OPENAI_API_KEY not set (see .env.example)")
            from openai import OpenAI

            self._client = OpenAI(api_key=key)
        elif provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not set (see .env.example)")
            import anthropic

            self._client = anthropic.Anthropic(api_key=key)
        elif provider in {"gemini", "google"}:
            key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY not set (see .env.example)")
            from google import genai

            self._client = genai.Client(api_key=key)
        elif provider == "mock":
            pass
        else:
            raise ValueError(f"unknown provider: {provider}")

    @classmethod
    def from_env(
        cls,
        provider: str | None = None,
        model: str | None = None,
        *,
        reasoning_effort: str | None = None,
        response_format: str = "prompt_json",
        response_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        cache_prompt: bool = False,
        thinking_mode: str | None = None,
    ) -> "LLMClient":
        load_dotenv()
        provider = provider or os.environ.get("DEFAULT_LLM_PROVIDER", "mock")
        model = model or os.environ.get("DEFAULT_GENERATION_MODEL", "mock")
        return cls(
            provider=provider,
            model=model,
            temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7")),
            max_tokens=max_tokens or int(os.environ.get("LLM_MAX_TOKENS", "8192")),
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            response_schema=response_schema,
            cache_prompt=cache_prompt,
            thinking_mode=thinking_mode,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_not_exception_type(TruncatedLLMResponseError),
        reraise=True,
    )
    def generate(self, system: str, user: str) -> str:
        if self._provider_attempts_since_success == 0:
            self._request_started_at = time.monotonic()
        self._provider_attempts_since_success += 1
        self.last_response_metadata = {}
        if self.provider == "openai":
            kwargs = {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }
            if _openai_uses_max_completion_tokens(self.model):
                kwargs["max_completion_tokens"] = self.max_tokens
            else:
                kwargs["max_tokens"] = self.max_tokens
            temperature_applied: float | None = None
            temperature_omission_reason: str | None = None
            if _openai_supports_temperature(self.model):
                kwargs["temperature"] = self.temperature
                temperature_applied = self.temperature
            else:
                # The frontier models reject a non-default temperature, so a
                # requested 0.0 is silently NOT honored -- the call samples at
                # the provider default. Recorded rather than dropped: a
                # single-sample protocol has to be able to state whether its
                # runs were deterministic.
                temperature_omission_reason = "model_rejects_temperature"
            if self.reasoning_effort is not None and _openai_supports_reasoning_effort(self.model):
                kwargs["reasoning_effort"] = self.reasoning_effort
            if self.response_format == "json_schema":
                if not self.response_schema:
                    raise ValueError("response_format=json_schema requires response_schema")
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "dialogue_response",
                        "strict": True,
                        "schema": self.response_schema,
                    },
                }
            response = self._client.chat.completions.create(**kwargs)
            choice = response.choices[0] if response.choices else None
            message = getattr(choice, "message", None)
            text = getattr(message, "content", "") or ""
            usage = _usage_metadata(getattr(response, "usage", None)) or {}
            duration_ms = round((time.monotonic() - (self._request_started_at or time.monotonic())) * 1000, 3)
            self.last_response_metadata = {
                "provider": self.provider,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "response_format": self.response_format,
                "finish_reason": getattr(choice, "finish_reason", None),
                "temperature_requested": self.temperature,
                "temperature_applied": temperature_applied,
                "temperature_omission_reason": temperature_omission_reason,
                "usage": usage,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "cached_tokens": usage.get("cached_tokens"),
                "request_duration_ms": duration_ms,
                "retry_count": self._provider_attempts_since_success - 1,
            }
            if not text.strip():
                raise EmptyLLMResponseError(f"empty text response from {self.provider}")
            self._provider_attempts_since_success = 0
            self._request_started_at = None
            return text
        if self.provider == "anthropic":
            system_param: Any = system
            if self.cache_prompt and isinstance(system, str) and system.strip():
                # Cache the stable system prefix (rubric/instructions). The last
                # cacheable block carries the breakpoint; per-request user content
                # stays uncached after it.
                system_param = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            kwargs = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system_param,
                "messages": [{"role": "user", "content": user}],
            }
            adaptive = _anthropic_uses_adaptive_thinking(self.model, self.thinking_mode)
            temperature_applied: float | None = None
            temperature_omission_reason: str | None = None
            effort_sent = False
            effort_transport: str | None = None
            if adaptive:
                # Adaptive thinking: the model decides its own budget, so no
                # legacy budget_tokens is sent. Temperature is omitted under the
                # adaptive contract.
                kwargs["thinking"] = {"type": "adaptive"}
                effort = self.reasoning_effort
                if effort in ANTHROPIC_EFFORT_VALUES:
                    send = (
                        self._client.messages.stream
                        if adaptive
                        else self._client.messages.create
                    )
                    if _accepts_output_config(send):
                        kwargs["output_config"] = {"effort": effort}
                        effort_transport = "named_parameter"
                    else:
                        extra_body = dict(kwargs.get("extra_body") or {})
                        extra_body["output_config"] = {"effort": effort}
                        kwargs["extra_body"] = extra_body
                        effort_transport = "extra_body"
                    effort_sent = True
                temperature_omission_reason = "adaptive_thinking_provider_contract"
            elif _anthropic_supports_temperature(self.model):
                kwargs["temperature"] = self.temperature
                temperature_applied = self.temperature
            else:
                temperature_omission_reason = "model_rejects_temperature"

            if adaptive:
                # Streaming keeps the SDK from rejecting a request whose thinking
                # budget could push it past the non-streaming time limit.
                with self._client.messages.stream(**kwargs) as stream:
                    response = stream.get_final_message()
                streaming_used = True
            else:
                response = self._client.messages.create(**kwargs)
                streaming_used = False

            blocks = list(response.content or [])
            block_types = [
                getattr(block, "type", type(block).__name__) for block in blocks
            ]
            usage_raw = getattr(response, "usage", None)
            usage = _usage_metadata(usage_raw) or {}
            thinking_tokens, thinking_tokens_source = anthropic_thinking_tokens(usage_raw)
            stop_reason = getattr(response, "stop_reason", None)
            duration_ms = round((time.monotonic() - (self._request_started_at or time.monotonic())) * 1000, 3)
            self.last_response_metadata = {
                "provider": self.provider,
                "model": self.model,
                # Only safe counters and flags. Thinking block *content* is never
                # read out of the response, stored, or logged.
                "thinking_mode_requested": self.thinking_mode,
                "thinking_mode_applied": "adaptive" if adaptive else None,
                "thinking_block_present": "thinking" in block_types,
                "thinking_tokens": thinking_tokens,
                "thinking_tokens_source": thinking_tokens_source,
                "reasoning_effort_requested": self.reasoning_effort,
                # The API does not echo effort back; "applied" means the provider
                # accepted this value without error.
                "reasoning_effort_applied": (
                    self.reasoning_effort if adaptive and effort_sent else None
                ),
                "reasoning_effort_transport": effort_transport,
                "temperature_requested": self.temperature,
                "temperature_applied": temperature_applied,
                "temperature_omission_reason": temperature_omission_reason,
                "streaming_used": streaming_used,
                "truncated": stop_reason == "max_tokens",
                "response_format": self.response_format,
                "stop_reason": stop_reason,
                "stop_sequence": getattr(response, "stop_sequence", None),
                "content_block_types": block_types,
                "usage": usage,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cached_tokens": usage.get("cache_read_input_tokens"),
                "request_duration_ms": duration_ms,
                "retry_count": self._provider_attempts_since_success - 1,
            }
            # Only text blocks reach the JSON parser; thinking blocks are dropped.
            text = "".join(
                getattr(block, "text", "")
                for block in blocks
                if getattr(block, "type", "") == "text"
            )
            if not text.strip():
                # A real budget exhaustion produced blocks (typically thinking)
                # before running out. An empty content list at max_tokens is a
                # blank response instead, and blank responses can be transient --
                # so that case keeps the existing retry.
                if stop_reason == "max_tokens" and blocks:
                    raise TruncatedLLMResponseError(
                        f"{self.model} spent its entire {self.max_tokens}-token output "
                        f"budget without emitting answer text "
                        f"(blocks={block_types}, effort={self.reasoning_effort!r}); "
                        f"raise --max-tokens or lower the effort level. "
                        f"metadata={self.last_response_metadata}"
                    )
                raise EmptyLLMResponseError(
                    f"empty text response from {self.provider}; metadata={self.last_response_metadata}"
                )
            self._provider_attempts_since_success = 0
            self._request_started_at = None
            return text
        if self.provider in {"gemini", "google"}:
            from google.genai import types

            response = self._client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=self.temperature,
                    max_output_tokens=self.max_tokens,
                ),
            )
            usage = _usage_metadata(getattr(response, "usage_metadata", None)) or {}
            duration_ms = round((time.monotonic() - (self._request_started_at or time.monotonic())) * 1000, 3)
            self.last_response_metadata = {
                "provider": self.provider,
                "model": self.model,
                "reasoning_effort": None,
                "response_format": self.response_format,
                # Gemini accepts temperature on every model this repo uses, so
                # the requested value is always the applied one.
                "temperature_requested": self.temperature,
                "temperature_applied": self.temperature,
                "temperature_omission_reason": None,
                "usage": usage,
                "request_duration_ms": duration_ms,
                "retry_count": self._provider_attempts_since_success - 1,
            }
            text = _gemini_response_text(response)
            if not text.strip():
                raise EmptyLLMResponseError(
                    f"empty text response from {self.provider}; metadata={self.last_response_metadata}"
                )
            self._provider_attempts_since_success = 0
            self._request_started_at = None
            return text
        raise RuntimeError("mock provider has no generate(); use mock dialogue mode instead")
