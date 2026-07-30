"""Alignment tests: monotonic DP over ordered event instances."""

from __future__ import annotations

from fin_life_benchmark.benchmark.rq1_alignment import align_events
from fin_life_benchmark.benchmark.rq1_models import (
    RQ1GoldEventInstance,
    RQ1PredictedEvent,
)


def _gold(event_id: str, first: str, anchor: str, core: list[str] | None = None, status: str = "occurred", iid: str = "") -> RQ1GoldEventInstance:
    return RQ1GoldEventInstance(
        event_instance_id=iid or f"gi_{event_id}_{first}",
        event_id=event_id,
        event_status=status,
        first_evidence_session=first,
        status_anchor_session=anchor,
        core_evidence_sessions=core or [first, anchor],
    )


def _pred(event_id: str, first: str, anchor: str, core: list[str] | None = None, status: str = "occurred", conf: float = 0.9) -> RQ1PredictedEvent:
    return RQ1PredictedEvent(
        event_id=event_id,
        status=status,
        first_evidence_session=first,
        status_anchor_session=anchor,
        core_evidence_sessions=core or [first, anchor],
        confidence=conf,
    )


def test_exact_match_aligns_everything():
    gold = [_gold("career_employment", "S003", "S012"), _gold("housing_move", "S020", "S028", status="upcoming")]
    pred = [_pred("career_employment", "S003", "S012"), _pred("housing_move", "S020", "S028", status="upcoming")]
    result = align_events(gold, pred)
    assert result.matched_count == 2
    assert result.unmatched_gold == [] and result.unmatched_pred == []
    assert all(p.status_correct for p in result.pairs)
    assert [p.anchor_distance for p in result.pairs] == [0, 0]


def test_missing_earlier_instance_leaves_gold_unmatched():
    gold = [
        _gold("career_employment", "S003", "S012", iid="a"),
        _gold("career_employment", "S018", "S026", iid="b"),
    ]
    pred = [_pred("career_employment", "S018", "S026")]
    result = align_events(gold, pred)
    assert result.matched_count == 1
    # anchor distance pulls the prediction to the later gold instance
    assert result.pairs[0].gold_index == 1
    assert result.unmatched_gold == [0]


def test_extra_duplicate_prediction_is_unmatched():
    gold = [_gold("career_employment", "S003", "S012")]
    pred = [
        _pred("career_employment", "S003", "S012"),
        _pred("career_employment", "S014", "S014"),
    ]
    result = align_events(gold, pred)
    assert result.matched_count == 1
    assert result.unmatched_pred == [1]


def test_event_id_mismatch_never_matches():
    gold = [_gold("career_employment", "S003", "S012")]
    pred = [_pred("relationship_marriage", "S003", "S012")]
    result = align_events(gold, pred)
    assert result.matched_count == 0
    assert result.unmatched_gold == [0]
    assert result.unmatched_pred == [0]


def test_reversed_order_matches_monotonically():
    gold = [
        _gold("career_employment", "S003", "S012", iid="a"),
        _gold("housing_move", "S020", "S028", iid="b"),
    ]
    # predictions listed in reverse; alignment orders by first evidence
    pred = [
        _pred("housing_move", "S020", "S028"),
        _pred("career_employment", "S003", "S012"),
    ]
    result = align_events(gold, pred)
    assert result.matched_count == 2
    assert {p.pred_index for p in result.pairs} == {0, 1}


def test_repeated_event_ids_align_in_order():
    gold = [
        _gold("career_employment", "S003", "S012", iid="a"),
        _gold("career_employment", "S018", "S026", iid="b"),
    ]
    pred = [
        _pred("career_employment", "S004", "S012"),
        _pred("career_employment", "S018", "S026"),
    ]
    result = align_events(gold, pred)
    assert result.matched_count == 2
    assert [(p.gold_index, p.pred_index) for p in result.pairs] == [(0, 0), (1, 1)]


def test_anchor_distance_breaks_identical_label_ties():
    # one prediction, two same-label gold instances with different anchors
    gold = [
        _gold("career_employment", "S003", "S005", iid="near"),
        _gold("career_employment", "S018", "S026", iid="far"),
    ]
    pred = [_pred("career_employment", "S017", "S025")]
    result = align_events(gold, pred)
    assert result.matched_count == 1
    assert gold[result.pairs[0].gold_index].event_instance_id == "far"


def test_evidence_overlap_breaks_equal_anchor_ties():
    gold = [
        _gold("career_employment", "S003", "S010", core=["S003", "S010"], iid="left"),
        _gold("career_employment", "S012", "S014", core=["S012", "S014"], iid="right"),
    ]
    # anchor S012 is 2 sessions from both anchors; overlap decides
    pred = [_pred("career_employment", "S012", "S012", core=["S012", "S014"])]
    result = align_events(gold, pred)
    assert result.matched_count == 1
    assert gold[result.pairs[0].gold_index].event_instance_id == "right"


def test_alignment_is_deterministic():
    gold = [
        _gold("career_employment", "S003", "S012", iid="a"),
        _gold("career_employment", "S018", "S026", iid="b"),
    ]
    pred = [
        _pred("career_employment", "S010", "S019"),
    ]
    results = [align_events(gold, pred) for _ in range(5)]
    picks = {r.pairs[0].gold_index for r in results}
    assert len(picks) == 1
