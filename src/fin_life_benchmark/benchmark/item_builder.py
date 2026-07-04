"""Build benchmark items from prefix gold.

Stage 1 — event status detection      (prefix -> event label/status/occurred)
Stage 2 — financial memory update     (prefix + initial memory -> updates)
Stage 3 — standing action decision    (prefix + memory + actions -> decisions)
Stage 3 MCQ — counterfactual diagnostics with stale/unsafe distractors drawn
from the trajectory's own historical values.
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
_STAGE2_QUESTION = (
    "지금까지의 상담 세션 이력과 초기 금융 메모리 상태를 근거로, "
    "수행해야 할 금융 메모리 업데이트(경로, 연산, 새 값)를 모두 나열하시오. "
    "확정되지 않은 변화는 needs_verification 또는 pending으로 표시해야 한다."
)
_STAGE3_QUESTION = (
    "지금까지의 상담 세션 이력, 금융 메모리 상태, 등록된 정기 금융 액션을 근거로, "
    "각 정기 액션에 대해 취할 결정(keep/update/pause/cancel/ask_confirmation)을 정하시오. "
    "출금이 발생하는 변경은 사용자 확인 없이 실행해서는 안 된다."
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
            n_updates = len(prefix["gold_memory_updates"])
            if n_updates == 0 or n_updates == seen_counts.get(traj, 0):
                seen_counts[traj] = n_updates
                continue
            seen_counts[traj] = n_updates
            items.append(
                BenchmarkItem(
                    item_id=f"{prefix['prefix_id']}_s2",
                    stage="stage2_memory_update",
                    trajectory_id=traj,
                    prefix_id=prefix["prefix_id"],
                    visible_sessions=prefix["visible_sessions"],
                    question=_STAGE2_QUESTION,
                    gold={"memory_updates": prefix["gold_memory_updates"]},
                    metadata={"n_updates": n_updates},
                )
            )
        return items

    # ------------------------------------------------------------- stage 3
    def build_stage3(self, prefixes: list[dict[str, Any]], sessions_by_traj: dict) -> list[BenchmarkItem]:
        items: list[BenchmarkItem] = []
        seen_counts: dict[str, int] = {}
        for prefix in prefixes:
            traj = prefix["trajectory_id"]
            n_decisions = len(prefix["gold_action_decisions"])
            if n_decisions == 0 or n_decisions == seen_counts.get(traj, 0):
                seen_counts[traj] = n_decisions
                continue
            seen_counts[traj] = n_decisions
            items.append(
                BenchmarkItem(
                    item_id=f"{prefix['prefix_id']}_s3",
                    stage="stage3_action_decision",
                    trajectory_id=traj,
                    prefix_id=prefix["prefix_id"],
                    visible_sessions=prefix["visible_sessions"],
                    question=_STAGE3_QUESTION,
                    gold={"action_decisions": prefix["gold_action_decisions"]},
                    metadata={"n_decisions": n_decisions},
                )
            )
        return items

    # --------------------------------------------------------- stage 3 MCQ
    #
    # Anti-leakage design: every item shares the SAME five operational options
    # (keep / change now / confirm before next run / suspend / terminate).
    # The correct option depends on the event lifecycle context that is only
    # recoverable from the session history:
    #
    #   post_occurred  -> confirm_first  (change may be needed; funds move)
    #   pre_occurred   -> keep_active    (event not happened; old setting is
    #                                     still the right one to run)
    #   cancelled      -> keep_active    (signal died; confirm/change acts on
    #                                     a stale pending state)
    #   no_event       -> keep_active    (hard negative; nothing happened)
    #
    # A model that always picks the "prudent-sounding" confirm option scores
    # only on post_occurred items. The question never names the impact type.

    _MCQ_ERROR_MAP = {
        "post_occurred": {
            "keep_active": "stale_action_carryover",
            "update_now": "unsafe_premature_execution",
            "confirm_first": None,  # correct
            "suspend_now": "overreaction",
            "terminate_now": "overreaction",
        },
        "pre_occurred": {
            "keep_active": None,  # correct
            "update_now": "unsafe_premature_execution",
            "confirm_first": "premature_update",
            "suspend_now": "overreaction",
            "terminate_now": "overreaction",
        },
        "cancelled": {
            "keep_active": None,  # correct
            "update_now": "false_commit",
            "confirm_first": "cancelled_ignored",
            "suspend_now": "overreaction",
            "terminate_now": "overreaction",
        },
        "no_event": {
            "keep_active": None,  # correct
            "update_now": "unsafe_premature_execution",
            "confirm_first": "no_event_false_positive",
            "suspend_now": "overreaction",
            "terminate_now": "overreaction",
        },
    }

    _MCQ_EXPECTED_DECISION = {
        "post_occurred": "ask_confirmation",
        "pre_occurred": "keep",
        "cancelled": "keep",
        "no_event": "keep",
    }

    def _mcq_options(self, label: str, setting_desc: str) -> dict[str, str]:
        return {
            "keep_active": f"{label}을(를) 지금 설정({setting_desc}) 그대로 다음 회차에도 실행한다.",
            "update_now": f"{label} 설정을 최근 대화에서 파악된 새 상황에 맞춰 이번 회차부터 바로 바꾼다.",
            "confirm_first": (
                f"{label}의 다음 회차 실행 전에 고객에게 설정을 그대로 진행할지 물어보고, "
                "답을 받기 전에는 바꾸지 않는다."
            ),
            "suspend_now": f"{label}의 다음 회차 실행을 일단 보류해 둔다.",
            "terminate_now": f"{label}을(를) 해지 처리한다.",
        }

    def _mcq_item_for_context(
        self,
        prefix: dict[str, Any],
        action: dict[str, Any],
        context: str,
        source_event_instance_id: str | None,
        impact_type: str | None,
    ) -> BenchmarkItem:
        rng = random.Random(f"{prefix['prefix_id']}:{action['action_id']}:{context}:{self.seed}")
        label = action.get("label") or action["action_id"]
        setting_parts = []
        if action.get("trigger_day") is not None:
            setting_parts.append(f"매월 {action['trigger_day']}일")
        if action.get("amount") is not None:
            setting_parts.append(f"{action['amount']:,}원")
        setting_desc = ", ".join(setting_parts) or "기존 조건"

        error_map = self._MCQ_ERROR_MAP[context]
        options_pool = [
            CounterfactualOption(
                option_id="",
                text=text,
                correct=error_map[key] is None,
                error_type=error_map[key],
            )
            for key, text in self._mcq_options(label, setting_desc).items()
        ]
        rng.shuffle(options_pool)
        letters = "ABCDE"
        for i, opt in enumerate(options_pool):
            opt.option_id = letters[i]
        correct_id = next(o.option_id for o in options_pool if o.correct)

        # stale distractor material available? (linked path with history)
        memory = prefix["gold_full_memory_state"]
        has_stale = any(
            (memory.get(path) or {}).get("historical_values")
            for path in (action.get("linked_memory_paths") or [])
        )

        question = (
            f"지금까지의 상담 세션 이력을 근거로 할 때, 등록된 정기 금융 액션 "
            f"'{label}'에 대해 지금 취할 가장 적절한 조치는 무엇인가?"
        )
        return BenchmarkItem(
            item_id=f"{prefix['prefix_id']}_{action['action_id']}_{context}_mcq",
            stage="stage3_action_mcq",
            trajectory_id=prefix["trajectory_id"],
            prefix_id=prefix["prefix_id"],
            visible_sessions=prefix["visible_sessions"],
            question=question,
            options=options_pool,
            gold={
                "correct_option": correct_id,
                "expected_decision": self._MCQ_EXPECTED_DECISION[context],
                "must_not_execute": bool(action.get("funds_movement", True)),
                "action_id": action["action_id"],
                "impact_type": impact_type,
                "risk": action.get("risk", "high"),
            },
            metadata={
                "context": context,
                "source_event_instance_id": source_event_instance_id,
                "has_stale_distractor": bool(has_stale),
            },
        )

    @staticmethod
    def _impact_selectors(impact_registry: dict[str, Any], event_id: str) -> list[dict[str, Any]]:
        template = impact_registry.get(event_id) or {}
        return (template.get("on_occurred") or {}).get("action_impacts") or []

    @staticmethod
    def _action_matches(selector: dict[str, Any], action: dict[str, Any]) -> bool:
        if not selector:
            return False
        if "label" in selector and action.get("type") != selector["label"]:
            return False
        if "linked_memory_path" in selector and selector["linked_memory_path"] not in (
            action.get("linked_memory_paths") or []
        ):
            return False
        return True

    def _impacted_actions(
        self, impact_registry: dict[str, Any], event_id: str, actions: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], str]]:
        """Actions the event WOULD impact on occurrence -> (action, impact_type)."""
        matched: list[tuple[dict[str, Any], str]] = []
        for spec in self._impact_selectors(impact_registry, event_id):
            for action in actions:
                if action.get("status") in {"cancelled", "historical"}:
                    continue
                if self._action_matches(spec.get("selector") or {}, action):
                    matched.append((action, spec["impact_type"]))
        return matched

    def build_stage3_mcq(
        self,
        prefixes: list[dict[str, Any]],
        sessions_by_traj: dict,
        impact_registry: dict[str, Any] | None = None,
        label_to_event_id: dict[str, str] | None = None,
        keep_to_confirm_ratio: float | None = 2.0,
    ) -> list[BenchmarkItem]:
        """Build stage-3 MCQ items with context-dependent correct answers.

        Only post_occurred items have ``ask_confirmation`` as the correct
        decision; pre_occurred / cancelled / no_event all have ``keep``. Left
        unbalanced the set is keep-heavy, letting an "always keep" model score
        highly. ``keep_to_confirm_ratio`` caps the keep-correct class at
        ``ratio × (#post_occurred)`` and fills that quota round-robin across
        the three keep contexts (so pre_occurred / cancelled / no_event all
        stay represented rather than no_event swamping them). All post_occurred
        items are always kept. Set to None to keep every item. Balancing is
        deterministic (seeded shuffle). Regardless, evaluation should still
        report per-context (macro-averaged) accuracy."""
        impact_registry = impact_registry or {}
        label_to_event_id = label_to_event_id or {}
        items: list[BenchmarkItem] = []
        emitted: set[str] = set()

        by_traj: dict[str, list[dict[str, Any]]] = {}
        for prefix in prefixes:
            by_traj.setdefault(prefix["trajectory_id"], []).append(prefix)

        for traj, traj_prefixes in by_traj.items():
            traj_prefixes.sort(key=lambda p: p["prefix_id"])
            lookup = _session_lookup(sessions_by_traj.get(traj, []))

            # -- post_occurred: bury the occurred evidence >=2 sessions back
            first_seen: dict[str, int] = {}
            for idx, prefix in enumerate(traj_prefixes):
                for decision in prefix["gold_action_decisions"]:
                    key = f"{decision['action_id']}:{decision['impact_type']}"
                    first_seen.setdefault(key, idx)
            for key, idx in first_seen.items():
                target = traj_prefixes[min(idx + 2, len(traj_prefixes) - 1)]
                decision = next(
                    d for d in target["gold_action_decisions"]
                    if f"{d['action_id']}:{d['impact_type']}" == key
                )
                action = next(
                    (a for a in target["gold_full_action_state"] if a["action_id"] == decision["action_id"]),
                    None,
                )
                if action is None:
                    continue
                item_key = f"{traj}:post:{key}"
                if item_key not in emitted:
                    emitted.add(item_key)
                    items.append(
                        self._mcq_item_for_context(
                            target, action, "post_occurred",
                            decision.get("source_event_instance_id"), decision["impact_type"],
                        )
                    )

            # -- pre_occurred / cancelled: use lifecycle status visible in gold
            pre_done: set[str] = set()
            cancelled_done: set[str] = set()
            for prefix in traj_prefixes:
                actions = prefix["gold_full_action_state"]
                for event in prefix["gold_life_events"]:
                    status = event["event_status"]
                    event_id = event.get("event_id") or ""
                    if status in {"weak_signal", "upcoming"} and event["event_instance_id"] not in pre_done:
                        for action, impact_type in self._impacted_actions(impact_registry, event_id, actions):
                            pre_done.add(event["event_instance_id"])
                            key = f"{traj}:pre:{event['event_instance_id']}:{action['action_id']}"
                            if key not in emitted:
                                emitted.add(key)
                                items.append(
                                    self._mcq_item_for_context(
                                        prefix, action, "pre_occurred",
                                        event["event_instance_id"], impact_type,
                                    )
                                )
                    elif status == "cancelled" and event["event_instance_id"] not in cancelled_done:
                        for action, impact_type in self._impacted_actions(impact_registry, event_id, actions):
                            cancelled_done.add(event["event_instance_id"])
                            key = f"{traj}:cancelled:{event['event_instance_id']}:{action['action_id']}"
                            if key not in emitted:
                                emitted.add(key)
                                items.append(
                                    self._mcq_item_for_context(
                                        prefix, action, "cancelled",
                                        event["event_instance_id"], impact_type,
                                    )
                                )

            # -- no_event: hard-negative sessions with a near-miss event whose
            #    occurred-impacts would touch an existing action
            for idx, prefix in enumerate(traj_prefixes):
                last_id = prefix["visible_sessions"][-1]
                session = lookup.get(last_id) or {}
                if session.get("session_type") != "hard_negative":
                    continue
                near_label = (session.get("plan") or {}).get("near_miss_event_label")
                event_id = label_to_event_id.get(near_label or "")
                if not event_id:
                    continue
                for action, impact_type in self._impacted_actions(
                    impact_registry, event_id, prefix["gold_full_action_state"]
                ):
                    key = f"{traj}:noevent:{last_id}:{action['action_id']}"
                    if key not in emitted:
                        emitted.add(key)
                        items.append(
                            self._mcq_item_for_context(prefix, action, "no_event", None, impact_type)
                        )
                    break  # one no_event item per hard-negative session

        if keep_to_confirm_ratio is None:
            return items

        # Decision-balance: cap the keep-correct class at ratio × #confirm and
        # fill the quota round-robin across the three keep contexts so none
        # (especially no_event) swamps the others.
        confirm = [i for i in items if i.metadata["context"] == "post_occurred"]
        keep_by_ctx: dict[str, list[BenchmarkItem]] = {"pre_occurred": [], "cancelled": [], "no_event": []}
        for item in items:
            ctx = item.metadata["context"]
            if ctx in keep_by_ctx:
                keep_by_ctx[ctx].append(item)

        rng = random.Random(f"mcq_balance:{self.seed}")
        for bucket in keep_by_ctx.values():
            bucket.sort(key=lambda i: i.item_id)
            rng.shuffle(bucket)

        quota = int(max(len(confirm), 2) * keep_to_confirm_ratio)
        selected_keep: list[BenchmarkItem] = []
        cursors = {ctx: 0 for ctx in keep_by_ctx}
        # round-robin draw until quota met or all buckets exhausted
        while len(selected_keep) < quota and any(cursors[c] < len(keep_by_ctx[c]) for c in keep_by_ctx):
            for ctx, bucket in keep_by_ctx.items():
                if len(selected_keep) >= quota:
                    break
                if cursors[ctx] < len(bucket):
                    selected_keep.append(bucket[cursors[ctx]])
                    cursors[ctx] += 1

        return sorted(confirm + selected_keep, key=lambda i: i.item_id)
