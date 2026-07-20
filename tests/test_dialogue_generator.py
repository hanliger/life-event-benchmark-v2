"""LLM dialogue generation repair behavior."""

import json

import pytest

from fin_life_benchmark.dialogue.generator import DialogueGenerator, LLMOutputValidationError
from fin_life_benchmark.dialogue.models import (
    ActionExecutionContract,
    DialogueGenerationPlan,
    EvidenceDimension,
    PlannedCue,
    StaleMemoryPair,
)
from fin_life_benchmark.io import RepoPaths
from fin_life_benchmark.persona.models import HouseholdState, HousingState, NormalizedPersona, OccupationState


class FakeLLMClient:
    provider = "fake"

    def __init__(self, responses: list[str], metadata: list[dict] | None = None):
        self.responses = list(responses)
        self.metadata = list(metadata or [])
        self.last_response_metadata: dict = {}
        self.requests: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> str:
        self.requests.append((system, user))
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        self.last_response_metadata = self.metadata.pop(0) if self.metadata else {}
        return self.responses.pop(0)


def _persona() -> NormalizedPersona:
    return NormalizedPersona(
        persona_id="p_test",
        persona_source_id="test",
        locale="ko_KR",
        age=54,
        sex="여자",
        persona_text="테스트 페르소나",
        occupation_state=OccupationState(occupation="사무직", employment_status="employed", income_stability="stable"),
        household=HouseholdState(marital_status="single"),
        housing=HousingState(residence_status="wolse", region="서울 마포구"),
    )


def _plan() -> DialogueGenerationPlan:
    return DialogueGenerationPlan(
        session_id="S001",
        trajectory_id="traj_test",
        month_index=1,
        age=54,
        session_type="routine_financial",
        event_status_after_session="no_event",
        mapped_action="FA-01",
        financial_task="예금 금리 문의",
    )


def test_llm_session_repairs_turn_schema_typo(tmp_path):
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리 좀 알려주세요."},
        {"speaner": "assistant", "text": "네, 안내드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {"financial_task_clear": true}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리 좀 알려주세요."},
        {"speaker": "assistant", "text": "네, 안내드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {"financial_task_clear": true}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(_plan(), _persona(), tmp_path)

    assert session.turns[1].speaker == "assistant"
    assert (tmp_path / "traj_test_S001.txt").exists()
    assert (tmp_path / "traj_test_S001_repair.txt").exists()
    assert "turns[1] missing required key(s): speaker" in client.requests[1][1]


def test_llm_session_reports_invalid_repair(tmp_path):
    broken = '{"turns": [{"speaner": "assistant", "text": "오타"}]}'
    still_broken = '{"turns": [{"speaker": "banker", "text": "잘못된 화자"}]}'
    client = FakeLLMClient([broken, still_broken, still_broken, still_broken])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    with pytest.raises(LLMOutputValidationError, match="after 3 repair attempts"):
        generator._llm_session(_plan(), _persona(), tmp_path)


def test_llm_session_canonicalizes_action_resolution_planner_aliases(tmp_path):
    plan = _plan()
    plan.mapped_action = "FA-08"
    plan.action_execution_contract = ActionExecutionContract(
        action_mode="pending_required_information",
        required_slots=["source_account", "amount", "explicit_confirmation"],
        grounded_slots={"amount": 500000},
        missing_slots=["source_account"],
        completion_allowed=False,
        confirmation_required=True,
    )
    plan.structured_context = {"event": {"params": {"amount": 500000}}}
    response = """
    {
      "turns": [
        {"speaker": "user", "text": "50만원 정기이체를 알아보려고요."},
        {"speaker": "assistant", "text": "출금 계좌가 정해지기 전에는 실행하지 않고 대기하겠습니다."}
      ],
      "cue_annotations": [],
      "action_resolution": {
        "action_mode": "pending_required_information",
        "collected_slots": {"amount": 500000},
        "missing_slots": ["source_account"],
        "completion_allowed": false,
        "confirmation_required": true,
        "final_status": "pending"
      }
    }
    """
    client = FakeLLMClient([response])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(plan, _persona(), tmp_path)

    assert len(client.requests) == 1
    assert session.action_resolution.mode == "pending_required_information"
    assert session.action_resolution.provided_slots == {"amount": 500000}
    assert session.action_resolution.missing_slots == ["source_account"]


def test_llm_session_canonicalizes_dimension_cue_types(tmp_path):
    plan = _plan()
    plan.session_type = "weak_signal_evidence"
    plan.event_status_after_session = "weak_signal"
    plan.financial_task = "자동저축 설정 점검"
    plan.evidence_placement_strategy = "task_and_evidence_opening"
    plan.evidence_placement_slots = [0]
    plan.evidence_dimensions = [
        EvidenceDimension(
            dimension_id="possible_state_change",
            role="uncertainty",
            semantic_instruction_ko="현재 상태가 달라질 가능성을 드러낸다.",
        ),
        EvidenceDimension(
            dimension_id="possible_financial_adjustment",
            role="financial_consequence",
            semantic_instruction_ko="금융 설정 조정 가능성을 드러낸다.",
        ),
    ]
    response = """
    {
      "turns": [
        {"speaker": "user", "text": "상황이 달라질 수도 있어서 자동저축 설정을 조정할 수 있는지 점검해 주세요."},
        {"speaker": "assistant", "text": "현재 설정을 유지한 채 조정 가능한 범위를 확인하겠습니다."}
      ],
      "cue_annotations": [
        {"turn_index": 0, "cue_type": "task_intent", "evidence_text": "자동저축 설정을 조정할 수 있는지 점검해 주세요"},
        {"turn_index": 0, "cue_type": "event_signal", "evidence_text": "상황이 달라질 수도 있어서", "evidence_dimension_id": "possible_state_change"},
        {"turn_index": 0, "cue_type": "possible_financial_adjustment", "evidence_text": "자동저축 설정을 조정할 수 있는지", "evidence_dimension_id": "possible_financial_adjustment"}
      ]
    }
    """
    client = FakeLLMClient([response])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(plan, _persona(), tmp_path)

    assert len(client.requests) == 1
    assert [cue.cue_type for cue in session.cue_annotations[1:]] == [
        "uncertainty",
        "financial_consequence",
    ]


def test_llm_session_canonicalizes_stale_recall_cue_types(tmp_path):
    plan = _plan()
    plan.session_type = "stale_recall_session"
    plan.target_memory_paths = ["employment.employment_status"]
    plan.stale_memory_pairs = [
        StaleMemoryPair(
            path="employment.employment_status",
            old_value="unemployed",
            current_value="employed",
        )
    ]
    response = """
    {
      "turns": [
        {"speaker": "user", "text": "예전에는 소득이 없었지만 지금은 일하고 있어서 현재 상태를 확인하고 싶어요."},
        {"speaker": "assistant", "text": "과거 기록과 현재 유효한 상태를 구분해 확인하겠습니다."}
      ],
      "cue_annotations": [
        {"turn_index": 0, "cue_type": "event_signal", "linked_memory_path": "employment.employment_status", "linked_memory_value": "unemployed", "evidence_text": "예전에는 소득이 없었지만"},
        {"turn_index": 0, "cue_type": "event_evidence", "linked_memory_path": "employment.employment_status", "linked_memory_value": "employed", "evidence_text": "지금은 일하고 있어서"}
      ]
    }
    """
    client = FakeLLMClient([response])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(plan, _persona(), tmp_path)

    assert len(client.requests) == 1
    assert [cue.cue_type for cue in session.cue_annotations] == [
        "stale_value",
        "current_value",
    ]


def test_non_binding_surface_hint_is_not_sent_to_provider():
    plan = _plan()
    plan.planned_cues = [
        PlannedCue(
            cue_id="example",
            semantic_instruction_ko="시점과 금융 결과를 자연스럽게 드러낸다.",
            status="occurred",
            cue_role="event_signal",
            surface_hint="이번에 실제로 반영돼 금융 설정을 정리하려고요",
            exact_surface_required=False,
        )
    ]
    generator = DialogueGenerator(paths=RepoPaths.default())

    prompt = generator._build_prompt(plan, _persona())
    repair_contract = generator._build_repair_constraints(plan)

    assert "이번에 실제로 반영" not in prompt
    assert "이번에 실제로 반영" not in repair_contract


def test_llm_session_repairs_dialogue_that_ends_with_user(tmp_path):
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리 좀 알려주세요."},
        {"speaker": "assistant", "text": "네, 안내드리겠습니다."},
        {"speaker": "user", "text": "감사합니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리 좀 알려주세요."},
        {"speaker": "assistant", "text": "네, 안내드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(_plan(), _persona(), tmp_path)

    assert session.turns[-1].speaker == "assistant"
    assert "dialogue must end with an assistant turn" in client.requests[1][1]


def test_llm_session_repairs_invented_linked_memory_path(tmp_path):
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "여행 경비 모으는 통장 하나 보고 있어요."},
        {"speaker": "assistant", "text": "네, 목적자금 통장으로 안내드리겠습니다."}
      ],
      "cue_annotations": [
        {"turn_index": 0, "cue_type": "near_miss", "linked_memory_path": "life_event.travel_fund.no_event"}
      ],
      "quality_self_check": {}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "여행 경비 모으는 통장 하나 보고 있어요."},
        {"speaker": "assistant", "text": "네, 목적자금 통장으로 안내드리겠습니다."}
      ],
      "cue_annotations": [
        {"turn_index": 0, "cue_type": "near_miss", "linked_memory_path": null}
      ],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(_plan(), _persona(), tmp_path)

    assert session.cue_annotations[0].linked_memory_path is None
    assert "linked_memory_path must be null or one of target_memory_paths" in client.requests[1][1]


def test_llm_session_repairs_dialogue_conflicting_with_retired_persona(tmp_path):
    persona = _persona()
    persona.occupation_state.employment_status = "retired"
    plan = _plan()
    plan.structured_context = {"event": {"params": {"payment_day": 25}}}
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "월급 들어오는 다음 날 자동이체하고 싶어요."},
        {"speaker": "assistant", "text": "네, 급여일 다음 날로 설정해드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "매달 25일에 자동이체하고 싶어요."},
        {"speaker": "assistant", "text": "네, 매달 25일로 설정해드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(plan, persona, tmp_path)

    visible = " ".join(turn.text for turn in session.turns)
    assert "월급" not in visible
    assert "employment_status=retired" in client.requests[1][1]


def test_llm_session_repairs_dialogue_validator_violations(tmp_path):
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리 좀 알려주세요."},
        {"speaker": "assistant", "text": "네, 안내드리겠습니다."},
        {"speaker": "user", "text": "ㅇㅇ 좋아요"},
        {"speaker": "assistant", "text": "확인했습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리 좀 알려주세요."},
        {"speaker": "assistant", "text": "네, 안내드리겠습니다."},
        {"speaker": "user", "text": "네 좋아요"},
        {"speaker": "assistant", "text": "확인했습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(_plan(), _persona(), tmp_path)

    visible = " ".join(turn.text for turn in session.turns)
    assert "ㅇㅇ" not in visible
    assert "dialogue validator violations" in client.requests[1][1]


def test_llm_session_repairs_hard_negative_memory_fact(tmp_path):
    plan = _plan()
    plan.session_type = "hard_negative"
    plan.expected_memory_operation = "no_update"
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "동호회 회비 계좌를 확인하려고요."},
        {"speaker": "assistant", "text": "네, 확인해 드릴게요."}
      ],
      "cue_annotations": [
        {
          "turn_index": 0,
          "cue_type": "memory_fact",
          "linked_memory_path": null,
          "linked_memory_operation": "update",
          "linked_memory_value": "변경"
        }
      ],
      "quality_self_check": {}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "동호회 회비 계좌를 확인하려고요."},
        {"speaker": "assistant", "text": "네, 확인해 드릴게요."}
      ],
      "cue_annotations": [
        {"turn_index": 0, "cue_type": "near_miss", "linked_memory_path": null}
      ],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(plan, _persona(), tmp_path)

    assert all(cue.cue_type != "memory_fact" for cue in session.cue_annotations)
    assert "hard_negative_unintended_update" in client.requests[1][1]
    assert '"expected_memory_operation": "no_update"' in client.requests[1][1]


def test_repair_uses_compact_constraints_and_cumulative_violations(tmp_path):
    plan = _plan()
    plan.must_not_include_terms = ["집주인"]
    plan.target_memory_paths = ["housing.rent_payee"]
    plan.structured_context = {
        "padding": "x" * 10_000,
        "session_memory_updates": [
            {
                "path": "housing.rent_payee",
                "operation": "archive",
                "new_value": None,
            }
        ],
    }
    invalid_json = '{"turns": [{"speaker": "user"'
    forbidden = """
    {
      "turns": [
        {"speaker": "user", "text": "집주인에게 보내던 납부는 끝났어요."},
        {"speaker": "assistant", "text": "네, 확인했습니다."}
      ],
      "cue_annotations": [
        {
          "turn_index": 0,
          "cue_type": "memory_fact",
          "linked_memory_path": "housing.rent_payee",
          "linked_memory_operation": "archive",
          "linked_memory_value": null,
          "evidence_text": "집주인에게 보내던 납부는 끝났어요"
        }
      ],
      "quality_self_check": {}
    }
    """
    repaired = forbidden.replace("집주인에게", "이전 납부처에")
    client = FakeLLMClient([invalid_json, forbidden, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(plan, _persona(), tmp_path)

    assert "집주인" not in " ".join(turn.text for turn in session.turns)
    final_repair_prompt = client.requests[2][1]
    assert "no JSON object" in final_repair_prompt
    assert "forbidden_term" in final_repair_prompt
    assert '"must_not_include_terms": [\n    "집주인"' in final_repair_prompt
    assert '"path": "housing.rent_payee"' in final_repair_prompt


def test_llm_session_repairs_ungrounded_concrete_value(tmp_path):
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리를 알려주세요."},
        {"speaker": "assistant", "text": "현재 금리는 3.5%입니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리를 알려주세요."},
        {"speaker": "assistant", "text": "현재 적용 가능한 금리를 조회해 드릴게요."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(_plan(), _persona(), tmp_path)

    assert "3.5" not in " ".join(turn.text for turn in session.turns)
    assert "concrete_value_hallucination" in client.requests[1][1]


def test_initial_prompt_lists_allowed_concrete_values():
    plan = _plan()
    plan.structured_context = {
        "event": {"params": {"payment_day": 25, "amount_krw": 350_000}}
    }
    generator = DialogueGenerator(paths=RepoPaths.default())

    prompt = generator._build_prompt(plan, _persona())

    assert "대화에서 사용할 수 있는 숫자의 정규화 목록" in prompt
    assert '"25"' in prompt
    assert '"350000"' in prompt


def test_llm_session_normalizes_planner_style_cue_aliases(tmp_path):
    plan = _plan()
    plan.target_memory_paths = ["employment.employment_status"]
    plan.structured_context = {
        "session_memory_updates": [
            {
                "path": "employment.employment_status",
                "operation": "update",
                "new_value": "on_leave",
            }
        ]
    }
    raw = """
    {
      "turns": [
        {"speaker": "user", "text": "회사 소속은 유지되고 지금은 쉬는 중이에요."},
        {"speaker": "assistant", "text": "네, 현재 상태를 확인하겠습니다."}
      ],
      "cue_annotations": [
        {
          "turn_index": 0,
          "cue_id": "memory_fact_employment_status",
          "cue_role": "memory_fact",
          "path": "employment.employment_status",
          "operation": "update",
          "value": "on_leave",
          "evidence_text": "회사 소속은 유지되고 지금은 쉬는 중이에요"
        }
      ],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([raw])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(plan, _persona(), tmp_path)

    fact = session.cue_annotations[0]
    assert fact.cue_type == "memory_fact"
    assert fact.linked_memory_path == "employment.employment_status"
    assert fact.linked_memory_operation == "update"
    assert fact.linked_memory_value == "on_leave"


def test_llm_session_repairs_provider_token_truncation(tmp_path):
    truncated = '{"turns": [{"speaker": "user", "text": "잘린 응답"}'
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리를 확인하고 싶어요."},
        {"speaker": "assistant", "text": "네, 조회해 드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient(
        [truncated, repaired],
        metadata=[{"stop_reason": "max_tokens"}, {"stop_reason": "end_turn"}],
    )
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(_plan(), _persona(), tmp_path)

    assert session.turns[-1].speaker == "assistant"
    assert "truncated at the token limit" in client.requests[1][1]


def test_public_generation_repairs_out_of_range_turn_count(tmp_path):
    def payload(turn_count: int) -> str:
        turns = [
            {
                "speaker": "user" if index % 2 == 0 else "assistant",
                "text": (
                    f"예금 상품 조건을 확인하고 싶어요 항목 {index}"
                    if index % 2 == 0
                    else f"네 해당 조건을 순서대로 안내하겠습니다 항목 {index}"
                ),
            }
            for index in range(turn_count)
        ]
        return json.dumps(
            {"turns": turns, "cue_annotations": [], "quality_self_check": {}},
            ensure_ascii=False,
        )

    plan = _plan()
    plan.structured_context = {
        "event": {"params": {"allowed_indices": list(range(8))}}
    }
    client = FakeLLMClient([payload(6), payload(8)])
    generator = DialogueGenerator(
        mode="llm",
        client=client,
        paths=RepoPaths.default(),
        raw_output_dir=tmp_path,
    )

    session = generator.generate_session(plan, _persona())

    assert session is not None
    assert len(session.turns) == 8
    assert sum(turn.speaker == "user" for turn in session.turns) == 4
    assert "turn count must be 8..8, got 6" in client.requests[1][1]


def test_mock_session_is_exactly_eight_turns_and_opens_with_evidence():
    plan = _plan()
    plan.session_type = "occurred_evidence"
    plan.event_status_after_session = "occurred"
    plan.must_include_cues = ["새 납부 정보"]
    generator = DialogueGenerator(mode="mock", paths=RepoPaths.default())

    session = generator.generate_session(plan, _persona())

    assert session is not None
    assert len(session.turns) == 8
    assert [turn.speaker for turn in session.turns] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert "예금 금리 문의" in session.turns[0].text
    assert "새 납부 정보" in session.turns[0].text
    assert any(
        cue.turn_index == 0 and cue.cue_type != "memory_fact"
        for cue in session.cue_annotations
    )


def test_llm_session_normalizes_one_based_cue_index(tmp_path):
    raw = """
    {
      "turns": [
        {"speaker": "user", "text": "여행 경비 모으는 통장 하나 보고 있어요."},
        {"speaker": "assistant", "text": "네, 목적자금 통장으로 안내드리겠습니다."}
      ],
      "cue_annotations": [
        {"turn_index": 1, "cue_type": "near_miss", "linked_memory_path": null}
      ],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([raw])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(_plan(), _persona(), tmp_path)

    assert session.cue_annotations[0].turn_index == 0
    assert len(client.requests) == 1


def test_successful_llm_session_removes_stale_repair_file(tmp_path):
    (tmp_path / "traj_test_S001_repair.txt").write_text("old repair", encoding="utf-8")
    raw = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리 좀 알려주세요."},
        {"speaker": "assistant", "text": "네, 안내드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([raw])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    generator._llm_session(_plan(), _persona(), tmp_path)

    assert not (tmp_path / "traj_test_S001_repair.txt").exists()


def test_llm_session_writes_raw_response_metadata(tmp_path):
    raw = """
    {
      "turns": [
        {"speaker": "user", "text": "예금 금리 좀 알려주세요."},
        {"speaker": "assistant", "text": "네, 안내드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient(
        [raw],
        metadata=[
            {
                "provider": "anthropic",
                "model": "claude-sonnet-5",
                "stop_reason": "end_turn",
                "content_block_types": ["text"],
            }
        ],
    )
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    generator._llm_session(_plan(), _persona(), tmp_path)

    metadata = json.loads((tmp_path / "traj_test_S001.meta.json").read_text(encoding="utf-8"))
    assert metadata["stop_reason"] == "end_turn"
    assert metadata["content_block_types"] == ["text"]


def test_llm_session_repairs_offline_branch_style_dialogue(tmp_path):
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "생활비 계좌를 정리하고 싶어요."},
        {"speaker": "assistant", "text": "알겠습니다. 신청서 작성 도와드리겠습니다. 잠시 안내 창구로 모시겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "생활비 계좌를 정리하고 싶어요."},
        {"speaker": "assistant", "text": "알겠습니다. 앱에서 계좌 관리 메뉴로 이동해 설정을 도와드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(_plan(), _persona(), tmp_path)

    visible = " ".join(turn.text for turn in session.turns)
    assert "창구" not in visible
    assert "모시겠습니다" not in visible
    assert "online banking/chatbot style" in client.requests[1][1]


def test_weak_signal_allows_financial_product_confirmation_language():
    generator = DialogueGenerator(paths=RepoPaths.default())
    session = {
        "turns": [
            {"speaker": "user", "text": "예금 금리 좀 알려주세요."},
            {"speaker": "assistant", "text": "정확한 금리는 실제 신청 시 확정됩니다."},
        ],
        "event_status_after_session": "weak_signal",
        "session_type": "pre_event_signal",
        "mapped_action": "FA-01",
        "cue_annotations": [],
        "plan": {"must_include_cues": [], "must_not_include_terms": [], "target_memory_paths": []},
    }

    violations = generator.validator.validate_session(session)

    assert "weak_signal_overcommitted" not in {violation["code"] for violation in violations}


def test_weak_signal_flags_event_confirmation_language():
    generator = DialogueGenerator(paths=RepoPaths.default())
    session = {
        "turns": [
            {"speaker": "user", "text": "생활비 계좌를 미리 정리해두려고요."},
            {"speaker": "assistant", "text": "이미 확정된 일정이면 자동이체 변경을 이어서 안내드리겠습니다."},
        ],
        "event_status_after_session": "weak_signal",
        "session_type": "pre_event_signal",
        "mapped_action": "FA-01",
        "cue_annotations": [],
        "plan": {"must_include_cues": [], "must_not_include_terms": [], "target_memory_paths": []},
    }

    violations = generator.validator.validate_session(session)

    assert "weak_signal_overcommitted" in {violation["code"] for violation in violations}


def test_llm_session_repairs_missing_required_cue_annotation(tmp_path):
    plan = _plan()
    plan.must_include_cues = ["새 주소"]
    plan.target_memory_paths = ["housing.address"]
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "새 주소로 안내문 받는 곳도 바꿔야 할 것 같아요."},
        {"speaker": "assistant", "text": "네, 안내문 수령 주소 확인을 도와드리겠습니다."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "새 주소로 안내문 받는 곳도 바꿔야 할 것 같아요."},
        {"speaker": "assistant", "text": "네, 안내문 수령 주소 확인을 도와드리겠습니다."}
      ],
      "cue_annotations": [
        {"turn_index": 0, "cue_type": "address_update_reference", "linked_memory_path": "housing.address"}
      ],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(plan, _persona(), tmp_path)

    assert session.cue_annotations[0].turn_index == 0
    assert session.cue_annotations[0].linked_memory_path == "housing.address"
    assert "cue_annotations must include at least one user-turn annotation" in client.requests[1][1]


def test_llm_session_requires_exact_memory_fact_grounding(tmp_path):
    plan = _plan()
    plan.target_memory_paths = ["employment.salary_day"]
    plan.structured_context = {
        "session_memory_updates": [
            {
                "path": "employment.salary_day",
                "operation": "update",
                "new_value": 25,
            }
        ]
    }
    broken = """
    {
      "turns": [
        {"speaker": "user", "text": "급여 계좌를 확인하려고요."},
        {"speaker": "assistant", "text": "네, 확인해 드릴게요."}
      ],
      "cue_annotations": [],
      "quality_self_check": {}
    }
    """
    repaired = """
    {
      "turns": [
        {"speaker": "user", "text": "급여가 매달 25일에 들어와요."},
        {"speaker": "assistant", "text": "네, 입금 날짜를 확인해 드릴게요."}
      ],
      "cue_annotations": [
        {
          "turn_index": 0,
          "cue_type": "memory_fact",
          "linked_memory_path": "employment.salary_day",
          "linked_memory_operation": "update",
          "linked_memory_value": 25,
          "evidence_text": "급여가 매달 25일에 들어와요"
        }
      ],
      "quality_self_check": {}
    }
    """
    client = FakeLLMClient([broken, repaired])
    generator = DialogueGenerator(mode="llm", client=client, paths=RepoPaths.default())

    session = generator._llm_session(plan, _persona(), tmp_path)

    fact = session.cue_annotations[0]
    assert fact.linked_memory_operation == "update"
    assert fact.linked_memory_value == 25
    assert fact.evidence_text == "급여가 매달 25일에 들어와요"
    assert "ground every session_memory_update" in client.requests[1][1]


def test_validator_flags_missing_required_cue_annotation():
    generator = DialogueGenerator(paths=RepoPaths.default())
    session = {
        "turns": [
            {"speaker": "user", "text": "새 주소로 안내문 받는 곳도 바꿔야 할 것 같아요."},
            {"speaker": "assistant", "text": "네, 안내문 수령 주소 확인을 도와드리겠습니다."},
        ],
        "event_status_after_session": "occurred",
        "session_type": "consequence_session",
        "mapped_action": "FA-01",
        "cue_annotations": [],
        "plan": {
            "must_include_cues": ["새 주소"],
            "must_not_include_terms": [],
            "target_memory_paths": ["housing.address"],
        },
    }

    violations = generator.validator.validate_session(session)

    assert "missing_cue_annotation" in {violation["code"] for violation in violations}


def test_validator_requires_required_cue_in_user_turn():
    generator = DialogueGenerator(paths=RepoPaths.default())
    session = {
        "turns": [
            {"speaker": "user", "text": "여러 건을 정리할 게 좀 있어서요."},
            {"speaker": "assistant", "text": "부의금 정리 때문에 확인이 필요하신 거군요."},
        ],
        "event_status_after_session": "weak_signal",
        "session_type": "weak_signal_evidence",
        "mapped_action": "FA-01",
        "cue_annotations": [{"turn_index": 0, "cue_type": "generic", "linked_memory_path": None}],
        "plan": {
            "must_include_cues": ["부의금 정리"],
            "must_not_include_terms": [],
            "target_memory_paths": [],
        },
    }

    violations = generator.validator.validate_session(session)

    codes = {violation["code"] for violation in violations}
    assert "required_cue_not_in_user_turn" in codes
    assert "required_cue_not_annotated" in codes


def test_validator_requires_required_cue_in_annotated_user_turn():
    generator = DialogueGenerator(paths=RepoPaths.default())
    session = {
        "turns": [
            {"speaker": "user", "text": "새 주소로 안내문 받는 곳도 바꿔야 할 것 같아요."},
            {"speaker": "assistant", "text": "네, 안내문 수령 주소 확인을 도와드리겠습니다."},
            {"speaker": "user", "text": "그럼 알림도 같이 설정해 주세요."},
            {"speaker": "assistant", "text": "네, 알림 설정도 확인하겠습니다."},
        ],
        "event_status_after_session": "occurred",
        "session_type": "occurred_evidence",
        "mapped_action": "FA-01",
        "cue_annotations": [{"turn_index": 2, "cue_type": "wrong_turn", "linked_memory_path": "housing.address"}],
        "plan": {
            "must_include_cues": ["새 주소"],
            "must_not_include_terms": [],
            "target_memory_paths": ["housing.address"],
        },
    }

    violations = generator.validator.validate_session(session)

    assert "required_cue_not_annotated" in {violation["code"] for violation in violations}
