"""LLM dialogue generation repair behavior."""

import json

import pytest

from fin_life_benchmark.dialogue.generator import DialogueGenerator, LLMOutputValidationError
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan
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

    session = generator._llm_session(_plan(), persona, tmp_path)

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
