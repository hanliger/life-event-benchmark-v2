#!/usr/bin/env python
"""Build an evaluator-only human review packet for one canary trajectory."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401

from fin_life_benchmark.io import read_jsonl, write_jsonl

HIGH_RISK = {"FA-07", "FA-08", "FA-09", "FA-10"}

REVIEW_GUIDE_LINES = (
    "## 검토 방법",
    "",
    "- 이 packet에 표시된 최종 생성 대화를 판정합니다. `repairs`가 1 이상이거나 자동 flag가 있다는 사실은 주의해서 볼 이유일 뿐, 그 자체로 fail은 아닙니다.",
    "- evaluator-only event, lifecycle, task, cue, memory metadata는 정답 기준으로 사용할 수 있지만 benchmark-visible dialogue에 복사하면 안 됩니다.",
    "- 실제 결과는 `sampled_sessions.jsonl`에 기록합니다. 기준을 충족하면 `true`/`pass`, 충족하지 못하면 `false`/`fail`을 입력합니다. 이 Markdown의 체크박스만 수정해도 scoring에는 반영되지 않습니다.",
    "- N/A 값은 지원하지 않습니다. routine 또는 low-risk session에서 직접 적용되지 않는 항목은 관련 위험이 대화에 우발적으로 발생하지 않았을 때 pass로 판정합니다.",
    "- fail에는 `comments`에 문제가 발생한 발화와 간단한 이유를 남깁니다. 경계 사례에 대한 판단 근거도 기록하는 것을 권장합니다.",
    "",
    "## Reviewer field 상세 기준",
    "",
    "### `natural_korean_dialogue`",
    "",
    "8개 발화가 하나의 은행 업무에 관한 자연스럽고 간결한 한국어 모바일뱅킹 대화를 구성하는지 확인합니다.",
    "",
    "- **Pass:** user 발화가 실제 구어체로 자연스럽고, assistant가 정중한 앱 챗봇 말투를 사용합니다. 각 답변이 직전 발화에 맞게 이어지고 확인 질문이 실무적으로 필요하며, 단순 업무를 억지로 늘이지 않고 자연스럽게 끝납니다.",
    "- **Fail:** 번역체, 앞뒤가 맞지 않는 응답, 동일 질문·사실의 반복, 불필요한 서류·상품 설명, 한 session 안의 복수 업무, 일관되지 않은 존댓말, 창구 방문을 전제로 한 안내 등으로 대화가 부자연스럽습니다.",
    "",
    "### `event_task_alignment`",
    "",
    "계획된 금융 업무와 대화에 드러난 life-event evidence가 이 session 안에서 자연스럽게 연결되는지 확인합니다.",
    "",
    "- **Pass:** user가 해당 금융 업무를 처리하면서 event 단서를 말할 현실적인 이유가 있고, session 전체가 한 가지 `financial_task`에 머뭅니다. 월세·전세·매매 등 event subtype의 세부사항도 업무와 호환됩니다.",
    "- **Fail:** event 단서가 무관한 업무에 억지로 붙거나, 중간에 다른 업무로 전환되거나, 서로 다른 event의 세부사항이 결합되거나, subtype·현재 상태가 업무와 충돌합니다. 예를 들어 이사 상담 중 무관한 병원비 이체가 나오거나 매매 scenario에 월세 납부 논리가 등장하는 경우입니다.",
    "",
    "### `lifecycle_calibration`",
    "",
    "대화의 시제와 확실성 수준이 evaluator lifecycle과 일치하며, event 상태를 과장하거나 뒤집지 않는지 확인합니다.",
    "",
    "- **Pass:** `weak_signal`은 가능성으로 남고, `upcoming`은 예정·준비 중으로 표현되며, `occurred`는 이미 발생했고 관련 금융 결과가 드러납니다. `cancelled`는 이전 계획과 취소 사실이 모두 복원 가능하고, stale recall은 과거 값과 현재 값을 명확히 구분합니다. routine과 hard-negative는 event가 발생한 것처럼 말하지 않습니다.",
    "- **Fail:** 가능성을 확정 사실로 말하거나, 미래 event를 완료된 것으로 말하거나, occurred event를 단순 가정으로 남깁니다. 취소에서 이전 계획 또는 철회 단서가 빠지거나, 과거·현재 값을 혼동하거나, hard negative를 positive evidence처럼 표현하는 경우도 fail입니다.",
    "",
    "### `memory_grounding`",
    "",
    "모든 expected long-term memory operation이 user 발화의 명시적 근거를 가지며, 지원되지 않은 update가 추가로 암시되지 않는지 확인합니다.",
    "",
    "- **Pass:** 각 expected path·operation·value를 정당화할 정보가 user 발화에 있습니다. 금액·날짜·주거 형태·관계 등의 정확한 값이 evaluator metadata와 일치하고, archive·stale·clear에는 변경·종료·취소 근거가 명시됩니다. no-update session은 그대로 no-update이며 현재 상태와도 충돌하지 않습니다.",
    "- **Fail:** memory fact가 assistant 발화에만 있거나 추측에 의존하고, 제공되지 않은 값 또는 불일치하는 값을 만들거나, 과거와 현재 상태를 혼동합니다. 한 사건의 bundled update 중 일부 근거가 누락되거나 task-local 선택·hard negative를 장기 memory update로 해석하는 경우도 fail입니다.",
    "",
    "### `assistant_semantic_leakage`",
    "",
    "assistant가 user가 아직 드러내지 않은 숨은 event semantics를 먼저 제공하지 않는지 확인합니다. 이 항목에서 **pass/true는 leakage가 없었다는 뜻**입니다.",
    "",
    "- **Pass:** assistant가 중립적인 업무 질문을 하고 user가 이미 말한 사실만 반영하며 life event를 명명하거나 요약하지 않습니다. event identity·subtype·status·value를 새로 추가하지 않는 짧은 확인 응답은 허용됩니다.",
    "- **Fail:** assistant가 event identity, 근접 직설 표현, lifecycle status, subtype, 금액·날짜·주소·관계 또는 정답 추론에 필요한 다른 evidence를 먼저 말합니다. user가 같은 의미를 아직 말하지 않았는데 ‘이사하셨군요’, ‘퇴직하신 뒤’처럼 요약하는 경우가 해당합니다.",
    "",
    "### `high_risk_safety`",
    "",
    "이체·송금·납부·해지·등록 등 영향이 큰 action이 required slot과 명시적 confirmation 규칙을 지키는지 확인합니다.",
    "",
    "- **Pass:** 계좌·수취인·금액·날짜 등 누락된 required slot은 미확정으로 유지하고 event fact를 실행 slot으로 전용하지 않습니다. contract가 허용하고 user가 명시적으로 확인한 뒤에만 완료를 말하며, 대화와 `action_resolution`이 일치합니다. non-high-risk session은 우발적인 고위험 실행이나 false completion이 없으면 pass입니다.",
    "- **Fail:** slot이 누락됐는데 완료를 주장하거나 실행값을 만들거나 추론하고, 모호한 동의를 confirmation으로 취급하거나, 필요한 confirmation 전에 실행합니다. `action_resolution`과 모순되거나 자격·수수료·은행 정책을 근거 없이 보장하는 경우도 fail입니다.",
    "",
    "### `event_implicit_but_recoverable`",
    "",
    "의도한 event label을 직접 말하지 않으면서도 하나의 session에 정확한 event 해석을 single-hop으로 복원할 충분한 user evidence가 있는지 확인합니다.",
    "",
    "- **Pass:** event-bearing session에서는 user 발화를 종합해 intended event, 필요한 subtype, lifecycle 차이를 추론할 수 있지만 literal event label이나 금지된 근접 직설 표현은 없습니다. occurred fact bundle 전체를 다른 session에 의존하지 않고 이 session에서 복원할 수 있습니다. routine 또는 hard-negative에서는 qualifying event/update가 없다는 점과 near-miss 차이를 false positive 없이 판단할 수 있습니다.",
    "- **Fail:** evidence가 너무 모호하거나 다른 session을 봐야 event를 식별할 수 있고, 필수 subtype·status 구분이 빠지거나, 여러 event가 동등하게 가능하거나, 정답을 너무 직접적으로 밝혀 추론 과제가 사라집니다.",
    "",
    "### `comments`",
    "",
    "fail 또는 경계 사례의 이유를 짧고 수정 가능하게 기록합니다. speaker와 발화 순서를 밝히고 문제가 되는 최소 문구만 인용하며, 가능하면 benchmark dialogue에 evaluator-only label을 노출하지 않는 수정 방향을 적습니다.",
    "",
    "## Scoring gate",
    "",
    "- `memory_grounding`, `assistant_semantic_leakage`, `high_risk_safety`: fail 0건이어야 합니다.",
    "- `event_task_alignment`, `lifecycle_calibration`, `event_implicit_but_recoverable`: pass rate 95% 이상이어야 합니다.",
    "- `natural_korean_dialogue`: pass rate 90% 이상이어야 합니다.",
    "- scoring 전에 모든 reviewer field를 입력해야 합니다.",
    "",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectory-id", required=True)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--target", type=int, default=40)
    args = parser.parse_args()
    plans = {item["session_id"]: item for item in read_jsonl(Path(args.plans_dir) / f"plans_{args.trajectory_id}.jsonl")}
    sessions = {item["session_id"]: item for item in read_jsonl(Path(args.sessions_dir) / f"sessions_{args.trajectory_id}.jsonl")}
    audit = json.loads((Path(args.audit_dir) / "dialogue_generation_audit.json").read_text(encoding="utf-8"))
    violations: dict[str, list[dict]] = defaultdict(list)
    for item in audit.get("violations", []):
        violations[item["session_id"]].append(item)
    rng = random.Random(args.seed)
    selected: list[str] = []

    def add(candidates, limit=None):
        values = [session_id for session_id in candidates if session_id in sessions and session_id not in selected]
        rng.shuffle(values)
        selected.extend(values if limit is None else values[:limit])

    add([sid for sid, session in sessions.items() if violations[sid] or (session.get("generation_metadata") or {}).get("repair_count")], None)
    add([sid for sid, plan in plans.items() if plan.get("session_type") == "cancellation_evidence"], 5)
    add([sid for sid, plan in plans.items() if plan.get("session_type") == "stale_recall_session"], 5)
    occurred_by_domain: dict[str, list[str]] = defaultdict(list)
    for sid, plan in plans.items():
        if plan.get("session_type") == "occurred_evidence":
            occurred_by_domain[((plan.get("structured_context") or {}).get("event") or {}).get("domain", "unknown")].append(sid)
    occurred = []
    for domain in sorted(occurred_by_domain):
        occurred.append(rng.choice(occurred_by_domain[domain]))
    occurred.extend(sid for sid, plan in plans.items() if plan.get("session_type") == "occurred_evidence")
    add(occurred, 8)
    add([sid for sid, plan in plans.items() if plan.get("session_type") in {"weak_signal_evidence", "upcoming_evidence"}], 5)
    add([sid for sid, plan in plans.items() if plan.get("session_type") == "hard_negative"], 5)
    add([sid for sid, plan in plans.items() if plan.get("mapped_action") in HIGH_RISK], 5)
    add([sid for sid, plan in plans.items() if plan.get("session_type") == "routine_financial"], max(0, args.target - len(selected)))

    records = []
    for sid in selected:
        plan, session = plans[sid], sessions[sid]
        metadata = session.get("generation_metadata") or {}
        event = (plan.get("structured_context") or {}).get("event") or {}
        records.append({
            "evaluator_only": {
                "trajectory_id": args.trajectory_id,
                "session_id": sid,
                "session_type": plan.get("session_type"),
                "lifecycle_status": plan.get("event_status_after_session"),
                "event_id": event.get("event_id"),
                "financial_task": plan.get("financial_task"),
                "planned_cues": plan.get("planned_cues") or [],
                "expected_memory_updates": (plan.get("structured_context") or {}).get("session_memory_updates") or [],
                "validator_results": violations[sid],
                "repair_count": metadata.get("repair_count", 0),
                "provider": metadata.get("provider"),
                "model": metadata.get("model"),
                "token_usage": metadata.get("usage") or {},
                "latency_ms": metadata.get("request_duration_ms"),
                "automatic_flags": {
                    "direct_disclosure_patterns": [
                        item
                        for item in violations[sid]
                        if item.get("code")
                        in {
                            "direct_event_disclosure",
                            "near_direct_event_disclosure",
                            "forbidden_event_paraphrase",
                        }
                    ],
                    "lifecycle_phrase_family": plan.get(
                        "lifecycle_surface_family"
                    ),
                    "evidence_dimensions_planned": [
                        item.get("dimension_id")
                        for item in plan.get("evidence_dimensions") or []
                    ],
                    "evidence_dimensions_realized": sorted(
                        {
                            item.get("evidence_dimension_id")
                            for item in session.get("cue_annotations") or []
                            if item.get("evidence_dimension_id")
                        }
                    ),
                    "evidence_dimensions_missing": sorted(
                        set(
                            item.get("dimension_id")
                            for item in plan.get("evidence_dimensions") or []
                            if item.get("required", True)
                        )
                        - {
                            item.get("evidence_dimension_id")
                            for item in session.get("cue_annotations") or []
                            if item.get("evidence_dimension_id")
                        }
                    ),
                    "evidence_dimension_violations": [
                        item
                        for item in violations[sid]
                        if item.get("code")
                        in {
                            "required_evidence_not_realized",
                            "insufficient_event_evidence",
                            "missing_required_evidence_role",
                            "subtype_not_disambiguated",
                        }
                    ],
                    "high_risk_contract": plan.get(
                        "action_execution_contract"
                    ),
                    "high_risk_slots": {
                        "required": (
                            plan.get("action_execution_contract") or {}
                        ).get("required_slots") or [],
                        "grounded": (
                            plan.get("action_execution_contract") or {}
                        ).get("grounded_slots") or {},
                        "plan_missing": (
                            plan.get("action_execution_contract") or {}
                        ).get("missing_slots") or [],
                        "provided": (session.get("action_resolution") or {}).get(
                            "provided_slots"
                        ) or {},
                        "resolution_missing": (
                            session.get("action_resolution") or {}
                        ).get("missing_slots") or [],
                    },
                    "action_resolution": session.get("action_resolution"),
                    "policy_violations": [
                        item
                        for item in violations[sid]
                        if item.get("code")
                        in {
                            "unsupported_bank_policy_claim",
                            "bank_policy_contradiction",
                        }
                    ],
                    "semantic_template_concentration_group": {
                        "placement_strategy": plan.get(
                            "evidence_placement_strategy"
                        ),
                        "lifecycle_surface_variant_id": plan.get(
                            "lifecycle_surface_variant_id"
                        ),
                        "hard_negative_surface_variant_id": plan.get(
                            "hard_negative_surface_variant_id"
                        ),
                    },
                },
            },
            "generated_dialogue": session.get("turns") or [],
            "cue_annotations": session.get("cue_annotations") or [],
            "reviewer": {
                "natural_korean_dialogue": None,
                "event_task_alignment": None,
                "lifecycle_calibration": None,
                "memory_grounding": None,
                "assistant_semantic_leakage": None,
                "high_risk_safety": None,
                "event_implicit_but_recoverable": None,
                "comments": "",
            },
        })
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "sampled_sessions.jsonl", records)
    index = {"trajectory_id": args.trajectory_id, "seed": args.seed, "count": len(records), "session_ids": selected}
    (output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Canary dialogue human-review packet",
        "",
        "> Evaluator-only metadata below must not be copied into benchmark-visible dialogue.",
        "",
        *REVIEW_GUIDE_LINES,
    ]
    for record in records:
        meta = record["evaluator_only"]
        lines.extend([
            f"## {meta['session_id']} — {meta['session_type']}", "",
            f"- evaluator event: `{meta['event_id']}`", f"- lifecycle: `{meta['lifecycle_status']}`",
            f"- task: {meta['financial_task']}", f"- repairs: {meta['repair_count']}", "", "### Dialogue", "",
        ])
        lines.extend(f"- **{turn['speaker']}**: {turn['text']}" for turn in record["generated_dialogue"])
        lines.extend(["", "### Reviewer fields", "", "- natural Korean dialogue: [ ] pass [ ] fail", "- event-task alignment: [ ] pass [ ] fail", "- lifecycle calibration: [ ] pass [ ] fail", "- memory grounding: [ ] pass [ ] fail", "- assistant semantic leakage: [ ] pass [ ] fail", "- high-risk safety: [ ] pass [ ] fail", "- event implicit but recoverable: [ ] pass [ ] fail", "- comments:", ""])
    (output_dir / "review_packet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"review packet: {len(records)} sessions -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
