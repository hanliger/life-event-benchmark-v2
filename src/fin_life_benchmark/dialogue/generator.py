"""Generate banking sessions from DialogueGenerationPlans.

Modes:
  mock    — deterministic template dialogues, no API (default for smoke runs)
  dry_run — write the would-be prompts to raw_model_outputs, produce nothing
  llm     — call the configured provider (OpenAI/Anthropic) via LLMClient

Visible dialogue must never contain event labels, FA codes, or metadata.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..io import RepoPaths
from ..io import load_yaml
from ..llm.client import LLMClient
from ..persona.models import NormalizedPersona
from ..fsm.registry import load_life_event_templates
from ..validation.dialogue_validator import DialogueValidator, grounded_concrete_values
from .models import (
    ActionResolution,
    CueAnnotation,
    DialogueGenerationPlan,
    QualitySelfCheck,
    Session,
    Turn,
)

_HIGH_RISK_FA = {"FA-07", "FA-08", "FA-09", "FA-10"}
_REVIEW_ONLY_VALIDATION_CODES = {"near_direct_event_disclosure"}

_ASSISTANT_OPENINGS = [
    "네, 요청하신 범위에서 현재 상태부터 확인하겠습니다.",
    "알겠습니다. 해당 업무의 현재 설정을 먼저 살펴볼게요.",
    "네, 앱에서 처리할 수 있는 범위를 우선 확인하겠습니다.",
    "요청 내용을 확인했습니다. 필요한 항목만 차례로 볼게요.",
    "네, 기존 상태와 선택 가능한 절차를 먼저 확인하겠습니다.",
    "알겠습니다. 이 업무에 필요한 정보부터 점검하겠습니다.",
]

_ASSISTANT_MIDDLES = [
    "현재 기준으로 확인한 뒤 선택이 필요한 부분만 안내드릴게요.",
    "조회 결과를 바탕으로 이 업무에 필요한 단계만 이어가겠습니다.",
    "불필요한 변경 없이 요청하신 항목만 확인해 보겠습니다.",
    "적용 전 확인할 조건을 정리해서 보여드릴게요.",
    "지금 설정을 유지한 채 가능한 선택지를 먼저 살펴보겠습니다.",
    "확인 결과에서 고객님이 결정할 부분만 구분해 드리겠습니다.",
]

_ASSISTANT_CONFIRMATIONS = [
    "최종 반영 전에는 변경 범위와 주의사항을 다시 보여드리겠습니다.",
    "마지막 단계에서 요청 내용이 맞는지 한 번 더 확인하겠습니다.",
    "적용되는 항목만 요약한 뒤 고객님 확인을 받겠습니다.",
    "처리 전 화면에서 대상과 범위를 다시 확인할 수 있습니다.",
    "확정하기 전에 바뀌는 부분을 따로 안내드리겠습니다.",
    "마지막 확인 전까지는 현재 설정이 그대로 유지됩니다.",
]

_ASSISTANT_CLOSINGS = [
    "요청하신 범위의 처리가 끝났습니다.",
    "해당 업무를 요청하신 내용대로 마쳤습니다.",
    "확인하신 범위로 처리가 완료됐습니다.",
    "선택하신 내용만 반영해 마무리했습니다.",
]
_ASSISTANT_CLOSINGS_HIGH_RISK = [
    "출금이 발생하는 항목이라 지금 바로 실행하지 않고, 고객님 최종 확인 후 진행됩니다.",
    "자금 이동이 포함돼 현재는 준비만 됐으며, 확인 화면에서 승인해야 실행됩니다.",
    "이 요청은 출금 전 최종 확인이 필요하며, 승인하기 전에는 반영되지 않습니다.",
    "금액이 이동하는 변경이라 확인 단계까지 안내했고, 고객님 승인 후에만 처리됩니다.",
]

_DETAIL_USER = [
    "현재 설정 기준으로 확인해 주세요",
    "앱에서 진행할 수 있는 범위로 부탁드려요",
    "요청한 항목만 기준으로 봐주세요",
    "기존 상태를 먼저 확인하고 진행해 주세요",
    "필요한 선택 항목만 알려주세요",
    "지금 적용된 조건부터 보여주세요",
]

_CONFIRMATION_USER = [
    "마지막 적용 전에 바뀌는 부분을 보여주세요",
    "제가 확인해야 할 단계도 같이 안내해 주세요",
    "처리 범위를 다시 확인한 뒤 결정할게요",
    "현재 설정과 달라지는 항목만 알려주세요",
    "최종 확인 전까지는 그대로 두면 됩니다",
    "적용 전에 주의할 점도 확인할게요",
]

_MOCK_OPENING_PREFIXES = [
    "먼저", "우선", "오늘은", "지금은", "일단", "앱에서", "현재 기준으로", "이번에는",
    "필요한 범위에서", "가능한 절차부터", "다른 변경 없이", "기존 설정을 둔 채", "제가 원하는 범위만",
    "관련 항목 가운데", "처리 전에", "선택할 내용만", "현재 화면에서", "급한 것부터", "이 기능으로",
    "지금 적용된 기준에서",
]
_MOCK_OPENING_SUFFIXES = [
    "확인하고 싶어요", "살펴봐 주세요", "진행 방법을 알려주세요", "필요한 단계만 보고 싶어요",
    "가능한 범위를 확인해 주세요", "현재 상태부터 보여주세요", "바뀌는 항목을 알고 싶어요",
    "앱에서 처리하려고요", "선택지를 보고 결정할게요", "적용 전 내용을 확인할게요",
    "요청 범위만 점검해 주세요", "기존 값과 비교해 주세요", "필요한 조건을 알려주세요",
    "어디까지 가능한지 볼게요", "관련 설정을 찾아주세요", "지금 상태를 기준으로 볼게요",
    "변경 없이 먼저 조회할게요", "처리 순서를 확인해 주세요", "주의할 항목도 함께 볼게요",
    "해당 메뉴에서 시작하려고요",
]

_CUE_WRAPPER_BY_STATUS = {
    "weak_signal": "아직 확정된 건 아닌데 {cue} 쪽을 미리 알아보려고요",
    "upcoming": "다음 달쯤 {cue} 건이 있어서요",
    "occurred": "이번에 {cue} 건이 생겨서 정리하려고요",
    "cancelled": "지난번에 말씀드렸던 {cue} 건은 없던 일이 됐어요",
    "no_event": "{cue} 관련해서 확인 부탁드려요",
}

_OFFLINE_BANKING_TERMS = [
    "창구",
    "영업점",
    "방문",
    "모시겠습니다",
    "신분증 지참",
    "실물 신분증",
    "신청서",
    "서명",
    "출력",
    "우편 발송",
    "우편 배송",
    "배송",
    "방문 수령",
    "창구 수령",
    "실물 수령",
]

_MEMORY_LABELS_KO = {
    "cashflow.recent_one_off_expense": "이번 일회성 지출",
    "education.child_education_stage": "자녀 교육 단계",
    "education.self_education_status": "본인 교육 상태",
    "employment.employer": "직장",
    "employment.employment_status": "고용 상태",
    "employment.income_stability": "소득 안정성",
    "employment.salary_account": "급여 계좌",
    "employment.salary_day": "급여 입금 날짜",
    "financial_products.loans": "대출 정보",
    "financial_products.pension_or_irp": "연금 수령 상태",
    "goals.child_education_goal": "자녀 교육 목표",
    "goals.retirement_goal": "은퇴 준비 목표",
    "household.child_support_arrangement": "양육비 약정",
    "household.children": "자녀 나이",
    "household.dependents": "부양가족 수",
    "household.marital_status": "혼인 상태",
    "household.spouse_or_partner": "배우자 정보",
    "housing.address": "거주 주소",
    "housing.contract_type": "주거 계약 유형",
    "housing.maintenance_fee_payee": "관리비 납부처",
    "housing.mortgage_status": "주택담보대출 상태",
    "housing.primary_residence_property_id": "주 거주 주택",
    "housing.properties": "보유 주택",
    "housing.rent_amount": "월세 금액",
    "housing.rent_payee": "월세 납부처",
    "housing.residence_status": "주거 상태",
}

_VALUE_LABELS_KO = {
    "active": "활성",
    "closed": "종료",
    "divorced": "혼인 관계 종료",
    "employed": "재직",
    "enrolled": "재학",
    "family_home": "가족 주택 거주",
    "jeonse": "전세",
    "main_checking": "주거래 입출금 계좌",
    "married": "기혼",
    "owner": "자가",
    "planning": "준비 중",
    "receiving": "수령 중",
    "reduced": "감소",
    "retired": "퇴직 후 상태",
    "self_employed": "자영업",
    "separated": "별도 거주",
    "stable": "안정",
    "study_abroad": "해외 교육 과정",
    "unemployed": "미재직",
    "unstable": "불안정",
    "widowed": "사별",
    "wolse": "월세",
}


def _visible_memory_value(value: Any) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니요"
    if isinstance(value, int):
        return f"{value:,}원"
    if isinstance(value, list):
        return ", ".join(_visible_memory_value(item) for item in value) if value else "없음"
    if isinstance(value, dict):
        if "amount_krw" in value:
            category = {
                "medical": "의료비",
                "accident_or_disaster": "긴급 복구비",
                "fraud_loss": "사기 피해액",
                "funeral": "장례 비용",
            }.get(value.get("category"), "비용")
            return f"{category} {int(value['amount_krw']):,}원"
        return ", ".join(f"{key}={item}" for key, item in sorted(value.items()))
    return _VALUE_LABELS_KO.get(str(value), str(value))


def _memory_fact_text(update: dict[str, Any]) -> str:
    path = str(update.get("path"))
    label = _MEMORY_LABELS_KO.get(path, path.replace(".", " "))
    operation = str(update.get("operation"))
    value = _visible_memory_value(update.get("new_value"))
    if operation == "set_pending":
        return f"{label}은 {value}(으)로 바뀔 예정이에요"
    if operation == "clear_pending":
        return f"말씀드렸던 {label} 변경 계획은 취소됐어요"
    if operation in {"archive", "mark_stale"}:
        return f"기존 {label} 정보는 이제 사용하지 않아요"
    if operation == "set_not_applicable":
        return f"{label}은 이제 해당되지 않아요"
    return f"{label}은 지금 {value}예요"


def _mock_dimension_text(dimension_id: str, role: str, status: str) -> str:
    """Safe indirect surfaces for offline tests; IDs stay annotation-only."""
    if dimension_id == "guardian_family_registration_transition":
        return "병원 관련 절차가 아니라 보호자와 가족관계 등록 기준으로 확인해 주세요"
    if dimension_id == "employment_relationship_remains":
        return "정기 입금은 멈췄지만 재직 정보 자체는 유지되고 있어요"
    surfaces = {
        "uncertainty": "현재 조건이 계속될지 몰라 연결된 금융 설정을 점검하고 싶어요",
        "future_timing": "지금 값은 그대로고 정해진 시점 뒤에 새 설정이 유효해져요",
        "state_change": "은행에 등록된 현재 정보가 예전 값과 달라진 걸 확인했어요",
        "entity_change": "가족 금융 등록 대상이 한 명 늘어 관련 설정이 필요해요",
        "financial_consequence": "관련 입금이나 납부 흐름이 달라져 금융 설정을 확인해야 해요",
        "prior_current_contrast": "준비했던 값 대신 지금 유효한 설정을 유지해야 해요",
        "cancellation": "준비하던 변경은 진행하지 않기로 했어요",
        "subtype_disambiguation": "보호자와 가족관계 등록 기준으로 금융 설정을 확인해 주세요",
    }
    return surfaces.get(role, f"현재 금융 설정과 연결된 변화 가능성을 확인해 주세요")


def _mock_task_opening(plan: DialogueGenerationPlan, safe_task: str) -> str:
    try:
        ordinal = max(0, int(plan.session_id.removeprefix("S")) - 1)
    except ValueError:
        ordinal = sum(ord(char) for char in plan.session_id)
    prefix = _MOCK_OPENING_PREFIXES[ordinal % len(_MOCK_OPENING_PREFIXES)]
    suffix = _MOCK_OPENING_SUFFIXES[
        (ordinal // len(_MOCK_OPENING_PREFIXES))
        % len(_MOCK_OPENING_SUFFIXES)
    ]
    return f"{prefix} {safe_task} 업무를 {suffix}"


class LLMOutputValidationError(ValueError):
    """Raised when an LLM response is JSON but not a valid session payload."""


def _slugify(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "_", text).strip("_")[:40]


def _contains_any(text: str, terms: list[str]) -> str | None:
    for term in terms:
        if term and term in text:
            return term
    return None


def _context_memory_value(plan: DialogueGenerationPlan, path: str) -> Any:
    cell = (plan.structured_context.get("current_memory") or {}).get(path) or {}
    if not isinstance(cell, dict):
        return None
    return cell.get("value")


def _context_life_state_value(plan: DialogueGenerationPlan, key: str) -> Any:
    life_state = (
        (plan.structured_context.get("current_state") or plan.structured_context.get("persona_state") or {})
        .get("life_state")
        or {}
    )
    if not isinstance(life_state, dict):
        return None
    return life_state.get(key)


def _session_context_value(plan: DialogueGenerationPlan, path: str, fallback: Any) -> Any:
    value = _context_memory_value(plan, path)
    if value is not None:
        return value
    value = _context_life_state_value(plan, path.split(".")[-1])
    if value is not None:
        return value
    return fallback


class DialogueGenerator:
    def __init__(
        self,
        mode: str = "mock",
        client: LLMClient | None = None,
        paths: RepoPaths | None = None,
        raw_output_dir: Path | None = None,
        raw_filename_suffix: str = "",
    ):
        if mode not in {"mock", "dry_run", "llm"}:
            raise ValueError(f"unknown dialogue mode: {mode}")
        self.mode = mode
        self.client = client
        self.paths = paths or RepoPaths.default()
        self.raw_output_dir = raw_output_dir
        self.raw_filename_suffix = raw_filename_suffix
        self.cfg = load_yaml(self.paths.generation / "dialogue.yaml")
        self.prompt_template = (self.paths.prompts / "dialogue" / "generate_banking_session_ko.md").read_text(encoding="utf-8")
        self.repair_template = (self.paths.prompts / "dialogue" / "repair_banking_session_ko.md").read_text(encoding="utf-8")
        self.validator = DialogueValidator(load_life_event_templates(self.paths))

    # ------------------------------------------------------------------ mock
    def _mock_session(self, plan: DialogueGenerationPlan, persona: NormalizedPersona) -> Session:
        rng = random.Random(f"{plan.trajectory_id}:{plan.session_id}:mock")
        turns: list[Turn] = []
        cues: list[CueAnnotation] = []
        safe_task = plan.financial_task
        leaked = _contains_any(safe_task, plan.must_not_include_terms)
        if leaked is None:
            leaked = next(
                (
                    label
                    for label in self.validator.event_labels
                    if label in safe_task
                    and not any(label in cue for cue in plan.must_include_cues)
                ),
                None,
            )
        if leaked:
            safe_task = "자동이체 설정 확인"

        memory_facts = list(plan.structured_context.get("session_memory_updates") or [])
        user_parts: dict[int, list[str]] = {
            0: [_mock_task_opening(plan, safe_task)],
            2: [rng.choice(_DETAIL_USER)],
            4: [rng.choice(_CONFIRMATION_USER)],
            6: ["안내받은 범위까지만 진행해 주세요"],
        }
        cues.append(
            CueAnnotation(
                turn_index=0,
                cue_type="task_intent",
                cue_text=safe_task,
                evidence_text=safe_task,
            )
        )
        placement_slots = list(plan.evidence_placement_slots or [0])
        for index, dimension in enumerate(plan.evidence_dimensions):
            turn_index = placement_slots[index % len(placement_slots)]
            evidence_text = _mock_dimension_text(
                dimension.dimension_id, dimension.role, plan.event_status_after_session
            )
            user_parts[turn_index].append(evidence_text)
            cues.append(
                CueAnnotation(
                    turn_index=turn_index,
                    cue_type=dimension.role,
                    cue_text=evidence_text,
                    evidence_text=evidence_text,
                    evidence_dimension_id=dimension.dimension_id,
                    linked_memory_path=(
                        dimension.linked_memory_paths[0]
                        if dimension.linked_memory_paths
                        and dimension.linked_memory_paths[0] in plan.target_memory_paths
                        else None
                    ),
                )
            )
        surface_cues = list(plan.must_include_cues)
        if not surface_cues and plan.session_type == "hard_negative":
            surface_cues = [
                cue.surface_hint
                for cue in plan.planned_cues
                if cue.surface_hint and cue.cue_role != "memory_fact"
            ]
        for index, cue_text in enumerate(surface_cues):
            turn_index = placement_slots[index % len(placement_slots)]
            user_parts[turn_index].append(cue_text)
            planned = next(
                (item for item in plan.planned_cues if item.surface_hint == cue_text),
                None,
            )
            cues.append(
                CueAnnotation(
                    turn_index=turn_index,
                    cue_type=planned.cue_role if planned else _slugify(cue_text),
                    cue_text=cue_text,
                    evidence_text=cue_text,
                    evidence_dimension_id=(
                        planned.evidence_dimension_id if planned else None
                    ),
                    linked_memory_path=(
                        planned.linked_memory_paths[0]
                        if planned
                        and planned.linked_memory_paths
                        and planned.linked_memory_paths[0] in plan.target_memory_paths
                        else None
                    ),
                )
            )
        for index, update in enumerate(memory_facts):
            turn_index = placement_slots[index % len(placement_slots)]
            fact_text = _memory_fact_text(update)
            user_parts[turn_index].append(fact_text)
            dimension_id = next(
                (
                    dimension.dimension_id
                    for dimension in plan.evidence_dimensions
                    if update.get("path") in dimension.linked_memory_paths
                ),
                None,
            )
            cues.append(
                CueAnnotation(
                    turn_index=turn_index,
                    cue_type="memory_fact",
                    cue_text=fact_text,
                    linked_memory_path=update.get("path"),
                    linked_memory_operation=update.get("operation"),
                    linked_memory_value=update.get("new_value"),
                    evidence_text=fact_text,
                    evidence_dimension_id=dimension_id,
                )
            )
        for pair in plan.stale_memory_pairs:
            old_text = (
                f"예전 {_MEMORY_LABELS_KO.get(pair.path, '등록 정보')} 값은 "
                f"{_visible_memory_value(pair.old_value)}였어요"
            )
            current_text = (
                f"지금 유효한 값은 {_visible_memory_value(pair.current_value)}예요"
            )
            user_parts[0].append(old_text)
            user_parts[2].append(current_text)
            cues.extend(
                [
                    CueAnnotation(
                        turn_index=0,
                        cue_type="stale_value",
                        linked_memory_path=pair.path,
                        linked_memory_value=pair.old_value,
                        evidence_text=old_text,
                    ),
                    CueAnnotation(
                        turn_index=2,
                        cue_type="current_value",
                        linked_memory_path=pair.path,
                        linked_memory_value=pair.current_value,
                        evidence_text=current_text,
                    ),
                ]
            )

        turns_min = int(self.cfg.get("turns_min", 8))
        turns_max = int(self.cfg.get("turns_max", 8))
        if turns_min != 8 or turns_max != 8:
            raise ValueError("mock dialogue contract requires exactly 8 turns")
        turns.append(Turn(speaker="user", text=". ".join(user_parts[0])))
        turns.append(Turn(speaker="assistant", text=rng.choice(_ASSISTANT_OPENINGS)))
        turns.append(Turn(speaker="user", text=". ".join(user_parts[2])))
        turns.append(Turn(speaker="assistant", text=rng.choice(_ASSISTANT_MIDDLES)))
        turns.append(Turn(speaker="user", text=". ".join(user_parts[4])))
        turns.append(Turn(speaker="assistant", text=rng.choice(_ASSISTANT_CONFIRMATIONS)))
        turns.append(Turn(speaker="user", text=". ".join(user_parts[6])))
        high_risk = plan.mapped_action in _HIGH_RISK_FA
        turns.append(
            Turn(
                speaker="assistant",
                text=rng.choice(
                    _ASSISTANT_CLOSINGS_HIGH_RISK
                    if high_risk
                    else _ASSISTANT_CLOSINGS
                ),
            )
        )

        visible = " ".join(t.text for t in turns)
        check = QualitySelfCheck(
            no_direct_life_event_mention=_contains_any(visible, plan.must_not_include_terms) is None,
            no_assistant_label_leakage=True,
            financial_task_clear=bool(safe_task),
            turn_count_ok=turns_min <= len(turns) <= turns_max,
        )
        return Session(
            session_id=plan.session_id,
            trajectory_id=plan.trajectory_id,
            month_index=plan.month_index,
            age=plan.age,
            transition_order=plan.transition_order,
            window_index=plan.window_index,
            position_in_window=plan.position_in_window,
            window_event_instance_id=plan.window_event_instance_id,
            session_type=plan.session_type,
            linked_event_instance_id=plan.linked_event_instance_id,
            event_status_after_session=plan.event_status_after_session,
            mapped_action=plan.mapped_action,
            financial_task=safe_task,
            turns=turns,
            cue_annotations=cues,
            quality_self_check=check,
            action_resolution=ActionResolution(
                mode=plan.action_execution_contract.action_mode,
                provided_slots=dict(plan.action_execution_contract.grounded_slots),
                missing_slots=list(plan.action_execution_contract.missing_slots),
            ),
            generator="mock",
            plan=plan,
        )

    # ------------------------------------------------------------------- llm
    def _build_prompt(self, plan: DialogueGenerationPlan, persona: NormalizedPersona) -> str:
        employment_status = _session_context_value(
            plan,
            "employment.employment_status",
            persona.occupation_state.employment_status,
        )
        residence_status = _session_context_value(
            plan,
            "housing.residence_status",
            persona.housing.residence_status,
        )
        marital_status = _session_context_value(
            plan,
            "household.marital_status",
            persona.household.marital_status,
        )
        replacements = {
            "{age}": str(plan.age),
            "{user_style}": persona.style.formality + ", " + persona.style.verbosity,
            "{persona_summary}": persona.persona_text[:200],
            "{employment_status}": str(employment_status),
            "{residence_status}": str(residence_status),
            "{marital_status}": str(marital_status),
            "{has_loan}": str(persona.financial_profile.has_loan).lower(),
            "{session_type}": plan.session_type,
            "{financial_task}": plan.financial_task,
            "{task_user_goal_instruction}": str(
                plan.task_user_goal_instruction or "지정된 금융 업무 하나만 수행한다."
            ),
            "{event_status}": plan.event_status_after_session,
            "{expected_memory_operation}": str(plan.expected_memory_operation),
            "{allowed_concrete_values}": json.dumps(
                sorted(grounded_concrete_values(plan.model_dump(mode="json"))),
                ensure_ascii=False,
            ),
            "{must_include_cues}": json.dumps(plan.must_include_cues, ensure_ascii=False),
            "{planned_cues}": json.dumps(
                [cue.model_dump(mode="json") for cue in plan.planned_cues], ensure_ascii=False
            ),
            "{evidence_realization_contract}": json.dumps(
                {
                    "strategy": plan.evidence_realization_strategy,
                    "placement_strategy": plan.evidence_placement_strategy,
                    "placement_slots": plan.evidence_placement_slots,
                    "dimensions": [
                        item.model_dump(mode="json")
                        for item in plan.evidence_dimensions
                    ],
                    "lifecycle_surface_family": plan.lifecycle_surface_family,
                    "lifecycle_surface_variant_id": plan.lifecycle_surface_variant_id,
                    "forbidden_direct_event_patterns": plan.forbidden_direct_event_patterns,
                    "directness_level": plan.directness_level,
                },
                ensure_ascii=False,
            ),
            "{action_execution_contract}": json.dumps(
                plan.action_execution_contract.model_dump(mode="json"),
                ensure_ascii=False,
            ),
            "{bank_policy_profile_id}": plan.bank_policy_profile_id,
            "{must_not_include_terms}": json.dumps(plan.must_not_include_terms, ensure_ascii=False),
            "{target_memory_paths}": json.dumps(plan.target_memory_paths, ensure_ascii=False),
            "{structured_context}": json.dumps(plan.structured_context, ensure_ascii=False),
            "{turns_min}": str(self.cfg.get("turns_min", 8)),
            "{turns_max}": str(self.cfg.get("turns_max", 8)),
            "{user_turns_min}": str(self.cfg.get("user_turns_min", 4)),
            "{user_turns_max}": str(self.cfg.get("user_turns_max", 4)),
        }
        prompt = self.prompt_template
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        return prompt

    def _build_repair_constraints(
        self, plan: DialogueGenerationPlan
    ) -> str:
        """Build a compact, lossless contract for every repair attempt.

        The full generation prompt can exceed 20k characters because state
        aliases duplicate large structures. Prefix slicing that prompt drops
        the forbidden terms and exact memory updates, so repairs must use this
        deliberately compact representation instead.
        """
        context = plan.structured_context or {}
        event = context.get("event") or {}
        payload = {
            "session_type": plan.session_type,
            "event_status": plan.event_status_after_session,
            "financial_task": plan.financial_task,
            "task_user_goal_instruction": plan.task_user_goal_instruction,
            "turn_limits": {
                "total_min": int(self.cfg.get("turns_min", 8)),
                "total_max": int(self.cfg.get("turns_max", 8)),
                "user_min": int(self.cfg.get("user_turns_min", 4)),
                "user_max": int(self.cfg.get("user_turns_max", 4)),
            },
            "must_include_cues": plan.must_include_cues,
            "planned_cues": [
                cue.model_dump(mode="json") for cue in plan.planned_cues
            ],
            "must_not_include_terms": plan.must_not_include_terms,
            "target_memory_paths": plan.target_memory_paths,
            "protected_memory_paths": plan.protected_memory_paths,
            "expected_memory_operation": plan.expected_memory_operation,
            "evidence_realization_contract": {
                "strategy": plan.evidence_realization_strategy,
                "placement_strategy": plan.evidence_placement_strategy,
                "placement_slots": plan.evidence_placement_slots,
                "dimensions": [
                    item.model_dump(mode="json") for item in plan.evidence_dimensions
                ],
                "forbidden_direct_event_patterns": plan.forbidden_direct_event_patterns,
                "lifecycle_surface_family": plan.lifecycle_surface_family,
                "lifecycle_surface_variant_id": plan.lifecycle_surface_variant_id,
            },
            "action_execution_contract": plan.action_execution_contract.model_dump(
                mode="json"
            ),
            "bank_policy_profile_id": plan.bank_policy_profile_id,
            "session_memory_updates": context.get("session_memory_updates") or [],
            "event_params": event.get("params") or {},
            "allowed_concrete_values": sorted(
                grounded_concrete_values(plan.model_dump(mode="json"))
            ),
            "cue_annotation_output_contract": {
                "required_field_names": [
                    "turn_index",
                    "cue_type",
                    "linked_memory_path",
                    "linked_memory_operation",
                    "linked_memory_value",
                    "evidence_text",
                    "evidence_dimension_id",
                ],
                "planner_aliases_not_for_output": [
                    "cue_id",
                    "cue_role",
                    "path",
                    "operation",
                    "value",
                    "linked_memory_paths",
                ],
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _parse_llm_json(raw: str) -> dict:
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON object in LLM output")
        return json.loads(text[start : end + 1])

    def _write_raw_llm_output(self, raw_path: Path, raw: str) -> None:
        raw_path.write_text(raw, encoding="utf-8")
        metadata = getattr(self.client, "last_response_metadata", None)
        if not metadata:
            return
        metadata_path = raw_path.with_suffix(".meta.json")
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _repair_cue_annotations(
        turns: list[Turn],
        cues: list[CueAnnotation],
        plan: DialogueGenerationPlan,
    ) -> list[CueAnnotation]:
        if not plan.must_include_cues:
            return cues

        repaired = list(cues)
        memory_paths = list(plan.target_memory_paths)
        annotated_keys = {
            (cue.turn_index, cue.cue_text or cue.cue_type)
            for cue in repaired
        }

        for cue_index, required_cue in enumerate(plan.must_include_cues):
            if not required_cue:
                continue
            matching_turn = None
            for turn_index, turn in enumerate(turns):
                if turn.speaker == "user" and required_cue in turn.text:
                    matching_turn = turn_index
                    break
            if matching_turn is None:
                continue
            key = (matching_turn, required_cue)
            if key in annotated_keys:
                continue
            linked_memory_path = memory_paths[cue_index % len(memory_paths)] if memory_paths else None
            repaired.append(
                CueAnnotation(
                    turn_index=matching_turn,
                    cue_type=_slugify(required_cue) or "required_cue",
                    cue_text=required_cue,
                    linked_memory_path=linked_memory_path,
                )
            )
            annotated_keys.add(key)

        return repaired

    def _payload_to_parts(
        self,
        payload: dict[str, Any],
        plan: DialogueGenerationPlan,
        persona: NormalizedPersona,
        *,
        enforce_turn_limits: bool = False,
    ) -> tuple[list[Turn], list[CueAnnotation], QualitySelfCheck, ActionResolution]:
        if not isinstance(payload, dict):
            raise LLMOutputValidationError("payload must be a JSON object")

        raw_turns = payload.get("turns")
        if not isinstance(raw_turns, list) or not raw_turns:
            raise LLMOutputValidationError("payload.turns must be a non-empty list")

        turns: list[Turn] = []
        for index, item in enumerate(raw_turns):
            if not isinstance(item, dict):
                raise LLMOutputValidationError(f"turns[{index}] must be an object")
            missing = [key for key in ("speaker", "text") if key not in item]
            if missing:
                raise LLMOutputValidationError(f"turns[{index}] missing required key(s): {', '.join(missing)}")
            speaker = item["speaker"]
            text = item["text"]
            if speaker not in {"user", "assistant"}:
                raise LLMOutputValidationError(f"turns[{index}].speaker must be 'user' or 'assistant'")
            if not isinstance(text, str) or not text.strip():
                raise LLMOutputValidationError(f"turns[{index}].text must be a non-empty string")
            turns.append(Turn(speaker=speaker, text=text))

        for index, turn in enumerate(turns):
            expected = "user" if index % 2 == 0 else "assistant"
            if turn.speaker != expected:
                raise LLMOutputValidationError(f"turns[{index}].speaker must be '{expected}' for strict alternation")
        if turns[-1].speaker != "assistant":
            raise LLMOutputValidationError("dialogue must end with an assistant turn")
        if enforce_turn_limits:
            turns_min = int(self.cfg.get("turns_min", 1))
            turns_max = int(self.cfg.get("turns_max", 10_000))
            user_turns_min = int(self.cfg.get("user_turns_min", 1))
            user_turns_max = int(self.cfg.get("user_turns_max", 10_000))
            user_turn_count = sum(turn.speaker == "user" for turn in turns)
            if not turns_min <= len(turns) <= turns_max:
                raise LLMOutputValidationError(
                    f"turn count must be {turns_min}..{turns_max}, got {len(turns)}"
                )
            if not user_turns_min <= user_turn_count <= user_turns_max:
                raise LLMOutputValidationError(
                    "user turn count must be "
                    f"{user_turns_min}..{user_turns_max}, got {user_turn_count}"
                )
        visible = " ".join(turn.text for turn in turns)
        employment_status = _session_context_value(
            plan, "employment.employment_status", persona.occupation_state.employment_status
        )
        event_context = plan.structured_context.get("event") or {}
        employment_introduced = (
            str(event_context.get("event_id", "")).startswith("career_")
            and plan.event_status_after_session in {"upcoming", "occurred"}
        )
        if employment_status not in {"employed", "on_leave"} and not employment_introduced:
            for term in ("월급", "급여일"):
                if term in visible:
                    raise LLMOutputValidationError(
                        f"visible dialogue conflicts with employment_status={employment_status}: '{term}'"
                    )
        residence_status = _session_context_value(plan, "housing.residence_status", persona.housing.residence_status)
        rent_introduced = (
            str(event_context.get("event_id", "")).startswith("housing_")
            and plan.event_status_after_session in {"upcoming", "occurred"}
        )
        if residence_status != "wolse" and not rent_introduced:
            for term in ("월세", "집주인"):
                if term in visible:
                    raise LLMOutputValidationError(
                        f"visible dialogue conflicts with residence_status={residence_status}: '{term}'"
                    )
        for term in _OFFLINE_BANKING_TERMS:
            if term in visible:
                raise LLMOutputValidationError(f"visible dialogue must be online banking/chatbot style, not branch style: '{term}'")

        raw_cues = payload.get("cue_annotations") or []
        if not isinstance(raw_cues, list):
            raise LLMOutputValidationError("payload.cue_annotations must be a list when present")

        cues: list[CueAnnotation] = []
        allowed_memory_paths = set(plan.target_memory_paths)
        for index, item in enumerate(raw_cues):
            if not isinstance(item, dict):
                raise LLMOutputValidationError(f"cue_annotations[{index}] must be an object")
            try:
                turn_index = int(item.get("turn_index", 0))
            except (TypeError, ValueError) as exc:
                raise LLMOutputValidationError(f"cue_annotations[{index}].turn_index must be an integer") from exc
            if not (0 <= turn_index < len(turns)):
                raise LLMOutputValidationError(f"cue_annotations[{index}].turn_index out of range: {turn_index}")
            if turns[turn_index].speaker != "user":
                one_based_candidate = turn_index - 1
                if 0 <= one_based_candidate < len(turns) and turns[one_based_candidate].speaker == "user":
                    turn_index = one_based_candidate
                else:
                    raise LLMOutputValidationError(f"cue_annotations[{index}].turn_index must point to a user turn")
            cue_type = item.get("cue_type") or item.get("cue_role") or item.get("cue_id") or "unknown"
            linked_memory_path = item.get("linked_memory_path")
            if linked_memory_path is None:
                linked_memory_path = item.get("path")
            linked_memory_operation = item.get("linked_memory_operation")
            if linked_memory_operation is None:
                linked_memory_operation = item.get("operation")
            if "linked_memory_value" in item:
                linked_memory_value = item.get("linked_memory_value")
            else:
                linked_memory_value = item.get("value")
            if linked_memory_path is not None and linked_memory_path not in allowed_memory_paths:
                raise LLMOutputValidationError(
                    f"cue_annotations[{index}].linked_memory_path must be null or one of target_memory_paths"
                )
            cues.append(
                CueAnnotation(
                    turn_index=turn_index,
                    cue_type=str(cue_type),
                    cue_text=item.get("cue_text"),
                    linked_memory_path=linked_memory_path,
                    linked_memory_operation=linked_memory_operation,
                    linked_memory_value=linked_memory_value,
                    evidence_text=item.get("evidence_text") or item.get("cue_text"),
                    evidence_dimension_id=item.get("evidence_dimension_id"),
                )
            )
        expected_memory_facts = list(
            plan.structured_context.get("session_memory_updates") or []
        )
        for expected in expected_memory_facts:
            matches = [
                cue
                for cue in cues
                if cue.cue_type == "memory_fact"
                and cue.linked_memory_path == expected.get("path")
                and cue.linked_memory_operation == expected.get("operation")
                and cue.linked_memory_value == expected.get("new_value")
            ]
            if not matches:
                raise LLMOutputValidationError(
                    "cue_annotations must ground every session_memory_update "
                    f"with exact path/operation/value: {expected.get('path')} "
                    f"{expected.get('operation')} {expected.get('new_value')!r}"
                )
            if not any(
                cue.evidence_text
                and cue.evidence_text in turns[cue.turn_index].text
                for cue in matches
            ):
                raise LLMOutputValidationError(
                    f"memory fact evidence_text must be visible in a user turn: {expected.get('path')}"
                )
        if plan.must_include_cues and not cues:
            raise LLMOutputValidationError(
                "cue_annotations must include at least one user-turn annotation "
                "when plan.must_include_cues is non-empty"
            )
        raw_check = payload.get("quality_self_check") or {}
        if not isinstance(raw_check, dict):
            raise LLMOutputValidationError("payload.quality_self_check must be an object when present")
        check = QualitySelfCheck(**raw_check)
        raw_resolution = payload.get("action_resolution") or {}
        if not isinstance(raw_resolution, dict):
            raise LLMOutputValidationError(
                "payload.action_resolution must be an object when present"
            )
        resolution = ActionResolution(**raw_resolution)
        return turns, cues, check, resolution

    def _llm_session(
        self,
        plan: DialogueGenerationPlan,
        persona: NormalizedPersona,
        raw_dir: Path,
        *,
        enforce_turn_limits: bool = False,
    ) -> Session:
        assert self.client is not None, "llm mode requires an LLMClient"
        prompt = self._build_prompt(plan, persona)
        system = "당신은 은행 상담 대화 데이터 생성기입니다. JSON만 출력합니다."
        raw = self.client.generate(system, prompt)
        response_metadata = [dict(getattr(self.client, "last_response_metadata", {}) or {})]

        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_stem = f"{plan.trajectory_id}_{plan.session_id}{self.raw_filename_suffix}"
        raw_path = raw_dir / f"{raw_stem}.txt"
        max_repair_attempts = int(self.cfg.get("repair_attempts", 3))
        repair_paths = [
            raw_dir / f"{raw_stem}_repair{'' if attempt == 1 else attempt}.txt"
            for attempt in range(1, max_repair_attempts + 1)
        ]
        for repair_path in repair_paths:
            if repair_path.exists():
                repair_path.unlink()
            metadata_path = repair_path.with_suffix(".meta.json")
            if metadata_path.exists():
                metadata_path.unlink()
        self._write_raw_llm_output(raw_path, raw)

        current_raw = raw
        last_error: Exception | None = None
        session: Session | None = None
        validation_errors: list[str] = []
        repair_count = 0
        for attempt in range(max_repair_attempts + 1):
            try:
                current_metadata = response_metadata[-1] if response_metadata else {}
                stop_reason = current_metadata.get("stop_reason") or current_metadata.get("finish_reason")
                if stop_reason in {"max_tokens", "length"}:
                    raise LLMOutputValidationError(
                        "provider output was truncated at the token limit; "
                        "return a concise, complete JSON object"
                    )
                payload = self._parse_llm_json(current_raw)
                turns, cues, check, resolution = self._payload_to_parts(
                    payload,
                    plan,
                    persona,
                    enforce_turn_limits=enforce_turn_limits,
                )
                candidate = Session(
                    session_id=plan.session_id,
                    trajectory_id=plan.trajectory_id,
                    month_index=plan.month_index,
                    age=plan.age,
                    transition_order=plan.transition_order,
                    window_index=plan.window_index,
                    position_in_window=plan.position_in_window,
                    window_event_instance_id=plan.window_event_instance_id,
                    session_type=plan.session_type,
                    linked_event_instance_id=plan.linked_event_instance_id,
                    event_status_after_session=plan.event_status_after_session,
                    mapped_action=plan.mapped_action,
                    financial_task=plan.financial_task,
                    turns=turns,
                    cue_annotations=cues,
                    quality_self_check=check,
                    action_resolution=resolution,
                    generator=self.client.provider,
                    generation_metadata={},
                    plan=plan,
                )
                violations = self.validator.validate_session(candidate.model_dump(mode="json"))
                blocking_violations = [
                    item
                    for item in violations
                    if item.get("code") not in _REVIEW_ONLY_VALIDATION_CODES
                ]
                if blocking_violations:
                    details = "; ".join(f"{v['code']}: {v['detail']}" for v in blocking_violations)
                    raise LLMOutputValidationError(f"dialogue validator violations: {details}")
                session = candidate
                break
            except (ValueError, json.JSONDecodeError, LLMOutputValidationError) as exc:
                last_error = exc
                validation_errors.append(f"{type(exc).__name__}: {exc}")
                if attempt >= max_repair_attempts:
                    raise LLMOutputValidationError(
                        f"{plan.trajectory_id}_{plan.session_id}: LLM output is invalid after "
                        f"{max_repair_attempts} repair attempts: {last_error}"
                    ) from exc
                cumulative_violations = "\n".join(
                    f"{index}. {error}"
                    for index, error in enumerate(validation_errors, start=1)
                )
                repair = (
                    self.repair_template
                    .replace("{violations}", cumulative_violations)
                    .replace("{repair_constraints}", self._build_repair_constraints(plan))
                    .replace("{original_prompt}", prompt)
                    .replace("{previous_output}", current_raw)
                )
                current_raw = self.client.generate(system, repair)
                repair_count += 1
                response_metadata.append(dict(getattr(self.client, "last_response_metadata", {}) or {}))
                self._write_raw_llm_output(repair_paths[attempt], current_raw)
        else:
            raise LLMOutputValidationError(f"{plan.trajectory_id}_{plan.session_id}: LLM output is invalid")

        if session is None:
            raise LLMOutputValidationError(f"{plan.trajectory_id}_{plan.session_id}: LLM output is invalid")
        final_metadata = dict(response_metadata[-1] if response_metadata else {})
        total_input = sum(
            int(item.get("prompt_tokens") or item.get("input_tokens") or (item.get("usage") or {}).get("prompt_tokens") or (item.get("usage") or {}).get("input_tokens") or 0)
            for item in response_metadata
        )
        total_output = sum(
            int(item.get("completion_tokens") or item.get("output_tokens") or (item.get("usage") or {}).get("completion_tokens") or (item.get("usage") or {}).get("output_tokens") or 0)
            for item in response_metadata
        )
        total_cached = sum(
            int(item.get("cached_tokens") or (item.get("usage") or {}).get("cached_tokens") or (item.get("usage") or {}).get("cache_read_input_tokens") or 0)
            for item in response_metadata
        )
        final_metadata.update(
            {
                "provider_request_count": sum(
                    1 + int(item.get("retry_count") or 0) for item in response_metadata
                ),
                "repair_count": repair_count,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cached_tokens": total_cached,
                "request_duration_ms": round(sum(float(item.get("request_duration_ms") or 0) for item in response_metadata), 3),
                "final_validation_status": "passed",
                "validation_errors": validation_errors,
                "repair_reason_counts": dict(
                    sorted(
                        Counter(
                            code
                            for error in validation_errors
                            for code in re.findall(
                                r"(?:violations:\s*|;\s*)([a-z][a-z0-9_]+):",
                                error,
                            )
                        ).items()
                    )
                ),
                "responses": response_metadata,
            }
        )
        session.generation_metadata = final_metadata
        raw_path.with_suffix(".meta.json").write_text(
            json.dumps(final_metadata, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return session

    # ------------------------------------------------------------------ main
    def generate_session(self, plan: DialogueGenerationPlan, persona: NormalizedPersona) -> Session | None:
        raw_dir = self.raw_output_dir or self.paths.raw_model_outputs / "dialogue"
        if self.mode == "mock":
            return self._mock_session(plan, persona)
        if self.mode == "dry_run":
            raw_dir.mkdir(parents=True, exist_ok=True)
            prompt = self._build_prompt(plan, persona)
            raw_stem = f"{plan.trajectory_id}_{plan.session_id}{self.raw_filename_suffix}"
            (raw_dir / f"{raw_stem}_prompt.txt").write_text(prompt, encoding="utf-8")
            return None
        return self._llm_session(
            plan,
            persona,
            raw_dir,
            enforce_turn_limits=True,
        )
