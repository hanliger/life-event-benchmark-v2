from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from ..safety import assert_provider_construction_allowed


def _usage_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    fields = (
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "thoughts_token_count",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    result = {
        field: int(value)
        for field in fields
        if (value := getattr(usage, field, None)) is not None
    }
    return result or None


class Reader(Protocol):
    def generate(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> tuple[str, dict[str, Any]]: ...


@dataclass
class MockReader:
    answer: str = "<answer>A</answer>"

    def generate(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> tuple[str, dict[str, Any]]:
        return self.answer, {"provider": "mock", "model": "mock", "paid": False}


class ProviderReader:
    """Fail-closed provider adapter with explicit timeout and zero SDK retries."""

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        max_tokens: int = 4096,
        timeout_seconds: float = 120,
        generation_settings: dict[str, Any] | None = None,
    ):
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.generation_settings = deepcopy(generation_settings or {})
        reserved = {
            "anthropic": {"model", "max_tokens", "messages", "system"},
            "openai": {"model", "instructions", "input", "max_output_tokens"},
            "google": {"model", "contents", "system_instruction", "max_output_tokens"},
            "gemini": {"model", "contents", "system_instruction", "max_output_tokens"},
        }
        overlap = reserved.get(provider, set()) & self.generation_settings.keys()
        if overlap:
            raise ValueError(
                "generation_settings cannot override required request fields: "
                f"{sorted(overlap)}"
            )
        assert_provider_construction_allowed()
        if provider == "anthropic":
            import anthropic

            self.client = anthropic.Anthropic(
                max_retries=0,
                timeout=timeout_seconds,
            )
        elif provider == "openai":
            from openai import OpenAI

            self.client = OpenAI(
                max_retries=0,
                timeout=timeout_seconds,
            )
        elif provider in {"google", "gemini"}:
            from google import genai
            from google.genai import types

            self.client = genai.Client(
                http_options=types.HttpOptions(
                    timeout=int(timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                )
            )
        else:
            raise ValueError(f"unsupported provider: {provider}")

    def generate(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> tuple[str, dict[str, Any]]:
        output_tokens = int(max_tokens or self.max_tokens)
        started = perf_counter()
        if self.provider == "anthropic":
            request = {
                "model": self.model,
                "max_tokens": output_tokens,
                "messages": [{"role": "user", "content": user}],
                "system": system,
                **deepcopy(self.generation_settings),
            }
            response = self.client.messages.create(**request)
            text = "".join(getattr(block, "text", "") for block in response.content)
            usage = getattr(response, "usage", None)
        elif self.provider == "openai":
            request = {
                "model": self.model,
                "instructions": system,
                "input": user,
                "max_output_tokens": output_tokens,
                **deepcopy(self.generation_settings),
            }
            response = self.client.responses.create(**request)
            text = response.output_text
            usage = getattr(response, "usage", None)
        else:
            config = {
                "system_instruction": system,
                "max_output_tokens": output_tokens,
                **deepcopy(self.generation_settings),
            }
            response = self.client.models.generate_content(
                model=self.model,
                contents=user,
                config=config,
            )
            text = response.text
            usage = getattr(response, "usage_metadata", None)
        latency_seconds = perf_counter() - started
        if not str(text).strip():
            raise RuntimeError(f"empty response from {self.provider}/{self.model}")
        return str(text), {
            "provider": self.provider,
            "model": self.model,
            "usage": _usage_dict(usage),
            "automatic_retries": 0,
            "request_timeout_seconds": self.timeout_seconds,
            "max_output_tokens": output_tokens,
            "generation_settings": deepcopy(self.generation_settings),
            "latency_seconds": round(latency_seconds, 6),
        }
