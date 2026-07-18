"""Contracts for the v2 representative-event registry."""

import random

from fin_life_benchmark.fsm.event_lifecycle import sample_event_params
from fin_life_benchmark.fsm.life_state_machine import LifeStateMachine
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, load_yaml
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.trajectory.models import ChildState, LifeState, PropertyState


def test_v2_registry_has_24_non_military_events_and_valid_aliases():
    paths = RepoPaths.default()
    templates = load_life_event_templates(paths)
    memory_templates = load_yaml(paths.registries / "event_to_memory_delta.yaml")
    action_templates = load_yaml(paths.registries / "event_to_action_impact.yaml")

    assert len(templates) == 24
    assert not any("military" in event_id for event_id in templates)
    assert all(t.memory_delta_template_id in memory_templates for t in templates.values())
    assert all(t.action_impact_template_id in action_templates for t in templates.values())


def test_every_parameterized_event_samples_a_complete_valid_payload():
    paths = RepoPaths.default()
    templates = load_life_event_templates(paths)
    locale = load_locale("ko_KR", paths)
    fsm = LifeStateMachine(templates)
    states = {
        "relationship_divorce_or_separation": LifeState(marital_status="married"),
        "relationship_childbirth": LifeState(marital_status="married"),
        "relationship_adoption": LifeState(marital_status="married"),
        "relationship_dependent_addition": LifeState(dependents_count=0),
        "relationship_dependent_end": LifeState(dependents_count=1),
        "relationship_family_death": LifeState(dependents_count=1),
        "housing_move": LifeState(residence_status="wolse"),
        "housing_home_purchase": LifeState(home_owned=False),
        "housing_home_sale": LifeState(
            home_owned=True,
            residence_status="owner",
            properties=[PropertyState(property_id="property_test", address="서울")],
            primary_residence_property_id="property_test",
        ),
        "career_employment": LifeState(employment_status="unemployed"),
        "career_reinstatement": LifeState(employment_status="on_leave"),
        "career_job_change": LifeState(employment_status="employed"),
        "career_employment_end": LifeState(employment_status="employed"),
        "career_self_employment": LifeState(employment_status="unemployed"),
        "retirement_start": LifeState(employment_status="employed"),
        "career_leave_of_absence": LifeState(employment_status="employed"),
        "education_child_stage_entry": LifeState(
            children_ages=[7],
            children=[ChildState(child_id="child_001", age=7)],
        ),
    }

    assert set(states) == {
        event_id for event_id, template in templates.items()
        if template.event_parameter_schema
    }
    for index, (event_id, state) in enumerate(states.items()):
        template = templates[event_id]
        assert fsm.guards_pass(template, state, max(35, template.age_guard.min_age), 0, {}, [])
        params = sample_event_params(template, state, locale, random.Random(index))
        assert set(template.event_parameter_schema) <= set(params)


def test_representative_event_keeps_cause_in_params():
    templates = load_life_event_templates()
    locale = load_locale("ko_KR")
    state = LifeState(employment_status="self_employed")
    params = sample_event_params(
        templates["career_employment_end"], state, locale, random.Random(0)
    )
    assert params["end_reason"] == "business_closure"
