"""Build the initial financial memory state from a normalized persona.

Consistency rules:
- salary_day/salary_account only when employed (self_employed gets variable
  income deposits and no salary_day).
- rent fields only when residence_status is wolse/jeonse.
- spouse cell only when married/cohabiting.
- loans only when the financial profile says so.
"""

from __future__ import annotations

import hashlib
import random

from ..locale.loader import LocaleConfig
from ..persona.models import NormalizedPersona
from .models import CellStatus, FinancialMemoryState


def _rng_for(persona_id: str, seed: int) -> random.Random:
    digest = hashlib.sha256(f"{persona_id}:{seed}:initial_memory".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def build_initial_memory(persona: NormalizedPersona, locale: LocaleConfig, seed: int = 0) -> FinancialMemoryState:
    rng = _rng_for(persona.persona_id, seed)
    memory = FinancialMemoryState()

    def set_(path: str, value, status: CellStatus = CellStatus.CURRENT) -> None:
        memory.set_initial(path, value, month_index=0, status=status)

    # profile
    set_("profile.age", persona.age)
    set_("profile.locale", persona.locale)
    set_("profile.region", persona.housing.region)

    # household
    set_("household.marital_status", persona.household.marital_status)
    if persona.household.marital_status == "married":
        set_("household.spouse_or_partner", "spouse")
    else:
        set_("household.spouse_or_partner", None, status=CellStatus.UNKNOWN)
    set_("household.children", list(persona.household.children_ages))
    set_("household.dependents", persona.household.dependents_count)
    set_("household.child_support_arrangement", None, status=CellStatus.UNKNOWN)

    # employment
    emp = persona.occupation_state
    set_("employment.employment_status", emp.employment_status)
    set_("employment.occupation", emp.occupation)
    set_("employment.income_stability", emp.income_stability)
    if emp.employment_status == "employed":
        employer = emp.employer or rng.choice(locale.pool("employer_pool"))
        set_("employment.employer", employer)
        set_("employment.salary_day", rng.choice(locale.pool("salary_days")))
        set_("employment.salary_account", "main_checking")
    else:
        set_("employment.employer", None, status=CellStatus.UNKNOWN)
        set_("employment.salary_day", None, status=CellStatus.UNKNOWN)
        set_("employment.salary_account", None, status=CellStatus.UNKNOWN)

    # housing
    housing = persona.housing
    set_("housing.residence_status", housing.residence_status)
    set_("housing.address", housing.region or rng.choice(locale.pool("address_pool")))
    if housing.residence_status in {"wolse", "jeonse"}:
        set_("housing.contract_type", housing.residence_status)
        if housing.residence_status == "wolse":
            set_("housing.rent_amount", rng.choice(locale.pool("rent_amounts_krw")))
            set_("housing.rent_payee", locale.banking_terms.get("rent_payee") or "집주인")
        else:
            set_("housing.rent_amount", None, status=CellStatus.UNKNOWN)
            set_("housing.rent_payee", None, status=CellStatus.UNKNOWN)
        set_("housing.maintenance_fee_payee", "관리사무소")
        set_("housing.mortgage_status", "none")
    elif housing.residence_status == "owner":
        set_("housing.contract_type", "owner")
        set_("housing.rent_amount", None, status=CellStatus.UNKNOWN)
        set_("housing.rent_payee", None, status=CellStatus.UNKNOWN)
        set_("housing.maintenance_fee_payee", "관리사무소")
        set_("housing.mortgage_status", "active" if persona.financial_profile.loan_type == "mortgage" else "none")
    else:
        set_("housing.contract_type", "family_home" if housing.residence_status == "family_home" else "other")
        set_("housing.rent_amount", None, status=CellStatus.UNKNOWN)
        set_("housing.rent_payee", None, status=CellStatus.UNKNOWN)
        set_("housing.maintenance_fee_payee", None, status=CellStatus.UNKNOWN)
        set_("housing.mortgage_status", "none")

    # education
    set_("education.self_education_status", "none")
    if persona.household.children_ages:
        oldest = max(persona.household.children_ages)
        stage = "preschool" if oldest < 7 else "primary" if oldest < 13 else "middle" if oldest < 16 else "high" if oldest < 20 else "adult"
        set_("education.child_education_stage", stage)
    else:
        set_("education.child_education_stage", None, status=CellStatus.UNKNOWN)

    # financial products
    set_("financial_products.checking_accounts", ["main_checking"])
    savings = ["savings_1"] if persona.financial_profile.savings_propensity != "low" else []
    set_("financial_products.savings_accounts", savings)
    loans = [persona.financial_profile.loan_type] if persona.financial_profile.has_loan else []
    set_("financial_products.loans", loans)
    set_("financial_products.pension_or_irp", "irp" if persona.financial_profile.has_pension_or_irp else None,
         status=CellStatus.CURRENT if persona.financial_profile.has_pension_or_irp else CellStatus.UNKNOWN)

    # goals
    set_("goals.emergency_fund", "building" if persona.financial_profile.savings_propensity == "high" else None,
         status=CellStatus.CURRENT if persona.financial_profile.savings_propensity == "high" else CellStatus.UNKNOWN)
    set_("goals.housing_deposit_goal", "active" if housing.residence_status in {"wolse", "jeonse"} and persona.age < 45 else None,
         status=CellStatus.CURRENT if housing.residence_status in {"wolse", "jeonse"} and persona.age < 45 else CellStatus.UNKNOWN)
    set_("goals.child_education_goal", "active" if any(a < 19 for a in persona.household.children_ages) else None,
         status=CellStatus.CURRENT if any(a < 19 for a in persona.household.children_ages) else CellStatus.UNKNOWN)
    set_("goals.retirement_goal", "active" if persona.age >= 45 and persona.financial_profile.has_pension_or_irp else None,
         status=CellStatus.CURRENT if persona.age >= 45 and persona.financial_profile.has_pension_or_irp else CellStatus.UNKNOWN)

    return memory
