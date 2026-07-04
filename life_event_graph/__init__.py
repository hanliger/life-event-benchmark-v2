"""Minimal shim for the original ``life_event_graph`` package.

``life_generator`` depends on ``life_event_graph.build_graphs()`` for its
event-node registry, but the original package is not vendored in this repo.
This shim reconstructs the node registry from the Node-Action Mapping table in
``life_generator/README.md`` so that the existing sampler/rules code runs
unchanged.

Only the surface actually consumed by ``life_generator.rules.event_registry``
is implemented: ``build_graphs() -> dict[str, Graph]`` where each graph exposes
``.nodes`` mapping node_id -> node with ``id/name/domain/actions/description``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GraphNode:
    id: str
    name: str
    domain: str
    actions: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class Graph:
    domain: str
    nodes: dict[str, GraphNode] = field(default_factory=dict)


# (id_suffix, name, actions) per domain — from life_generator/README.md
_NODE_TABLE: dict[str, tuple[tuple[str, str, tuple[str, ...]], ...]] = {
    "relationship": (
        ("marriage", "결혼", ("FA-05", "FA-08", "FA-09")),
        ("childbirth", "출산", ("FA-01", "FA-08", "FA-09")),
        ("adoption", "입양", ("FA-01", "FA-08", "FA-09")),
        ("child_independence", "자녀 독립", ("FA-05", "FA-08", "FA-09")),
        ("separation", "별거", ("FA-01", "FA-07", "FA-08")),
        ("divorce", "이혼", ("FA-01", "FA-07", "FA-08")),
        ("independence", "독립", ("FA-05", "FA-08", "FA-09")),
        ("separate_household", "분가", ("FA-05", "FA-08", "FA-09")),
        ("dependent_added", "부양가족 발생", ("FA-01", "FA-07", "FA-08")),
        ("parent_care_end", "부모 요양 종료", ("FA-01", "FA-08")),
        ("family_death", "가족 사망", ("FA-01", "FA-07", "FA-08")),
    ),
    "career": (
        ("employment", "취업", ("FA-03", "FA-08", "FA-09")),
        ("education", "교육", ("FA-04", "FA-07", "FA-09")),
        ("job_change", "이직", ("FA-03", "FA-04", "FA-08")),
        ("transfer", "전근", ("FA-01", "FA-08")),
        ("leave", "휴직", ("FA-01", "FA-08", "FA-09")),
        ("reinstatement", "복직", ("FA-03", "FA-08")),
        ("study_abroad", "유학", ("FA-04", "FA-07", "FA-09")),
        ("resignation", "퇴사", ("FA-01", "FA-04", "FA-08", "FA-09")),
        ("unemployment", "실직", ("FA-01", "FA-04", "FA-08", "FA-09")),
        ("freelance", "창업/프리랜서 전환", ("FA-05", "FA-08", "FA-10")),
        ("business_closure", "폐업/사업 중단", ("FA-01", "FA-08", "FA-10")),
        ("retirement_prep", "은퇴 준비 시작", ("FA-02", "FA-04", "FA-09")),
        ("pension_start", "연금수령시작", ("FA-02", "FA-03", "FA-08")),
    ),
    "residence": (
        ("move", "이사", ("FA-01", "FA-08")),
        ("jeonse_contract", "전세계약", ("FA-04", "FA-07", "FA-09")),
        ("rent_contract", "월세계약", ("FA-07", "FA-08", "FA-09")),
        ("contract_change", "주거 계약 변경", ("FA-04", "FA-07", "FA-08")),
        ("move_out", "퇴거", ("FA-01", "FA-08")),
        ("home_purchase", "주택 구매", ("FA-04", "FA-07", "FA-10")),
        ("home_sale", "주택 매각", ("FA-01", "FA-08", "FA-10")),
    ),
    "accident": (
        ("family_illness", "가족 질병", ("FA-01", "FA-07", "FA-09")),
        ("family_hospitalization", "가족 입원", ("FA-01", "FA-07", "FA-09")),
        ("family_surgery", "가족 수술", ("FA-01", "FA-07", "FA-09")),
        ("dependent_added", "부양 가족 발생", ("FA-01", "FA-07", "FA-08")),
        ("parent_care_end", "부모 요양 종료", ("FA-01", "FA-08")),
        ("family_death", "가족 사망", ("FA-01", "FA-07", "FA-08")),
        ("accident", "사고", ("FA-01", "FA-07", "FA-10")),
        ("disaster", "재난발생", ("FA-01", "FA-07", "FA-10")),
        ("financial_fraud", "금융사기 발생", ("FA-01", "FA-06")),
    ),
}


def build_graphs() -> dict[str, Graph]:
    graphs: dict[str, Graph] = {}
    for domain, rows in _NODE_TABLE.items():
        nodes = {
            f"{domain}_{suffix}": GraphNode(
                id=f"{domain}_{suffix}",
                name=name,
                domain=domain,
                actions=actions,
            )
            for suffix, name, actions in rows
        }
        graphs[domain] = Graph(domain=domain, nodes=nodes)
    return graphs
