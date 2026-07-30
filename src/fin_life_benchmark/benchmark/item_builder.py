"""Build Stage 2 benchmark items from prefix gold and trajectory truth.

Official Stage 1 items use the cumulative pair builder in :mod:`rq1_builder`;
keeping the old lifecycle-status builder here made the default pipeline emit a
different task under the same Stage 1 name.
"""

from __future__ import annotations

from typing import Any

from ..trajectory.models import Trajectory
from .models import BenchmarkItem
from .stage2_memory import Stage2MemoryValueBuilder


class ItemBuilder:
    def __init__(self, seed: int = 0):
        self.seed = seed

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
