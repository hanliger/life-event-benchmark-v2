"""Simulator determinism, guard consistency, and end-to-end smoke."""

import random

from fin_life_benchmark.actions.initial_actions_generator import build_initial_actions
from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.generator import DialogueGenerator
from fin_life_benchmark.fsm.event_lifecycle import sample_event_params
from fin_life_benchmark.fsm.models import EventInstance, EventStatus
from fin_life_benchmark.fsm.life_state_machine import LifeStateMachine
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.gold.prefix_gold_exporter import export_prefix_gold
from fin_life_benchmark.io import RepoPaths, load_yaml
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.memory.delta_engine import DeltaEngine
from fin_life_benchmark.memory.initial_state_generator import build_initial_memory
from fin_life_benchmark.memory.models import CellStatus, FinancialMemoryState
from fin_life_benchmark.persona.models import HouseholdState, HousingState, NormalizedPersona, OccupationState
from fin_life_benchmark.trajectory.models import LifeState
from fin_life_benchmark.trajectory.simulator import TrajectorySimulator
from fin_life_benchmark.validation.dialogue_validator import DialogueValidator


def _persona() -> NormalizedPersona:
    return NormalizedPersona(
        persona_id="p_test",
        persona_source_id="test",
        locale="ko_KR",
        age=33,
        sex="여자",
        persona_text="테스트 페르소나",
        occupation_state=OccupationState(occupation="사무직", employment_status="employed", income_stability="stable"),
        household=HouseholdState(marital_status="single", children_ages=[], dependents_count=0),
        housing=HousingState(residence_status="wolse", region="서울 마포구"),
    )


def _simulate(seed: int = 7, horizon: int = 8):
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    sim_config = load_yaml(paths.generation / "simulation.yaml")
    simulator = TrajectorySimulator(templates, locale, sim_config)
    persona = _persona()
    memory = build_initial_memory(persona, locale, seed=seed)
    actions = build_initial_actions(persona, memory, locale, seed=seed)
    return simulator.simulate(persona, memory, actions, horizon_years=horizon, seed=seed, trajectory_id="traj_test")


def test_simulation_is_deterministic():
    t1 = _simulate()
    t2 = _simulate()
    assert t1.model_dump(mode="json") == t2.model_dump(mode="json")


def test_lifecycle_history_is_ordered_and_valid():
    trajectory = _simulate(seed=11, horizon=10)
    order = {"weak_signal": 0, "upcoming": 1, "occurred": 2, "cancelled": 2}
    for instance in trajectory.life_event_instances:
        statuses = [h.status.value for h in instance.status_history]
        months = [h.month_index for h in instance.status_history]
        assert months == sorted(months)
        ranks = [order[s] for s in statuses]
        assert ranks == sorted(ranks), f"invalid lifecycle {statuses}"
        assert statuses[-1] in {"occurred", "cancelled", "weak_signal", "upcoming"}


def test_dialogue_structured_context_is_prefix_safe():
    trajectory = _simulate(seed=13, horizon=8)
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, locale, paths)

    plans = planner.build_plans(trajectory, seed=0)
    checked = 0
    for plan in plans:
        event = plan.structured_context.get("event")
        if event is None:
            continue
        checked += 1
        history = event["status_history"]
        assert history
        assert all(item["month_index"] <= plan.month_index for item in history)
        assert event["status"] == history[-1]["status"]
        assert event["status"] == plan.event_status_after_session
        assert all(
            update["month_index"] <= plan.month_index
            for update in plan.structured_context["event_memory_updates"]
        )
    assert checked > 0


def test_family_death_requires_existing_dependent():
    templates = load_life_event_templates()
    template = templates["relationship_family_death"]
    fsm = LifeStateMachine(templates)

    no_dependents = LifeState(dependents_count=0)
    assert not fsm.guards_pass(template, no_dependents, 45, 0, {}, [])

    with_dependents = LifeState(dependents_count=1)
    assert fsm.guards_pass(template, with_dependents, 45, 0, {}, [])


def test_life_stage_invariant_guards():
    templates = load_life_event_templates()
    fsm = LifeStateMachine(templates)

    marriage = templates["relationship_marriage"]
    assert not fsm.guards_pass(marriage, LifeState(marital_status="separated"), 35, 0, {}, [])
    assert fsm.guards_pass(marriage, LifeState(marital_status="divorced"), 35, 0, {}, [])

    childbirth = templates["relationship_childbirth_or_adoption"]
    nearly_full_children = LifeState(marital_status="married", children_ages=[1, 3, 5, 7], dependents_count=3)
    assert not fsm.guards_pass(childbirth, nearly_full_children, 35, 0, {}, [])
    nearly_full_dependents = LifeState(marital_status="married", children_ages=[1, 3, 5], dependents_count=4)
    assert not fsm.guards_pass(childbirth, nearly_full_dependents, 35, 0, {}, [])
    room_for_one = LifeState(marital_status="married", children_ages=[1, 3, 5], dependents_count=3)
    assert fsm.guards_pass(childbirth, room_for_one, 35, 0, {}, [])

    in_school = LifeState(in_education=True, employment_status="employed")
    assert not fsm.guards_pass(templates["education_self_program_start"], in_school, 35, 0, {}, [])
    assert not fsm.guards_pass(templates["education_study_abroad"], in_school, 35, 0, {}, [])


def test_dependent_change_never_exceeds_four_dependents():
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    state = LifeState(dependents_count=4)

    params = sample_event_params(templates["relationship_dependent_change"], state, locale, random.Random(0))

    assert params["dependent_delta"] == -1
    assert params["dependents_after"] == 3


def test_jeonse_rental_contract_keeps_rent_fields_not_applicable():
    memory = FinancialMemoryState()
    memory.set_initial("housing.residence_status", "jeonse")
    memory.set_initial("housing.contract_type", "jeonse")
    memory.set_initial("housing.rent_amount", None, status=CellStatus.NOT_APPLICABLE)
    memory.set_initial("housing.rent_payee", None, status=CellStatus.NOT_APPLICABLE)
    instance = EventInstance(
        event_instance_id="ev_rental",
        event_id="housing_rental_contract",
        label_ko="전세·월세 계약/갱신",
        domain="housing",
        params={"new_contract_type": "jeonse", "new_rent_amount": 0, "new_payee": "집주인"},
    )

    DeltaEngine().apply_transition(memory, instance, EventStatus.OCCURRED, month_index=3, rng=random.Random(0))

    assert memory.latest("housing.rent_amount").status == CellStatus.NOT_APPLICABLE
    assert memory.latest("housing.rent_payee").status == CellStatus.NOT_APPLICABLE


def test_forced_duplicate_event_start_is_blocked_by_pending_instance():
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    sim_config = {
        "global_hazard_scale": 0.0,
        "max_concurrent_active_events": 4,
        "max_events_per_trajectory": 12,
        "min_months_between_event_starts": 0,
        "snapshot_every_transition": True,
    }
    simulator = TrajectorySimulator(templates, locale, sim_config)
    persona = _persona()  # wolse renter, so rental_contract guards pass
    memory = build_initial_memory(persona, locale, seed=2)
    actions = build_initial_actions(persona, memory, locale, seed=2)

    trajectory = simulator.simulate(
        persona,
        memory,
        actions,
        horizon_years=2,
        seed=2,
        trajectory_id="traj_duplicate_forced",
        forced_events=[("housing_rental_contract", 0), ("housing_rental_contract", 0)],
    )

    rental_starts = [
        instance.start_month
        for instance in trajectory.life_event_instances
        if instance.event_id == "housing_rental_contract"
    ]
    assert rental_starts[0] == 0
    assert len(rental_starts) == len(set(rental_starts))
    assert all(
        later - earlier >= templates["housing_rental_contract"].cooldown_months
        for earlier, later in zip(rental_starts, rental_starts[1:])
    )


def test_end_to_end_mock_pipeline_in_memory():
    trajectory = _simulate(seed=13, horizon=8)
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, locale, paths)
    plans = planner.build_plans(trajectory, seed=0)
    assert plans and plans[0].session_id == "S001"

    generator = DialogueGenerator(mode="mock", paths=paths)
    sessions = [generator.generate_session(p, trajectory.persona).model_dump(mode="json") for p in plans]

    validator = DialogueValidator(templates)
    violations = [v for s in sessions for v in validator.validate_session(s)]
    assert violations == []

    prefixes = export_prefix_gold(trajectory, sessions)
    assert len(prefixes) == len(sessions)
    # weak_signal / upcoming events must not allow updates
    for prefix in prefixes:
        for event in prefix.gold_life_events:
            if event.event_status in {"weak_signal", "upcoming", "cancelled", "no_event"}:
                assert not event.update_allowed
    # risk policy: every gold action decision on funds movement is guarded
    for prefix in prefixes:
        for decision in prefix.gold_action_decisions:
            if decision.funds_movement:
                assert decision.must_not_execute


def test_prefix_gold_dedup_roundtrips(tmp_path):
    """Exported gold blanks repeated payloads; read_prefix_gold must restore
    them so consumers see identical records to a non-deduped read."""
    import json as _json

    from fin_life_benchmark.gold.loader import read_prefix_gold

    trajectory = _simulate(seed=13, horizon=8)
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, locale, paths)
    generator = DialogueGenerator(mode="mock", paths=paths)
    plans = planner.build_plans(trajectory, seed=0)
    sessions = [generator.generate_session(p, trajectory.persona).model_dump(mode="json") for p in plans]
    prefixes = export_prefix_gold(trajectory, sessions)

    # some prefixes must be deduped (blanked), and a reference (pre-dedup) copy
    assert any(p.repeats_previous for p in prefixes)

    out = tmp_path / "gold.jsonl"
    out.write_text("\n".join(_json.dumps(p.model_dump(mode="json"), ensure_ascii=False) for p in prefixes), encoding="utf-8")

    restored = list(read_prefix_gold(out))
    assert len(restored) == len(prefixes)
    # carry-forward: no restored record is left blank-because-repeated with an
    # empty payload when its predecessor had content
    last_events = None
    for rec in restored:
        if rec["gold_life_events"]:
            last_events = rec["gold_life_events"]
        # a repeated prefix must have inherited the previous non-empty payload
        if rec.get("repeats_previous") and last_events is not None:
            assert rec["gold_life_events"] == last_events


def test_episode_forced_events_are_guaranteed_and_impact_actions():
    """Coverage path: forcing a home-purchase episode onto a wolse renter (who
    has rent_autopay) must produce the occurred event AND a post_occurred
    action impact on the rent action — the (event × matching action) pairing
    the hazard sampler produces only rarely."""
    from fin_life_benchmark.trajectory.episode_bridge import (
        episode_scripted_events,
        templates_for_event,
    )

    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    sim_config = load_yaml(paths.generation / "simulation.yaml")
    simulator = TrajectorySimulator(templates, locale, sim_config)

    persona = _persona()  # wolse renter -> gets rent_autopay
    memory = build_initial_memory(persona, locale, seed=1)
    actions = build_initial_actions(persona, memory, locale, seed=1)
    assert any(a.type == "rent_autopay" for a in actions)

    template_ids = templates_for_event(paths)["housing_home_purchase"]
    forced = episode_scripted_events(
        seed=1, horizon_months=144, start_age=persona.age,
        template_ids=template_ids[:1], paths=paths,
    )
    assert any(eid == "housing_home_purchase" for eid, _ in forced)

    trajectory = simulator.simulate(
        persona, memory, actions, horizon_years=12, seed=1,
        trajectory_id="traj_cov_test", forced_events=forced,
    )
    occurred = {i.event_id for i in trajectory.life_event_instances if i.status.value == "occurred"}
    assert "housing_home_purchase" in occurred
    impacts = [a for s in trajectory.timeline_steps for a in s.action_impacts]
    assert any(i.action_type == "rent_autopay" for i in impacts)
    # forced events still respect guards: no divorce without marriage, etc.
    for instance in trajectory.life_event_instances:
        if instance.event_id == "relationship_divorce_or_separation":
            raise AssertionError("guard bypassed: divorce on single persona")


def test_stage2_memory_mcq_builds_single_and_multi_hop_items():
    prefixes = [
        {
            "prefix_id": "traj_mem_pfx001",
            "trajectory_id": "traj_mem",
            "visible_sessions": ["S001"],
            "gold_memory_updates": [
                {
                    "path": "employment.salary_day",
                    "operation": "update",
                    "old_value": 10,
                    "new_value": 25,
                }
            ],
            "gold_full_memory_state": {
                "employment.salary_day": {
                    "value": 25,
                    "status": "current",
                    "historical_values": [10],
                }
            },
        },
        {
            "prefix_id": "traj_mem_pfx002",
            "trajectory_id": "traj_mem",
            "visible_sessions": ["S001", "S002"],
            "gold_memory_updates": [
                {
                    "path": "employment.salary_day",
                    "operation": "update",
                    "old_value": 10,
                    "new_value": 25,
                },
                {
                    "path": "housing.rent_payee",
                    "operation": "update",
                    "old_value": "기존 임대인",
                    "new_value": "새 임대인",
                },
            ],
            "gold_full_memory_state": {
                "employment.salary_day": {
                    "value": 25,
                    "status": "current",
                    "historical_values": [10],
                },
                "housing.rent_payee": {
                    "value": "새 임대인",
                    "status": "needs_verification",
                    "historical_values": ["기존 임대인"],
                },
            },
        },
    ]

    items = ItemBuilder(seed=0).build_stage2(prefixes, {"traj_mem": []})
    assert {item.stage for item in items} == {"stage2_memory_mcq"}
    assert {"single", "multi"} <= {item.metadata["hop_type"] for item in items}

    for item in items:
        correct = [option for option in item.options if option.correct]
        assert len(correct) == 1
        assert item.gold["correct_option"] == correct[0].option_id
        assert "memory_updates" not in item.gold
        assert any(option.error_type == "stale_memory_carryover" for option in item.options)
