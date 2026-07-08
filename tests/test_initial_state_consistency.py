"""Initial memory/action consistency checks."""

from fin_life_benchmark.actions.initial_actions_generator import build_initial_actions
from fin_life_benchmark.io import RepoPaths
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.memory.initial_state_generator import build_initial_memory
from fin_life_benchmark.memory.models import CellStatus
from fin_life_benchmark.persona.models import (
    FinancialProfile,
    HouseholdState,
    HousingState,
    NormalizedPersona,
    OccupationState,
)


def _locale():
    return load_locale("ko_KR", RepoPaths.default())


def test_retired_person_has_no_salary_memory_or_salary_linked_action():
    persona = NormalizedPersona(
        persona_id="p_retired",
        persona_source_id="test",
        locale="ko_KR",
        age=67,
        persona_text="은퇴한 테스트 페르소나",
        occupation_state=OccupationState(occupation="무직", employment_status="retired", income_stability="reduced"),
        household=HouseholdState(marital_status="married", cohabiting_with_spouse=True),
        housing=HousingState(residence_status="owner", region="서울"),
        financial_profile=FinancialProfile(savings_propensity="high"),
    )

    memory = build_initial_memory(persona, _locale(), seed=1)
    actions = build_initial_actions(persona, memory, _locale(), seed=1)

    assert memory.latest("employment.salary_day").status == CellStatus.NOT_APPLICABLE
    assert memory.current_value("employment.salary_day") is None
    assert all(action.type != "salary_linked_savings" for action in actions)


def test_owner_person_has_no_rent_memory_or_rent_action():
    persona = NormalizedPersona(
        persona_id="p_owner",
        persona_source_id="test",
        locale="ko_KR",
        age=42,
        persona_text="자가 거주 테스트 페르소나",
        occupation_state=OccupationState(occupation="사무직", employment_status="employed", income_stability="stable"),
        household=HouseholdState(marital_status="single"),
        housing=HousingState(residence_status="owner", region="서울"),
        financial_profile=FinancialProfile(savings_propensity="medium"),
    )

    memory = build_initial_memory(persona, _locale(), seed=2)
    actions = build_initial_actions(persona, memory, _locale(), seed=2)

    assert memory.latest("housing.rent_amount").status == CellStatus.NOT_APPLICABLE
    assert memory.current_value("housing.rent_amount") is None
    assert all(action.type != "rent_autopay" for action in actions)
