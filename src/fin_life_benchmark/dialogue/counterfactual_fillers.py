"""Persona-style-only reserve fillers for counterfactual lifecycle masking."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..io import RepoPaths, load_yaml
from ..trajectory.models import Trajectory
from .models import ActionResolution, CueAnnotation, Turn

FILLER_CONTRACT_VERSION = "counterfactual-filler-v1"
FILLERS_PER_PERSONA = 20

# Ten state-independent tasks, each realized twice with a different discourse
# shape. Tasks that commonly elicit a concrete balance, device count, setting
# value, transfer, calculation, or account mutation are deliberately absent.
SAFE_FILLER_TASK_TEMPLATE_IDS = (
    "routine_recent_transactions",
    "routine_incoming_history",
    "routine_outgoing_history",
    "routine_statement_download",
    "routine_fee_history",
    "routine_deposit_rate",
    "routine_savings_rate",
    "routine_loan_rate",
    "routine_login_security",
    "routine_phishing_prevention",
)

SURFACE_VARIANTS = (
    (
        "direct_procedure",
        "사용자가 바로 조회 방법을 묻고, 챗봇은 화면 경로와 선택 기준만 간결하게 안내한다.",
    ),
    (
        "guided_navigation",
        "사용자가 목적을 짧게 설명하고, 챗봇은 한 번의 확인 질문 뒤 스스로 확인할 절차를 안내한다.",
    ),
)

LIFECYCLE_LEAK_TERMS = (
    "결혼",
    "이혼",
    "별거",
    "배우자",
    "출산",
    "자녀",
    "입양",
    "이사",
    "주소 변경",
    "월세",
    "전세",
    "주택 구입",
    "주택 매각",
    "취업",
    "이직",
    "퇴사",
    "실직",
    "휴직",
    "복직",
    "은퇴",
    "퇴직",
    "직장",
    "회사",
    "급여",
    "입원",
    "장례",
    "사망",
    "유학",
    "재난",
    "피해를 입",
    "피해를 당",
)

# A reserve filler may explain how the user can inspect data, but must not
# invent personalized banking results. These patterns target assistant claims,
# not the neutral task itself.
PERSONAL_RESULT_PATTERNS = (
    re.compile(r"조회\s*(?:결과|해\s*보니)"),
    re.compile(r"확인해\s*보니"),
    re.compile(r"현재.{0,25}(?:켜져|꺼져|설정되어|등록되어|보유하고)"),
    re.compile(r"(?:내역|계좌|기기|잔액).{0,25}(?:조회되|확인되|있으시|없으시)"),
    re.compile(r"(?:보여|띄워|정렬해)\s*드렸"),
)


class CounterfactualFillerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filler_id: str
    trajectory_id: str
    persona_id: str
    task_template_id: str
    mapped_action: str
    financial_task: str
    task_user_goal_instruction: str
    surface_variant_id: str
    surface_variant_instruction: str
    style_formality: str
    style_verbosity: str
    turn_count: int = 8
    contract_version: str = FILLER_CONTRACT_VERSION


class CounterfactualFiller(BaseModel):
    """A timeless donor dialogue, never a canonical trajectory session."""

    model_config = ConfigDict(extra="forbid")

    filler_id: str
    session_id: str
    trajectory_id: str
    persona_id: str
    source_kind: Literal["synthetic_reserve"] = "synthetic_reserve"
    month_index: None = None
    session_type: Literal["routine_financial"] = "routine_financial"
    linked_event_instance_id: None = None
    event_status_after_session: Literal["no_event"] = "no_event"
    mapped_action: str
    financial_task: str
    turns: list[Turn]
    cue_annotations: list[CueAnnotation] = Field(default_factory=list)
    action_resolution: ActionResolution = Field(default_factory=ActionResolution)
    generation_metadata: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any]


def build_filler_plans(
    trajectory: Trajectory,
    paths: RepoPaths | None = None,
) -> list[CounterfactualFillerPlan]:
    """Build exactly 20 style-only plans without trajectory state."""
    paths = paths or RepoPaths.default()
    registry = load_yaml(paths.registries / "dialogue_routine_tasks.yaml")
    by_id = {
        item["task_template_id"]: item
        for item in registry.get("routine_tasks") or []
    }
    missing = sorted(set(SAFE_FILLER_TASK_TEMPLATE_IDS) - set(by_id))
    if missing:
        raise ValueError(f"routine filler registry entries missing: {', '.join(missing)}")

    style = trajectory.persona.style
    plans: list[CounterfactualFillerPlan] = []
    for variant_index, (variant_id, variant_instruction) in enumerate(SURFACE_VARIANTS):
        for task_index, task_template_id in enumerate(SAFE_FILLER_TASK_TEMPLATE_IDS):
            task = by_id[task_template_id]
            ordinal = variant_index * len(SAFE_FILLER_TASK_TEMPLATE_IDS) + task_index + 1
            plans.append(
                CounterfactualFillerPlan(
                    filler_id=f"CF{ordinal:03d}",
                    trajectory_id=trajectory.trajectory_id,
                    persona_id=trajectory.persona.persona_id,
                    task_template_id=task_template_id,
                    mapped_action=task["fa_code"],
                    financial_task=task["visible_task_ko"],
                    task_user_goal_instruction=task["user_goal_instruction"],
                    surface_variant_id=variant_id,
                    surface_variant_instruction=variant_instruction,
                    style_formality=style.formality,
                    style_verbosity=style.verbosity,
                )
            )
    if len(plans) != FILLERS_PER_PERSONA:
        raise AssertionError(f"expected {FILLERS_PER_PERSONA} plans, got {len(plans)}")
    return plans


def make_filler(
    plan: CounterfactualFillerPlan,
    turns: list[dict[str, Any]],
    generation_metadata: dict[str, Any],
) -> CounterfactualFiller:
    parsed_turns = [Turn.model_validate(turn) for turn in turns]
    first_user_text = parsed_turns[0].text if parsed_turns else ""
    return CounterfactualFiller(
        filler_id=plan.filler_id,
        session_id=plan.filler_id,
        trajectory_id=plan.trajectory_id,
        persona_id=plan.persona_id,
        mapped_action=plan.mapped_action,
        financial_task=plan.financial_task,
        turns=parsed_turns,
        cue_annotations=[
            CueAnnotation(
                turn_index=0,
                cue_type="task_intent",
                evidence_text=first_user_text,
            )
        ],
        action_resolution=ActionResolution(mode="information_only"),
        generation_metadata=generation_metadata,
        plan={
            "task_template_id": plan.task_template_id,
            "task_user_goal_instruction": plan.task_user_goal_instruction,
            "surface_variant_id": plan.surface_variant_id,
            "style": {
                "formality": plan.style_formality,
                "verbosity": plan.style_verbosity,
            },
            "contract_version": plan.contract_version,
        },
    )


def validate_filler(
    filler: CounterfactualFiller,
    plan: CounterfactualFillerPlan,
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []

    def add(code: str, detail: str) -> None:
        violations.append({"code": code, "detail": detail})

    if filler.filler_id != plan.filler_id or filler.session_id != plan.filler_id:
        add("identity_mismatch", "filler/session ID must match the frozen plan")
    if filler.trajectory_id != plan.trajectory_id or filler.persona_id != plan.persona_id:
        add("persona_mismatch", "trajectory/persona must match the frozen plan")
    if filler.source_kind != "synthetic_reserve" or filler.month_index is not None:
        add("not_timeless_reserve", "reserve filler must have source_kind synthetic_reserve and no month")
    if filler.linked_event_instance_id is not None:
        add("linked_event", "reserve filler must not link to an event")
    if filler.plan.get("task_template_id") != plan.task_template_id:
        add("task_mismatch", "task template must match the frozen plan")
    if filler.plan.get("contract_version") != FILLER_CONTRACT_VERSION:
        add("contract_version", "unexpected or missing filler contract version")
    if len(filler.turns) != plan.turn_count:
        add("turn_count", f"expected {plan.turn_count} turns, got {len(filler.turns)}")
    for index, turn in enumerate(filler.turns):
        expected = "user" if index % 2 == 0 else "assistant"
        if turn.speaker != expected:
            add("speaker_order", f"turn {index} must be {expected}")
    if len(filler.cue_annotations) != 1:
        add("cue_count", "exactly one deterministic task_intent cue is required")
    else:
        cue = filler.cue_annotations[0]
        if cue.cue_type != "task_intent" or cue.turn_index != 0:
            add("cue_contract", "only a turn-0 task_intent cue is allowed")
        if any(
            value is not None
            for value in (
                cue.linked_memory_path,
                cue.linked_memory_operation,
                cue.linked_memory_value,
                cue.evidence_dimension_id,
            )
        ):
            add("memory_cue", "task_intent cue must not carry memory/event fields")
    if filler.action_resolution.mode != "information_only":
        add("action_mode", "reserve fillers must be information_only")

    visible = " ".join(turn.text for turn in filler.turns)
    if re.search(r"\d", visible):
        add("concrete_number", "reserve filler must not invent numbers, amounts, rates, or counts")
    leaked_terms = sorted(term for term in LIFECYCLE_LEAK_TERMS if term in visible)
    if leaked_terms:
        add("lifecycle_leak", f"contains lifecycle/persona terms: {', '.join(leaked_terms)}")
    assistant_visible = " ".join(
        turn.text for turn in filler.turns if turn.speaker == "assistant"
    )
    matched_patterns = [
        pattern.pattern
        for pattern in PERSONAL_RESULT_PATTERNS
        if pattern.search(assistant_visible)
    ]
    if matched_patterns:
        add(
            "invented_personal_result",
            "assistant claims a personalized lookup result instead of explaining a procedure",
        )
    return violations


def audit_filler_bank(
    plans: list[CounterfactualFillerPlan],
    fillers: list[CounterfactualFiller],
) -> dict[str, Any]:
    plan_counts = Counter(plan.filler_id for plan in plans)
    filler_counts = Counter(filler.filler_id for filler in fillers)
    plan_by_id = {plan.filler_id: plan for plan in plans}
    violations: list[dict[str, Any]] = []
    for filler_id, count in sorted(filler_counts.items()):
        if count > 1:
            violations.append({
                "filler_id": filler_id,
                "code": "duplicate_filler_id",
                "detail": f"found {count} records",
            })
    for filler_id in sorted(set(plan_by_id) - set(filler_counts)):
        violations.append({
            "filler_id": filler_id,
            "code": "missing_filler",
            "detail": "planned filler is absent",
        })
    for filler_id in sorted(set(filler_counts) - set(plan_by_id)):
        violations.append({
            "filler_id": filler_id,
            "code": "unexpected_filler",
            "detail": "filler has no frozen plan",
        })
    for filler in fillers:
        plan = plan_by_id.get(filler.filler_id)
        if plan is None:
            continue
        for violation in validate_filler(filler, plan):
            violations.append({"filler_id": filler.filler_id, **violation})

    duplicate_dialogues = [
        signature
        for signature, count in Counter(
            "\n".join(f"{turn.speaker}:{turn.text.strip()}" for turn in filler.turns)
            for filler in fillers
        ).items()
        if count > 1
    ]
    if duplicate_dialogues:
        violations.append({
            "filler_id": None,
            "code": "duplicate_dialogue",
            "detail": f"{len(duplicate_dialogues)} exact dialogue duplicate(s)",
        })

    expected = len(plans)
    return {
        "decision": "PASS" if not violations and len(fillers) == expected else "FAIL",
        "contract_version": FILLER_CONTRACT_VERSION,
        "expected_fillers": expected,
        "actual_fillers": len(fillers),
        "unique_plan_ids": len(plan_counts),
        "unique_filler_ids": len(filler_counts),
        "task_distribution": dict(sorted(Counter(
            filler.plan.get("task_template_id") for filler in fillers
        ).items())),
        "violations": violations,
    }
