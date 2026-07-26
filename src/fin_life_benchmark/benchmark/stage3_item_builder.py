"""Build Stage 3 Multi-hop benchmark items without changing Stage 2."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

from .item_builder import ItemBuilder
from .models import BenchmarkItem, CounterfactualOption
from .multihop import Stage3MultiHopTarget


class Stage3ItemBuilder(ItemBuilder):
    """Build Stage 3 items using an isolated extension of ItemBuilder."""

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
        target: Stage3MultiHopTarget,
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
                        "reasoning_type": "multi_hop",
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
