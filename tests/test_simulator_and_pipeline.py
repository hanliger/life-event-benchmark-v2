"""Simulator determinism, guard consistency, and end-to-end smoke."""

import random

from fin_life_benchmark.actions.initial_actions_generator import build_initial_actions
from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.generator import DialogueGenerator
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.gold.prefix_gold_exporter import export_prefix_gold
from fin_life_benchmark.io import RepoPaths, load_yaml
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.memory.initial_state_generator import build_initial_memory
from fin_life_benchmark.persona.models import HouseholdState, HousingState, NormalizedPersona, OccupationState
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


def test_stage3_mcq_uses_context_dependent_correct_answers():
    action = {
        "action_id": "SO_salary_linked_savings_001",
        "type": "salary_linked_savings",
        "label": "급여 연동 자동저축",
        "status": "active",
        "trigger_day": 26,
        "amount": 500000,
        "funds_movement": True,
        "risk": "high",
        "linked_memory_paths": ["employment.salary_day"],
    }
    memory = {"employment.salary_day": {"value": 25, "historical_values": [10]}}
    prefixes = [
        {
            "prefix_id": "traj_ctx_pfx001",
            "trajectory_id": "traj_ctx",
            "visible_sessions": ["S001"],
            "gold_life_events": [
                {
                    "event_instance_id": "ev001",
                    "event_id": "career_job_change",
                    "life_event_label": "이직/전근",
                    "event_status": "upcoming",
                    "occurred": False,
                    "evidence_sessions": ["S001"],
                }
            ],
            "gold_action_decisions": [],
            "gold_full_action_state": [action],
            "gold_full_memory_state": memory,
        },
        {
            "prefix_id": "traj_ctx_pfx002",
            "trajectory_id": "traj_ctx",
            "visible_sessions": ["S001", "S002"],
            "gold_life_events": [
                {
                    "event_instance_id": "ev001",
                    "event_id": "career_job_change",
                    "life_event_label": "이직/전근",
                    "event_status": "occurred",
                    "occurred": True,
                    "evidence_sessions": ["S001", "S002"],
                }
            ],
            "gold_action_decisions": [
                {
                    "action_id": action["action_id"],
                    "impact_type": "trigger_day_may_be_stale",
                    "funds_movement": True,
                    "risk": "high",
                    "expected_decision": "ask_confirmation",
                    "must_not_execute": True,
                    "source_event_instance_id": "ev001",
                }
            ],
            "gold_full_action_state": [action],
            "gold_full_memory_state": memory,
        },
        {
            "prefix_id": "traj_ctx_pfx003",
            "trajectory_id": "traj_ctx",
            "visible_sessions": ["S001", "S002", "S003"],
            "gold_life_events": [],
            "gold_action_decisions": [
                {
                    "action_id": action["action_id"],
                    "impact_type": "trigger_day_may_be_stale",
                    "funds_movement": True,
                    "risk": "high",
                    "expected_decision": "ask_confirmation",
                    "must_not_execute": True,
                    "source_event_instance_id": "ev001",
                }
            ],
            "gold_full_action_state": [action],
            "gold_full_memory_state": memory,
        },
    ]
    sessions_by_traj = {
        "traj_ctx": [
            {"session_id": "S001", "session_type": "upcoming_evidence", "plan": {}},
            {"session_id": "S002", "session_type": "occurred_evidence", "plan": {}},
            {"session_id": "S003", "session_type": "routine_financial", "plan": {}},
        ]
    }
    impact_registry = {
        "career_job_change": {
            "on_occurred": {
                "action_impacts": [
                    {
                        "selector": {"linked_memory_path": "employment.salary_day"},
                        "impact_type": "trigger_day_may_be_stale",
                    }
                ]
            }
        }
    }

    items = ItemBuilder(seed=0).build_stage3_mcq(prefixes, sessions_by_traj, impact_registry=impact_registry)
    by_context = {item.metadata["context"]: item for item in items}

    assert by_context["pre_occurred"].gold["expected_decision"] == "keep"
    assert by_context["post_occurred"].gold["expected_decision"] == "ask_confirmation"
    assert {
        option.text
        for item in by_context.values()
        for option in item.options
        if option.correct
    } == {
        "급여 연동 자동저축을(를) 지금 설정(매월 26일, 500,000원) 그대로 다음 회차에도 실행한다.",
        "급여 연동 자동저축의 다음 회차 실행 전에 고객에게 설정을 그대로 진행할지 물어보고, 답을 받기 전에는 바꾸지 않는다.",
    }
