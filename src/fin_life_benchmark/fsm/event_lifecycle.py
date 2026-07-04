"""Event lifecycle scheduling and event-parameter sampling.

Lifecycle: inactive -> [weak_signal] -> [upcoming] -> occurred
           weak_signal -> cancelled, upcoming -> cancelled
Durations and cancel probabilities come from each template's LifecycleConfig.
"""

from __future__ import annotations

import random
from typing import Any

from ..locale.loader import LocaleConfig
from ..trajectory.models import LifeState
from .models import EventStatus, LifeEventTemplate


def plan_lifecycle(
    template: LifeEventTemplate,
    start_month: int,
    rng: random.Random,
    force_occur: bool = False,
) -> list[tuple[int, EventStatus]]:
    """Return [(month_index, status), ...] for one event instance.

    ``force_occur`` disables the cancellation branches so the instance is
    guaranteed to reach OCCURRED (still passing through weak_signal/upcoming).
    Used for episode/coverage-forced events, which must occur by construction."""
    cfg = template.lifecycle
    schedule: list[tuple[int, EventStatus]] = []
    month = start_month

    has_weak = rng.random() >= cfg.p_skip_weak_signal
    if has_weak:
        schedule.append((month, EventStatus.WEAK_SIGNAL))
        month += rng.randint(*cfg.weak_signal_months) if cfg.weak_signal_months[1] > 0 else 0
        if not force_occur and rng.random() < cfg.p_cancel_from_weak:
            schedule.append((max(month, start_month + 1), EventStatus.CANCELLED))
            return schedule

    has_upcoming = rng.random() >= cfg.p_skip_upcoming
    if has_upcoming:
        schedule.append((month, EventStatus.UPCOMING))
        month += rng.randint(*cfg.upcoming_months) if cfg.upcoming_months[1] > 0 else 0
        if not force_occur and rng.random() < cfg.p_cancel_from_upcoming:
            schedule.append((max(month, schedule[-1][0] + 1), EventStatus.CANCELLED))
            return schedule

    if not schedule:
        schedule.append((month, EventStatus.OCCURRED))
    else:
        schedule.append((max(month, schedule[-1][0] + 1), EventStatus.OCCURRED))
    return schedule


def sample_event_params(
    template: LifeEventTemplate,
    state: LifeState,
    locale: LocaleConfig,
    rng: random.Random,
) -> dict[str, Any]:
    """Event-specific payload consumed by delta templates (param:<name>)."""
    event_id = template.event_id
    params: dict[str, Any] = {}

    def pick(pool: str) -> Any:
        return rng.choice(locale.pool(pool))

    if event_id == "relationship_marriage":
        params["partner_ref"] = "spouse"
        params["joint_living_expense_amount"] = pick("living_expense_amounts_krw")
    elif event_id == "relationship_divorce_or_separation":
        params["child_support_amount"] = pick("support_amounts_krw") if state.has_children else None
    elif event_id == "relationship_childbirth_or_adoption":
        params["children_after"] = sorted(state.children_ages + [0])
        params["dependents_after"] = state.dependents_count + 1
    elif event_id == "relationship_dependent_change":
        delta = 1 if state.dependents_count == 0 or rng.random() < 0.7 else -1
        params["dependent_delta"] = delta
        params["dependents_after"] = max(0, state.dependents_count + delta)
        params["support_amount"] = pick("support_amounts_krw")
    elif event_id == "relationship_family_death":
        params["dependents_after"] = max(0, state.dependents_count - 1)
    elif event_id in {"housing_move", "housing_independence"}:
        params["new_address"] = pick("address_pool")
        params["new_rent_amount"] = pick("rent_amounts_krw")
    elif event_id == "housing_rental_contract":
        new_type = rng.choice(["wolse", "jeonse"]) if state.residence_status in {"family_home", "other"} else state.residence_status
        params["new_contract_type"] = new_type
        params["new_rent_amount"] = pick("rent_amounts_krw") if new_type == "wolse" else 0
        params["new_payee"] = locale.banking_terms.get("rent_payee") or "집주인"
    elif event_id == "housing_home_purchase":
        params["new_address"] = pick("address_pool")
        params["mortgage_monthly"] = pick("mortgage_monthly_krw")
        params["mortgage_payment_day"] = rng.choice([10, 15, 27])
        params["loans_after"] = ["mortgage"]
    elif event_id in {"career_job_change", "career_employment_or_return"}:
        params["new_employer"] = pick("employer_pool")
        params["new_salary_day"] = pick("salary_days")
    elif event_id == "education_child_stage_entry":
        entries = [(7, "primary"), (13, "middle"), (16, "high")]
        candidates = [stage for entry, stage in entries if any(abs(a - entry) <= 1 for a in state.children_ages)]
        params["new_stage"] = candidates[0] if candidates else "primary"
        params["monthly_edu_cost"] = pick("savings_amounts_krw")
    elif event_id == "crisis_health_event":
        params["one_off_cost"] = rng.choice([1500000, 3000000, 5000000])
    elif event_id == "crisis_accident_or_disaster":
        params["one_off_cost"] = rng.choice([1000000, 2000000, 4000000])

    return params


def apply_occurred_to_life_state(event_id: str, state: LifeState, params: dict[str, Any]) -> None:
    """Mutate LifeState when an event occurs. Mirrors (and extends) the state
    effects in life_generator.rules._apply_event."""
    if event_id == "relationship_marriage":
        state.marital_status = "married"
    elif event_id == "relationship_divorce_or_separation":
        state.marital_status = "divorced"
    elif event_id == "relationship_childbirth_or_adoption":
        state.children_ages = list(params.get("children_after") or (state.children_ages + [0]))
        state.dependents_count = int(params.get("dependents_after", state.dependents_count + 1))
    elif event_id == "relationship_dependent_change":
        state.dependents_count = int(params.get("dependents_after", state.dependents_count + 1))
    elif event_id == "relationship_family_death":
        state.dependents_count = int(params.get("dependents_after", max(0, state.dependents_count - 1)))
    elif event_id == "housing_independence":
        state.lives_with_parents = False
        state.residence_status = "wolse"
    elif event_id == "housing_move":
        pass  # address handled in memory; residence type unchanged
    elif event_id == "housing_rental_contract":
        state.residence_status = str(params.get("new_contract_type", state.residence_status))
    elif event_id == "housing_home_purchase":
        state.residence_status = "owner"
        state.home_owned = True
    elif event_id == "housing_home_sale_or_moveout":
        state.home_owned = False
        state.residence_status = "jeonse"
    elif event_id == "career_employment_or_return":
        state.employment_status = "employed"
    elif event_id == "career_job_change":
        state.employment_status = "employed"
    elif event_id == "career_leave":
        state.employment_status = "on_leave"
    elif event_id == "career_resignation_or_job_loss":
        state.employment_status = "unemployed"
    elif event_id == "career_startup_or_freelance":
        state.employment_status = "self_employed"
    elif event_id == "career_business_closure":
        state.employment_status = "unemployed"
    elif event_id in {"education_self_program_start", "education_study_abroad"}:
        state.in_education = True
    elif event_id == "retirement_prep_start":
        state.retirement_prepared = True
    elif event_id == "retirement_pension_start":
        state.employment_status = "retired"
        state.pension_receiving = True
    # crisis events: no persistent LifeState change (financial impact only)
