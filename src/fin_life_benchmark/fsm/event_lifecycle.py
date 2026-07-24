"""Event lifecycle scheduling and event-parameter sampling.

Lifecycle: inactive -> [weak_signal] -> [upcoming] -> occurred
           weak_signal -> cancelled, upcoming -> cancelled
Durations and cancel probabilities come from each template's LifecycleConfig.
"""

from __future__ import annotations

import random
from typing import Any

from ..locale.loader import LocaleConfig
from ..trajectory.models import ChildState, LifeState, PropertyState
from .models import EventStatus, LifeEventTemplate

MAX_COUNT_BELOW_FIVE = 4

# Ordered child education stages. A stage-entry event always advances by one, so
# the recorded transition is derived from this order (previous = the stage right
# below new_stage) rather than from the shared, non-child-specific
# education.child_education_stage memory cell -- which could otherwise hold
# another child's stage or a later stage and yield a same-stage/backward update.
_EDU_STAGE_ORDER = ["pre_school", "primary", "middle", "high"]


def _normalize_edu_stage(stage: Any) -> str:
    return "pre_school" if stage in (None, "preschool") else str(stage)


def education_previous_stage(new_stage: Any) -> str:
    """The stage immediately below ``new_stage`` in the education progression."""
    new = _normalize_edu_stage(new_stage)
    idx = _EDU_STAGE_ORDER.index(new) if new in _EDU_STAGE_ORDER else 0
    return _EDU_STAGE_ORDER[idx - 1] if idx > 0 else _EDU_STAGE_ORDER[0]


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
        params["has_child_support"] = bool(state.has_children)
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
        # Preserve the historical draw sequence so lifecycle scheduling stays
        # deterministic across schema versions, then normalize impossible
        # identities against the current state.
        relation = rng.choice(["parent", "spouse", "child", "sibling", "other"])
        if relation == "spouse" and state.marital_status != "married":
            relation = "parent" if state.dependents_count > 0 else "other"
        if relation == "child" and not state.children:
            relation = "parent" if state.dependents_count > 0 else "other"
        params["deceased_relation"] = relation
        if relation == "child":
            child = rng.choice(state.children)
            params["deceased_child_id"] = child.child_id
            params["children_after"] = [
                candidate.age for candidate in state.children if candidate.child_id != child.child_id
            ]
        # Dependence is a state-conditioned fact, not an unrelated random flag.
        dependency_draw = rng.random()
        params["was_dependent"] = bool(
            state.dependents_count > 0
            and (
                relation in {"child", "parent"}
            )
        )
        params["dependents_after"] = max(0, state.dependents_count - int(params["was_dependent"]))
        params["one_off_cost"] = [3000000, 5000000, 7000000][min(2, int(dependency_draw * 3))]
        params["one_off_expense"] = {
            "category": "funeral",
            "amount_krw": params["one_off_cost"],
        }
    elif event_id == "housing_move":
        new_status = rng.choice(["jeonse", "wolse", "family_home", "other"])
        params["new_address"] = pick("address_pool")
        params["new_residence_status"] = new_status
        params["new_contract_type"] = new_status
        params["new_rent_amount"] = pick("rent_amounts_krw") if new_status == "wolse" else 0
        params["new_payee"] = (
            locale.banking_terms.get("rent_payee") or "집주인"
        ) if new_status == "wolse" else None
        params["has_rent_payment"] = new_status == "wolse"
        params["new_maintenance_fee_payee"] = (
            locale.banking_terms.get("maintenance_fee_payee") or "관리사무소"
        ) if new_status in {"jeonse", "wolse"} else None
        params["has_maintenance_fee"] = params["new_maintenance_fee_payee"] is not None
        params["housing_payment_type"] = (
            "rent" if new_status == "wolse"
            else "household_contribution" if new_status == "family_home"
            else "none"
        )
        params["move_reason"] = rng.choice([
            "ordinary_move", "independence", "separate_household",
            "return_to_family_home", "caregiving", "other",
        ])
    elif event_id == "housing_home_purchase":
        params["ownership_transition"] = "acquire"
        # Replaced with the globally unique event-instance-derived ID by the
        # simulator immediately after sampling.
        params["property_id"] = f"property_candidate_{len(state.properties) + 1:03d}"
        params["new_address"] = pick("address_pool")
        params["post_purchase_move"] = rng.choice([True, False])
        params["purchase_role"] = "primary_residence" if params["post_purchase_move"] else "secondary_property"
        params["property_address"] = params["new_address"]
        params["post_purchase_residence_status"] = "owner" if params["post_purchase_move"] else state.residence_status
        params["post_purchase_contract_type"] = "owner" if params["post_purchase_move"] else state.residence_status
        params["mortgage_monthly"] = pick("mortgage_monthly_krw")
        params["mortgage_payment_day"] = rng.choice([10, 15, 27])
        params["loans_after"] = ["mortgage"]
    elif event_id == "housing_home_sale":
        owned = [p for p in state.properties if p.ownership_status == "owned"]
        if not owned:
            raise ValueError("housing_home_sale: no identified owned property")
        sold = rng.choice(owned)
        is_primary = sold.property_id == state.primary_residence_property_id
        post_sale = rng.choice(["jeonse", "wolse", "family_home", "other"]) if is_primary else state.residence_status
        params["ownership_transition"] = "dispose"
        params["sold_property_id"] = sold.property_id
        params["sold_property_address"] = sold.address
        params["sold_property_role"] = sold.role
        params["post_sale_residence_status"] = post_sale
        params["post_sale_contract_type"] = post_sale
        params["remaining_property_ids"] = [p.property_id for p in owned if p.property_id != sold.property_id]
    elif event_id == "career_employment":
        params["employment_transition_type"] = "new_employment"
        params["new_employer"] = pick("employer_pool")
        params["new_salary_day"] = pick("salary_days")
        params["new_salary_account"] = "main_checking"
    elif event_id == "career_reinstatement":
        params["employment_transition_type"] = "reinstatement"
        params["previous_employer"] = state.current_employer or pick("employer_pool")
        params["new_employer"] = params["previous_employer"]
        params["new_salary_day"] = pick("salary_days")
        params["new_salary_account"] = "main_checking"
    elif event_id == "career_job_change":
        params["change_type"] = "external_employer"
        params["new_employer"] = pick("employer_pool")
        params["new_salary_day"] = pick("salary_days")
        params["new_salary_account"] = "main_checking"
    elif event_id == "career_employment_end":
        params["end_reason"] = "business_closure" if state.employment_status == "self_employed" else rng.choice(["resignation", "job_loss"])
    elif event_id == "career_self_employment":
        params["self_employment_type"] = rng.choice(["startup", "freelance"])
    elif event_id == "career_leave_of_absence":
        params["leave_reason"] = rng.choice(["family_care", "health", "other"])
        params["employment_relationship_maintained"] = True
    elif event_id == "education_child_stage_entry":
        entries = [(7, "primary"), (13, "middle"), (16, "high")]
        candidates = [
            (child, stage)
            for entry, stage in entries
            for child in state.children
            if abs(child.age - entry) <= 1
        ]
        child, stage = candidates[0] if candidates else (state.children[0], "primary")
        params["child_id"] = child.child_id
        params["child_age_months"] = child.age * 12
        params["new_stage"] = stage
        # Derive the prior stage from the ordered progression so the transition
        # is always a real forward step for THIS child, independent of the shared
        # education memory cell.
        params["previous_stage"] = education_previous_stage(stage)
        params["monthly_edu_cost"] = pick("savings_amounts_krw")
    elif event_id == "crisis_health_event":
        params["one_off_cost"] = rng.choice([1500000, 3000000, 5000000])
        params["one_off_expense"] = {"category": "medical", "amount_krw": params["one_off_cost"]}
    elif event_id == "crisis_accident_or_disaster":
        params["one_off_cost"] = rng.choice([1000000, 2000000, 4000000])
        params["one_off_expense"] = {"category": "accident_or_disaster", "amount_krw": params["one_off_cost"]}
    elif event_id == "crisis_financial_fraud":
        params["one_off_cost"] = rng.choice([500000, 1500000, 3000000])
        params["one_off_expense"] = {"category": "fraud_loss", "amount_krw": params["one_off_cost"]}
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

    if template.event_id == "relationship_family_death" and params.get("deceased_relation") == "child":
        child_ids = {child.child_id for child in state.children}
        if params.get("deceased_child_id") not in child_ids:
            raise ValueError("relationship_family_death: deceased_child_id must identify an existing child")
    if template.event_id == "relationship_family_death" and params.get("deceased_relation") == "spouse":
        if state.marital_status != "married":
            raise ValueError("relationship_family_death: spouse requires married state")
    if template.event_id == "education_child_stage_entry":
        child = next((c for c in state.children if c.child_id == params.get("child_id")), None)
        if child is None:
            raise ValueError("education_child_stage_entry: child_id must identify an existing child")
        if params.get("child_age_months") != child.age * 12:
            raise ValueError("education_child_stage_entry: child_age_months does not match child state")
    if template.event_id == "housing_home_sale":
        owned_ids = {p.property_id for p in state.properties if p.ownership_status == "owned"}
        if params.get("sold_property_id") not in owned_ids:
            raise ValueError("housing_home_sale: sold_property_id must identify an owned property")
    if template.event_id == "housing_move" and params.get("new_residence_status") != "wolse":
        if params.get("new_payee") is not None:
            raise ValueError("housing_move: non-wolse residence cannot have a rent payee")


def apply_occurred_to_life_state(
    event_id: str,
    state: LifeState,
    params: dict[str, Any],
    *,
    event_instance_id: str | None = None,
    month_index: int = 0,
) -> None:
    """Mutate LifeState when an event occurs. Mirrors (and extends) the state
    effects in life_generator.rules._apply_event."""
    if event_id == "relationship_marriage":
        state.marital_status = "married"
    elif event_id == "relationship_divorce_or_separation":
        state.marital_status = str(params.get("marital_status_after", "divorced"))
    elif event_id in {"relationship_childbirth", "relationship_adoption"}:
        child_id = str(params.get("child_id") or f"child_{len(state.children) + 1:03d}")
        if not any(child.child_id == child_id for child in state.children):
            state.children.append(ChildState(child_id=child_id, age=0))
        children_after = list(params.get("children_after") or (state.children_ages + [0]))
        state.children_ages = sorted(children_after[:MAX_COUNT_BELOW_FIVE])
        state.dependents_count = int(params.get("dependents_after", state.dependents_count + 1))
    elif event_id == "relationship_dependent_addition":
        state.dependents_count = int(params.get("dependents_after", min(MAX_COUNT_BELOW_FIVE, state.dependents_count + 1)))
    elif event_id == "relationship_dependent_end":
        state.dependents_count = int(params.get("dependents_after", max(0, state.dependents_count - 1)))
    elif event_id == "relationship_family_death":
        relation = params.get("deceased_relation")
        if relation == "spouse":
            state.marital_status = "widowed"
        elif relation == "child":
            deceased_id = params.get("deceased_child_id")
            state.children = [child for child in state.children if child.child_id != deceased_id]
            state.children_ages = sorted(child.age for child in state.children)
        state.dependents_count = int(params.get("dependents_after", max(0, state.dependents_count - 1)))
    elif event_id == "housing_move":
        state.residence_status = str(params.get("new_residence_status", state.residence_status))
        state.lives_with_parents = state.residence_status == "family_home"
    elif event_id == "housing_home_purchase":
        property_id = str(params.get("property_id") or event_instance_id or f"property_{len(state.properties) + 1:03d}")
        role = str(params.get("purchase_role", "primary_residence"))
        if not any(prop.property_id == property_id for prop in state.properties):
            state.properties.append(PropertyState(
                property_id=property_id,
                address=str(params.get("property_address") or params.get("new_address") or "unknown"),
                acquired_month=month_index,
                acquisition_event_instance_id=event_instance_id,
                role=role,
                mortgage_status="active" if params.get("mortgage_monthly") else "none",
            ))
        if params.get("post_purchase_move", True):
            state.residence_status = "owner"
            state.primary_residence_property_id = property_id
        state.home_owned = True
        params["properties_after"] = [prop.model_dump(mode="json") for prop in state.properties]
        params["primary_residence_property_id_after"] = state.primary_residence_property_id
    elif event_id == "housing_home_sale":
        sold_id = params.get("sold_property_id")
        for prop in state.properties:
            if prop.property_id == sold_id:
                prop.ownership_status = "sold"
                prop.disposed_month = month_index
                prop.disposal_event_instance_id = event_instance_id
        remaining = [p for p in state.properties if p.ownership_status == "owned"]
        state.home_owned = bool(remaining)
        if sold_id == state.primary_residence_property_id:
            state.primary_residence_property_id = None
            state.residence_status = str(params.get("post_sale_residence_status", "jeonse"))
        params["properties_after"] = [prop.model_dump(mode="json") for prop in state.properties]
        params["primary_residence_property_id_after"] = state.primary_residence_property_id
    elif event_id in {"career_employment", "career_reinstatement"}:
        state.employment_status = "employed"
        state.current_employer = params.get("new_employer") or state.current_employer
    elif event_id == "career_job_change":
        state.employment_status = "employed"
        state.current_employer = params.get("new_employer") or state.current_employer
    elif event_id == "career_leave_of_absence":
        state.employment_status = "on_leave"
    elif event_id == "career_employment_end":
        state.employment_status = "unemployed"
    elif event_id == "career_self_employment":
        state.employment_status = "self_employed"
    elif event_id == "education_child_stage_entry":
        for child in state.children:
            if child.child_id == params.get("child_id"):
                child.education_stage = str(params.get("new_stage", child.education_stage))
    elif event_id in {"education_self_program_start", "education_study_abroad"}:
        state.in_education = True
    elif event_id == "retirement_start":
        state.employment_status = "retired"
        state.retirement_prepared = True
        state.pension_receiving = bool(params.get("pension_started_same_time", False))
    elif event_id == "retirement_pension_start":
        state.pension_receiving = True
    # crisis events: no persistent LifeState change (financial impact only)
