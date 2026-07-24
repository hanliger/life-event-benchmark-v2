"""Simulator determinism, guard consistency, and end-to-end smoke."""

import random

from fin_life_benchmark.actions.initial_actions_generator import build_initial_actions
from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.generator import DialogueGenerator
from fin_life_benchmark.fsm.event_lifecycle import apply_occurred_to_life_state, sample_event_params
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


def test_family_death_only_removes_a_dependent_when_applicable():
    templates = load_life_event_templates()
    template = templates["relationship_family_death"]
    no_dependents = LifeState(dependents_count=0)
    params = sample_event_params(template, no_dependents, load_locale("ko_KR"), random.Random(0))
    assert params["was_dependent"] is False
    apply_occurred_to_life_state(template.event_id, no_dependents, params)
    assert no_dependents.dependents_count == 0


def test_life_stage_invariant_guards():
    templates = load_life_event_templates()
    fsm = LifeStateMachine(templates)

    marriage = templates["relationship_marriage"]
    assert not fsm.guards_pass(marriage, LifeState(marital_status="separated"), 35, 0, {}, [])
    assert fsm.guards_pass(marriage, LifeState(marital_status="divorced"), 35, 0, {}, [])

    childbirth = templates["relationship_childbirth"]
    nearly_full_children = LifeState(marital_status="married", children_ages=[1, 3, 5, 7], dependents_count=3)
    assert not fsm.guards_pass(childbirth, nearly_full_children, 35, 0, {}, [])
    nearly_full_dependents = LifeState(marital_status="married", children_ages=[1, 3, 5], dependents_count=4)
    assert not fsm.guards_pass(childbirth, nearly_full_dependents, 35, 0, {}, [])
    room_for_one = LifeState(marital_status="married", children_ages=[1, 3, 5], dependents_count=3)
    assert fsm.guards_pass(childbirth, room_for_one, 35, 0, {}, [])

    in_school = LifeState(in_education=True, employment_status="employed")
    assert not fsm.guards_pass(templates["education_self_program_start"], in_school, 35, 0, {}, [])
    assert not fsm.guards_pass(templates["education_study_abroad"], in_school, 35, 0, {}, [])


def test_dependent_addition_never_exceeds_four_dependents():
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    state = LifeState(dependents_count=4)

    addition = templates["relationship_dependent_addition"]
    assert not LifeStateMachine(templates).guards_pass(addition, state, 45, 0, {}, [])


def test_jeonse_move_keeps_rent_fields_not_applicable():
    memory = FinancialMemoryState()
    memory.set_initial("housing.residence_status", "jeonse")
    memory.set_initial("housing.contract_type", "jeonse")
    memory.set_initial("housing.rent_amount", None, status=CellStatus.NOT_APPLICABLE)
    memory.set_initial("housing.rent_payee", None, status=CellStatus.NOT_APPLICABLE)
    instance = EventInstance(
        event_instance_id="ev_move",
        event_id="housing_move",
        label_ko="이사",
        domain="housing",
        params={
            "new_address": "서울 마포구",
            "new_residence_status": "jeonse",
            "new_contract_type": "jeonse",
            "new_rent_amount": 0,
            "new_payee": "집주인",
        },
        memory_delta_template_id="housing_move",
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
    persona = _persona()
    memory = build_initial_memory(persona, locale, seed=2)
    actions = build_initial_actions(persona, memory, locale, seed=2)

    trajectory = simulator.simulate(
        persona,
        memory,
        actions,
        horizon_years=2,
        seed=2,
        trajectory_id="traj_duplicate_forced",
        forced_events=[("housing_move", 0), ("housing_move", 0)],
    )

    move_starts = [
        instance.start_month
        for instance in trajectory.life_event_instances
        if instance.event_id == "housing_move"
    ]
    assert move_starts[0] == 0
    assert len(move_starts) == len(set(move_starts))
    assert all(
        later - earlier >= templates["housing_move"].cooldown_months
        for earlier, later in zip(move_starts, move_starts[1:])
    )


def test_month_zero_snapshot_records_post_transition_state():
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    simulator = TrajectorySimulator(
        templates,
        locale,
        {
            "global_hazard_scale": 0.0,
            "max_concurrent_active_events": 4,
            "max_events_per_trajectory": 12,
            "min_months_between_event_starts": 0,
            "snapshot_every_transition": True,
        },
    )
    persona = _persona()
    persona.household.dependents_count = 1
    memory = build_initial_memory(persona, locale, seed=42)
    actions = build_initial_actions(persona, memory, locale, seed=42)

    trajectory = simulator.simulate(
        persona,
        memory,
        actions,
        horizon_years=2,
        seed=42,
        trajectory_id="traj_month_zero_snapshot",
        forced_events=[("relationship_family_death", 0)],
    )

    assert trajectory.initial_persona_state.life_state.dependents_count == 1
    assert trajectory.state_snapshots["0"].life_state.dependents_count == 0
    assert any(step.month_index == 0 for step in trajectory.timeline_steps)


def test_occurred_event_crossing_age_guard_is_cancelled():
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    base_templates = load_life_event_templates(paths)
    from fin_life_benchmark.fsm.models import LifecycleConfig

    templates = dict(base_templates)
    templates["career_job_change"] = base_templates["career_job_change"].model_copy(
        deep=True,
        update={
            "lifecycle": LifecycleConfig(
                weak_signal_months=(1, 1),
                upcoming_months=(1, 1),
                p_skip_weak_signal=0.0,
                p_skip_upcoming=0.0,
                p_cancel_from_weak=0.0,
                p_cancel_from_upcoming=0.0,
            )
        },
    )
    simulator = TrajectorySimulator(
        templates,
        locale,
        {
            "global_hazard_scale": 0.0,
            "max_concurrent_active_events": 4,
            "max_events_per_trajectory": 12,
            "min_months_between_event_starts": 0,
            "snapshot_every_transition": True,
        },
    )
    persona = _persona()
    persona.age = 65
    persona.occupation_state.employment_status = "employed"
    memory = build_initial_memory(persona, locale, seed=42)
    actions = build_initial_actions(persona, memory, locale, seed=42)

    trajectory = simulator.simulate(
        persona,
        memory,
        actions,
        horizon_years=2,
        seed=42,
        trajectory_id="traj_age_guard",
        forced_events=[("career_job_change", 11)],
    )

    instance = next(i for i in trajectory.life_event_instances if i.event_id == "career_job_change")
    assert instance.status.value == "cancelled"
    assert instance.cancelled_month == 13


def test_forced_events_respect_active_and_total_caps():
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    simulator = TrajectorySimulator(
        templates,
        locale,
        {
            "global_hazard_scale": 0.0,
            "max_concurrent_active_events": 1,
            "max_events_per_trajectory": 2,
            "min_months_between_event_starts": 0,
            "snapshot_every_transition": True,
        },
    )
    persona = _persona()
    memory = build_initial_memory(persona, locale, seed=42)
    actions = build_initial_actions(persona, memory, locale, seed=42)

    trajectory = simulator.simulate(
        persona,
        memory,
        actions,
        horizon_years=2,
        seed=42,
        trajectory_id="traj_forced_caps",
        forced_events=[("crisis_health_event", 0), ("crisis_accident_or_disaster", 0)],
    )

    assert len(trajectory.life_event_instances) <= 2
    for month in range(trajectory.horizon_months):
        active = sum(
            instance.status_as_of(month).value in {"weak_signal", "upcoming"}
            for instance in trajectory.life_event_instances
        )
        assert active <= 1


def test_target_occurred_count_stops_trajectory():
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    simulator = TrajectorySimulator(
        templates,
        locale,
        {
            "global_hazard_scale": 0.0,
            "max_concurrent_active_events": 2,
            "max_events_per_trajectory": 10,
            "min_months_between_event_starts": 0,
            "snapshot_every_transition": True,
        },
    )
    persona = _persona()
    memory = build_initial_memory(persona, locale, seed=42)
    actions = build_initial_actions(persona, memory, locale, seed=42)
    trajectory = simulator.simulate(
        persona,
        memory,
        actions,
        horizon_years=20,
        seed=42,
        trajectory_id="traj_target_count",
        forced_events=[
            ("crisis_health_event", 0),
            ("crisis_health_event", 24),
            ("crisis_health_event", 48),
        ],
        target_occurred_events=3,
    )
    assert sum(i.occurred_month is not None for i in trajectory.life_event_instances) == 3
    assert trajectory.horizon_months < 240
    assert trajectory.state_snapshots["0"] == trajectory.initial_persona_state


def test_childbirth_can_repeat_after_cooldown():
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    simulator = TrajectorySimulator(
        templates,
        locale,
        {
            "global_hazard_scale": 0.0,
            "max_concurrent_active_events": 2,
            "max_events_per_trajectory": 10,
            "min_months_between_event_starts": 0,
            "snapshot_every_transition": True,
        },
    )
    persona = _persona()
    persona.household.marital_status = "married"
    memory = build_initial_memory(persona, locale, seed=42)
    actions = build_initial_actions(persona, memory, locale, seed=42)
    trajectory = simulator.simulate(
        persona,
        memory,
        actions,
        horizon_years=10,
        seed=42,
        trajectory_id="traj_repeat_childbirth",
        forced_events=[
            ("relationship_childbirth", 0),
            ("relationship_childbirth", 36),
            ("relationship_childbirth", 72),
        ],
        target_occurred_events=3,
    )
    births = [
        i for i in trajectory.life_event_instances
        if i.event_id == "relationship_childbirth" and i.occurred_month is not None
    ]
    assert len(births) == 3


def test_end_to_end_mock_pipeline_in_memory():
    trajectory = _simulate(seed=13, horizon=8)
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, locale, paths)
    plans = planner.build_plans(trajectory, seed=0)
    assert plans and plans[0].session_id == "S001"
    occurred_count = sum(instance.occurred_month is not None for instance in trajectory.life_event_instances)
    assert len(plans) == occurred_count * 15
    assert [(plan.month_index, plan.transition_order) for plan in plans] == sorted(
        (plan.month_index, plan.transition_order) for plan in plans
    )
    for window_index in range(1, occurred_count + 1):
        window = [plan for plan in plans if plan.window_index == window_index]
        assert len(window) == 15
        assert sum(
            plan.session_type == "occurred_evidence"
            and plan.event_status_after_session == "occurred"
            for plan in window
        ) == 1
        assert len({plan.window_event_instance_id for plan in window}) == 1

    generator = DialogueGenerator(mode="mock", paths=paths)
    sessions = [generator.generate_session(p, trajectory.persona).model_dump(mode="json") for p in plans]

    validator = DialogueValidator(templates)
    # The mock generator emits templated turns that do not surface the event cue
    # verbatim, a mock-generation limitation the validator should not hold real
    # dialogue to here. (provided_slot_not_grounded_in_dialogue used to be listed
    # too, but generation now reconciles grounded slots against the realized
    # turns, so ungrounded provided_slots no longer occur -- see
    # DialogueGenerator._reconcile_with_dialogue.)
    mock_allowed = {"near_direct_event_disclosure"}
    violations = [
        violation
        for session in sessions
        for violation in validator.validate_session(session)
        if violation["code"] not in mock_allowed
    ]
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
        visible_sources = {event.event_instance_id for event in prefix.gold_life_events}
        for cell in prefix.gold_full_memory_state.values():
            source = cell.get("source_event_instance_id")
            pending_source = (cell.get("pending_proposal") or {}).get("source_event_instance_id")
            assert source is None or source in visible_sources
            assert pending_source is None or pending_source in visible_sources
            assert cell.get("status") != "cancelled"

    checkpoints = export_prefix_gold(trajectory, sessions, checkpoint_stride=15)
    assert [prefix.checkpoint_session_count for prefix in checkpoints] == [
        15 * index for index in range(1, occurred_count + 1)
    ]
    assert [prefix.occurred_event_count for prefix in checkpoints] == list(
        range(1, occurred_count + 1)
    )


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


def test_forced_event_is_guarded_and_impacts_actions():
    paths = RepoPaths.default()
    locale = load_locale("ko_KR", paths)
    templates = load_life_event_templates(paths)
    sim_config = load_yaml(paths.generation / "simulation.yaml")
    simulator = TrajectorySimulator(templates, locale, sim_config)

    persona = _persona()  # wolse renter -> gets rent_autopay
    memory = build_initial_memory(persona, locale, seed=1)
    actions = build_initial_actions(persona, memory, locale, seed=1)
    assert any(a.type == "rent_autopay" for a in actions)

    trajectory = simulator.simulate(
        persona, memory, actions, horizon_years=12, seed=1,
        trajectory_id="traj_forced_test", forced_events=[("housing_home_purchase", 0)],
    )
    occurred = {i.event_id for i in trajectory.life_event_instances if i.status.value == "occurred"}
    assert "housing_home_purchase" in occurred
    impacts = [a for s in trajectory.timeline_steps for a in s.action_impacts]
    assert any(i.action_type == "rent_autopay" for i in impacts)
    # Forced/background events still respect guards: a divorce on this
    # initially-single persona must be preceded by an occurred marriage.
    marriage_months = [
        instance.occurred_month
        for instance in trajectory.life_event_instances
        if instance.event_id == "relationship_marriage"
        and instance.occurred_month is not None
    ]
    for instance in trajectory.life_event_instances:
        if (
            instance.event_id == "relationship_divorce_or_separation"
            and instance.occurred_month is not None
        ):
            assert any(month < instance.occurred_month for month in marriage_months)


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
                    "evidence_turns": ["S001:0"],
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
                    "evidence_turns": ["S001:0"],
                },
                {
                    "path": "housing.rent_payee",
                    "operation": "update",
                    "old_value": "기존 임대인",
                    "new_value": "새 임대인",
                    "evidence_turns": ["S002:0"],
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

    true_initial = {
        "traj_mem": {
            "employment.salary_day": {"value": 10, "status": "current", "historical_values": []},
            "housing.rent_payee": {"value": "기존 임대인", "status": "current", "historical_values": []},
        }
    }
    items = ItemBuilder(seed=0).build_stage2(prefixes, {"traj_mem": []}, true_initial)
    assert {item.stage for item in items} == {"stage2_memory_mcq"}
    assert {"single", "multi"} <= {item.metadata["hop_type"] for item in items}

    for item in items:
        correct = [option for option in item.options if option.correct]
        assert len(correct) == 1
        assert item.gold["correct_option"] == correct[0].option_id
        assert "memory_updates" not in item.gold
        assert any(option.error_type == "stale_memory_carryover" for option in item.options)
