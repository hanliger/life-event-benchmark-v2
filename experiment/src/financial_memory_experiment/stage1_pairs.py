"""Stage 1: occurred-event / evidence-session pair reconstruction.

The reported Stage 1 task asks the model to reconstruct, for a whole prefix,
every life event that actually occurred together with the session that first
establishes each occurrence. It is scored by
`strict_occurred_event_evidence_f1`: an exact multiset F1 with no partial
credit.

Everything task-specific already exists as library code under
`fin_life_benchmark.benchmark.rq1_*`, which the root evaluator
(`scripts/evaluate_rq1_pairs.py`) also uses. This module is the experiment
harness's adapter onto it, so both entrypoints score identically.

Two invariants the harness must not break:

* The model sees public `D###` ids and dialogue turns only. The `S### -> D###`
  map is `to_public_session_id`, a deterministic rename, so a prompt can be
  rendered without touching item Gold.
* Gold is always projected over the **full** prefix. The no_prospective corpus
  changes what the model reads, never what is correct.
"""

from __future__ import annotations

from typing import Any

from fin_life_benchmark.benchmark.rq1_builder import (
    render_sessions_block,
    render_taxonomy_block,
    to_public_session_id,
    visible_ids_for_condition,
)
from fin_life_benchmark.benchmark.rq1_models import RQ1Item
from fin_life_benchmark.benchmark.rq1_pair_metrics import pair_item_metrics
from fin_life_benchmark.benchmark.rq1_pair_models import (
    RQ1_PAIR_METRICS_VERSION,
    RQ1_PAIR_PROMPT_FILE,
    RQ1_PAIR_PROTOCOL_VERSION,
    RQ1_PAIR_STAGE,
    gold_pairs_from_occurred_trajectory,
)
from fin_life_benchmark.benchmark.rq1_pair_no_prospective import (
    NO_PROSPECTIVE_SUBSTITUTED_CONDITION,
    surviving_prospective_sessions,
)
from fin_life_benchmark.benchmark.rq1_pair_parser import parse_pair_prediction


STAGE1_PAIRS = RQ1_PAIR_STAGE
CONDITION = NO_PROSPECTIVE_SUBSTITUTED_CONDITION
PROTOCOL_VERSION = RQ1_PAIR_PROTOCOL_VERSION
METRICS_VERSION = RQ1_PAIR_METRICS_VERSION
PROMPT_FILE = RQ1_PAIR_PROMPT_FILE
HEADLINE_METRIC = "strict_occurred_event_evidence_f1"
# Anthropic counts adaptive thinking against max_tokens, and the answer is a
# whole pair list rather than one token.
MAX_OUTPUT_TOKENS = 20_000


def public_id_map(session_ids: list[str]) -> dict[str, str]:
    """Derive the model-visible id map without reading Gold."""

    return {session_id: to_public_session_id(session_id) for session_id in session_ids}


def render_prompt(
    *,
    prompt_template: str,
    taxonomy: list[dict[str, str]],
    evidence: list[dict[str, Any]],
) -> str:
    """Fill the frozen pair prompt with the taxonomy and the given sessions.

    `evidence` is whatever the method chose to supply: the full prefix for Full
    Context, the retrieved subset for BM25/Dense/Mem0. Only public ids and turns
    are rendered.
    """

    id_map = public_id_map([str(row["session_id"]) for row in evidence])
    return prompt_template.replace(
        "{{TAXONOMY}}", render_taxonomy_block(taxonomy)
    ).replace("{{SESSIONS}}", render_sessions_block(evidence, id_map))


def gold_pairs(
    item: dict[str, Any],
    *,
    sessions: dict[str, dict[str, Any]],
    taxonomy_event_ids: set[str],
) -> list[tuple[str, str]]:
    """Project Gold over the full prefix, independent of what the model saw."""

    parsed = RQ1Item.model_validate(item)
    prefix_ids = visible_ids_for_condition(parsed, "full_prefix")
    return gold_pairs_from_occurred_trajectory(
        parsed.gold.occurred_trajectory,
        session_id_map=public_id_map(prefix_ids),
        sessions=sessions,
        taxonomy_event_ids=taxonomy_event_ids,
    )


def assert_substituted_corpus(
    item_id: str,
    *,
    session_ids: list[str],
    sessions: dict[str, dict[str, Any]],
) -> None:
    """Refuse to score a full_prefix run as if it were the ablation.

    A surviving prospective session means the sessions directory is not the
    substituted corpus, which would silently turn the default condition into the
    untouched baseline.
    """

    survivors = surviving_prospective_sessions(session_ids, sessions)
    if survivors:
        raise RuntimeError(
            f"{item_id}: condition {CONDITION} needs a corpus with every "
            f"prospective session substituted, but {len(survivors)} survive "
            f"(e.g. {survivors[:5]}); prepare the no_prospective corpus"
        )


def score(
    *,
    raw_answer: str,
    gold: list[tuple[str, str]],
    visible_session_ids: list[str],
    sessions: dict[str, dict[str, Any]],
    taxonomy_event_ids: set[str],
) -> dict[str, Any]:
    """Parse and score one prediction with the shared strict-multiset metric."""

    id_map = public_id_map(visible_session_ids)
    prediction = parse_pair_prediction(
        raw_answer,
        visible_public_ids=set(id_map.values()),
        taxonomy_event_ids=taxonomy_event_ids,
    )
    metrics = pair_item_metrics(
        gold,
        prediction,
        session_type_by_public_id={
            public: sessions[session_id].get("session_type", "")
            for session_id, public in id_map.items()
            if session_id in sessions
        },
    )
    return {
        "prediction": [
            {
                "event_id": pair.event_id,
                "evidence_session_id": pair.evidence_session_id,
            }
            for pair in prediction.valid_pairs
        ],
        "rejected_records": list(prediction.rejected_records),
        "validation_errors": list(prediction.validation_errors),
        "gold": [
            {"event_id": event_id, "evidence_session_id": public}
            for event_id, public in gold
        ],
        "parse_error": prediction.parse_error,
        "invalid_record_count": prediction.invalid_record_count,
        "metrics": metrics,
        "metrics_version": METRICS_VERSION,
    }
