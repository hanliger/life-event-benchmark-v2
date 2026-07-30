from __future__ import annotations

from typing import Any, Iterable

from ..prompts import s000_as_session
from ..stage2_2 import STAGE2_2, VALUE_KINDS


STAGE2_2_RETRIEVAL_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "group_id": "profile_household",
        "prefixes": ("profile.", "household."),
        "description_ko": "개인 기본정보, 가족, 배우자·파트너, 자녀와 부양가족",
    },
    {
        "group_id": "employment_education",
        "prefixes": ("employment.", "education."),
        "description_ko": "고용, 직장, 소득 안정성, 급여와 본인·자녀 교육",
    },
    {
        "group_id": "housing_financial_products",
        "prefixes": ("housing.", "financial_products."),
        "description_ko": "주거, 주소, 임대차, 부동산, 계좌, 저축, 대출과 연금",
    },
    {
        "group_id": "goals_cashflow",
        "prefixes": ("goals.", "cashflow."),
        "description_ko": "비상금·주거·교육·은퇴 목표와 최근 일회성 지출",
    },
)


def stage2_2_retrieval_queries() -> tuple[dict[str, Any], ...]:
    """Return the frozen Gold-independent retrieval queries.

    Only public schema paths, public value kinds, and neutral Korean domain
    descriptions are used. Candidate values, Gold values, and dynamic-path
    annotations are deliberately excluded.
    """

    queries: list[dict[str, Any]] = []
    for group in STAGE2_2_RETRIEVAL_GROUPS:
        paths = [
            path
            for path in VALUE_KINDS
            if path.startswith(tuple(group["prefixes"]))
        ]
        typed_paths = ", ".join(
            f"{path} ({VALUE_KINDS[path]})" for path in paths
        )
        queries.append(
            {
                "group_id": group["group_id"],
                "paths": paths,
                "query": (
                    "현재 금융 상태 변경과 최신 사실을 찾는다. "
                    f"범위: {group['description_ko']}. "
                    f"관련 state path와 자료형: {typed_paths}. "
                    "계획·희망·문의가 아니라 실제 발생·취소·정정·갱신을 우선한다."
                ),
            }
        )
    return tuple(queries)


def stage2_2_output_tokens(item: dict[str, Any]) -> int | None:
    if item.get("stage") != STAGE2_2:
        return None
    return int((item.get("metadata") or {}).get("max_output_tokens", 20_000))


def pin_initial_state(
    s000: dict[str, Any] | None,
    evidence: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    if s000 is None:
        raise RuntimeError("Stage 2.2 retrieval requires S000 initial state")
    rows = [
        row
        for row in evidence
        if str(row.get("session_id")) not in {"S000", "D000"}
    ]
    return [s000_as_session(s000), *rows]


def deduplicate_ranked_sessions(
    ranked_groups: Iterable[Iterable[tuple[int, float]]],
    *,
    max_evidence: int,
) -> list[int]:
    best: dict[int, float] = {}
    for group in ranked_groups:
        for index, score in group:
            best[index] = max(float(score), best.get(index, float("-inf")))
    selected = sorted(best, key=lambda index: (-best[index], index))[
        :max_evidence
    ]
    return sorted(selected)
