"""Locale config loading. Country-specific logic must live in these configs so
new locales can be added without touching generation code paths."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..io import RepoPaths, load_yaml


class DialogueStyle(BaseModel):
    user_style: str
    assistant_style: str
    ban_direct_life_event_mention: bool = True
    ban_assistant_event_summary: bool = True


class LocaleConfig(BaseModel):
    locale: str
    country: str
    language: str
    currency: str
    legal_adulthood_age: int
    typical_retirement_age_range: tuple[int, int]
    school_ages: dict[str, int]
    housing_contract_types: list[str]
    financial_products: list[str]
    banking_terms: dict[str, str | None]
    dialogue_style: DialogueStyle
    value_pools: dict[str, Any] = Field(default_factory=dict)

    def pool(self, name: str) -> list[Any]:
        values = self.value_pools.get(name) or []
        if not values:
            raise ValueError(f"locale {self.locale}: empty value pool '{name}'")
        return list(values)


def load_locale(locale: str, paths: RepoPaths | None = None) -> LocaleConfig:
    paths = paths or RepoPaths.default()
    return LocaleConfig.model_validate(load_yaml(paths.locales / f"{locale}.yaml"))
