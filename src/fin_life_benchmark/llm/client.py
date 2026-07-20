"""Provider-agnostic LLM client. Keys come from .env; never hard-code or
print secrets."""

from __future__ import annotations

import os
import time
from typing import Any

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential


def _anthropic_supports_temperature(model: str) -> bool:
    """Newer Claude generation models reject the temperature parameter."""
    lowered = model.lower()
    return not (
        lowered.startswith("claude-")
        and any(family in lowered for family in ("sonnet-5", "fable-5", "mythos-5", "opus-5"))
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
        value = getattr(usage, field, None)
        if value is not None:
            metadata[field] = int(value)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cached_tokens = getattr(prompt_details, "cached_tokens", None)
    if cached_tokens is not None:
        metadata["cached_tokens"] = int(cached_tokens)
    return metadata or None


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
    ):
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        if reasoning_effort not in {None, "none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"unsupported reasoning_effort: {reasoning_effort}")
        if response_format not in {"prompt_json", "json_schema"}:
            raise ValueError(f"unsupported response_format: {response_format}")
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
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
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
            if _openai_supports_temperature(self.model):
                kwargs["temperature"] = self.temperature
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
            kwargs = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
            if _anthropic_supports_temperature(self.model):
                kwargs["temperature"] = self.temperature
            response = self._client.messages.create(**kwargs)
            blocks = list(response.content or [])
            usage = _usage_metadata(getattr(response, "usage", None)) or {}
            duration_ms = round((time.monotonic() - (self._request_started_at or time.monotonic())) * 1000, 3)
            self.last_response_metadata = {
                "provider": self.provider,
                "model": self.model,
                "reasoning_effort": None,
                "response_format": self.response_format,
                "stop_reason": getattr(response, "stop_reason", None),
                "stop_sequence": getattr(response, "stop_sequence", None),
                "content_block_types": [getattr(block, "type", type(block).__name__) for block in blocks],
                "usage": usage,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cached_tokens": usage.get("cache_read_input_tokens"),
                "request_duration_ms": duration_ms,
                "retry_count": self._provider_attempts_since_success - 1,
            }
            text = "".join(
                getattr(block, "text", "")
                for block in blocks
                if getattr(block, "type", "") == "text"
            )
            if not text.strip():
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
