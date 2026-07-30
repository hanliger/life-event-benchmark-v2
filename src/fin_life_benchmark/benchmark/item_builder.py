"""Build benchmark items from prefix gold and trajectory truth.

Stage 1 detects life-event labels/statuses from dialogue evidence.
Stage 2 recovers dated memory values from occurred-event updates.
"""

from __future__ import annotations

from typing import Any

from ..trajectory.models import Trajectory
from .models import BenchmarkItem
from .stage2_memory import Stage2MemoryValueBuilder

_STAGE1_QUESTION = (
    "지금까지의 상담 세션 이력만을 근거로, 감지되는 고객 Life Event와 "
    "각 이벤트의 상태(weak_signal/upcoming/occurred/cancelled)를 모두 나열하시오. "
    "확인되는 이벤트가 없으면 no_event라고 답하시오."
)


def _session_lookup(sessions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {session["session_id"]: session for session in sessions}


def _last_session_type(
    prefix: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> str:
    last_id = prefix["visible_sessions"][-1]
    session = lookup.get(last_id) or {}
    return session.get("session_type", "")


class ItemBuilder:
    def __init__(self, seed: int = 0):
        self.seed = seed

    def build_stage1(
        self,
        prefixes: list[dict[str, Any]],
        sessions_by_traj: dict[str, list[dict[str, Any]]],
    ) -> list[BenchmarkItem]:
        items: list[BenchmarkItem] = []
        for prefix in prefixes:
            lookup = _session_lookup(
                sessions_by_traj.get(prefix["trajectory_id"], [])
            )
            gold_events = [
                {
                    "life_event_label": event["life_event_label"],
                    "event_status": event["event_status"],
                    "occurred": event["occurred"],
                    "evidence_sessions": event["evidence_sessions"],
                }
                for event in prefix["gold_life_events"]
            ]
            items.append(
                BenchmarkItem(
                    item_id=f"{prefix['prefix_id']}_s1",
                    stage="stage1_event_status",
                    trajectory_id=prefix["trajectory_id"],
                    prefix_id=prefix["prefix_id"],
                    visible_sessions=prefix["visible_sessions"],
                    question=_STAGE1_QUESTION,
                    gold={
                        "life_events": gold_events
                        or [
                            {
                                "life_event_label": None,
                                "event_status": "no_event",
                            }
                        ]
                    },
                    metadata={
                        "last_session_type": _last_session_type(prefix, lookup),
                        "checkpoint_session_count": prefix.get(
                            "checkpoint_session_count"
                        ),
                        "occurred_event_count": prefix.get(
                            "occurred_event_count"
                        ),
                    },
                )
            )
        return items

    def build_stage2(
        self,
        prefixes: list[dict[str, Any]],
        sessions_by_traj: dict[str, list[dict[str, Any]]],
        trajectories_by_traj: dict[str, Trajectory],
    ) -> list[BenchmarkItem]:
        return Stage2MemoryValueBuilder(seed=self.seed).build(
            prefixes=prefixes,
            sessions_by_traj=sessions_by_traj,
            trajectories_by_traj=trajectories_by_traj,
        )
