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
    # calc_result_without_required_input and assistant_premature_slot_disclosure
    # are content rules real generation satisfies via reject+repair; the mock's
    # fixed templates cannot, so they are mock-generation limitations here too.
    mock_allowed = {
        "near_direct_event_disclosure",
        "calc_result_without_required_input",
        "assistant_premature_slot_disclosure",
    }
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


def test_stage2_policy_matches_confirmed_event_questions():
    from fin_life_benchmark.benchmark.mcq_input import load_stage2_question_policy

    policy = load_stage2_question_policy(
        "configs/registries/stage2_question_policy.yaml"
    )
    expected_paths = {
        "career_employment": "employment.employer",
        "career_job_change": "employment.employer",
        "career_employment_end": "employment.employment_status",
        "career_leave_of_absence": "employment.income_stability",
        "career_reinstatement": "employment.income_stability",
        "career_self_employment": "employment.income_stability",
        "crisis_accident_or_disaster": "cashflow.recent_one_off_expense",
        "crisis_financial_fraud": "cashflow.recent_one_off_expense",
        "crisis_health_event": "cashflow.recent_one_off_expense",
        "education_child_stage_entry": "education.child_education_stage",
        "education_self_program_start": "education.self_education_status",
        "education_study_abroad": "education.self_education_status",
        "retirement_pension_start": "financial_products.pension_or_irp",
        "retirement_start": "employment.employment_status",
        "housing_home_purchase": "housing.properties",
        "housing_home_sale": "housing.properties",
        "housing_move": "housing.contract_type",
        "relationship_adoption": "household.dependents",
    }

    assert {
        event_id: policy[event_id]["target_memory_path"]
        for event_id in expected_paths
    } == expected_paths
    assert policy["career_job_change"]["question_scope"] == "latest_window"
    assert policy["education_child_stage_entry"]["value_selector"] == "stage_transition"
    assert policy["education_child_stage_entry"]["allow_noop_current_value"] is True
    assert policy["relationship_adoption"]["option_pool"] == (1, 2, 3, 4)
    assert policy["relationship_childbirth"]["option_pool"] == (1, 2, 3, 4)
    assert policy["relationship_dependent_addition"]["option_pool"] == (1, 2, 3, 4)
    assert policy["relationship_dependent_end"]["option_pool"] == (0, 1, 2, 3)
    assert policy["relationship_divorce_or_separation"]["option_pool"] == ("single", "married", "separated", "divorced")
    assert policy["relationship_marriage"]["option_pool"] == ("single", "married", "separated", "divorced")
    for event_id in (
        "crisis_accident_or_disaster",
        "crisis_financial_fraud",
        "crisis_health_event",
        "relationship_family_death",
    ):
        assert policy[event_id]["value_selector"] == "amount_krw"
        assert policy[event_id]["question_scope"] == "latest_window"
        assert policy[event_id]["option_pool_type"] == "numeric"
    assert policy["relationship_family_death"]["option_pool"] == (3000000, 4000000, 5000000, 7000000)
    assert policy["housing_home_purchase"]["value_selector"] == "property_loan_type"
    assert policy["housing_move"]["allow_noop_current_value"] is True


def test_stage2_checkpoint_allows_configured_noop_current_value():
    from fin_life_benchmark.benchmark.mcq_input import build_stage2_checkpoints

    visible_sessions = [f"S{i:03d}" for i in range(1, 16)]
    prefixes = [{
        "trajectory_id": "traj_noop",
        "prefix_id": "traj_noop_pfx015",
        "checkpoint_session_count": 15,
        "visible_sessions": visible_sessions,
        "gold_life_events": [{
            "event_instance_id": "traj_noop_ev001",
            "event_id": "housing_move",
            "life_event_label": "이사",
            "occurred": True,
            "evidence_turns": ["S015:2"],
        }],
        "gold_memory_updates": [],
        "gold_full_memory_state": {
            "housing.contract_type": {
                "value": "wolse",
                "status": "current",
                "pending_proposal": None,
            }
        },
    }]
    initial_memory = {
        "traj_noop": {
            "housing.contract_type": {
                "value": "wolse",
                "status": "current",
                "pending_proposal": None,
            }
        }
    }
    sessions = {
        "traj_noop": [{
            "session_id": "S015",
            "linked_event_instance_id": "traj_noop_ev001",
            "event_status_after_session": "occurred",
        }]
    }
    policy = {
        "housing_move": {
            "target_memory_path": "housing.contract_type",
            "question_label": "주거 유형",
            "allow_noop_current_value": True,
            "option_pool_type": "categorical",
            "option_pool": ("jeonse", "wolse", "family_home", "other"),
        }
    }

    checkpoints = build_stage2_checkpoints(
        prefixes,
        sessions_by_traj=sessions,
        initial_memory_by_traj=initial_memory,
        question_policy=policy,
    )

    assert len(checkpoints) == 1
    assert len(checkpoints[0].targets) == 1
    target = checkpoints[0].targets[0]
    assert target.operation == "no_change"
    assert target.after_state["value"] == "wolse"


def test_stage2_child_stage_noop_maps_to_change_none():
    from fin_life_benchmark.benchmark.mcq_input import Stage2Checkpoint, Stage2Target

    target = Stage2Target(
        canonical_target_id="traj_child:ev001:education.child_education_stage:no_change:abc123",
        trajectory_id="traj_child",
        target_event_instance_id="traj_child_ev001",
        target_event_id="education_child_stage_entry",
        target_event_label="자녀 교육 단계 진입",
        memory_path="education.child_education_stage",
        operation="no_change",
        first_visible_checkpoint=15,
        evidence_sessions=("S015",),
        evidence_turns=("S015:2",),
        before_state={"value": "high", "status": "current", "pending_proposal": None},
        after_state={"value": "high", "status": "current", "pending_proposal": None},
        value_selector="stage_transition",
        question_template=(
            "제공된 전체 상담 이력을 참고하여, {window_range}에서 "
            "자녀 교육 단계에 반영된 변화는 무엇인가?"
        ),
        question_label="자녀 교육 단계 변화",
        option_pool_type="categorical",
        option_pool=("primary", "middle", "high", "no_change"),
    )
    checkpoint = Stage2Checkpoint(
        trajectory_id="traj_child",
        prefix_id="traj_child_pfx015",
        checkpoint_session_count=15,
        visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 16)),
        targets=(target,),
    )

    item = ItemBuilder(seed=0).build_stage2([checkpoint])[0]

    assert item.gold["answer_value"] == "no_change"
    assert item.question == (
        "제공된 전체 상담 이력을 참고하여, S001~S015에서 "
        "자녀 교육 단계에 반영된 변화는 무엇인가?"
    )
    assert [option.text for option in item.options] == [
        "초등학교",
        "중학교",
        "고등학교",
        "변화 없음",
    ]
    assert item.options[3].correct is True


def test_stage2_latest_window_template_uses_exact_session_range():
    from fin_life_benchmark.benchmark.mcq_input import Stage2Checkpoint, Stage2Target

    target = Stage2Target(
        canonical_target_id="traj_job:ev010:employment.employer:update:abc123",
        trajectory_id="traj_job",
        target_event_instance_id="traj_job_ev010",
        target_event_id="career_job_change",
        target_event_label="이직",
        memory_path="employment.employer",
        operation="update",
        first_visible_checkpoint=150,
        evidence_sessions=("S150",),
        evidence_turns=("S150:2",),
        before_state={"value": "이전 직장", "status": "current", "pending_proposal": None},
        after_state={"value": "새 직장", "status": "current", "pending_proposal": None},
        question_template=(
            "제공된 전체 상담 이력을 참고하여, {window_range}에서 "
            "새로 반영된 현재 직장은 무엇인가?"
        ),
        question_label="현재 직장",
        question_scope="latest_window",
        option_pool_type="entity",
        option_pool=("가나직장", "나나직장", "다나직장", "새 직장"),
    )
    checkpoint = Stage2Checkpoint(
        trajectory_id="traj_job",
        prefix_id="traj_job_pfx150",
        checkpoint_session_count=150,
        visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 151)),
        targets=(target,),
        target_date_start="2020-01-01",
        target_date_end="2020-01-15",
    )

    item = ItemBuilder(seed=0).build_stage2([checkpoint], window_size=15)[0]

    assert item.question == (
        "제공된 전체 상담 이력을 참고하여, 2020년 1월 1일~2020년 1월 15일에서 "
        "새로 반영된 현재 직장은 무엇인가?"
    )
    assert item.metadata["target_date_start"] == "2020-01-01"


def test_stage2_home_purchase_uses_the_event_property_loan_type():
    from fin_life_benchmark.benchmark.mcq_input import Stage2Checkpoint, Stage2Target

    target = Stage2Target(
        canonical_target_id="traj_purchase:ev001:housing.properties:update:abc123",
        trajectory_id="traj_purchase",
        target_event_instance_id="traj_purchase_ev001",
        target_event_id="housing_home_purchase",
        target_event_label="주택 구매",
        memory_path="housing.properties",
        operation="update",
        first_visible_checkpoint=15,
        evidence_sessions=("S015",),
        evidence_turns=("S015:2",),
        before_state={"value": [], "status": "current", "pending_proposal": None},
        after_state={
            "value": [{
                "property_id": "property_1",
                "acquisition_event_instance_id": "traj_purchase_ev001",
                "mortgage_status": "active",
                "ownership_status": "owned",
            }],
            "status": "current",
            "pending_proposal": None,
        },
        value_selector="property_loan_type",
        question_template="해당 부동산에 연결된 대출 유형은 무엇인가?",
        question_label="해당 부동산의 대출 유형",
        option_pool_type="categorical",
        option_pool=("none", "credit_loan", "jeonse_loan", "mortgage"),
    )
    checkpoint = Stage2Checkpoint(
        trajectory_id="traj_purchase",
        prefix_id="traj_purchase_pfx015",
        checkpoint_session_count=15,
        visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 16)),
        targets=(target,),
    )

    item = ItemBuilder(seed=0).build_stage2([checkpoint])[0]

    assert item.question == (
        "제공된 전체 상담 이력을 참고하여, S001~S015 기간 기준으로 "
        "해당 부동산에 연결된 대출 유형은 무엇인가?"
    )
    assert item.gold["answer_value"] == "mortgage"
    assert [option.text for option in item.options] == [
        "대출 없음",
        "신용대출",
        "전세자금대출",
        "주택담보대출",
    ]
    assert item.options[3].correct is True


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




def test_stage2_memory_mcq_retains_prior_targets_at_later_checkpoints():
    from fin_life_benchmark.benchmark.mcq_input import Stage2Checkpoint, Stage2Target

    target_1 = Stage2Target(
        canonical_target_id="traj_mem:ev001:employment.salary_day:update:abc123",
        trajectory_id="traj_mem",
        target_event_instance_id="traj_mem_ev001",
        target_event_id="career_employment",
        target_event_label="취업",
        memory_path="employment.salary_day",
        operation="update",
        first_visible_checkpoint=15,
        evidence_sessions=("S015",),
        evidence_turns=("S015:2",),
        before_state={"value": 10, "status": "current", "pending_proposal": None},
        after_state={"value": 25, "status": "current", "pending_proposal": None},
        option_pool_type="numeric",
        option_pool=(10, 15, 20, 25),
        question_label="급여일",
    )
    target_2 = Stage2Target(
        canonical_target_id="traj_mem:ev002:employment.employer:update:def456",
        trajectory_id="traj_mem",
        target_event_instance_id="traj_mem_ev002",
        target_event_id="career_job_change",
        target_event_label="이직",
        memory_path="employment.employer",
        operation="update",
        first_visible_checkpoint=30,
        evidence_sessions=("S030",),
        evidence_turns=("S030:2",),
        before_state={"value": "이전 직장", "status": "current", "pending_proposal": None},
        after_state={"value": "새 직장", "status": "current", "pending_proposal": None},
        option_pool_type="entity",
        option_pool=("가나직장", "나나직장", "다나직장", "새 직장"),
        question_label="현재 직장",
    )
    checkpoints = [
        Stage2Checkpoint(
            trajectory_id="traj_mem",
            prefix_id="traj_mem_pfx015",
            checkpoint_session_count=15,
            visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 16)),
            targets=(target_1,),
        ),
        Stage2Checkpoint(
            trajectory_id="traj_mem",
            prefix_id="traj_mem_pfx030",
            checkpoint_session_count=30,
            visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 31)),
            targets=(target_1, target_2),
        ),
    ]

    items = ItemBuilder(seed=0).build_stage2(
        checkpoints,
        initial_memory_by_traj={
            "traj_mem": {
                "employment.salary_day": {
                    "value": 10,
                    "status": "current",
                    "historical_values": [],
                },
                "employment.employer": {
                    "value": "이전 직장",
                    "status": "current",
                    "historical_values": [],
                },
            }
        },
    )

    assert len(items) == 3
    assert items[0].item_id.startswith("traj_mem_s015_")
    assert items[1].item_id.startswith("traj_mem_s030_")
    assert items[2].item_id.startswith("traj_mem_s030_")
    assert items[0].gold["canonical_target_id"] == target_1.canonical_target_id
    assert items[1].gold["canonical_target_id"] == target_1.canonical_target_id
    assert items[2].gold["canonical_target_id"] == target_2.canonical_target_id
    assert items[0].visible_sessions == [f"S{i:03d}" for i in range(1, 16)]
    assert items[1].visible_sessions == [f"S{i:03d}" for i in range(1, 31)]
    assert items[2].visible_sessions == [f"S{i:03d}" for i in range(1, 31)]
    assert all("제공된 전체 상담 이력을 참고하여" in item.question for item in items)
    assert items[0].question == items[1].question
    assert items[1].question != items[2].question

    for item in items:
        correct = [option for option in item.options if option.correct]
        assert len(item.options) == 4
        assert len(correct) == 1
        assert item.gold["correct_option"] == correct[0].option_id

def test_stage2_memory_mcq_keeps_noop_final_value_and_property_ownership():
    from fin_life_benchmark.benchmark.mcq_input import Stage2Checkpoint, Stage2Target

    move_target = Stage2Target(
        canonical_target_id="traj_noop:ev001:housing.contract_type:update:abc123",
        trajectory_id="traj_noop",
        target_event_instance_id="traj_noop_ev001",
        target_event_id="housing_move",
        target_event_label="이사",
        memory_path="housing.contract_type",
        operation="update",
        first_visible_checkpoint=15,
        evidence_sessions=("S015",),
        evidence_turns=("S015:2",),
        before_state={"value": "wolse", "status": "current", "pending_proposal": None},
        after_state={"value": "wolse", "status": "current", "pending_proposal": None},
        option_pool_type="categorical",
        option_pool=("jeonse", "wolse", "family_home", "other"),
        question_label="주거 유형",
    )
    sale_target = Stage2Target(
        canonical_target_id="traj_sale:ev001:housing.properties:update:def456",
        trajectory_id="traj_sale",
        target_event_instance_id="traj_sale_ev001",
        target_event_id="housing_home_sale",
        target_event_label="주택 매각",
        memory_path="housing.properties",
        operation="update",
        first_visible_checkpoint=15,
        evidence_sessions=("S015",),
        evidence_turns=("S015:2",),
        before_state={
            "value": [{"property_id": "p1", "ownership_status": "owned"}],
            "status": "current",
            "pending_proposal": None,
        },
        after_state={
            "value": [{
                "property_id": "p1",
                "ownership_status": "sold",
                "disposal_event_instance_id": "traj_sale_ev001",
            }],
            "status": "current",
            "pending_proposal": None,
        },
        value_selector="property_ownership_status",
        question_template="담보대출 상환과 관련된 부동산의 현재 소유 상태는 무엇인가?",
        question_label="담보대출 상환과 관련된 부동산의 현재 소유 상태",
        option_pool_type="categorical",
        option_pool=("owned", "sold", "pending_sale", "unknown"),
    )
    checkpoints = [
        Stage2Checkpoint(
            trajectory_id="traj_noop",
            prefix_id="traj_noop_pfx015",
            checkpoint_session_count=15,
            visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 16)),
            targets=(move_target,),
        ),
        Stage2Checkpoint(
            trajectory_id="traj_sale",
            prefix_id="traj_sale_pfx015",
            checkpoint_session_count=15,
            visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 16)),
            targets=(sale_target,),
        ),
    ]

    items = ItemBuilder(seed=0).build_stage2(
        checkpoints,
        initial_memory_by_traj={
            "traj_noop": {
                "housing.contract_type": {
                    "value": "wolse",
                    "status": "current",
                    "historical_values": [],
                }
            },
            "traj_sale": {
                "housing.properties": {
                    "value": [{"property_id": "p1", "ownership_status": "owned"}],
                    "status": "current",
                    "historical_values": [],
                }
            },
        },
    )

    move_item, sale_item = items
    assert move_item.gold["answer_value"] == "wolse"
    assert any(option.text == "월세" and option.correct for option in move_item.options)
    assert sale_item.question == (
        "제공된 전체 상담 이력을 참고하여, S001~S015 기간 기준으로 "
        "담보대출 상환과 관련된 부동산의 현재 소유 상태는 무엇인가?"
    )
    assert sale_item.gold["answer_value"] == "sold"
    assert [option.text for option in sale_item.options] == [
        "현재 보유 중",
        "매각 완료",
        "매각 예정",
        "확인 불가",
    ]
    assert any(option.text == "매각 완료" and option.correct for option in sale_item.options)


def test_stage2_fixed_four_option_pool_is_authoritative():
    from fin_life_benchmark.benchmark.mcq_input import Stage2Checkpoint, Stage2Target

    count_target = Stage2Target(
        canonical_target_id="traj_fixed:ev001:household.dependents:update",
        trajectory_id="traj_fixed",
        target_event_instance_id="traj_fixed_ev001",
        target_event_id="relationship_dependent_addition",
        target_event_label="부양가족 추가",
        memory_path="household.dependents",
        operation="update",
        first_visible_checkpoint=15,
        evidence_sessions=("S015",),
        evidence_turns=("S015:2",),
        before_state={"value": 0, "status": "current"},
        after_state={"value": 1, "status": "current"},
        option_pool_type="count",
        option_pool=(1, 2, 3, 4),
    )
    amount_target = Stage2Target(
        canonical_target_id="traj_fixed:ev002:cashflow.recent_one_off_expense:update",
        trajectory_id="traj_fixed",
        target_event_instance_id="traj_fixed_ev002",
        target_event_id="relationship_family_death",
        target_event_label="가족 사망",
        memory_path="cashflow.recent_one_off_expense",
        operation="update",
        first_visible_checkpoint=30,
        evidence_sessions=("S030",),
        evidence_turns=("S030:2",),
        before_state={"value": {"amount_krw": 2000000}, "status": "current"},
        after_state={"value": {"amount_krw": 3000000}, "status": "current"},
        value_selector="amount_krw",
        question_scope="latest_window",
        option_pool_type="numeric",
        option_pool=(3000000, 4000000, 5000000, 7000000),
    )
    checkpoints = [
        Stage2Checkpoint(
            trajectory_id="traj_fixed",
            prefix_id="traj_fixed_pfx015",
            checkpoint_session_count=15,
            visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 16)),
            targets=(count_target,),
        ),
        Stage2Checkpoint(
            trajectory_id="traj_fixed",
            prefix_id="traj_fixed_pfx030",
            checkpoint_session_count=30,
            visible_session_ids=tuple(f"S{i:03d}" for i in range(1, 31)),
            targets=(count_target, amount_target),
        ),
    ]

    items = ItemBuilder(seed=0).build_stage2(checkpoints)

    assert [option.text for option in items[0].options] == [
        "1명", "2명", "3명", "4명"
    ]
    assert [option.text for option in items[1].options] == [
        "1명", "2명", "3명", "4명"
    ]
    assert [option.text for option in items[2].options] == [
        "3,000,000원", "4,000,000원", "5,000,000원", "7,000,000원"
    ]


def test_stage2_numeric_distractors_are_trajectory_local():
    from fin_life_benchmark.benchmark.mcq_input import Stage2Checkpoint, Stage2Target

    def target(trajectory_id, event_id, value):
        return Stage2Target(
            canonical_target_id=f"{trajectory_id}:{event_id}",
            trajectory_id=trajectory_id,
            target_event_instance_id=f"{trajectory_id}_ev001",
            target_event_id="crisis_health_event",
            target_event_label="건강 사건",
            memory_path="cashflow.recent_one_off_expense",
            operation="update",
            first_visible_checkpoint=15,
            evidence_sessions=("S015",),
            evidence_turns=("S015:2",),
            before_state={"value": None, "status": "not_applicable"},
            after_state={"value": {"amount_krw": value}, "status": "current"},
            value_selector="amount_krw",
            option_pool_type="numeric",
            option_pool=(100, 200, 300, 400, 500, 600, 700, 800),
        )

    local = target("traj_a", "ev_a", 100)
    foreign = target("traj_b", "ev_b", 900)
    checkpoints = [
        Stage2Checkpoint("traj_a", "pfx_a", 15, ("S001",), (local,)),
        Stage2Checkpoint("traj_b", "pfx_b", 15, ("S001",), (foreign,)),
    ]
    items = ItemBuilder(seed=0).build_stage2(checkpoints)

    assert all("900" not in option.text for option in items[0].options)
