"""Monotonic event-instance alignment between gold and predicted ledgers.

Set equality cannot score RQ1 because the same ``event_id`` may occur as
several distinct instances. Instead, gold and predicted instances are kept
in temporal order and aligned with a dynamic program that only pairs
instances with identical ``event_id`` and never crosses pairs.

The objective is lexicographic:

1. maximize the number of matched instance pairs;
2. minimize the total absolute status-anchor distance (in session units);
3. maximize the total core-evidence overlap (matched session count);
4. break remaining ties deterministically (prefer matching at the earliest
   gold/predicted positions: match > advance gold > advance prediction).

Distances compare canonical session numbers, so a missing/unparseable
predicted anchor never occurs here — the parser rejects malformed session
ids before alignment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rq1_models import (
    RQ1GoldEventInstance,
    RQ1PredictedEvent,
    session_number,
)


@dataclass(frozen=True)
class AlignedPair:
    gold_index: int
    pred_index: int
    event_id: str
    anchor_distance: int
    evidence_overlap: int
    status_correct: bool
    gold_status: str
    pred_status: str


@dataclass
class AlignmentResult:
    pairs: list[AlignedPair] = field(default_factory=list)
    unmatched_gold: list[int] = field(default_factory=list)
    unmatched_pred: list[int] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return len(self.pairs)


def order_predictions(events: list[RQ1PredictedEvent]) -> list[int]:
    """Deterministic prediction order: first evidence session, then input order."""

    return sorted(
        range(len(events)),
        key=lambda i: (session_number(events[i].first_evidence_session), i),
    )


def _pair_stats(
    gold: RQ1GoldEventInstance, pred: RQ1PredictedEvent
) -> tuple[int, int]:
    distance = abs(
        session_number(pred.status_anchor_session)
        - session_number(gold.status_anchor_session)
    )
    overlap = len(
        set(pred.core_evidence_sessions) & set(gold.core_evidence_sessions)
    )
    return distance, overlap


def align_events(
    gold_ledger: list[RQ1GoldEventInstance],
    predicted: list[RQ1PredictedEvent],
) -> AlignmentResult:
    """Align ordered gold instances with ordered predicted instances."""

    pred_order = order_predictions(predicted)
    ordered_preds = [predicted[i] for i in pred_order]
    n, m = len(gold_ledger), len(ordered_preds)

    # score = (matches, -total_anchor_distance, total_overlap); maximize.
    NEG = (-1, 0, 0)
    best: list[list[tuple[int, int, int]]] = [
        [(0, 0, 0)] * (m + 1) for _ in range(n + 1)
    ]
    # 0 = match, 1 = skip gold, 2 = skip pred, -1 = end
    move: list[list[int]] = [[-1] * (m + 1) for _ in range(n + 1)]

    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            candidates: list[tuple[tuple[int, int, int], int]] = []
            if gold_ledger[i].event_id == ordered_preds[j].event_id:
                distance, overlap = _pair_stats(gold_ledger[i], ordered_preds[j])
                down = best[i + 1][j + 1]
                if down != NEG:
                    candidates.append(
                        (
                            (down[0] + 1, down[1] - distance, down[2] + overlap),
                            0,
                        )
                    )
            candidates.append((best[i + 1][j], 1))
            candidates.append((best[i][j + 1], 2))
            # max by score; on ties the earliest-listed move wins
            # (match > skip gold > skip pred), which is deterministic.
            chosen_score, chosen_move = candidates[0]
            for score, mv in candidates[1:]:
                if score > chosen_score:
                    chosen_score, chosen_move = score, mv
            best[i][j] = chosen_score
            move[i][j] = chosen_move
    for i in range(n - 1, -1, -1):
        move[i][m] = 1
    for j in range(m - 1, -1, -1):
        move[n][j] = 2

    result = AlignmentResult()
    i = j = 0
    while i < n or j < m:
        mv = move[i][j]
        if mv == 0:
            gold = gold_ledger[i]
            pred = ordered_preds[j]
            distance, overlap = _pair_stats(gold, pred)
            result.pairs.append(
                AlignedPair(
                    gold_index=i,
                    pred_index=pred_order[j],
                    event_id=gold.event_id,
                    anchor_distance=distance,
                    evidence_overlap=overlap,
                    status_correct=gold.event_status == pred.status,
                    gold_status=gold.event_status,
                    pred_status=pred.status,
                )
            )
            i += 1
            j += 1
        elif mv == 1:
            result.unmatched_gold.append(i)
            i += 1
        elif mv == 2:
            result.unmatched_pred.append(pred_order[j])
            j += 1
        else:  # pragma: no cover - loop bounds guarantee a move
            raise RuntimeError("alignment backtrack out of bounds")
    return result
