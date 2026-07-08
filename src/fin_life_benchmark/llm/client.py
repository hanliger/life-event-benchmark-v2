"""Provider-agnostic LLM client. Keys come from .env; never hard-code or
print secrets."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential


def _anthropic_supports_temperature(model: str) -> bool:
    """Claude 5 generation models reject the temperature parameter."""
    lowered = model.lower()
    return not (
        lowered.startswith("claude-")
        and any(family in lowered for family in ("sonnet-5", "fable-5", "mythos-5"))
    )


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
    )
    metadata: dict[str, int] = {}
    for field in fields:
        value = getattr(usage, field, None)
        if value is not None:
            metadata[field] = int(value)
    return metadata or None


class LLMClient:
    def __init__(
        self,
        provider: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ):
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
        self.last_response_metadata: dict[str, Any] = {}
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
        elif provider == "mock":
            pass
        else:
            raise ValueError(f"unknown provider: {provider}")

    @classmethod
    def from_env(cls, provider: str | None = None, model: str | None = None) -> "LLMClient":
        load_dotenv()
        provider = provider or os.environ.get("DEFAULT_LLM_PROVIDER", "mock")
        model = model or os.environ.get("DEFAULT_GENERATION_MODEL", "mock")
        return cls(
            provider=provider,
            model=model,
            temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7")),
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "8192")),
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
    def generate(self, system: str, user: str) -> str:
        self.last_response_metadata = {}
        if self.provider == "openai":
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            choice = response.choices[0] if response.choices else None
            message = getattr(choice, "message", None)
            text = getattr(message, "content", "") or ""
            self.last_response_metadata = {
                "provider": self.provider,
                "model": self.model,
                "finish_reason": getattr(choice, "finish_reason", None),
                "usage": _usage_metadata(getattr(response, "usage", None)),
            }
            if not text.strip():
                raise EmptyLLMResponseError(f"empty text response from {self.provider}")
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
            self.last_response_metadata = {
                "provider": self.provider,
                "model": self.model,
                "stop_reason": getattr(response, "stop_reason", None),
                "stop_sequence": getattr(response, "stop_sequence", None),
                "content_block_types": [getattr(block, "type", type(block).__name__) for block in blocks],
                "usage": _usage_metadata(getattr(response, "usage", None)),
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
            return text
        raise RuntimeError("mock provider has no generate(); use mock dialogue mode instead")
