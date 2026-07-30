from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from threading import BoundedSemaphore
from time import perf_counter
from typing import Any, Protocol

from ..safety import assert_provider_construction_allowed


_GLOBAL_GENERATION_SEMAPHORE: BoundedSemaphore | None = None
_PROVIDER_GENERATION_SEMAPHORES: dict[str, BoundedSemaphore] = {}


def configure_generation_limits(
    *,
    max_in_flight: int | None,
    provider_limits: dict[str, int] | None = None,
) -> None:
    global _GLOBAL_GENERATION_SEMAPHORE
    global _PROVIDER_GENERATION_SEMAPHORES
    if max_in_flight is not None and max_in_flight <= 0:
        raise ValueError("max_in_flight must be positive")
    _GLOBAL_GENERATION_SEMAPHORE = (
        BoundedSemaphore(max_in_flight)
        if max_in_flight is not None
        else None
    )
    limits = provider_limits or {}
    if any(value <= 0 for value in limits.values()):
        raise ValueError("provider generation limits must be positive")
    _PROVIDER_GENERATION_SEMAPHORES = {
        provider: BoundedSemaphore(value)
        for provider, value in limits.items()
    }


@contextmanager
def generation_slot(provider: str):
    with ExitStack() as stack:
        if _GLOBAL_GENERATION_SEMAPHORE is not None:
            stack.enter_context(_GLOBAL_GENERATION_SEMAPHORE)
        provider_limit = _PROVIDER_GENERATION_SEMAPHORES.get(provider)
        if provider_limit is not None:
            stack.enter_context(provider_limit)
        yield


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
        api_surface: str | None = None,
    ):
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.generation_settings = deepcopy(generation_settings or {})
        self.api_surface = api_surface
        if provider == "openai" and api_surface not in {
            None,
            "responses",
            "chat_completions",
        }:
            raise ValueError(f"unsupported OpenAI API surface: {api_surface}")
        reserved = {
            "anthropic": {"model", "max_tokens", "messages", "system"},
            "openai": {
                "model",
                "instructions",
                "input",
                "max_output_tokens",
                "messages",
                "max_completion_tokens",
            },
            "google": {"model", "contents", "system_instruction", "max_output_tokens"},
            "gemini": {"model", "contents", "system_instruction", "max_output_tokens"},
            "openrouter": {"model", "messages", "max_tokens"},
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
        elif provider == "openrouter":
            import os

            from openai import OpenAI

            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise RuntimeError("OPENROUTER_API_KEY is required")
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                max_retries=0,
                timeout=timeout_seconds,
            )
        else:
            raise ValueError(f"unsupported provider: {provider}")

    def generate(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> tuple[str, dict[str, Any]]:
        with generation_slot(self.provider):
            return self._generate_unlimited(
                system=system,
                user=user,
                max_tokens=max_tokens,
            )

    def _generate_unlimited(
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
            if getattr(self, "api_surface", None) == "chat_completions":
                request = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_completion_tokens": output_tokens,
                    **deepcopy(self.generation_settings),
                }
                response = self.client.chat.completions.create(**request)
                choice = response.choices[0] if response.choices else None
                message = getattr(choice, "message", None)
                text = getattr(message, "content", "") or ""
                usage = getattr(response, "usage", None)
            else:
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
        elif self.provider in {"google", "gemini"}:
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
        else:
            request = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": output_tokens,
                "extra_body": deepcopy(self.generation_settings),
            }
            response = self.client.chat.completions.create(**request)
            choice = response.choices[0]
            content = getattr(choice.message, "content", "")
            if isinstance(content, list):
                text = "".join(
                    str(
                        block.get("text", "")
                        if isinstance(block, dict)
                        else getattr(block, "text", "")
                    )
                    for block in content
                )
            else:
                text = str(content or "")
            usage = getattr(response, "usage", None)
        latency_seconds = perf_counter() - started
        if not str(text).strip():
            raise RuntimeError(f"empty response from {self.provider}/{self.model}")
        result = {
            "provider": self.provider,
            "model": self.model,
            "usage": _usage_dict(usage),
            "automatic_retries": 0,
            "request_timeout_seconds": self.timeout_seconds,
            "max_output_tokens": output_tokens,
            "generation_settings": deepcopy(self.generation_settings),
            "api_surface": getattr(self, "api_surface", None) or (
                "responses" if self.provider == "openai" else None
            ),
            "latency_seconds": round(latency_seconds, 6),
        }
        if self.provider == "openrouter":
            result.update(
                {
                    "response_model": getattr(response, "model", None),
                    "response_id": getattr(response, "id", None),
                    "routed_provider": getattr(response, "provider", None),
                }
            )
        return str(text), result
