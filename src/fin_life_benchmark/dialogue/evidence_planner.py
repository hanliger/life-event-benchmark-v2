"""Plan multi-session evidence for each event instance.

Key design: not every event is recoverable from one session. Drift events
spread cues across sessions so only the cumulative history identifies them.
Hard-negative and stale-recall plans provide distractor material.
"""

from __future__ import annotations

import random
import re
from typing import Any

from ..fsm.models import EventStatus, LifeEventTemplate
from ..io import RepoPaths, load_yaml
from ..locale.loader import LocaleConfig
from ..trajectory.models import Trajectory
from .models import DialogueGenerationPlan

_SESSION_TYPE_BY_STATUS = {
    "weak_signal": "weak_signal_evidence",
    "upcoming": "upcoming_evidence",
    "occurred": "occurred_evidence",
    "cancelled": "cancellation_evidence",
}

_ROUTINE_TASKS = [
    ("FA-01", "거래내역 조회"),
    ("FA-02", "입금 알림 설정"),
    ("FA-04", "예금 금리 조회"),
    ("FA-01", "이체확인증 발급"),
]

# generic near-miss cue material for hard negatives (no life-event implication)
_NEAR_MISS_CUES = [
    "회사 경비 처리용 이체",
    "동호회 회비 정기이체",
    "친구한테 빌린 돈 상환",
    "여행 경비 모으는 통장",
]

def _slugify(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "_", text).strip("_")[:40]


class EvidencePlanner:
    def __init__(
        self,
        templates: dict[str, LifeEventTemplate],
        locale: LocaleConfig,
        paths: RepoPaths | None = None,
    ):
        paths = paths or RepoPaths.default()
        self.templates = templates
        self.locale = locale
        self.cfg = load_yaml(paths.generation / "dialogue.yaml")
        self.fa_registry = load_yaml(paths.registries / "financial_actions.yaml")
        # leakage vocabulary: all event labels (split composite labels)
        from ..fsm.registry import all_event_labels_ko

        self.all_labels = all_event_labels_ko(templates)

    def _forbidden_terms(self, template: LifeEventTemplate, required_cues: list[str]) -> list[str]:
        """Template forbidden cues + event labels, minus labels that are
        substrings of this event's own required cues (e.g. '수술' vs
        '수술비 수납')."""
        labels = [l for l in self.all_labels if not any(l in cue for cue in required_cues)]
        return list(template.discriminative_cues_ko.forbidden) + labels

    def _fa_task(self, fa_code: str | None, rng: random.Random) -> str:
        if fa_code and fa_code in self.fa_registry:
            return rng.choice(self.fa_registry[fa_code]["examples"])
        return rng.choice(_ROUTINE_TASKS)[1]

    @staticmethod
    def _enum_value(value: Any) -> Any:
        return getattr(value, "value", value)

    @classmethod
    def _memory_update_context(cls, update: Any, month_index: int) -> dict[str, Any]:
        return {
            "month_index": month_index,
            "path": update.path,
            "operation": cls._enum_value(update.operation),
            "old_value": update.old_value,
            "new_value": update.new_value,
            "event_status": update.event_status,
            "source_event_instance_id": update.source_event_instance_id,
        }

    @classmethod
    def _memory_cell_context(cls, memory: Any, path: str) -> dict[str, Any] | None:
        cell = memory.latest(path)
        if cell is None:
            return None
        return {
            "path": path,
            "value": cell.value,
            "status": cls._enum_value(cell.status),
            "valid_from": cell.valid_from,
            "valid_until": cell.valid_until,
            "source_event_instance_id": cell.source_event_instance_id,
        }

    @staticmethod
    def _snapshot_at(snapshots: dict[str, Any], month_index: int, initial: Any) -> Any:
        selected = initial
        selected_month = 0
        for key, value in snapshots.items():
            try:
                candidate_month = int(key)
            except (TypeError, ValueError):
                continue
            if selected_month <= candidate_month <= month_index:
                selected = value
                selected_month = candidate_month
        return selected

    def _persona_state_context(self, trajectory: Trajectory, month_index: int) -> dict[str, Any]:
        snapshot = self._snapshot_at(trajectory.state_snapshots, month_index, trajectory.initial_persona_state)
        persona = trajectory.persona
        return {
            "month_index": month_index,
            "age": snapshot.age,
            "life_state": snapshot.life_state.model_dump(mode="json"),
            "persona": {
                "employment_status": persona.occupation_state.employment_status,
                "residence_status": persona.housing.residence_status,
                "marital_status": persona.household.marital_status,
                "children_ages": list(persona.household.children_ages),
                "dependents_count": persona.household.dependents_count,
                "has_loan": persona.financial_profile.has_loan,
            },
        }

    def _structured_context(
        self,
        trajectory: Trajectory,
        instance: Any | None,
        month_index: int,
        session_updates: list[Any],
        event_updates: list[tuple[int, Any]],
        target_paths: list[str],
    ) -> dict[str, Any]:
        memory = self._snapshot_at(trajectory.memory_snapshots, month_index, trajectory.initial_financial_memory_state)
        unique_paths = list(dict.fromkeys(target_paths))
        event_context = None
        if instance is not None:
            visible_history = [
                item
                for item in instance.status_history
                if item.month_index <= month_index
            ]
            status_at_session = (
                self._enum_value(visible_history[-1].status)
                if visible_history
                else self._enum_value(instance.status)
            )
            event_context = {
                "event_id": instance.event_id,
                "domain": instance.domain,
                "status": status_at_session,
                "status_history": [
                    {"status": self._enum_value(item.status), "month_index": item.month_index, "age": item.age}
                    for item in visible_history
                ],
                "params": dict(instance.params),
            }
        return {
            "event": event_context,
            "persona_state": self._persona_state_context(trajectory, month_index),
            "session_memory_updates": [
                self._memory_update_context(update, month_index)
                for update in session_updates
            ],
            "event_memory_updates": [
                self._memory_update_context(update, update_month)
                for update_month, update in event_updates
                if update_month <= month_index
            ],
            "current_memory": {
                path: self._memory_cell_context(memory, path)
                for path in unique_paths
            },
        }

    def build_plans(self, trajectory: Trajectory, seed: int = 0) -> list[DialogueGenerationPlan]:
        rng = random.Random(f"{trajectory.trajectory_id}:{seed}")
        plans: list[DialogueGenerationPlan] = []
        start_age = trajectory.initial_persona_state.age

        # index memory updates / action impacts by (instance, month)
        updates_by_key: dict[tuple[str, int], list] = {}
        updates_by_instance: dict[str, list[tuple[int, Any]]] = {}
        impacts_by_key: dict[tuple[str, int], list] = {}
        for step in trajectory.timeline_steps:
            for u in step.memory_updates:
                source = u.source_event_instance_id or ""
                updates_by_key.setdefault((source, step.month_index), []).append(u)
                updates_by_instance.setdefault(source, []).append((step.month_index, u))
            for a in step.action_impacts:
                impacts_by_key.setdefault((a.source_event_instance_id or "", step.month_index), []).append(a)

        # 1. evidence sessions per event instance
        for instance in trajectory.life_event_instances:
            template = self.templates[instance.event_id]
            required = list(template.discriminative_cues_ko.required)
            rng.shuffle(required)
            is_drift = (
                instance.status == EventStatus.OCCURRED
                and len(instance.status_history) >= 2
                and rng.random() < float(self.cfg.get("drift_event_ratio", 0.3))
            )
            forbidden_terms = self._forbidden_terms(template, required)

            cue_cursor = 0
            for idx, item in enumerate(instance.status_history):
                status = item.status.value
                session_type = _SESSION_TYPE_BY_STATUS.get(status)
                if session_type is None:
                    continue
                mapped = template.mapped_actions_by_status.get(status) or []
                fa_code = mapped[0] if mapped else None

                if is_drift:
                    cues = required[cue_cursor : cue_cursor + 1]
                    cue_cursor = min(cue_cursor + 1, max(0, len(required) - 1))
                    single_rec = "low"
                else:
                    lo, hi = self.cfg.get("cues_per_session", [1, 3])
                    k = min(len(required), rng.randint(int(lo), int(hi)))
                    cues = required[:k] if k else []
                    single_rec = "high" if status == "occurred" else "medium"

                month_updates = updates_by_key.get((instance.event_instance_id, item.month_index), [])
                month_impacts = impacts_by_key.get((instance.event_instance_id, item.month_index), [])
                event_updates = updates_by_instance.get(instance.event_instance_id, [])
                target_paths = [u.path for u in month_updates]

                plans.append(
                    DialogueGenerationPlan(
                        trajectory_id=trajectory.trajectory_id,
                        month_index=item.month_index,
                        age=item.age,
                        session_type=session_type,
                        linked_event_instance_id=instance.event_instance_id,
                        event_status_after_session=status,
                        mapped_action=fa_code,
                        financial_task=self._fa_task(fa_code, rng),
                        must_include_cues=cues,
                        must_not_include_terms=forbidden_terms,
                        target_memory_paths=target_paths,
                        target_action_ids=[a.action_id for a in month_impacts],
                        structured_context=self._structured_context(
                            trajectory, instance, item.month_index, month_updates, event_updates, target_paths
                        ),
                        desired_single_session_recoverability=single_rec,
                        desired_cumulative_recoverability="high",
                    )
                )

            # consequence session after occurred
            if instance.occurred_month is not None and rng.random() < 0.6:
                lo, hi = self.cfg.get("consequence_session_delay_months", [1, 4])
                month = min(instance.occurred_month + rng.randint(int(lo), int(hi)), trajectory.horizon_months - 1)
                occurred_updates = updates_by_key.get((instance.event_instance_id, instance.occurred_month), [])
                event_updates = updates_by_instance.get(instance.event_instance_id, [])
                target_paths = [u.path for u in occurred_updates]
                cue = required[-1] if required else None
                plans.append(
                    DialogueGenerationPlan(
                        trajectory_id=trajectory.trajectory_id,
                        month_index=month,
                        age=start_age + month // 12,
                        session_type="consequence_session",
                        linked_event_instance_id=instance.event_instance_id,
                        event_status_after_session="occurred",
                        mapped_action="FA-01",
                        financial_task="거래내역 조회",
                        must_include_cues=[cue] if cue else [],
                        must_not_include_terms=forbidden_terms,
                        target_memory_paths=target_paths,
                        structured_context=self._structured_context(
                            trajectory, instance, month, [], event_updates, target_paths
                        ),
                        desired_single_session_recoverability="low",
                        desired_cumulative_recoverability="high",
                    )
                )

            # stale recall session: old value asked about after archive/update
            occurred_updates = updates_by_key.get((instance.event_instance_id, instance.occurred_month or -1), [])
            stale_paths = [
                u.path for u in occurred_updates
                if u.operation.value in {"update", "archive", "mark_stale"} and u.old_value is not None
            ]
            if stale_paths and rng.random() < float(self.cfg.get("stale_recall_ratio", 0.3)):
                month = min((instance.occurred_month or 0) + rng.randint(2, 6), trajectory.horizon_months - 1)
                event_updates = updates_by_instance.get(instance.event_instance_id, [])
                target_paths = stale_paths[:2]
                plans.append(
                    DialogueGenerationPlan(
                        trajectory_id=trajectory.trajectory_id,
                        month_index=month,
                        age=start_age + month // 12,
                        session_type="stale_recall_session",
                        linked_event_instance_id=instance.event_instance_id,
                        event_status_after_session="occurred",
                        mapped_action="FA-01",
                        financial_task="예전 설정 확인",
                        must_include_cues=["예전에 쓰던 설정"],
                        must_not_include_terms=forbidden_terms,
                        target_memory_paths=target_paths,
                        structured_context=self._structured_context(
                            trajectory, instance, month, [], event_updates, target_paths
                        ),
                        desired_single_session_recoverability="low",
                        desired_cumulative_recoverability="high",
                    )
                )

        # 2. routine sessions. Dense benchmark runs target a fixed number of
        # sessions per trajectory and allow multiple independent bank visits in
        # the same month; sparse smoke runs retain the older per-year fallback.
        target_sessions = self.cfg.get("target_sessions_per_trajectory")
        target_hard = None
        if target_sessions is not None:
            target_sessions = int(target_sessions)
            target_hard = int(target_sessions * float(self.cfg.get("hard_negative_target_ratio", 0.15)))
            n_routine = max(0, target_sessions - len(plans) - target_hard)
            for _ in range(n_routine):
                month = rng.randint(0, trajectory.horizon_months - 1)
                fa_code, task = rng.choice(_ROUTINE_TASKS)
                plans.append(
                    DialogueGenerationPlan(
                        trajectory_id=trajectory.trajectory_id,
                        month_index=month,
                        age=start_age + month // 12,
                        session_type="routine_financial",
                        event_status_after_session="no_event",
                        mapped_action=fa_code,
                        financial_task=task,
                        must_include_cues=[],
                        must_not_include_terms=self.all_labels,
                        structured_context=self._structured_context(
                            trajectory, None, month, [], [], []
                        ),
                        desired_single_session_recoverability="low",
                        desired_cumulative_recoverability="medium",
                    )
                )
        else:
            per_year = int(self.cfg.get("routine_sessions_per_year", 2))
            busy_months = {p.month_index for p in plans}
            for year in range(trajectory.horizon_months // 12):
                for _ in range(per_year):
                    month = year * 12 + rng.randint(0, 11)
                    if month in busy_months:
                        continue
                    fa_code, task = rng.choice(_ROUTINE_TASKS)
                    plans.append(
                        DialogueGenerationPlan(
                            trajectory_id=trajectory.trajectory_id,
                            month_index=month,
                            age=start_age + month // 12,
                            session_type="routine_financial",
                            event_status_after_session="no_event",
                            mapped_action=fa_code,
                            financial_task=task,
                            must_include_cues=[],
                            must_not_include_terms=self.all_labels,
                            structured_context=self._structured_context(
                                trajectory, None, month, [], [], []
                            ),
                            desired_single_session_recoverability="low",
                            desired_cumulative_recoverability="medium",
                        )
                    )
                    busy_months.add(month)

        # 3. hard negatives: same FA family as a real event, no life event
        if target_hard is None:
            n_hard = int(
                len([p for p in plans if p.session_type.endswith("_evidence")])
                * float(self.cfg.get("hard_negative_ratio", 0.25))
            )
            busy_months = {p.month_index for p in plans}
        else:
            n_hard = max(0, target_hard)
            busy_months = None
        event_templates = list(self.templates.values())
        for _ in range(n_hard):
            template = rng.choice(event_templates)
            mapped = template.mapped_actions_by_status.get("occurred") or ["FA-01"]
            month = rng.randint(0, trajectory.horizon_months - 1)
            if busy_months is not None and month in busy_months:
                continue
            plans.append(
                DialogueGenerationPlan(
                    trajectory_id=trajectory.trajectory_id,
                    month_index=month,
                    age=start_age + month // 12,
                    session_type="hard_negative",
                    event_status_after_session="no_event",
                    near_miss_event_label=template.label_ko,
                    mapped_action=mapped[0],
                    financial_task=self._fa_task(mapped[0], rng),
                    must_include_cues=[rng.choice(_NEAR_MISS_CUES)],
                    must_not_include_terms=list(template.discriminative_cues_ko.required) + self.all_labels,
                    structured_context=self._structured_context(
                        trajectory, None, month, [], [], []
                    ),
                    desired_single_session_recoverability="low",
                    desired_cumulative_recoverability="medium",
                )
            )
            if busy_months is not None:
                busy_months.add(month)

        # order chronologically and assign session ids
        plans.sort(key=lambda p: (p.month_index, p.session_type))
        for i, plan in enumerate(plans, start=1):
            plan.session_id = f"S{i:03d}"
        return plans
