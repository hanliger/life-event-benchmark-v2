"""Distractor case construction + paired scoring tests."""

from __future__ import annotations

import json
import sys

import pytest

from fin_life_benchmark.benchmark.rq1_metrics import (
    clustered_bootstrap_ci,
    clustered_sign_flip_pvalue,
    item_metrics,
    paired_differences,
)
from fin_life_benchmark.benchmark.rq1_models import (
    RQ1GoldEventInstance,
    RQ1PredictedEvent,
)
from fin_life_benchmark.io.jsonl import write_jsonl

from rq1_fixtures import (
    TRAJ_ID,
    build_filler_bank,
    build_sessions,
    build_trajectory,
)


@pytest.fixture(scope="module")
def built_cases(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("rq1_distractor")
    sessions_dir = tmp_path / "sessions"
    traj_dir = tmp_path / "trajectories"
    fillers_dir = tmp_path / "fillers"
    traj_dir.mkdir()
    write_jsonl(sessions_dir / f"sessions_{TRAJ_ID}.jsonl", build_sessions())
    write_jsonl(fillers_dir / f"fillers_{TRAJ_ID}.jsonl", build_filler_bank())
    (traj_dir / f"{TRAJ_ID}.json").write_text(
        build_trajectory().model_dump_json(), encoding="utf-8"
    )
    output = tmp_path / "cases.jsonl"
    argv = [
        "build_rq1_distractor_cases.py",
        "--sessions-dir", str(sessions_dir),
        "--trajectories-dir", str(traj_dir),
        "--fillers-dir", str(fillers_dir),
        "--output", str(output),
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        from scripts import build_rq1_distractor_cases

        build_rq1_distractor_cases.main()
    finally:
        sys.argv = old_argv
    cases = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    return cases, tmp_path


def test_case_pairs_share_checkpoint_and_length(built_cases):
    cases, _ = built_cases
    assert len(cases) == 1  # one hard negative (S010) in the fixture corpus
    case = cases[0]
    assert case["target_session_id"] == "S010"
    assert case["checkpoint_session_count"] == 15
    assert case["gold"]["input_session_count"] == 15
    # same visible ids in every condition: the map covers the full prefix
    assert sorted(case["gold"]["session_id_map"]) == [
        f"S{i:03d}" for i in range(1, 16)
    ]


def test_mask_and_sham_slots_are_disjoint_and_typed(built_cases):
    cases, _ = built_cases
    case = cases[0]
    assert case["masked_session_ids"] == ["S010"]
    (sham_id,) = case["sham_session_ids"]
    assert sham_id != "S010"
    sessions = {s["session_id"]: s for s in build_sessions()}
    assert sessions[sham_id]["session_type"] == "routine_financial"
    # nearest routine neighbour of S010: S009 and S011 both qualify at
    # distance 1; the tie breaks to the earlier session.
    assert sham_id == "S009"


def test_same_donor_and_metadata_stored(built_cases):
    cases, _ = built_cases
    case = cases[0]
    donors = set(case["donor_by_slot"].values())
    assert len(donors) == 1
    assert next(iter(donors)).startswith("CF")
    assert case["hard_negative_type"] == "sibling_event_negative"
    assert case["near_miss_event_id"] == "housing_move"
    assert case["near_miss_explanation"]
    assert all(p["same_persona"] for p in case["donor_provenance"])
    assert case["metadata"]["gold_ledger_invariant"] is True


def test_donor_mapping_is_deterministic(built_cases):
    cases, tmp_path = built_cases
    output2 = tmp_path / "cases2.jsonl"
    argv = [
        "build_rq1_distractor_cases.py",
        "--sessions-dir", str(tmp_path / "sessions"),
        "--trajectories-dir", str(tmp_path / "trajectories"),
        "--fillers-dir", str(tmp_path / "fillers"),
        "--output", str(output2),
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        from scripts import build_rq1_distractor_cases

        build_rq1_distractor_cases.main()
    finally:
        sys.argv = old_argv
    rerun = [
        json.loads(line)
        for line in output2.read_text(encoding="utf-8").splitlines()
    ]
    assert rerun[0]["donor_by_slot"] == cases[0]["donor_by_slot"]


def test_gold_ledger_is_invariant_across_conditions(built_cases):
    cases, _ = built_cases
    case = cases[0]
    # builder fails loudly otherwise; the stored ledger matches the natural
    # checkpoint ledger for the same prefix
    ledger = case["gold"]["full_observed_ledger"]
    assert [e["event_instance_id"] for e in ledger] == [f"{TRAJ_ID}_ev001"]
    assert ledger[0]["core_evidence_sessions"] == ["S003", "S007", "S012"]


def test_masked_rendering_removes_target_and_sham_keeps_it(built_cases):
    cases, tmp_path = built_cases
    case = cases[0]
    from fin_life_benchmark.benchmark.rq1_builder import (
        apply_replacement_turns,
        render_sessions_block,
    )

    sessions = {s["session_id"]: s for s in build_sessions()}
    bank = {f["session_id"]: f for f in build_filler_bank()}
    id_map = case["gold"]["session_id_map"]
    visible = [sessions[f"S{i:03d}"] for i in range(1, 16)]
    target_text = sessions["S010"]["turns"][0]["text"]

    donor = case["donor_by_slot"]["S010"]
    masked = apply_replacement_turns(visible, {"S010": donor}, bank)
    masked_rendered = render_sessions_block(masked, id_map)
    assert target_text not in masked_rendered
    assert "[세션 D010]" in masked_rendered  # slot identity preserved

    sham_slot = case["sham_session_ids"][0]
    sham = apply_replacement_turns(
        visible, {sham_slot: case["donor_by_slot"][sham_slot]}, bank
    )
    sham_rendered = render_sessions_block(sham, id_map)
    assert target_text in sham_rendered


def _gold_ev():
    return RQ1GoldEventInstance(
        event_instance_id="gi",
        event_id="career_employment",
        event_status="occurred",
        first_evidence_session="S003",
        status_anchor_session="S012",
        core_evidence_sessions=["S003", "S012"],
    )


def _pred_ev():
    return RQ1PredictedEvent(
        event_id="career_employment",
        status="occurred",
        first_evidence_session="S003",
        status_anchor_session="S012",
        core_evidence_sessions=["S003", "S012"],
        confidence=0.9,
    )


def test_distractor_cost_and_replacement_artifact_signs():
    gold = [_gold_ev()]
    hit = item_metrics(gold, gold, [_pred_ev()])["full_ledger_event_f1"]
    miss = item_metrics(gold, gold, [])["full_ledger_event_f1"]
    # model fails with the distractor present, recovers when it is masked
    full_scores = {"case1": miss}
    masked_scores = {"case1": hit}
    sham_scores = {"case1": miss}
    cost = paired_differences(masked_scores, full_scores)
    artifact = paired_differences(full_scores, sham_scores)
    assert cost["case1"] > 0  # positive = removing distractor helped
    assert artifact["case1"] == 0.0  # sham identical -> no artifact


def test_clustered_bootstrap_and_permutation_are_deterministic():
    diffs = {f"c{i}": 0.1 for i in range(6)}
    clusters = {f"c{i}": f"t{i % 2}" for i in range(6)}
    ci1 = clustered_bootstrap_ci(diffs, clusters, n_boot=200, seed=7)
    ci2 = clustered_bootstrap_ci(diffs, clusters, n_boot=200, seed=7)
    assert ci1 == ci2
    assert ci1["mean"] == pytest.approx(0.1)
    p1 = clustered_sign_flip_pvalue(diffs, clusters, n_perm=200, seed=7)
    p2 = clustered_sign_flip_pvalue(diffs, clusters, n_perm=200, seed=7)
    assert p1 == p2
