"""Plan multi-session evidence for each event instance.

Key design: not every event is recoverable from one session. Drift events
spread cues across sessions so only the cumulative history identifies them.
Hard-negative and stale-recall plans provide distractor material.
"""

from __future__ import annotations

import random
import re

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

    def build_plans(self, trajectory: Trajectory, seed: int = 0) -> list[DialogueGenerationPlan]:
        rng = random.Random(f"{trajectory.trajectory_id}:{seed}")
        plans: list[DialogueGenerationPlan] = []
        start_age = trajectory.initial_persona_state.age

        # index memory updates / action impacts by (instance, month)
        updates_by_key: dict[tuple[str, int], list] = {}
        impacts_by_key: dict[tuple[str, int], list] = {}
        for step in trajectory.timeline_steps:
            for u in step.memory_updates:
                updates_by_key.setdefault((u.source_event_instance_id or "", step.month_index), []).append(u)
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
                        target_memory_paths=[u.path for u in month_updates],
                        target_action_ids=[a.action_id for a in month_impacts],
                        desired_single_session_recoverability=single_rec,
                        desired_cumulative_recoverability="high",
                    )
                )

            # consequence session after occurred
            if instance.occurred_month is not None and rng.random() < 0.6:
                lo, hi = self.cfg.get("consequence_session_delay_months", [1, 4])
                month = min(instance.occurred_month + rng.randint(int(lo), int(hi)), trajectory.horizon_months - 1)
                occurred_updates = updates_by_key.get((instance.event_instance_id, instance.occurred_month), [])
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
                        target_memory_paths=[u.path for u in occurred_updates],
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
                        target_memory_paths=stale_paths[:2],
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
