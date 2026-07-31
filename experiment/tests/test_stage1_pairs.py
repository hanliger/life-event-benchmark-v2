from __future__ import annotations

import pytest

from financial_memory_experiment import stage1_pairs as sp


SESSIONS = {
    "S002": {"session_id": "S002", "session_type": "occurred_evidence"},
    "S003": {"session_id": "S003", "session_type": "hard_negative"},
}
TAXONOMY_IDS = {"E001", "E002"}
EXACT = '{"pairs": [{"event_id": "E001", "evidence_session_id": "D002"}]}'


def _score(raw: str, gold: list[tuple[str, str]]) -> dict:
    return sp.score(
        raw_answer=raw,
        gold=gold,
        visible_session_ids=["S002", "S003"],
        sessions=SESSIONS,
        taxonomy_event_ids=TAXONOMY_IDS,
    )


def test_stage_and_condition_come_from_the_shared_protocol():
    assert sp.STAGE1_PAIRS == "stage1_occurred_event_evidence_pairs"
    assert sp.CONDITION == "no_prospective_substituted"
    assert sp.HEADLINE_METRIC == "strict_occurred_event_evidence_f1"


def test_prompt_renders_public_ids_only_without_touching_gold():
    rendered = sp.render_prompt(
        prompt_template="TAX:\n{{TAXONOMY}}\nSESS:\n{{SESSIONS}}",
        taxonomy=[{"event_id": "E001", "label_ko": "이직"}],
        evidence=[
            {
                "session_id": "S002",
                "turns": [{"speaker": "user", "text": "이직했어요"}],
            }
        ],
    )
    assert "D002" in rendered
    # The canonical id must never reach the model.
    assert "S002" not in rendered
    assert "이직했어요" in rendered


def test_public_id_map_is_a_deterministic_rename():
    assert sp.public_id_map(["S001", "S015", "S300"]) == {
        "S001": "D001",
        "S015": "D015",
        "S300": "D300",
    }


def test_scoring_is_strict_with_no_partial_credit():
    exact = _score(EXACT, [("E001", "D002")])
    assert exact["metrics"][sp.HEADLINE_METRIC] == 1.0
    assert exact["metrics"]["exact_pair_multiset_match"] == 1.0
    assert exact["prediction"] == [
        {"event_id": "E001", "evidence_session_id": "D002"}
    ]

    # Right event, wrong establishing session: no credit at all.
    wrong_session = _score(
        '{"pairs": [{"event_id": "E001", "evidence_session_id": "D003"}]}',
        [("E001", "D002")],
    )
    assert wrong_session["metrics"][sp.HEADLINE_METRIC] == 0.0
    assert wrong_session["metrics"]["false_positive_pair_count"] == 1
    assert wrong_session["metrics"]["false_negative_pair_count"] == 1

    partial = _score(EXACT, [("E001", "D002"), ("E002", "D003")])
    assert partial["metrics"][sp.HEADLINE_METRIC] == pytest.approx(2 / 3)
    assert partial["metrics"]["exact_pair_multiset_match"] == 0.0


def test_unparsable_answer_scores_zero_and_is_recorded():
    result = _score("태그도 JSON도 없이 설명만 했다", [("E001", "D002")])
    assert result["metrics"][sp.HEADLINE_METRIC] == 0.0
    assert result["parse_error"]


def test_ablation_guard_rejects_an_unsubstituted_corpus():
    # A surviving prospective session means this is really a full_prefix run.
    with pytest.raises(RuntimeError, match="no_prospective_substituted"):
        sp.assert_substituted_corpus(
            "traj_001_cp015",
            session_ids=["S004"],
            sessions={
                "S004": {
                    "session_id": "S004",
                    "session_type": "upcoming_evidence",
                }
            },
        )


def test_ablation_guard_passes_a_substituted_corpus():
    sp.assert_substituted_corpus(
        "traj_001_cp015", session_ids=["S002", "S003"], sessions=SESSIONS
    )
