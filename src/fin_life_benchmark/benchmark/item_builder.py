"""Build benchmark items from prefix gold and trajectory-level MCQ inputs."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
from datetime import date
from typing import Any

from .mcq_input import McqWindow, Stage2Checkpoint, Stage2Target
from .models import BenchmarkItem, CounterfactualOption
from .multihop import Stage3MultiHopTarget


class ItemBuilder:
    def __init__(self, seed: int = 0, shuffle_options: bool = False):
        self.seed = seed
        self.shuffle_options = shuffle_options

    # ------------------------------------------------------------- stage 1
    def build_stage1_event_identification(
        self,
        windows: list[McqWindow],
        event_templates: dict[str, Any],
    ) -> list[BenchmarkItem]:
        """Build one occurred-event item per cumulative 15-session window."""
        candidate_events = [
            {"event_id": template.event_id, "label_ko": template.label_ko}
            for template in sorted(event_templates.values(), key=lambda item: item.event_id)
            if template.active
        ]
        items: list[BenchmarkItem] = []
        for window in windows:
            prefix_id = f"{window.trajectory_id}_w{window.window_index:02d}"
            session_range = (
                f"{window.target_session_start}~{window.target_session_end}"
            )
            date_range = self._format_date_range(
                window.target_date_start,
                window.target_date_end,
                session_range,
            )
            items.append(
                BenchmarkItem(
                    item_id=f"{prefix_id}_s1_event",
                    stage="stage1_event_identification",
                    trajectory_id=window.trajectory_id,
                    prefix_id=prefix_id,
                    visible_sessions=list(window.visible_session_ids),
                    question=(
                        "전체 상담 이력을 참고하여, "
                        f"{date_range} 기간에 마지막으로 실제 발생한 Life Event는 "
                        "무엇인가? 가능한 목록에서 하나를 선택하시오."
                    ),
                    gold={
                        "event_id": window.target_event_id,
                        "event_label": window.target_event_label,
                        "event_instance_id": window.target_event_instance_id,
                    },
                    metadata={
                        "window_index": window.window_index,
                        "window_size": window.window_size,
                        "target_session_start": window.target_session_start,
                        "target_session_end": window.target_session_end,
                        "target_date_start": window.target_date_start,
                        "target_date_end": window.target_date_end,
                        "target_event_status": window.target_event_status,
                        "candidate_events": candidate_events,
                    },
                )
            )
        return items

    # ------------------------------------------------------------- stage 2
    @staticmethod
    def _initial_memory_subset(
        initial_memory: dict[str, Any],
        paths: list[str],
    ) -> dict[str, Any]:
        return {path: copy.deepcopy(initial_memory.get(path)) for path in paths}

    @staticmethod
    def _format_date(value: str | None) -> str | None:
        if not value:
            return None
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError:
            return str(value)
        return f"{parsed.year}년 {parsed.month}월 {parsed.day}일"

    @classmethod
    def _format_date_range(
        cls,
        start: str | None,
        end: str | None,
        fallback: str,
    ) -> str:
        start_text = cls._format_date(start)
        end_text = cls._format_date(end)
        if start_text and end_text:
            return f"{start_text}~{end_text}"
        return fallback

    @staticmethod
    def _memory_label(path: str) -> str:
        labels = {
            "employment.salary_day": "급여일",
            "employment.salary_account": "급여 계좌",
            "employment.employment_status": "고용 상태",
            "employment.employer": "직장",
            "employment.income_stability": "소득 안정성",
            "housing.address": "거주지",
            "housing.residence_status": "주거 상태",
            "housing.contract_type": "주거 계약 유형",
            "housing.rent_amount": "월세 금액",
            "housing.rent_payee": "월세 수취인",
            "housing.mortgage_status": "주택담보대출 상태",
            "housing.properties": "주택 정보",
            "household.marital_status": "혼인 상태",
            "household.dependents": "부양가족 수",
            "household.dependents_count": "부양가족 수",
            "household.children_ages": "자녀 나이",
            "education.child_education_stage": "자녀 교육 단계",
            "education.self_education_status": "본인 교육 상태",
            "cashflow.recent_one_off_expense": "최근 일회성 지출",
            "financial_products.pension_or_irp": "연금·IRP 상태",
        }
        return labels.get(path, path)

    @staticmethod
    def _project_value(target: Stage2Target, state: dict[str, Any]) -> Any:
        """Project a memory cell to the scalar asked by the policy."""

        value = copy.deepcopy(state.get("value"))
        selector = target.value_selector
        if selector == "amount_krw":
            if isinstance(value, dict):
                return value.get("amount_krw", value.get("amount"))
            return value
        if selector == "stage_transition":
            return "no_change" if target.operation == "no_change" else value
        if selector == "property_address":
            if isinstance(value, list):
                matched: list[Any] = []
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    event_keys = {
                        item.get("acquisition_event_instance_id"),
                        item.get("disposal_event_instance_id"),
                        item.get("event_instance_id"),
                    }
                    if target.target_event_instance_id in event_keys:
                        matched.append(item.get("address"))
                if matched:
                    return matched[-1]
                for item in reversed(value):
                    if isinstance(item, dict) and item.get("address"):
                        return item["address"]
                return None
            if isinstance(value, dict):
                return value.get("address", value)
        if selector in {"property_loan_type", "property_ownership_status"}:
            property_record: dict[str, Any] | None = None
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    event_keys = {
                        item.get("acquisition_event_instance_id"),
                        item.get("disposal_event_instance_id"),
                        item.get("event_instance_id"),
                    }
                    if target.target_event_instance_id in event_keys:
                        property_record = item
            elif isinstance(value, dict):
                property_record = value

            if property_record is None:
                return None
            if selector == "property_ownership_status":
                return property_record.get("ownership_status")

            mortgage_status = property_record.get("mortgage_status")
            if mortgage_status == "active":
                return "mortgage"
            if mortgage_status == "none":
                return "none"
            return mortgage_status
        return value

    @staticmethod
    def _value_key(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _topic_particle(label: str) -> str:
        """Return the Korean topic particle for a memory label."""

        last = label[-1:]
        if last and "가" <= last <= "힣":
            has_final = (ord(last) - 0xAC00) % 28 != 0
            return "은" if has_final else "는"
        return "은"

    @staticmethod
    def _object_particle(label: str) -> str:
        """Return the Korean object particle for a memory label."""

        last = label[-1:]
        if last and "가" <= last <= "힣":
            has_final = (ord(last) - 0xAC00) % 28 != 0
            return "을" if has_final else "를"
        return "을"

    @staticmethod
    def _format_option_value(
        target: Stage2Target | Stage3MultiHopTarget,
        value: Any,
    ) -> str:
        if value is None:
            if target.memory_path == "housing.rent_amount":
                return "월세 없음"
            return "값 없음"
        if isinstance(value, bool):
            return "예" if value else "아니오"
        if isinstance(value, (int, float)):
            if target.memory_path == "housing.rent_amount" and value == 0:
                return "월세 없음"
            if target.memory_path == "employment.salary_day":
                return f"{value:,.0f}일"
            if (
                target.value_selector == "amount_krw"
                or "expense" in target.memory_path
                or target.memory_path == "housing.rent_amount"
            ):
                return f"{value:,.0f}원"
            if target.option_pool_type == "count":
                return f"{value:,.0f}명"
            return f"{value:,}" if isinstance(value, int) else str(value)
        if target.memory_path == "housing.contract_type" and value == "other":
            return "주거 유형이 언급되지 않음"
        if target.value_selector == "property_loan_type":
            loan_labels = {
                "none": "대출 없음",
                "credit_loan": "신용대출",
                "jeonse_loan": "전세자금대출",
                "mortgage": "주택담보대출",
            }
            return loan_labels.get(str(value), str(value))
        if target.memory_path == "housing.mortgage_status":
            mortgage_labels = {
                "none": "주택담보대출 없음",
                "active": "주택담보대출 보유",
                "closed": "주택담보대출 종료",
                "unknown": "주택담보대출 상태 확인 필요",
            }
            return mortgage_labels.get(str(value), str(value))
        if target.value_selector in {
            "property_ownership_status",
            "event_property_ownership",
        }:
            ownership_labels = {
                "owned": "현재 보유 중",
                "sold": "매각 완료",
                "pending_sale": "매각 예정",
                "unknown": "확인 불가",
            }
            return ownership_labels.get(str(value), str(value))
        translations = {
            "stable": "안정적",
            "variable": "변동적",
            "reduced": "감소",
            "unstable": "불안정",
            "employed": "재직",
            "on_leave": "휴직",
            "self_employed": "자영업",
            "unemployed": "무직",
            "retired": "은퇴",
            "owner": "자가",
            "jeonse": "전세",
            "wolse": "월세",
            "family_home": "가족과 거주",
            "single": "미혼",
            "married": "기혼",
            "separated": "별거",
            "divorced": "이혼",
            "widowed": "사별",
            "pre_school": "취학 전",
            "primary": "초등학교",
            "middle": "중학교",
            "high": "고등학교",
            "none": "해당 없음",
            "enrolled": "교육 과정 등록",
            "study_abroad": "유학 중",
            "completed": "교육 과정 완료",
            "no_change": "변화 없음",
            "irp": "IRP",
            "receiving": "연금 수령",
            "both": "IRP 및 연금 수령",
        }
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return translations.get(str(value), str(value))

    @staticmethod
    def _unique_values(values: list[Any]) -> list[Any]:
        result: list[Any] = []
        seen: set[str] = set()
        for value in values:
            key = ItemBuilder._value_key(value)
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _candidate_values(
        self,
        target: Stage2Target,
        all_targets: list[Stage2Target],
    ) -> tuple[Any, list[Any]]:
        correct = self._project_value(target, target.after_state)
        policy_values = self._unique_values(list(target.option_pool))
        if len(policy_values) == 4:
            policy_keys = {self._value_key(value) for value in policy_values}
            if self._value_key(correct) not in policy_keys:
                raise ValueError(
                    "Stage 2 gold value is outside the fixed four-option policy pool: "
                    f"{target.canonical_target_id}; value={correct!r}; "
                    f"pool={policy_values!r}"
                )
            return correct, policy_values

        before = self._project_value(target, target.before_state)
        candidates: list[Any] = [correct]
        if self._value_key(before) != self._value_key(correct):
            candidates.append(before)
        candidates.extend(target.option_pool)

        # Entity and numeric policies can reuse values already grounded in the
        # same trajectory, while keeping the correct value tied to this event.
        if target.option_pool_type in {"entity", "numeric"}:
            for sibling in all_targets:
                if sibling.trajectory_id != target.trajectory_id:
                    continue
                if sibling.memory_path != target.memory_path:
                    continue
                sibling_value = self._project_value(sibling, sibling.after_state)
                if sibling_value is not None:
                    candidates.append(sibling_value)

        if len(self._unique_values(candidates)) < 4:
            if isinstance(correct, (int, float)) and not isinstance(correct, bool):
                step = 1 if isinstance(correct, int) else 0.5
                candidates.extend(
                    [correct - step, correct + step, correct - 2 * step, correct + 2 * step]
                )
            elif target.option_pool_type == "count":
                candidates.extend([0, 1, 2, 3, 4])
            elif target.option_pool_type == "entity":
                candidates.extend(["기타 지역", "기타 직장", "기타 기관", "기타 주소"])
            elif target.option_pool_type == "categorical":
                candidates.extend(["미정", "변경 없음", "기타", "해당 없음"])

        unique = self._unique_values(candidates)
        if len(unique) < 4:
            raise ValueError(
                f"Stage 2 target cannot form four distinct options: "
                f"{target.canonical_target_id}; values={unique!r}"
            )
        return correct, unique[:]

    @staticmethod
    def _option_sort_key(
        target: Stage2Target | Stage3MultiHopTarget,
        value: Any,
    ) -> tuple[int, Any]:
        """Return a stable ascending order for the option value."""

        if value is None:
            return (9, "")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (0, value)
        if target.option_pool_type == "count":
            try:
                return (0, int(value))
            except (TypeError, ValueError):
                pass
        if target.option_pool_type == "categorical":
            pool_order = {
                ItemBuilder._value_key(candidate): index
                for index, candidate in enumerate(target.option_pool)
            }
            key = ItemBuilder._value_key(value)
            if key in pool_order:
                return (1, pool_order[key])
        return (2, ItemBuilder._format_option_value(target, value))

    def _select_four_candidates(
        self,
        target: Stage2Target,
        candidates: list[Any],
        correct_value: Any,
    ) -> list[Any]:
        ordered = sorted(
            candidates,
            key=lambda value: self._option_sort_key(target, value),
        )
        if len(ordered) <= 4:
            return ordered

        correct_key = self._value_key(correct_value)
        correct_index = next(
            index
            for index, value in enumerate(ordered)
            if self._value_key(value) == correct_key
        )
        if target.option_pool_type == "count":
            start = max(0, min(correct_index - 3, len(ordered) - 4))
            return ordered[start : start + 4]

        selected = ordered[:4]
        if not any(self._value_key(value) == correct_key for value in selected):
            selected[-1] = correct_value
            selected.sort(key=lambda value: self._option_sort_key(target, value))
        return selected

    def _shuffle_stage2_options(
        self,
        target: Stage2Target | Stage3MultiHopTarget,
        options: list[CounterfactualOption],
        checkpoint_session_count: int,
    ) -> tuple[list[CounterfactualOption], str]:
        """Optionally shuffle one target for one checkpoint.

        The option values remain canonical for the target, while the
        checkpoint participates in the shuffle seed so later evaluations may
        use a different A-D assignment.
        """

        if not self.shuffle_options:
            correct = next(option.option_id for option in options if option.correct)
            return options, correct

        payload = (
            f"{self.seed}:{target.canonical_target_id}:"
            f"{checkpoint_session_count}"
        ).encode("utf-8")
        shuffle_seed = int.from_bytes(
            hashlib.sha256(payload).digest()[:8], byteorder="big", signed=False
        )
        shuffled = [option.model_copy(deep=True) for option in options]
        random.Random(shuffle_seed).shuffle(shuffled)
        relabeled: list[CounterfactualOption] = []
        for index, option in enumerate(shuffled):
            relabeled.append(
                option.model_copy(update={"option_id": "ABCD"[index]})
            )
        correct = next(option.option_id for option in relabeled if option.correct)
        return relabeled, correct

    def _stage2_options_for_target(
        self,
        target: Stage2Target,
        all_targets: list[Stage2Target],
    ) -> tuple[list[CounterfactualOption], str]:
        """Create four deterministic value-based options for one event target."""

        correct_value, candidates = self._candidate_values(target, all_targets)
        candidates = self._select_four_candidates(
            target, candidates, correct_value
        )
        correct_key = self._value_key(correct_value)

        options: list[CounterfactualOption] = []
        before_value = self._project_value(target, target.before_state)
        before_key = self._value_key(before_value)
        for index, value in enumerate(candidates):
            value_key = self._value_key(value)
            is_correct = value_key == correct_key
            error_type = None
            if not is_correct:
                error_type = (
                    "stale_memory_carryover"
                    if value_key == before_key
                    else "value_distractor"
                )
            options.append(
                CounterfactualOption(
                    option_id="ABCD"[index],
                    text=self._format_option_value(target, value),
                    error_type=error_type,
                    correct=is_correct,
                )
            )

        correct_options = [option for option in options if option.correct]
        option_ids = [option.option_id for option in options]
        option_texts = [option.text for option in options]
        if option_ids != list("ABCD") or len(set(option_texts)) != 4:
            raise ValueError(
                f"stage2 target must have four distinct A-D options: "
                f"{target.canonical_target_id}"
            )
        if len(correct_options) != 1:
            raise ValueError(
                f"stage2 target must have exactly one correct option: "
                f"{target.canonical_target_id}"
            )
        return options, correct_options[0].option_id

    def _build_canonical_stage2_payload(
        self,
        target: Stage2Target,
        initial_memory_by_traj: dict[str, dict[str, Any]],
        all_targets: list[Stage2Target],
        window_size: int,
        target_date_start: str | None = None,
        target_date_end: str | None = None,
    ) -> tuple[str, list[CounterfactualOption], dict[str, Any], dict[str, Any]]:
        question_label = target.question_label
        if question_label in {"memory", target.memory_path}:
            question_label = self._memory_label(target.memory_path)
        if question_label.startswith("현재 "):
            question_label = question_label.removeprefix("현재 ")
        particle = self._topic_particle(question_label)
        window_end = target.first_visible_checkpoint
        window_start = max(1, window_end - window_size + 1)
        session_window_range = f"S{window_start:03d}~S{window_end:03d}"
        window_range = self._format_date_range(
            target_date_start,
            target_date_end,
            session_window_range,
        )
        window_start_label = self._format_date(target_date_start) or f"S{window_start:03d}"
        window_end_label = self._format_date(target_date_end) or f"S{window_end:03d}"
        if target.question_scope == "latest_window":
            question_prefix = (
                f"제공된 전체 상담 이력을 참고하여, {window_range} 기간에 "
                f"새로 반영된 {question_label}{particle}"
            )
        else:
            question_prefix = (
                f"제공된 전체 상담 이력을 참고하여, {window_range} 기간 종료 시점의 "
                f"현재 {question_label}{particle}"
            )
        if target.question_template:
            question = (
                target.question_template
                .replace("{window_start}", window_start_label)
                .replace("{window_end}", window_end_label)
                .replace("{window_range}", window_range)
            )
            if "{window_range}" not in target.question_template:
                question = (
                    f"제공된 전체 상담 이력을 참고하여, {window_range} 기간 기준으로 "
                    f"{question}"
                )
        elif target.option_pool_type == "count":
            question = f"{question_prefix} 몇 명인가?"
        elif target.option_pool_type == "numeric" and target.value_selector == "amount_krw":
            question = f"{question_prefix} 얼마인가?"
        else:
            question = f"{question_prefix} 무엇인가?"
        options, correct_id = self._stage2_options_for_target(target, all_targets)
        gold = {
            "correct_option": correct_id,
            "answer_value": self._project_value(target, target.after_state),
            "canonical_target_id": target.canonical_target_id,
            "target_event_instance_id": target.target_event_instance_id,
            "target_event_id": target.target_event_id,
            "target_event_label": target.target_event_label,
            "memory_path": target.memory_path,
            "value_selector": target.value_selector,
            "operation": target.operation,
            "before_state": copy.deepcopy(target.before_state),
            "after_state": copy.deepcopy(target.after_state),
            "evidence_sessions": list(target.evidence_sessions),
            "evidence_turns": list(target.evidence_turns),
        }
        metadata = {
            "canonical_target_id": target.canonical_target_id,
            "target_event_instance_id": target.target_event_instance_id,
            "target_event_id": target.target_event_id,
            "target_event_label": target.target_event_label,
            "memory_path": target.memory_path,
            "value_selector": target.value_selector,
            "question_template": target.question_template,
            "question_label": target.question_label,
            "question_scope": target.question_scope,
            "option_pool_type": target.option_pool_type,
            "operation": target.operation,
            "first_visible_checkpoint": target.first_visible_checkpoint,
            "target_date_start": target_date_start,
            "target_date_end": target_date_end,
            "options_shuffled": self.shuffle_options,
            "evidence_sessions": list(target.evidence_sessions),
            "initial_memory": self._initial_memory_subset(
                initial_memory_by_traj.get(target.trajectory_id, {}),
                [target.memory_path],
            ),
        }
        return question, options, gold, metadata

    def build_stage2(
        self,
        checkpoints: list[Stage2Checkpoint],
        initial_memory_by_traj: dict[str, dict[str, Any]] | None = None,
        window_size: int = 15,
    ) -> list[BenchmarkItem]:
        """Build one item for every eligible event at every checkpoint.

        The visible context is cumulative. A target's question, options, and
        gold remain canonical across checkpoints; only the visible prefix gets
        longer as the checkpoint advances.
        """
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        initial_memory_by_traj = initial_memory_by_traj or {}
        all_targets_by_id: dict[str, Stage2Target] = {}
        for checkpoint in checkpoints:
            for target in checkpoint.targets:
                all_targets_by_id.setdefault(target.canonical_target_id, target)
        all_targets = list(all_targets_by_id.values())

        canonical_cache: dict[
            str, tuple[str, list[CounterfactualOption], dict[str, Any], dict[str, Any]]
        ] = {}
        items: list[BenchmarkItem] = []

        for checkpoint in checkpoints:
            ordered_targets = sorted(
                checkpoint.targets,
                key=lambda target: target.canonical_target_id,
            )
            for target in ordered_targets:
                payload = canonical_cache.get(target.canonical_target_id)
                if payload is None:
                    payload = self._build_canonical_stage2_payload(
                        target,
                        initial_memory_by_traj,
                        all_targets,
                        window_size,
                        checkpoint.target_date_start,
                        checkpoint.target_date_end,
                    )
                    canonical_cache[target.canonical_target_id] = payload

                question, canonical_options, canonical_gold, metadata = payload
                options = [
                    option.model_copy(deep=True) for option in canonical_options
                ]
                options, correct_id = self._shuffle_stage2_options(
                    target,
                    options,
                    checkpoint.checkpoint_session_count,
                )
                gold = copy.deepcopy(canonical_gold)
                gold["correct_option"] = correct_id
                safe_target_id = re.sub(
                    r"[^A-Za-z0-9_.-]+", "_", target.canonical_target_id
                )
                item_id = (
                    f"{checkpoint.trajectory_id}_s{checkpoint.checkpoint_session_count:03d}_"
                    f"s2_{safe_target_id}"
                )
                items.append(
                    BenchmarkItem(
                        item_id=item_id,
                        stage="stage2_memory_mcq",
                        reasoning_type="single_hop",
                        trajectory_id=checkpoint.trajectory_id,
                        prefix_id=checkpoint.prefix_id,
                        visible_sessions=list(checkpoint.visible_session_ids),
                        question=question,
                        options=options,
                        gold=copy.deepcopy(gold),
                        metadata={
                            **copy.deepcopy(metadata),
                            "checkpoint_session_count": checkpoint.checkpoint_session_count,
                            "n_visible_sessions": len(checkpoint.visible_session_ids),
                        },
                    )
                )
        return items

    # ------------------------------------------------------- stage 3 multi-hop
    @staticmethod
    def _multihop_semantic_value_key(memory_path: str, value: Any) -> str:
        """Collapse schema aliases that produce the same displayed answer."""

        if memory_path == "housing.rent_amount" and (
            value is None
            or (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value == 0
            )
        ):
            return ItemBuilder._value_key("__no_rent__")
        return ItemBuilder._value_key(value)

    def _multihop_alternate_value(
        self,
        target: Stage3MultiHopTarget,
        all_targets: list[Stage3MultiHopTarget],
    ) -> Any:
        """Choose a plausible third value instead of repeating either answer value."""

        first, second = (fact.projected_value for fact in target.hops)
        blocked = {
            self._multihop_semantic_value_key(target.memory_path, first),
            self._multihop_semantic_value_key(target.memory_path, second),
        }
        endpoint = target.first_visible_checkpoint
        ranked_candidates: list[tuple[int, Any]] = []

        # Prefer a value the same customer has already exposed by this checkpoint.
        for candidate_target in all_targets:
            if (
                candidate_target.trajectory_id != target.trajectory_id
                or candidate_target.memory_path != target.memory_path
            ):
                continue
            for fact in candidate_target.hops:
                if fact.checkpoint_session_count <= endpoint:
                    ranked_candidates.append((0, fact.projected_value))

        # Registry values are type-consistent and make stable categorical distractors.
        ranked_candidates.extend((1, value) for value in target.option_pool)

        # Entity-valued paths have no fixed pool, so use values observed elsewhere.
        for candidate_target in all_targets:
            if candidate_target.memory_path != target.memory_path:
                continue
            ranked_candidates.extend(
                (2, fact.projected_value) for fact in candidate_target.hops
            )

        unique: dict[str, tuple[int, Any]] = {}
        for source_rank, candidate in ranked_candidates:
            key = self._multihop_semantic_value_key(target.memory_path, candidate)
            if key in blocked:
                continue
            previous = unique.get(key)
            if previous is None or source_rank < previous[0]:
                unique[key] = (source_rank, copy.deepcopy(candidate))

        numeric_target = all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (first, second)
        )
        candidates = list(unique.values())
        if numeric_target:
            candidates = [
                row
                for row in candidates
                if isinstance(row[1], (int, float))
                and not isinstance(row[1], bool)
            ]
            if candidates:
                _, selected = min(
                    candidates,
                    key=lambda row: (
                        row[0],
                        abs(row[1] - first) + abs(row[1] - second),
                        self._value_key(row[1]),
                    ),
                )
                return copy.deepcopy(selected)
            return max(first, second) + 1

        if not candidates:
            raise ValueError(
                "Multi-hop target requires a plausible third value: "
                f"{target.canonical_target_id}"
            )
        _, selected = min(
            candidates,
            key=lambda row: (
                row[0],
                hashlib.sha256(
                    f"{target.canonical_target_id}:{self._value_key(row[1])}".encode(
                        "utf-8"
                    )
                ).hexdigest(),
            ),
        )
        return copy.deepcopy(selected)

    def _format_multihop_pair(
        self,
        target: Stage3MultiHopTarget,
        pair: list[Any],
    ) -> str:
        first_value = self._format_option_value(target, pair[0])
        second_value = self._format_option_value(target, pair[1])
        return f"{first_value} → {second_value}"

    @staticmethod
    def _expense_candidates(
        first: int | float,
        second: int | float,
    ) -> list[tuple[int | float, str | None]]:
        correct = first + second
        if first == second:
            step = max(
                100_000,
                math.ceil(abs(correct) * 0.1 / 100_000) * 100_000,
            )
            raw: list[tuple[int | float, str | None]] = [
                (correct, None),
                (first, "first_hop_only"),
                (max(1, correct - step), "underestimated_sum"),
                (correct + step, "overestimated_sum"),
                (max(1, correct - step / 2), "underestimated_sum"),
                (correct + step * 2, "overestimated_sum"),
            ]
        else:
            raw = [
                (correct, None),
                (first, "first_hop_only"),
                (second, "second_hop_only"),
            ]
            difference = abs(first - second)
            if difference > 0:
                raw.append((difference, "difference_instead_of_sum"))
            positive = [abs(value) for value in (first, second) if value]
            step = min(positive) if positive else 1
            raw.extend(
                [
                    (max(1, correct - step), "arithmetic_distractor"),
                    (correct + step, "arithmetic_distractor"),
                    ((first + second) / 2, "average_instead_of_sum"),
                ]
            )

        unique: list[tuple[int | float, str | None]] = []
        seen: set[str] = set()
        for value, error_type in raw:
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            key = ItemBuilder._value_key(value)
            if key in seen:
                continue
            seen.add(key)
            unique.append((value, error_type))
        if len(unique) < 4:
            raise ValueError(
                f"cannot form four expense aggregation options: {first}, {second}"
            )
        selected = unique[:4]
        if not any(error_type is None for _, error_type in selected):
            selected[-1] = (correct, None)
        return sorted(selected, key=lambda item: item[0])

    def _multihop_options(
        self,
        target: Stage3MultiHopTarget,
        all_targets: list[Stage3MultiHopTarget],
    ) -> tuple[list[CounterfactualOption], str]:
        first = target.hops[0].projected_value
        second = target.hops[1].projected_value
        raw_options: list[tuple[Any, str | None]]

        if target.derivation_type == "expense_aggregation":
            if not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in (first, second)
            ):
                raise ValueError(
                    "expense aggregation requires two numeric values: "
                    f"{target.canonical_target_id}"
                )
            raw_options = self._expense_candidates(first, second)
            option_rows = [
                (
                    value,
                    self._format_option_value(target, value),
                    error_type,
                    error_type is None,
                )
                for value, error_type in raw_options
            ]
        else:
            correct_pair = [copy.deepcopy(first), copy.deepcopy(second)]
            alternate = self._multihop_alternate_value(target, all_targets)
            if self._multihop_semantic_value_key(
                target.memory_path, first
            ) == self._multihop_semantic_value_key(target.memory_path, second):
                raw_options = [
                    (correct_pair, None),
                    ([alternate, second], "wrong_first_hop"),
                    ([first, alternate], "wrong_second_hop"),
                    ([alternate, alternate], "wrong_both_hops"),
                ]
            else:
                raw_options = [
                    (correct_pair, None),
                    ([second, first], "reversed_hop_order"),
                    ([alternate, second], "wrong_first_hop"),
                    ([first, alternate], "wrong_second_hop"),
                ]
            option_rows = sorted(
                [
                    (
                        pair,
                        self._format_multihop_pair(target, pair),
                        error_type,
                        error_type is None,
                    )
                    for pair, error_type in raw_options
                ],
                key=lambda row: row[1],
            )

        if len({text for _, text, _, _ in option_rows}) != 4:
            raise ValueError(
                "Multi-hop target must have four distinct option texts: "
                f"{target.canonical_target_id}"
            )
        options = [
            CounterfactualOption(
                option_id="ABCD"[index],
                text=text,
                error_type=error_type,
                correct=correct,
            )
            for index, (_, text, error_type, correct) in enumerate(option_rows)
        ]
        options, correct_id = self._shuffle_stage2_options(
            target,
            options,
            target.first_visible_checkpoint,
        )
        if sum(option.correct for option in options) != 1:
            raise ValueError(
                "Multi-hop target must have exactly one correct option: "
                f"{target.canonical_target_id}"
            )
        return options, correct_id

    def _multihop_question(self, target: Stage3MultiHopTarget) -> str:
        first_date = self._format_date(target.hops[0].evidence_date)
        second_date = self._format_date(target.hops[1].evidence_date)
        if target.derivation_type == "expense_aggregation":
            return (
                "제공된 전체 상담 이력을 참고하여, "
                f"{first_date}과 {second_date} 상담에서 확인된 "
                "일회성 지출의 대략적인 합계는 얼마인가?"
            )
        particle = self._object_particle(target.question_label)
        return (
            "제공된 전체 상담 이력을 참고하여, "
            f"{first_date}과 {second_date}의 각 상담 시점에 확인된 "
            f"{target.question_label}{particle} 시간순으로 올바르게 "
            "나열한 것은 무엇인가?"
        )

    @staticmethod
    def _multihop_gold_hop(fact: Any) -> dict[str, Any]:
        return {
            "fact_id": fact.fact_id,
            "prefix_id": fact.prefix_id,
            "checkpoint_session_count": fact.checkpoint_session_count,
            "event_instance_id": fact.event_instance_id,
            "event_id": fact.event_id,
            "event_label": fact.event_label,
            "memory_path": fact.memory_path,
            "operation": fact.operation,
            "old_value": copy.deepcopy(fact.old_value),
            "new_value": copy.deepcopy(fact.new_value),
            "projected_value": copy.deepcopy(fact.projected_value),
            "evidence_sessions": list(fact.evidence_sessions),
            "evidence_turns": list(fact.evidence_turns),
            "evidence_date": fact.evidence_date,
        }

    def build_stage3_multihop(
        self,
        targets: list[Stage3MultiHopTarget] | tuple[Stage3MultiHopTarget, ...],
        initial_memory_by_traj: dict[str, dict[str, Any]] | None = None,
    ) -> list[BenchmarkItem]:
        """Build one Multi-hop MCQ at the checkpoint where hop 2 becomes visible."""

        initial_memory_by_traj = initial_memory_by_traj or {}
        all_targets = list(targets)
        items: list[BenchmarkItem] = []
        for target in sorted(
            all_targets,
            key=lambda item: (
                item.trajectory_id,
                item.first_visible_checkpoint,
                item.canonical_target_id,
            ),
        ):
            options, correct_id = self._multihop_options(target, all_targets)
            safe_target_id = re.sub(
                r"[^A-Za-z0-9_.-]+", "_", target.canonical_target_id
            )
            hops = [self._multihop_gold_hop(fact) for fact in target.hops]
            items.append(
                BenchmarkItem(
                    item_id=f"{safe_target_id}_s3_multi",
                    stage="stage3_multi_hop_mcq",
                    reasoning_type="multi_hop",
                    trajectory_id=target.trajectory_id,
                    prefix_id=target.prefix_id,
                    visible_sessions=list(target.visible_session_ids),
                    question=self._multihop_question(target),
                    options=options,
                    gold={
                        "correct_option": correct_id,
                        "answer_value": copy.deepcopy(target.answer_value),
                        "canonical_target_id": target.canonical_target_id,
                        "derivation_type": target.derivation_type,
                        "hop_count": len(hops),
                        "memory_path": target.memory_path,
                        "value_selector": target.value_selector,
                        "source_event_instance_ids": [
                            fact.event_instance_id for fact in target.hops
                        ],
                        "source_prefix_ids": [fact.prefix_id for fact in target.hops],
                        "hops": hops,
                    },
                    metadata={
                        "canonical_target_id": target.canonical_target_id,
                        "derivation_type": target.derivation_type,
                        "hop_count": len(hops),
                        "memory_path": target.memory_path,
                        "question_label": target.question_label,
                        "option_pool_type": target.option_pool_type,
                        "first_visible_checkpoint": target.first_visible_checkpoint,
                        "n_visible_sessions": len(target.visible_session_ids),
                        "target_dates": [fact.evidence_date for fact in target.hops],
                        "options_shuffled": self.shuffle_options,
                        "initial_memory": self._initial_memory_subset(
                            initial_memory_by_traj.get(target.trajectory_id, {}),
                            [target.memory_path],
                        ),
                    },
                )
            )
        return items
