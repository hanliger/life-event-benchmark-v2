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
from pathlib import Path
from typing import Any

from ..io import RepoPaths
from ..io import load_yaml
from ..llm.client import LLMClient
from ..persona.models import NormalizedPersona
from ..fsm.registry import load_life_event_templates
from ..validation.dialogue_validator import DialogueValidator
from .models import CueAnnotation, DialogueGenerationPlan, QualitySelfCheck, Session, Turn

_HIGH_RISK_FA = {"FA-07", "FA-08", "FA-09", "FA-10"}

_ASSISTANT_QUESTIONS = [
    "네 고객님, 어떤 계좌 기준으로 도와드릴까요?",
    "확인했습니다. 원하시는 날짜가 언제일까요?",
    "네, 가능합니다. 금액은 어떻게 설정해 드릴까요?",
    "네, 접수했습니다. 본인 명의 계좌가 맞으실까요?",
]

_ASSISTANT_CLOSING = "처리해 드렸습니다. 더 필요하신 업무 있으실까요?"
_ASSISTANT_CLOSING_HIGH_RISK = (
    "이 변경은 출금이 발생하는 항목이라 바로 실행되지는 않고, 고객님 확인 후에 진행됩니다."
)

_FILLER_USER = [
    "잔액 확인도 같이 부탁드려요",
    "수수료는 따로 없는 거죠",
    "알림도 같이 설정해 주세요",
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
        (plan.structured_context.get("persona_state") or {})
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
        wrapper = _CUE_WRAPPER_BY_STATUS.get(plan.event_status_after_session, _CUE_WRAPPER_BY_STATUS["no_event"])

        safe_task = plan.financial_task
        leaked = _contains_any(safe_task, plan.must_not_include_terms)
        if leaked:
            safe_task = "자동이체 설정 확인"

        turns.append(Turn(speaker="user", text=f"{safe_task} 좀 하려고요"))
        turns.append(Turn(speaker="assistant", text=_ASSISTANT_QUESTIONS[0]))

        # cue turns (or fillers so length stays >= 8 turns)
        cue_texts = list(plan.must_include_cues)
        while len(cue_texts) < 2:
            cue_texts.append(None)  # filler slot
        memory_paths = list(plan.target_memory_paths) or [None]
        for i, cue in enumerate(cue_texts):
            if cue is not None:
                text = wrapper.format(cue=cue)
                turns.append(Turn(speaker="user", text=text))
                cues.append(
                    CueAnnotation(
                        turn_index=len(turns) - 1,
                        cue_type=_slugify(cue),
                        cue_text=cue,
                        linked_memory_path=memory_paths[i % len(memory_paths)],
                    )
                )
            else:
                turns.append(Turn(speaker="user", text=rng.choice(_FILLER_USER)))
            turns.append(Turn(speaker="assistant", text=_ASSISTANT_QUESTIONS[1 + i % (len(_ASSISTANT_QUESTIONS) - 1)]))

        turns_min = int(self.cfg.get("turns_min", 7))
        turns_max = int(self.cfg.get("turns_max", 10))
        target_turns = rng.randint(turns_min, turns_max)
        if target_turns % 2 == 1:
            target_turns = min(turns_max if turns_max % 2 == 0 else turns_max - 1, target_turns + 1)
        target_turns = max(6, target_turns)
        while len(turns) < target_turns - 2:
            turns.append(Turn(speaker="user", text=rng.choice(_FILLER_USER)))
            turns.append(Turn(speaker="assistant", text=rng.choice(_ASSISTANT_QUESTIONS)))

        turns.append(Turn(speaker="user", text="네 그렇게 해주세요"))
        high_risk = plan.mapped_action in _HIGH_RISK_FA
        turns.append(Turn(speaker="assistant", text=_ASSISTANT_CLOSING_HIGH_RISK if high_risk else _ASSISTANT_CLOSING))

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
            session_type=plan.session_type,
            linked_event_instance_id=plan.linked_event_instance_id,
            event_status_after_session=plan.event_status_after_session,
            mapped_action=plan.mapped_action,
            financial_task=safe_task,
            turns=turns,
            cue_annotations=cues,
            quality_self_check=check,
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
            "{event_status}": plan.event_status_after_session,
            "{must_include_cues}": json.dumps(plan.must_include_cues, ensure_ascii=False),
            "{must_not_include_terms}": json.dumps(plan.must_not_include_terms, ensure_ascii=False),
            "{target_memory_paths}": json.dumps(plan.target_memory_paths, ensure_ascii=False),
            "{structured_context}": json.dumps(plan.structured_context, ensure_ascii=False),
            "{turns_min}": str(self.cfg.get("turns_min", 7)),
            "{turns_max}": str(self.cfg.get("turns_max", 10)),
            "{user_turns_min}": str(self.cfg.get("user_turns_min", 4)),
            "{user_turns_max}": str(self.cfg.get("user_turns_max", 6)),
        }
        prompt = self.prompt_template
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        return prompt

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
    ) -> tuple[list[Turn], list[CueAnnotation], QualitySelfCheck]:
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
        visible = " ".join(turn.text for turn in turns)
        residence_status = _session_context_value(plan, "housing.residence_status", persona.housing.residence_status)
        if residence_status != "wolse":
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
            linked_memory_path = item.get("linked_memory_path")
            if linked_memory_path is not None and linked_memory_path not in allowed_memory_paths:
                raise LLMOutputValidationError(
                    f"cue_annotations[{index}].linked_memory_path must be null or one of target_memory_paths"
                )
            cues.append(
                CueAnnotation(
                    turn_index=turn_index,
                    cue_type=str(item.get("cue_type", "unknown")),
                    cue_text=item.get("cue_text"),
                    linked_memory_path=linked_memory_path,
                )
            )
        cues = self._repair_cue_annotations(turns, cues, plan)
        if plan.must_include_cues and not cues:
            raise LLMOutputValidationError(
                "cue_annotations must include at least one user-turn annotation "
                "when plan.must_include_cues is non-empty"
            )
        raw_check = payload.get("quality_self_check") or {}
        if not isinstance(raw_check, dict):
            raise LLMOutputValidationError("payload.quality_self_check must be an object when present")
        check = QualitySelfCheck(**raw_check)
        return turns, cues, check

    def _llm_session(self, plan: DialogueGenerationPlan, persona: NormalizedPersona, raw_dir: Path) -> Session:
        assert self.client is not None, "llm mode requires an LLMClient"
        prompt = self._build_prompt(plan, persona)
        system = "당신은 은행 상담 대화 데이터 생성기입니다. JSON만 출력합니다."
        raw = self.client.generate(system, prompt)

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
        for attempt in range(max_repair_attempts + 1):
            try:
                payload = self._parse_llm_json(current_raw)
                turns, cues, check = self._payload_to_parts(payload, plan, persona)
                candidate = Session(
                    session_id=plan.session_id,
                    trajectory_id=plan.trajectory_id,
                    month_index=plan.month_index,
                    age=plan.age,
                    session_type=plan.session_type,
                    linked_event_instance_id=plan.linked_event_instance_id,
                    event_status_after_session=plan.event_status_after_session,
                    mapped_action=plan.mapped_action,
                    financial_task=plan.financial_task,
                    turns=turns,
                    cue_annotations=cues,
                    quality_self_check=check,
                    generator=self.client.provider,
                    plan=plan,
                )
                violations = self.validator.validate_session(candidate.model_dump(mode="json"))
                if violations:
                    details = "; ".join(f"{v['code']}: {v['detail']}" for v in violations)
                    raise LLMOutputValidationError(f"dialogue validator violations: {details}")
                session = candidate
                break
            except (ValueError, json.JSONDecodeError, LLMOutputValidationError) as exc:
                last_error = exc
                if attempt >= max_repair_attempts:
                    raise LLMOutputValidationError(
                        f"{plan.trajectory_id}_{plan.session_id}: LLM output is invalid after "
                        f"{max_repair_attempts} repair attempts: {last_error}"
                    ) from exc
                repair = (
                    self.repair_template
                    .replace("{violations}", str(exc))
                    .replace("{original_prompt}", prompt[:4000])
                    .replace("{previous_output}", current_raw[:4000])
                )
                current_raw = self.client.generate(system, repair)
                self._write_raw_llm_output(repair_paths[attempt], current_raw)
        else:
            raise LLMOutputValidationError(f"{plan.trajectory_id}_{plan.session_id}: LLM output is invalid")

        if session is None:
            raise LLMOutputValidationError(f"{plan.trajectory_id}_{plan.session_id}: LLM output is invalid")
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
        return self._llm_session(plan, persona, raw_dir)
