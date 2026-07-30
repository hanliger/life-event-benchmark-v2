"""Metric tests: LCS F1, status macro-F1, evidence, anchors, AUC, progressive."""

from __future__ import annotations

import math

from fin_life_benchmark.benchmark.rq1_metrics import (
    aggregate_item_results,
    item_metrics,
    lcs_length,
    levenshtein,
    progressive_metrics,
)
from fin_life_benchmark.benchmark.rq1_models import (
    RQ1GoldEventInstance,
    RQ1PredictedEvent,
)


def _gold(event_id, first, anchor, core=None, supporting=None, status="occurred", iid=""):
    return RQ1GoldEventInstance(
        event_instance_id=iid or f"gi_{event_id}_{first}",
        event_id=event_id,
        event_status=status,
        first_evidence_session=first,
        status_anchor_session=anchor,
        core_evidence_sessions=core or [first, anchor],
        supporting_sessions=supporting or [],
    )


def _pred(event_id, first, anchor, core=None, supporting=None, status="occurred", conf=0.9):
    return RQ1PredictedEvent(
        event_id=event_id,
        status=status,
        first_evidence_session=first,
        status_anchor_session=anchor,
        core_evidence_sessions=core if core is not None else [first, anchor],
        supporting_sessions=supporting or [],
        confidence=conf,
    )


GOLD = [
    _gold("career_employment", "S003", "S012", iid="a"),
    _gold("career_employment", "S018", "S026", iid="b"),
    _gold("housing_move", "S020", "S028", status="upcoming", iid="c"),
]
OCCURRED = [GOLD[0], GOLD[1]]
PERFECT = [
    _pred("career_employment", "S003", "S012"),
    _pred("career_employment", "S018", "S026"),
    _pred("housing_move", "S020", "S028", status="upcoming"),
]


def test_perfect_prediction_scores_one():
    m = item_metrics(GOLD, OCCURRED, PERFECT)
    assert m["ordered_occurred_event_f1"] == 1.0
    assert m["full_ledger_event_f1"] == 1.0
    assert m["status_macro_f1"] == 1.0
    assert m["core_evidence_f1"] == 1.0
    assert m["core_evidence_f1_end_to_end"] == 1.0
    assert m["anchor_exact_accuracy"] == 1.0
    assert m["event_count_mae"] == 0.0
    assert m["exact_occurred_trajectory_match"] == 1.0
    assert m["normalized_sequence_edit_distance"] == 0.0
    assert m["mean_confidence_correct"] == 0.9
    assert m["mean_confidence_incorrect"] is None


def test_empty_gold_and_empty_prediction_is_perfect():
    m = item_metrics([], [], [])
    assert m["ordered_occurred_event_f1"] == 1.0
    assert m["full_ledger_event_f1"] == 1.0
    assert m["event_count_mae"] == 0.0
    assert m["normalized_sequence_edit_distance"] == 0.0


def test_reversed_occurred_sequence_is_penalized():
    gold = [
        _gold("career_employment", "S003", "S012", iid="a"),
        _gold("housing_move", "S018", "S026", iid="b"),
    ]
    reversed_pred = [
        _pred("housing_move", "S003", "S012"),
        _pred("career_employment", "S018", "S026"),
    ]
    m = item_metrics(gold, gold, reversed_pred)
    # LCS of [ce, hm] vs [hm, ce] is 1 -> F1 = 0.5
    assert m["ordered_occurred_event_f1"] == 0.5
    assert m["exact_occurred_trajectory_match"] == 0.0
    assert m["normalized_sequence_edit_distance"] == 1.0


def test_extra_prediction_lowers_precision_only():
    pred = PERFECT + [_pred("relationship_marriage", "S005", "S005")]
    m = item_metrics(GOLD, OCCURRED, pred)
    assert m["full_ledger_event_recall"] == 1.0
    assert m["full_ledger_event_precision"] == 0.75
    assert m["event_count_mae"] == 1.0


def test_missing_event_lowers_recall():
    m = item_metrics(GOLD, OCCURRED, PERFECT[:2])
    assert m["full_ledger_event_precision"] == 1.0
    assert math.isclose(m["full_ledger_event_recall"], 2 / 3)


def test_wrong_status_lowers_status_macro_f1_but_not_ledger_f1():
    pred = [
        PERFECT[0],
        _pred("career_employment", "S018", "S026", status="upcoming"),
        PERFECT[2],
    ]
    m = item_metrics(GOLD, OCCURRED, pred)
    assert m["full_ledger_event_f1"] == 1.0
    assert m["status_macro_f1"] < 1.0
    assert m["mean_confidence_incorrect"] == 0.9


def test_extra_evidence_lowers_evidence_precision():
    pred = [
        _pred("career_employment", "S003", "S012", core=["S003", "S012", "S005", "S006"]),
        PERFECT[1],
        PERFECT[2],
    ]
    m = item_metrics(GOLD, OCCURRED, pred)
    assert m["core_evidence_precision"] < 1.0
    assert m["core_evidence_recall"] == 1.0


def test_unmatched_gold_scores_zero_end_to_end_evidence():
    m = item_metrics(GOLD, OCCURRED, PERFECT[:1])
    assert m["core_evidence_f1"] == 1.0  # the one matched pair is perfect
    assert math.isclose(m["core_evidence_f1_end_to_end"], 1 / 3)


def test_no_event_handling_in_status_macro():
    # empty prediction: every gold instance becomes (status, no_event)
    m = item_metrics(GOLD, OCCURRED, [])
    assert m["status_macro_f1"] == 0.0
    assert m["full_ledger_event_recall"] == 0.0
    # hallucinated prediction against empty gold
    m2 = item_metrics([], [], [_pred("career_employment", "S001", "S001")])
    assert m2["status_macro_f1"] == 0.0
    assert m2["full_ledger_event_precision"] == 0.0


def test_lcs_and_levenshtein_basics():
    assert lcs_length("abc", "abc") == 3
    assert lcs_length("abc", "cba") == 1
    assert levenshtein(["a", "b"], ["b", "a"]) == 2
    assert levenshtein([], ["a"]) == 1


def _result(traj, cp, gold, occurred, pred):
    return {
        "trajectory_id": traj,
        "checkpoint_session_count": cp,
        "metrics": item_metrics(gold, occurred, pred),
    }


def test_auc_weights_checkpoints_equally():
    g1 = [_gold("career_employment", "S003", "S012", iid="a")]
    results = [
        _result("t1", 15, g1, g1, [_pred("career_employment", "S003", "S012")]),
        _result("t1", 30, g1, g1, []),
    ]
    agg = aggregate_item_results(results)
    per_cp = agg["per_checkpoint"]
    assert per_cp["15"]["macro_by_trajectory"]["full_ledger_event_f1"] == 1.0
    assert per_cp["30"]["macro_by_trajectory"]["full_ledger_event_f1"] == 0.0
    # equal weighting of the two checkpoints
    assert agg["checkpoint_macro_auc"]["full_ledger_event_f1"] == 0.5
    assert agg["final_checkpoint"] == 30
    assert agg["final_at_last_checkpoint"]["full_ledger_event_f1"] == 0.0


def test_macro_is_by_trajectory_not_by_instance():
    # traj A has 3 gold events all missed; traj B has 1 gold event matched.
    gold_a = [
        _gold("career_employment", "S003", "S012", iid=f"a{i}") for i in range(3)
    ]
    gold_b = [_gold("housing_move", "S003", "S012", iid="b")]
    results = [
        _result("A", 15, gold_a, gold_a, []),
        _result("B", 15, gold_b, gold_b, [_pred("housing_move", "S003", "S012")]),
    ]
    agg = aggregate_item_results(results)
    macro = agg["per_checkpoint"]["15"]["macro_by_trajectory"]
    micro = agg["per_checkpoint"]["15"]["micro_by_event_instance"]
    assert macro["full_ledger_event_recall"] == 0.5  # (0 + 1) / 2 trajectories
    assert micro["event_recall"] == 0.25  # 1 of 4 instances


FR = {"t1": {"a": {"session_id": "S012", "checkpoint": 15}}}


def test_detection_lag_and_retention():
    gold = [_gold("career_employment", "S003", "S012", iid="a")]
    hit = [_pred("career_employment", "S003", "S012")]
    results = [
        _result("t1", 15, gold, gold, []),          # miss at 15
        _result("t1", 30, gold, gold, hit),          # detect at 30
        _result("t1", 45, gold, gold, hit),          # retained
        _result("t1", 60, gold, gold, []),           # lost
    ]
    prog = progressive_metrics(results, first_recoverable=FR)
    assert prog["detection_lag_mean_checkpoints"] == 1.0  # (30-15)/15
    assert prog["detection_lag_mean_sessions"] == 15.0
    assert prog["post_detection_retention"] == 0.5
    assert prog["undetected_gold_instances"] == 0


def test_status_regression_rate():
    gold = [_gold("career_employment", "S003", "S012", iid="a")]
    occurred_hit = [_pred("career_employment", "S003", "S012")]
    weak_hit = [_pred("career_employment", "S003", "S012", status="weak_signal")]
    results = [
        _result("t1", 15, gold, gold, occurred_hit),
        _result("t1", 30, gold, gold, weak_hit),   # regression
        _result("t1", 45, gold, gold, occurred_hit),
        _result("t1", 60, gold, gold, occurred_hit),  # no regression
    ]
    prog = progressive_metrics(results, first_recoverable=FR)
    # opportunities: 15->30 and 45->60 (correct occurred at 15 and 45)
    assert prog["status_regression_opportunities"] == 2
    assert prog["status_regression_rate"] == 0.5


def test_hallucination_persistence_chains():
    gold: list[RQ1GoldEventInstance] = []
    phantom = [_pred("relationship_marriage", "S002", "S002")]
    results = [
        _result("t1", 15, gold, gold, phantom),
        _result("t1", 30, gold, gold, phantom),  # persists
        _result("t1", 45, gold, gold, []),       # gone
    ]
    prog = progressive_metrics(results, first_recoverable={})
    assert prog["hallucination_chain_count"] == 1
    assert prog["hallucination_persistence_mean_checkpoints"] == 2.0
    assert prog["hallucination_persistence_max_checkpoints"] == 2
