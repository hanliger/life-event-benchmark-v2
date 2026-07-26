from __future__ import annotations

import pytest

from financial_memory_experiment.metrics import hierarchical_stage2


def _row(trajectory: str, target: str, correct: bool):
    return {
        "trajectory_id": trajectory,
        "item_id": f"{trajectory}-{target}",
        "correct": correct,
        "item_metadata": {"canonical_target_id": target},
    }


def test_stage2_uses_trajectory_target_checkpoint_hierarchy():
    rows = [
        _row("t1", "a", True),
        _row("t1", "a", False),
        _row("t1", "b", True),
        _row("t2", "c", False),
    ]
    score, trajectories = hierarchical_stage2(rows)
    assert trajectories == pytest.approx({"t1": 0.75, "t2": 0.0})
    assert score == pytest.approx(0.375)

