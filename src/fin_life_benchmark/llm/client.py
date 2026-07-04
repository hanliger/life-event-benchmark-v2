"""Provider-agnostic LLM client. Keys come from .env; never hard-code or
print secrets."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential


class LLMClient:
    def __init__(
        self,
        provider: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
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
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "2048")),
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
    def generate(self, system: str, user: str) -> str:
        if self.provider == "openai":
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return response.choices[0].message.content or ""
        if self.provider == "anthropic":
            response = self._client.messages.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        raise RuntimeError("mock provider has no generate(); use mock dialogue mode instead")
