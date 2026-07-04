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

from ..io import RepoPaths
from ..io import load_yaml
from ..llm.client import LLMClient
from ..persona.models import NormalizedPersona
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


def _slugify(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "_", text).strip("_")[:40]


def _contains_any(text: str, terms: list[str]) -> str | None:
    for term in terms:
        if term and term in text:
            return term
    return None


class DialogueGenerator:
    def __init__(
        self,
        mode: str = "mock",
        client: LLMClient | None = None,
        paths: RepoPaths | None = None,
    ):
        if mode not in {"mock", "dry_run", "llm"}:
            raise ValueError(f"unknown dialogue mode: {mode}")
        self.mode = mode
        self.client = client
        self.paths = paths or RepoPaths.default()
        self.cfg = load_yaml(self.paths.generation / "dialogue.yaml")
        self.prompt_template = (self.paths.prompts / "dialogue" / "generate_banking_session_ko.md").read_text(encoding="utf-8")
        self.repair_template = (self.paths.prompts / "dialogue" / "repair_banking_session_ko.md").read_text(encoding="utf-8")

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
        replacements = {
            "{age}": str(plan.age),
            "{user_style}": persona.style.formality + ", " + persona.style.verbosity,
            "{persona_summary}": persona.persona_text[:200],
            "{session_type}": plan.session_type,
            "{financial_task}": plan.financial_task,
            "{event_status}": plan.event_status_after_session,
            "{must_include_cues}": json.dumps(plan.must_include_cues, ensure_ascii=False),
            "{must_not_include_terms}": json.dumps(plan.must_not_include_terms, ensure_ascii=False),
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

    def _llm_session(self, plan: DialogueGenerationPlan, persona: NormalizedPersona, raw_dir: Path) -> Session:
        assert self.client is not None, "llm mode requires an LLMClient"
        prompt = self._build_prompt(plan, persona)
        system = "당신은 은행 상담 대화 데이터 생성기입니다. JSON만 출력합니다."
        raw = self.client.generate(system, prompt)

        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{plan.trajectory_id}_{plan.session_id}.txt").write_text(raw, encoding="utf-8")

        try:
            payload = self._parse_llm_json(raw)
        except (ValueError, json.JSONDecodeError):
            repair = (
                self.repair_template
                .replace("{violations}", "출력이 유효한 JSON이 아닙니다.")
                .replace("{original_prompt}", prompt[:4000])
                .replace("{previous_output}", raw[:4000])
            )
            raw = self.client.generate(system, repair)
            (raw_dir / f"{plan.trajectory_id}_{plan.session_id}_repair.txt").write_text(raw, encoding="utf-8")
            payload = self._parse_llm_json(raw)

        turns = [Turn(speaker=t["speaker"], text=t["text"]) for t in payload.get("turns", [])]
        cues = [
            CueAnnotation(
                turn_index=int(c.get("turn_index", 0)),
                cue_type=str(c.get("cue_type", "unknown")),
                linked_memory_path=c.get("linked_memory_path"),
            )
            for c in payload.get("cue_annotations", [])
        ]
        check = QualitySelfCheck(**(payload.get("quality_self_check") or {}))
        return Session(
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

    # ------------------------------------------------------------------ main
    def generate_session(self, plan: DialogueGenerationPlan, persona: NormalizedPersona) -> Session | None:
        raw_dir = self.paths.raw_model_outputs / "dialogue"
        if self.mode == "mock":
            return self._mock_session(plan, persona)
        if self.mode == "dry_run":
            raw_dir.mkdir(parents=True, exist_ok=True)
            prompt = self._build_prompt(plan, persona)
            (raw_dir / f"{plan.trajectory_id}_{plan.session_id}_prompt.txt").write_text(prompt, encoding="utf-8")
            return None
        return self._llm_session(plan, persona, raw_dir)
