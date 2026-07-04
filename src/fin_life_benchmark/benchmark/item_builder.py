"""Build benchmark items from prefix gold.

Stage 1 — event status detection      (prefix -> event label/status/occurred)
Stage 2 — financial memory MCQ        (prefix + initial memory -> current state)
"""

from __future__ import annotations

import random
from typing import Any

from .models import BenchmarkItem, CounterfactualOption

_EVIDENCE_TYPES = {
    "weak_signal_evidence",
    "upcoming_evidence",
    "occurred_evidence",
    "cancellation_evidence",
    "consequence_session",
    "hard_negative",
    "stale_recall_session",
}

_STAGE1_QUESTION = (
    "지금까지의 상담 세션 이력만을 근거로, 감지되는 고객 Life Event와 "
    "각 이벤트의 상태(weak_signal/upcoming/occurred/cancelled)를 모두 나열하시오. "
    "확인되는 이벤트가 없으면 no_event라고 답하시오."
)
def _session_lookup(sessions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["session_id"]: s for s in sessions}


def _last_session_type(prefix: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> str:
    last_id = prefix["visible_sessions"][-1]
    session = lookup.get(last_id) or {}
    return session.get("session_type", "")


class ItemBuilder:
    def __init__(self, seed: int = 0):
        self.seed = seed

    # ------------------------------------------------------------- stage 1
    def build_stage1(
        self,
        prefixes: list[dict[str, Any]],
        sessions_by_traj: dict[str, list[dict[str, Any]]],
    ) -> list[BenchmarkItem]:
        items: list[BenchmarkItem] = []
        for prefix in prefixes:
            lookup = _session_lookup(sessions_by_traj.get(prefix["trajectory_id"], []))
            if _last_session_type(prefix, lookup) not in _EVIDENCE_TYPES:
                continue
            gold_events = [
                {
                    "life_event_label": e["life_event_label"],
                    "event_status": e["event_status"],
                    "occurred": e["occurred"],
                    "evidence_sessions": e["evidence_sessions"],
                }
                for e in prefix["gold_life_events"]
            ]
            items.append(
                BenchmarkItem(
                    item_id=f"{prefix['prefix_id']}_s1",
                    stage="stage1_event_status",
                    trajectory_id=prefix["trajectory_id"],
                    prefix_id=prefix["prefix_id"],
                    visible_sessions=prefix["visible_sessions"],
                    question=_STAGE1_QUESTION,
                    gold={"life_events": gold_events or [{"life_event_label": None, "event_status": "no_event"}]},
                    metadata={"last_session_type": _last_session_type(prefix, lookup)},
                )
            )
        return items

    # ------------------------------------------------------------- stage 2
    def build_stage2(self, prefixes: list[dict[str, Any]], sessions_by_traj: dict) -> list[BenchmarkItem]:
        items: list[BenchmarkItem] = []
        seen_counts: dict[str, int] = {}
        for prefix in prefixes:
            traj = prefix["trajectory_id"]
            updates = prefix["gold_memory_updates"]
            n_updates = len(updates)
            previous = seen_counts.get(traj, 0)
            if n_updates == 0 or n_updates == previous:
                seen_counts[traj] = n_updates
                continue
            seen_counts[traj] = n_updates
            new_updates = updates[previous:n_updates]
            single = self._stage2_single_hop_item(prefix, new_updates)
            if single is not None:
                items.append(single)
            multi = self._stage2_multi_hop_item(prefix, updates)
            if multi is not None:
                items.append(multi)
        return items

    @staticmethod
    def _format_memory_value(value: Any) -> str:
        if value is None:
            return "값 없음"
        if isinstance(value, bool):
            return "예" if value else "아니오"
        if isinstance(value, (int, float)):
            return f"{value:,}" if isinstance(value, int) else str(value)
        if isinstance(value, list):
            return ", ".join(str(v) for v in value) if value else "빈 목록"
        if isinstance(value, dict):
            parts = [f"{k}={v}" for k, v in sorted(value.items())[:3]]
            return ", ".join(parts) if parts else "빈 객체"
        return str(value)

    @staticmethod
    def _memory_label(path: str) -> str:
        labels = {
            "employment.salary_day": "급여일",
            "employment.employment_status": "고용 상태",
            "employment.employer": "직장",
            "housing.address": "거주지",
            "housing.residence_status": "주거 상태",
            "housing.contract_type": "주거 계약 유형",
            "housing.rent_amount": "월세 금액",
            "housing.rent_payee": "월세 수취인",
            "housing.mortgage_status": "주택담보대출 상태",
            "household.marital_status": "혼인 상태",
            "household.dependents_count": "부양가족 수",
            "household.children_ages": "자녀 나이",
            "financial_products.loans": "대출 정보",
        }
        return labels.get(path, path)

    def _memory_option_text(self, path: str, value: Any, status: str) -> str:
        return f"{self._memory_label(path)}: 상태={status}, 값={self._format_memory_value(value)}"

    def _stage2_options_for_path(
        self,
        prefix: dict[str, Any],
        update: dict[str, Any],
        rng: random.Random,
    ) -> tuple[list[CounterfactualOption], str] | None:
        path = update["path"]
        memory = prefix.get("gold_full_memory_state") or {}
        cell = memory.get(path) or {}
        correct_value = cell.get("value", update.get("new_value"))
        correct_status = cell.get("status", "current")
        historical = list(cell.get("historical_values") or [])
        if update.get("old_value") is not None:
            historical.append(update.get("old_value"))

        option_specs: list[tuple[str, str | None, bool]] = [
            (self._memory_option_text(path, correct_value, correct_status), None, True)
        ]
        if historical:
            stale_value = historical[-1]
            option_specs.append(
                (self._memory_option_text(path, stale_value, "current"), "stale_memory_carryover", False)
            )
        else:
            option_specs.append((f"{self._memory_label(path)}: 기존 정보에서 바뀐 점이 없다.", "missed_update", False))

        if correct_status == "current":
            option_specs.append(
                (self._memory_option_text(path, correct_value, "needs_verification"), "premature_update", False)
            )
        else:
            option_specs.append((self._memory_option_text(path, correct_value, "current"), "false_commit", False))

        option_specs.append((f"{self._memory_label(path)}: 관련 생애 사건이 없으므로 갱신하지 않는다.", "missed_update", False))

        # Add a wrong sibling path when available so models must track the path,
        # not just reuse a salient value from the prompt.
        for other_path, other_cell in sorted(memory.items()):
            if other_path == path:
                continue
            if other_cell.get("value") is not None:
                option_specs.append(
                    (
                        self._memory_option_text(other_path, other_cell.get("value"), other_cell.get("status", "current")),
                        "wrong_sibling_event",
                        False,
                    )
                )
                break

        # De-duplicate while preserving correctness.
        deduped: list[tuple[str, str | None, bool]] = []
        seen: set[str] = set()
        for text, error, correct in option_specs:
            if text in seen:
                continue
            seen.add(text)
            deduped.append((text, error, correct))
        if len(deduped) < 3:
            return None

        rng.shuffle(deduped)
        letters = "ABCDE"
        options = [
            CounterfactualOption(option_id=letters[i], text=text, error_type=error, correct=correct)
            for i, (text, error, correct) in enumerate(deduped[:5])
        ]
        if not any(o.correct for o in options):
            options[-1] = CounterfactualOption(
                option_id=options[-1].option_id,
                text=self._memory_option_text(path, correct_value, correct_status),
                correct=True,
            )
        correct_id = next(o.option_id for o in options if o.correct)
        return options, correct_id

    def _stage2_single_hop_item(
        self,
        prefix: dict[str, Any],
        new_updates: list[dict[str, Any]],
    ) -> BenchmarkItem | None:
        if not new_updates:
            return None
        rng = random.Random(f"{prefix['prefix_id']}:stage2_single:{self.seed}")
        update = rng.choice(new_updates)
        built = self._stage2_options_for_path(prefix, update, rng)
        if built is None:
            return None
        options, correct_id = built
        path = update["path"]
        question = (
            "지금까지의 상담 세션 이력과 초기 금융 메모리를 함께 고려할 때, "
            f"현재 금융 메모리의 '{self._memory_label(path)}' 항목으로 가장 적절한 것은 무엇인가?"
        )
        return BenchmarkItem(
            item_id=f"{prefix['prefix_id']}_{path.replace('.', '_')}_s2_single_mcq",
            stage="stage2_memory_mcq",
            trajectory_id=prefix["trajectory_id"],
            prefix_id=prefix["prefix_id"],
            visible_sessions=prefix["visible_sessions"],
            question=question,
            options=options,
            gold={
                "correct_option": correct_id,
                "memory_path": path,
                "current_cell": (prefix.get("gold_full_memory_state") or {}).get(path),
            },
            metadata={
                "hop_type": "single",
                "source_update_operation": update.get("operation"),
                "n_visible_sessions": len(prefix["visible_sessions"]),
            },
        )

    def _stage2_multi_hop_item(
        self,
        prefix: dict[str, Any],
        updates: list[dict[str, Any]],
    ) -> BenchmarkItem | None:
        memory = prefix.get("gold_full_memory_state") or {}
        paths = []
        for update in reversed(updates):
            path = update["path"]
            if path in memory and path not in paths:
                paths.append(path)
            if len(paths) == 2:
                break
        if len(paths) < 2:
            return None
        paths = list(reversed(paths))
        rng = random.Random(f"{prefix['prefix_id']}:stage2_multi:{self.seed}")

        def statement(path_a: str, value_a: Any, status_a: str, path_b: str, value_b: Any, status_b: str) -> str:
            return (
                f"{self._memory_option_text(path_a, value_a, status_a)} / "
                f"{self._memory_option_text(path_b, value_b, status_b)}"
            )

        a, b = paths
        cell_a = memory.get(a) or {}
        cell_b = memory.get(b) or {}
        value_a, status_a = cell_a.get("value"), cell_a.get("status", "current")
        value_b, status_b = cell_b.get("value"), cell_b.get("status", "current")
        hist_a = list(cell_a.get("historical_values") or [])
        hist_b = list(cell_b.get("historical_values") or [])

        option_specs: list[tuple[str, str | None, bool]] = [
            (statement(a, value_a, status_a, b, value_b, status_b), None, True)
        ]
        if hist_a:
            option_specs.append((statement(a, hist_a[-1], "current", b, value_b, status_b), "stale_memory_carryover", False))
        if hist_b:
            option_specs.append((statement(a, value_a, status_a, b, hist_b[-1], "current"), "stale_memory_carryover", False))
        option_specs.append((statement(a, value_b, status_b, b, value_a, status_a), "historical_state_contamination", False))
        option_specs.append((f"{self._memory_label(a)}와 {self._memory_label(b)} 모두 기존 정보에서 바뀐 점이 없다.", "missed_update", False))

        deduped: list[tuple[str, str | None, bool]] = []
        seen: set[str] = set()
        for text, error, correct in option_specs:
            if text in seen:
                continue
            seen.add(text)
            deduped.append((text, error, correct))
        if len(deduped) < 3:
            return None

        rng.shuffle(deduped)
        letters = "ABCDE"
        options = [
            CounterfactualOption(option_id=letters[i], text=text, error_type=error, correct=correct)
            for i, (text, error, correct) in enumerate(deduped[:5])
        ]
        if not any(o.correct for o in options):
            options[-1] = CounterfactualOption(
                option_id=options[-1].option_id,
                text=statement(a, value_a, status_a, b, value_b, status_b),
                correct=True,
            )
        correct_id = next(o.option_id for o in options if o.correct)
        question = (
            "지금까지의 상담 세션 이력 전체를 종합할 때, 다음 두 금융 메모리 항목의 "
            "현재 상태를 가장 정확하게 요약한 보기는 무엇인가?"
        )
        return BenchmarkItem(
            item_id=f"{prefix['prefix_id']}_{a.replace('.', '_')}__{b.replace('.', '_')}_s2_multi_mcq",
            stage="stage2_memory_mcq",
            trajectory_id=prefix["trajectory_id"],
            prefix_id=prefix["prefix_id"],
            visible_sessions=prefix["visible_sessions"],
            question=question,
            options=options,
            gold={
                "correct_option": correct_id,
                "memory_paths": [a, b],
                "current_cells": {a: cell_a, b: cell_b},
            },
            metadata={
                "hop_type": "multi",
                "n_visible_sessions": len(prefix["visible_sessions"]),
            },
        )
