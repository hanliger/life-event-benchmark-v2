"""Normalized persona schema. All personas are fictional/synthetic."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OccupationState(BaseModel):
    occupation: str | None = None
    employment_status: str = "unknown"  # employed|self_employed|unemployed|student|retired|homemaker|unknown
    employer: str | None = None
    income_stability: str = "unknown"  # stable|variable|reduced|unstable|unknown


class HouseholdState(BaseModel):
    marital_status: str = "unknown"  # single|married|separated|divorced|widowed|unknown
    family_type: str | None = None
    children_ages: list[int] = Field(default_factory=list)
    dependents_count: int = 0
    lives_with_parents: bool = False
    cohabiting_with_spouse: bool = False


class HousingState(BaseModel):
    residence_status: str = "unknown"  # owner|jeonse|wolse|family_home|other|unknown
    housing_type: str | None = None
    region: str | None = None


class FinancialProfile(BaseModel):
    has_loan: bool = False
    loan_type: str | None = None  # mortgage|jeonse_loan|credit|None
    has_pension_or_irp: bool = False
    savings_propensity: str = "medium"  # low|medium|high


class StyleProfile(BaseModel):
    formality: str = "casual"  # casual|polite
    verbosity: str = "short"  # short|medium
    notes: str = ""


class NormalizedPersona(BaseModel):
    persona_id: str
    persona_source_id: str
    locale: str
    age: int
    sex: str | None = None
    persona_text: str = ""
    occupation_state: OccupationState = Field(default_factory=OccupationState)
    household: HouseholdState = Field(default_factory=HouseholdState)
    housing: HousingState = Field(default_factory=HousingState)
    financial_profile: FinancialProfile = Field(default_factory=FinancialProfile)
    style: StyleProfile = Field(default_factory=StyleProfile)
    normalization_notes: list[str] = Field(default_factory=list)
