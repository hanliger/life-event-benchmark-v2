"""Persona-aware subgraph sampling: weighting, mid-entry, anchoring, guards."""

from life_generator.templates import EPISODE_TEMPLATES

from fin_life_benchmark.actions.initial_actions_generator import build_initial_actions
from fin_life_benchmark.fsm.life_state_machine import LifeStateMachine
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, load_yaml
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.memory.initial_state_generator import build_initial_memory
from fin_life_benchmark.persona.models import (
    FinancialProfile,
    HouseholdState,
    HousingState,
    NormalizedPersona,
    OccupationState,
)
from fin_life_benchmark.trajectory.episode_bridge import build_reverse_map
from fin_life_benchmark.trajectory.simulator import TrajectorySimulator, life_state_from_persona
from fin_life_benchmark.trajectory.subgraph_bridge import (
    event_entry_candidates,
    episode_entry_age,
    episode_weight,
    fixed_child_education_events,
    subgraph_scripted_events,
    valid_entry_doors,
)

PATHS = RepoPaths.default()
TEMPLATES = load_life_event_templates(PATHS)
FSM = LifeStateMachine(TEMPLATES)
REVERSE = build_reverse_map(PATHS)
TEMPLATE_BY_ID = {t.id: t for t in EPISODE_TEMPLATES}


def _persona(**kw) -> NormalizedPersona:
    base = dict(
        persona_id=kw.pop("persona_id", "p_test"),
        persona_source_id="test",
        locale="ko_KR",
        age=kw.pop("age", 30),
        sex="여자",
        persona_text="테스트",
        occupation_state=OccupationState(
            occupation="사무직",
            employment_status=kw.pop("employment_status", "employed"),
            income_stability="stable",
        ),
        household=HouseholdState(
            marital_status=kw.pop("marital_status", "single"),
            children_ages=kw.pop("children_ages", []),
            dependents_count=kw.pop("dependents_count", 0),
        ),
        housing=HousingState(residence_status=kw.pop("residence_status", "wolse"), region="서울 마포구"),
        financial_profile=FinancialProfile(savings_propensity=kw.pop("savings_propensity", "medium")),
    )
    return NormalizedPersona(**base)


def _doors_by_benchmark(template, persona):
    """valid_entry_doors mapped to {benchmark_event_id: propensity} plus the raw
    open indexes."""
    life_state = life_state_from_persona(persona)
    from fin_life_benchmark.trajectory.subgraph_bridge import persona_prior_events

    prior = persona_prior_events(life_state)
    entry_age = episode_entry_age(template, persona.age)
    # NB: valid_entry_doors takes the *benchmark* template dict (keyed by
    # benchmark event_id), not the episode-template dict.
    doors = valid_entry_doors(template, persona, life_state, FSM, REVERSE, TEMPLATES, prior, entry_age)
    mapped = {REVERSE[template.event_ids[i]]: p for i, p in doors}
    return doors, mapped


# -- refactor guard --------------------------------------------------------
def test_annual_propensity_and_monthly_hazard_relation():
    persona = _persona(age=30)
    life_state = life_state_from_persona(persona)
    for template in list(TEMPLATES.values())[:8]:
        for age in (25, 45, 70):
            ap = FSM.annual_propensity(template, life_state, age, persona)
            expected = (
                template.base_rate_per_year
                * template.age_weight(age)
                * FSM._state_modifier(template, life_state)
                * FSM._persona_modifier(template, persona)
                * FSM.global_hazard_scale
            )
            assert ap == expected
            assert FSM.monthly_hazard(template, life_state, age, persona) == max(0.0, min(0.5, ap / 12.0))


# -- determinism -----------------------------------------------------------
def test_subgraph_events_deterministic():
    persona = _persona(age=33, marital_status="single")
    a = subgraph_scripted_events(persona=persona, seed=42, horizon_months=120, templates=TEMPLATES, paths=PATHS)
    b = subgraph_scripted_events(persona=persona, seed=42, horizon_months=120, templates=TEMPLATES, paths=PATHS)
    assert a == b
    assert a  # non-empty for a plain single employed persona


# -- persona.age anchoring -------------------------------------------------
def test_events_anchored_to_persona_age():
    persona = _persona(age=33)
    horizon = 120
    events = subgraph_scripted_events(persona=persona, seed=7, horizon_months=horizon, templates=TEMPLATES, paths=PATHS)
    for event in events:
        month = event[1]
        assert 0 <= month < horizon  # inside the person's forward window


# -- weight is hazard-derived, age-conditioned -----------------------------
def test_episode_weight_equals_entry_event_hazard():
    marriage_core = TEMPLATE_BY_ID["marriage_only_core"]  # single entry door
    persona = _persona(age=28, marital_status="single")
    doors, mapped = _doors_by_benchmark(marriage_core, persona)
    assert len(doors) == 1
    entry_hazard = FSM.annual_propensity(
        TEMPLATES["relationship_marriage"], life_state_from_persona(persona), 28, persona
    )
    assert episode_weight(doors) == entry_hazard  # driven by hazard, not sampling_weight


def test_marriage_weight_higher_for_young_than_old():
    marriage_core = TEMPLATE_BY_ID["marriage_only_core"]
    young = _persona(age=28, marital_status="single")
    old = _persona(age=62, marital_status="single")
    w_young = episode_weight(_doors_by_benchmark(marriage_core, young)[0])
    w_old = episode_weight(_doors_by_benchmark(marriage_core, old)[0])
    assert w_young > w_old > 0


# -- entry-conflict filtering ----------------------------------------------
def test_married_persona_gets_no_fresh_marriage():
    persona = _persona(age=40, marital_status="married")
    events = subgraph_scripted_events(persona=persona, seed=3, horizon_months=120, templates=TEMPLATES, paths=PATHS)
    assert "relationship_marriage" not in {eid for eid, _ in events}


# -- mid-entry -------------------------------------------------------------
def test_employed_persona_midenters_career_arc():
    arc = TEMPLATE_BY_ID["early_career_learning_arc"]  # 취업 -> 교육 -> 이직 -> 전배
    persona = _persona(age=30, employment_status="employed")
    doors, _ = _doors_by_benchmark(arc, persona)
    open_indexes = {i for i, _ in doors}
    assert 0 not in open_indexes  # can't re-enter at first employment while employed
    assert open_indexes  # but the arc is still enterable mid-way


def test_midentry_doors_weighted_by_event_hazard():
    arc = TEMPLATE_BY_ID["employer_sponsored_study_obligation_arc"]
    persona = _persona(age=30, employment_status="employed")
    _, mapped = _doors_by_benchmark(arc, persona)
    # employed persona's open doors map to study-abroad (~0.01/yr) and
    # job-change (~0.08/yr); the higher-rate door must carry more weight so
    # proportional sampling favors it (confirmed mid-entry rule).
    assert "career_job_change" in mapped and "education_study_abroad" in mapped
    assert mapped["career_job_change"] > mapped["education_study_abroad"]


def test_event_first_weight_is_not_multiplied_by_subgraph_count():
    persona = _persona(age=30, employment_status="employed")
    state = life_state_from_persona(persona)
    branches, weights = event_entry_candidates(
        persona=persona,
        life_state=state,
        anchor_age=30,
        prior_nodes=set(),
        occurrence_counts={},
        fsm=FSM,
        reverse_map=REVERSE,
        templates_by_id=TEMPLATES,
    )
    assert len(branches["career_job_change"]) > 1
    expected = FSM.annual_propensity(TEMPLATES["career_job_change"], state, 30, persona)
    assert weights["career_job_change"] == expected


def test_married_persona_can_midenter_childbirth_arc_again():
    persona = _persona(age=35, marital_status="married", children_ages=[3])
    state = life_state_from_persona(persona)
    branches, weights = event_entry_candidates(
        persona=persona,
        life_state=state,
        anchor_age=35,
        prior_nodes=set(),
        occurrence_counts={"relationship_childbirth": 1},
        fsm=FSM,
        reverse_map=REVERSE,
        templates_by_id=TEMPLATES,
    )
    assert weights["relationship_childbirth"] > 0
    assert any(
        candidate.start_event_index > 0
        for candidate in branches["relationship_childbirth"]
    )


# -- horizon filter --------------------------------------------------------
def test_horizon_filter_excludes_future_arcs():
    from fin_life_benchmark.trajectory.subgraph_bridge import eligible_templates

    persona = _persona(age=30)
    life_state = life_state_from_persona(persona)
    eligible = eligible_templates(persona, life_state, 10, FSM, REVERSE, TEMPLATES)
    ids = {t.id for t, _, _ in eligible}
    assert "retirement_pension_arc" not in ids  # starts at 50, past a 10y horizon


# -- empty selection -------------------------------------------------------
def test_zero_episode_count_returns_empty():
    persona = _persona(age=30)
    events = subgraph_scripted_events(
        persona=persona, seed=1, horizon_months=120, episode_count=0, templates=TEMPLATES, paths=PATHS
    )
    assert events == []


# -- fixed child education events -----------------------------------------
def test_fixed_child_education_events_follow_horizon():
    persona = _persona(age=38, marital_status="married", children_ages=[6], dependents_count=1)

    assert fixed_child_education_events(persona, horizon_months=12) == []
    assert fixed_child_education_events(persona, horizon_months=13) == [
        (
            "education_child_stage_entry",
            12,
            {
                "child_id": "child_001",
                "child_age_months": 84,
                "previous_stage": "pre_school",
                "new_stage": "primary",
            },
            {
                "causal_bundle_id": "fixed_education_child_001_primary",
                "bundle_event_index": 0,
                "source_template_id": "fixed_child_education",
            },
        )
    ]


def test_fixed_child_education_event_occurs_with_stage_override():
    persona = _persona(
        age=38,
        marital_status="married",
        children_ages=[6],
        dependents_count=1,
        employment_status="employed",
    )
    locale = load_locale("ko_KR", PATHS)
    simulator = TrajectorySimulator(
        TEMPLATES,
        locale,
        {
            "global_hazard_scale": 0.0,
            "max_concurrent_active_events": 2,
            "max_events_per_trajectory": 12,
            "min_months_between_event_starts": 0,
            "snapshot_every_transition": True,
        },
    )
    memory = build_initial_memory(persona, locale, seed=1)
    actions = build_initial_actions(persona, memory, locale, seed=1)

    trajectory = simulator.simulate(
        persona=persona,
        initial_memory=memory,
        initial_actions=actions,
        horizon_years=2,
        seed=1,
        trajectory_id="traj_fixed_education",
        forced_events=fixed_child_education_events(persona, horizon_months=24),
    )

    education = [
        instance
        for instance in trajectory.life_event_instances
        if instance.event_id == "education_child_stage_entry"
    ]
    assert len(education) == 1
    assert education[0].status.value == "occurred"
    assert education[0].start_month == 12
    assert education[0].params["new_stage"] == "primary"


# -- end-to-end simulate ---------------------------------------------------
def _simulate_subgraph(persona, seed, horizon_years=10):
    locale = load_locale("ko_KR", PATHS)
    sim_config = load_yaml(PATHS.generation / "simulation.yaml")
    simulator = TrajectorySimulator(TEMPLATES, locale, {**sim_config, "global_hazard_scale": 0.0})
    memory = build_initial_memory(persona, locale, seed=42)
    actions = build_initial_actions(persona, memory, locale, seed=42)
    forced = subgraph_scripted_events(
        persona=persona, seed=seed, horizon_months=horizon_years * 12, templates=TEMPLATES, paths=PATHS
    )
    return simulator.simulate(
        persona=persona,
        initial_memory=memory,
        initial_actions=actions,
        horizon_years=horizon_years,
        seed=seed,
        trajectory_id=f"traj_{seed:05d}",
        forced_events=forced,
    )


def test_full_subgraph_simulate_occurs_and_is_deterministic():
    persona = _persona(age=30, marital_status="single", employment_status="employed", residence_status="wolse")
    t1 = _simulate_subgraph(persona, seed=42)
    t2 = _simulate_subgraph(persona, seed=42)
    assert t1.model_dump(mode="json") == t2.model_dump(mode="json")
    occurred = [i for i in t1.life_event_instances if i.status.value == "occurred"]
    assert occurred  # subgraph arcs became the trajectory backbone
    # every occurred event is anchored at or after the persona's age
    for inst in occurred:
        assert persona.age <= inst.status_history[-1].age
