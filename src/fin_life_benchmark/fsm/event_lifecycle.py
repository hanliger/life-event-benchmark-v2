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

MAX_COUNT_BELOW_FIVE = 4


def plan_lifecycle(
    template: LifeEventTemplate,
    start_month: int,
    rng: random.Random,
    force_occur: bool = False,
) -> list[tuple[int, EventStatus]]:
    """Return [(month_index, status), ...] for one event instance.

    ``force_occur`` disables the cancellation branches so the instance reaches
    the OCCURRED transition unless the simulator's occurrence guards reject it
    (still passing through weak_signal/upcoming). Used for episode/coverage
    forced events."""
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
    """Sample the concrete cause/details stored under a stable event ID."""
    event_id = template.event_id
    params: dict[str, Any] = {}

    def pick(pool: str) -> Any:
        return rng.choice(locale.pool(pool))

    if event_id == "relationship_marriage":
        params["partner_ref"] = "spouse"
        params["joint_living_expense_amount"] = pick("living_expense_amounts_krw")
    elif event_id == "relationship_divorce_or_separation":
        transition = "divorce" if state.marital_status == "separated" else rng.choice(["separation", "divorce"])
        params["relationship_transition_type"] = transition
        params["marital_status_after"] = "separated" if transition == "separation" else "divorced"
        params["child_support_amount"] = pick("support_amounts_krw") if state.has_children else None
    elif event_id in {"relationship_childbirth", "relationship_adoption"}:
        params["family_change_type"] = "birth" if event_id == "relationship_childbirth" else "adoption"
        params["children_after"] = sorted((state.children_ages + [0])[:MAX_COUNT_BELOW_FIVE])
        params["dependents_after"] = min(MAX_COUNT_BELOW_FIVE, state.dependents_count + 1)
    elif event_id == "relationship_dependent_addition":
        params["dependent_type"] = rng.choice(["parent", "other_adult"])
        params["cause"] = rng.choice(["ordinary", "accident"])
        params["dependents_after"] = min(MAX_COUNT_BELOW_FIVE, state.dependents_count + 1)
        params["support_amount"] = pick("support_amounts_krw")
    elif event_id == "relationship_dependent_end":
        child_can_leave = any(age >= 18 for age in state.children_ages)
        params["end_reason"] = rng.choice(["parent_care_end", "child_independence"]) if child_can_leave else "parent_care_end"
        params["cause"] = rng.choice(["ordinary", "accident"])
        params["dependents_after"] = max(0, state.dependents_count - 1)
    elif event_id == "relationship_family_death":
        params["deceased_relation"] = rng.choice(["parent", "spouse", "child", "sibling", "other"])
        params["was_dependent"] = state.dependents_count > 0 and rng.random() < 0.7
        params["dependents_after"] = max(0, state.dependents_count - int(params["was_dependent"]))
    elif event_id == "housing_move":
        new_status = rng.choice(["jeonse", "wolse", "family_home", "other"])
        params["new_address"] = pick("address_pool")
        params["new_residence_status"] = new_status
        params["new_contract_type"] = new_status
        params["new_rent_amount"] = pick("rent_amounts_krw") if new_status == "wolse" else 0
        params["new_payee"] = locale.banking_terms.get("rent_payee") or "집주인"
        params["move_reason"] = rng.choice([
            "ordinary_move", "independence", "separate_household",
            "return_to_family_home", "caregiving", "other",
        ])
    elif event_id == "housing_home_purchase":
        params["ownership_transition"] = "acquire"
        params["new_address"] = pick("address_pool")
        params["post_purchase_residence_status"] = "owner"
        params["post_purchase_contract_type"] = "owner"
        params["post_purchase_move"] = rng.choice([True, False])
        params["mortgage_monthly"] = pick("mortgage_monthly_krw")
        params["mortgage_payment_day"] = rng.choice([10, 15, 27])
        params["loans_after"] = ["mortgage"]
    elif event_id == "housing_home_sale":
        post_sale = rng.choice(["jeonse", "wolse", "family_home", "other"])
        params["ownership_transition"] = "dispose"
        params["post_sale_residence_status"] = post_sale
        params["post_sale_contract_type"] = post_sale
    elif event_id == "career_employment":
        params["employment_transition_type"] = "new_employment"
        params["new_employer"] = pick("employer_pool")
        params["new_salary_day"] = pick("salary_days")
    elif event_id == "career_reinstatement":
        params["employment_transition_type"] = "reinstatement"
        params["previous_employer"] = "previous_employer"
        params["new_employer"] = params["previous_employer"]
        params["new_salary_day"] = pick("salary_days")
    elif event_id == "career_job_change":
        params["change_type"] = "external_employer"
        params["new_employer"] = pick("employer_pool")
        params["new_salary_day"] = pick("salary_days")
    elif event_id == "career_employment_end":
        params["end_reason"] = "business_closure" if state.employment_status == "self_employed" else rng.choice(["resignation", "job_loss"])
    elif event_id == "career_self_employment":
        params["self_employment_type"] = rng.choice(["startup", "freelance"])
    elif event_id == "career_leave_of_absence":
        params["leave_reason"] = rng.choice(["family_care", "health", "other"])
        params["employment_relationship_maintained"] = True
    elif event_id == "education_child_stage_entry":
        entries = [(7, "primary"), (13, "middle"), (16, "high")]
        candidates = [stage for entry, stage in entries if any(abs(a - entry) <= 1 for a in state.children_ages)]
        params["new_stage"] = candidates[0] if candidates else "primary"
        params["monthly_edu_cost"] = pick("savings_amounts_krw")
    elif event_id == "crisis_health_event":
        params["one_off_cost"] = rng.choice([1500000, 3000000, 5000000])
    elif event_id == "crisis_accident_or_disaster":
        params["one_off_cost"] = rng.choice([1000000, 2000000, 4000000])
    elif event_id == "crisis_financial_fraud":
        params["one_off_cost"] = rng.choice([500000, 1500000, 3000000])
    elif event_id == "retirement_start":
        params["previous_employment_status"] = state.employment_status
        params["retirement_reason"] = rng.choice(["planned", "health", "family", "other"])
        params["pension_started_same_time"] = rng.choice([True, False])

    validate_event_params(template, state, params)
    return params


def validate_event_params(
    template: LifeEventTemplate,
    state: LifeState,
    params: dict[str, Any],
) -> None:
    """Validate enum contracts and state-dependent parameter guards."""
    for name, contract in template.event_parameter_schema.items():
        if name not in params:
            raise ValueError(f"{template.event_id}: missing event param '{name}'")
        if isinstance(contract, list) and params[name] not in contract:
            raise ValueError(
                f"{template.event_id}.{name}: {params[name]!r} not in {contract!r}"
            )

    for param_name, choices in template.parameter_guards.items():
        value = params.get(param_name)
        guard = (choices or {}).get(value) or {}
        for field, allowed in guard.items():
            if not hasattr(state, field):
                continue  # explanatory/non-LifeState constraints in YAML
            if state.guard_value(field) not in allowed:
                raise ValueError(
                    f"{template.event_id}.{param_name}={value!r} invalid for "
                    f"{field}={state.guard_value(field)!r}"
                )


def apply_occurred_to_life_state(event_id: str, state: LifeState, params: dict[str, Any]) -> None:
    """Mutate LifeState when an event occurs. Mirrors (and extends) the state
    effects in life_generator.rules._apply_event."""
    if event_id == "relationship_marriage":
        state.marital_status = "married"
    elif event_id == "relationship_divorce_or_separation":
        state.marital_status = str(params.get("marital_status_after", "divorced"))
    elif event_id in {"relationship_childbirth", "relationship_adoption"}:
        children_after = list(params.get("children_after") or (state.children_ages + [0]))
        state.children_ages = sorted(children_after[:MAX_COUNT_BELOW_FIVE])
        state.dependents_count = int(params.get("dependents_after", state.dependents_count + 1))
    elif event_id == "relationship_dependent_addition":
        state.dependents_count = int(params.get("dependents_after", min(MAX_COUNT_BELOW_FIVE, state.dependents_count + 1)))
    elif event_id == "relationship_dependent_end":
        state.dependents_count = int(params.get("dependents_after", max(0, state.dependents_count - 1)))
    elif event_id == "relationship_family_death":
        state.dependents_count = int(params.get("dependents_after", max(0, state.dependents_count - 1)))
    elif event_id == "housing_move":
        state.residence_status = str(params.get("new_residence_status", state.residence_status))
        state.lives_with_parents = state.residence_status == "family_home"
    elif event_id == "housing_home_purchase":
        state.residence_status = "owner"
        state.home_owned = True
    elif event_id == "housing_home_sale":
        state.home_owned = False
        state.residence_status = str(params.get("post_sale_residence_status", "jeonse"))
    elif event_id in {"career_employment", "career_reinstatement"}:
        state.employment_status = "employed"
    elif event_id == "career_job_change":
        state.employment_status = "employed"
    elif event_id == "career_leave_of_absence":
        state.employment_status = "on_leave"
    elif event_id == "career_employment_end":
        state.employment_status = "unemployed"
    elif event_id == "career_self_employment":
        state.employment_status = "self_employed"
    elif event_id in {"education_self_program_start", "education_study_abroad"}:
        state.in_education = True
    elif event_id == "retirement_start":
        state.employment_status = "retired"
        state.retirement_prepared = True
        state.pension_receiving = bool(params.get("pension_started_same_time", False))
    elif event_id == "retirement_pension_start":
        state.pension_receiving = True
    # crisis events: no persistent LifeState change (financial impact only)
