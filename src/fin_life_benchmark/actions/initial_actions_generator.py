"""Build initial standing actions consistent with the persona and memory state.

Rules (from spec):
- salary_linked_savings only if employed (trigger = salary_day + 1).
- rent_autopay only if wolse renter.
- spouse_living_expense_transfer only if married/cohabiting.
- parent_support_transfer only if dependents beyond own children exist.
- child_education_saving only if children exist.
- loan_repayment only if a loan exists.
- pension_contribution only if pension/IRP exists.
- business_expense_autopay only if self-employed.
Every action links to valid memory paths.
"""

from __future__ import annotations

import hashlib
import random

from ..locale.loader import LocaleConfig
from ..memory.models import CellStatus, FinancialMemoryState
from ..persona.models import NormalizedPersona
from .models import ActionStatus, StandingAction


def _rng_for(persona_id: str, seed: int) -> random.Random:
    digest = hashlib.sha256(f"{persona_id}:{seed}:initial_actions".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def build_initial_actions(
    persona: NormalizedPersona,
    memory: FinancialMemoryState,
    locale: LocaleConfig,
    seed: int = 0,
) -> list[StandingAction]:
    rng = _rng_for(persona.persona_id, seed)
    actions: list[StandingAction] = []
    counter = 0

    def add(type_: str, label: str, *, destination: str | None, amount: int | None,
            trigger_rule: str, trigger_day: int | None, linked: list[str]) -> None:
        nonlocal counter
        if any((cell := memory.latest(path)) is not None and cell.status == CellStatus.NOT_APPLICABLE for path in linked):
            return
        counter += 1
        actions.append(
            StandingAction(
                action_id=f"SO_{type_}_{counter:03d}",
                type=type_,
                label=label,
                status=ActionStatus.ACTIVE,
                source_account="main_checking",
                destination=destination,
                amount=amount,
                frequency="monthly",
                trigger_rule=trigger_rule,
                trigger_day=trigger_day,
                funds_movement=True,
                risk="high",
                linked_memory_paths=linked,
                validity_status="valid",
                last_confirmed_at=0,
            )
        )

    employed = persona.occupation_state.employment_status == "employed"
    self_employed = persona.occupation_state.employment_status == "self_employed"
    salary_day = memory.current_value("employment.salary_day")

    if employed and salary_day and persona.financial_profile.savings_propensity != "low":
        add(
            "salary_linked_savings",
            "급여일 다음날 자동저축",
            destination="savings_1",
            amount=rng.choice(locale.pool("savings_amounts_krw")),
            trigger_rule="salary_day_plus_1",
            trigger_day=(int(salary_day) % 28) + 1,
            linked=["employment.salary_day", "employment.salary_account"],
        )

    if persona.housing.residence_status == "wolse":
        rent = memory.current_value("housing.rent_amount")
        add(
            "rent_autopay",
            "월세 정기이체",
            destination=memory.current_value("housing.rent_payee") or "집주인",
            amount=int(rent) if rent else rng.choice(locale.pool("rent_amounts_krw")),
            trigger_rule="fixed_day",
            trigger_day=rng.choice([1, 5, 25]),
            linked=["housing.rent_amount", "housing.rent_payee", "housing.contract_type"],
        )

    if persona.household.marital_status == "married" and persona.household.cohabiting_with_spouse and rng.random() < 0.5:
        add(
            "spouse_living_expense_transfer",
            "배우자 생활비 정기이체",
            destination="spouse_account",
            amount=rng.choice(locale.pool("living_expense_amounts_krw")),
            trigger_rule="fixed_day",
            trigger_day=rng.choice([1, 10, 25]),
            linked=["household.marital_status", "household.spouse_or_partner"],
        )

    extra_dependents = persona.household.dependents_count - len([a for a in persona.household.children_ages if a < 19])
    if extra_dependents > 0:
        add(
            "parent_support_transfer",
            "부모님 생활비 정기송금",
            destination="parent_account",
            amount=rng.choice(locale.pool("support_amounts_krw")),
            trigger_rule="fixed_day",
            trigger_day=rng.choice([1, 5, 25]),
            linked=["household.dependents"],
        )

    if any(a < 19 for a in persona.household.children_ages):
        add(
            "child_education_saving",
            "자녀 교육비 적립",
            destination="child_edu_savings",
            amount=rng.choice(locale.pool("savings_amounts_krw")),
            trigger_rule="fixed_day",
            trigger_day=rng.choice([5, 15, 25]),
            linked=["household.children", "goals.child_education_goal", "education.child_education_stage"],
        )

    if persona.financial_profile.has_loan:
        loan_paths = ["financial_products.loans"]
        if persona.financial_profile.loan_type == "mortgage":
            loan_paths.append("housing.mortgage_status")
        add(
            "loan_repayment",
            "대출 자동상환",
            destination="loan_account",
            amount=rng.choice(locale.pool("mortgage_monthly_krw")) if persona.financial_profile.loan_type == "mortgage" else 400000,
            trigger_rule="fixed_day",
            trigger_day=rng.choice([10, 15, 27]),
            linked=loan_paths,
        )

    if persona.financial_profile.has_pension_or_irp:
        add(
            "pension_contribution",
            "IRP 정기 납입",
            destination="irp_account",
            amount=rng.choice([200000, 300000, 500000]),
            trigger_rule="fixed_day",
            trigger_day=rng.choice([10, 25]),
            linked=["financial_products.pension_or_irp", "goals.retirement_goal"],
        )

    if self_employed:
        add(
            "business_expense_autopay",
            "사업 비용 자동납부",
            destination="business_vendor",
            amount=rng.choice([300000, 500000, 800000]),
            trigger_rule="fixed_day",
            trigger_day=rng.choice([1, 15]),
            linked=["employment.employment_status"],
        )

    return actions
