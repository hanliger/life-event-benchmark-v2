#!/usr/bin/env python
"""OPTIONAL regeneration of sessions the advisory LLM judge flagged.

Consumes ``suggested_regeneration.jsonl`` from ``judge_dialogue_sessions.py``
and regenerates each flagged session, injecting the judge's dimension verdicts
and free-text comment as non-authoritative *guidance* into a fresh generation
(via ``DialogueGenerator.generate_session(..., extra_guidance=...)``).

This closes the judge → regeneration loop (design option A), but stays outside
the required procedural gate chain — nothing here runs unless you invoke it.

Guardrails baked in:
- The judge comment is a hint, never ground truth. Every regenerated session
  re-runs the full deterministic validator inside ``_llm_session``; a session
  that cannot pass the validator is NOT written (it stays as a failure).
- The judge note is marked non-visible so it is not quoted into the dialogue.
- One pass per invocation. To bound rounds, re-run the judge on the rewritten
  sessions and inspect ``suggested_regeneration.jsonl`` before regenerating
  again; sessions that keep getting flagged should go to human review, not an
  unbounded judge<->generator loop.

Example:

  python scripts/regenerate_judged_sessions.py \
    --regeneration-file data/runs/v4/reports/dialogue_judge/suggested_regeneration.jsonl \
    --trajectories-dir data/runs/v4/trajectories \
    --sessions-dir data/runs/v4/dialogues/sessions \
    --execute --provider anthropic --model claude-opus-4-8
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.regenerate_judged_sessions in tests
    from scripts import _bootstrap  # type: ignore # noqa: F401

from tqdm import tqdm

from fin_life_benchmark.dialogue.evidence_planner import EvidencePlanner
from fin_life_benchmark.dialogue.generator import DialogueGenerator
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, read_jsonl, write_jsonl
from fin_life_benchmark.llm.client import LLMClient
from fin_life_benchmark.locale import load_locale
from fin_life_benchmark.trajectory.models import Trajectory


# One-line, generator-facing description of each evaluated dimension. The judge
# reports failures as internal labels ("critical:<field>" / "soft:<field>"); the
# generator needs to know what each dimension actually checks to act on it.
DIMENSION_GUIDANCE = {
    "natural_korean_dialogue": "8턴이 하나의 은행 업무에 관한 자연스럽고 간결한 한국어 모바일뱅킹 대화를 이루는지 (번역체·반복·불필요한 설명 금지)",
    "event_task_alignment": "계획된 금융 업무와 life-event 단서가 한 session 안에서 자연스럽게 연결되고, 업무가 하나로 유지되는지",
    "lifecycle_calibration": "시제·확실성이 evaluator lifecycle과 일치하는지 (weak_signal=가능성, upcoming=예정, occurred=이미 발생·금융결과, cancelled=계획+취소 복원)",
    "memory_grounding": "expected long-term memory update가 모두 user 발화의 명시적 근거를 갖고, 지원되지 않은 update를 암시하지 않는지",
    "assistant_semantic_leakage": "assistant가 user가 아직 드러내지 않은 event semantics(정체·subtype·status·값)를 먼저 노출하지 않는지",
    "high_risk_safety": "이체·송금·해지 등 고위험 action이 required slot과 명시적 confirmation 규칙을 지키는지 (누락 slot으로 완료 주장 금지)",
    "event_implicit_but_recoverable": "event label을 직접 말하지 않으면서도 이 session만으로 정확한 event를 single-hop 복원할 user evidence가 충분한지",
}


def _describe_reason(reason: str) -> str:
    """Turn a 'critical:<field>' / 'soft:<field>' label into a readable line."""
    tier, _, field = reason.partition(":")
    field = field or tier
    tier_label = {"critical": "핵심 게이트", "soft": "품질 기준"}.get(tier, "평가 항목")
    description = DIMENSION_GUIDANCE.get(field, "")
    suffix = f": {description}" if description else ""
    return f"{field} ({tier_label}){suffix}"


def build_feedback(record: dict[str, Any]) -> str:
    """Render one suggested-regeneration record as a non-visible evaluator note."""
    reasons = record.get("reasons") or []
    reason_lines = "\n".join(f"  * {_describe_reason(reason)}" for reason in reasons) or "  * (사유 미기재)"
    comment = (record.get("comments") or "").strip() or "(코멘트 없음)"
    return (
        "[평가자 피드백 — 비공개, 대화에 인용/노출 금지]\n"
        "직전 생성본이 아래 평가 항목에서 미흡 판정을 받았습니다. 각 항목의 기준을 충족하도록 다시 생성하세요.\n"
        f"- 미흡 판정 항목:\n{reason_lines}\n"
        f"- 평가자 코멘트: {comment}\n"
        "계획(plan)의 제약을 지키면서 위 문제를 반복하지 않도록 대화를 다시 생성하고, "
        "이 노트의 내용을 대화 발화로 옮기지 마세요."
    )


def group_by_trajectory(records: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """{trajectory_id: {session_id: feedback_text}} from regeneration records."""
    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    for record in records:
        traj = record.get("trajectory_id")
        sid = record.get("session_id")
        if not traj or not sid:
            continue
        grouped[traj][sid] = build_feedback(record)
    return dict(grouped)


def _session_sort_key(session: dict[str, Any]) -> tuple[int, str]:
    session_id = str(session.get("session_id", ""))
    if session_id.startswith("S") and session_id[1:].isdigit():
        return int(session_id[1:]), session_id
    return 10**9, session_id


def _load_existing_sessions(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {
        str(session["session_id"]): session
        for session in read_jsonl(path)
        if session.get("session_id")
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--regeneration-file", required=True, help="suggested_regeneration.jsonl from the judge")
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument(
        "--plans-dir",
        default=None,
        help="load the FROZEN plans_<traj>.jsonl the sessions were generated from (the "
        "same plans the judge scores against). STRONGLY recommended: without it, plans "
        "are rebuilt via build_plans(--seed) and will diverge from the frozen plans "
        "unless the seed and planner code exactly match plan-build time, silently "
        "regenerating sessions against the wrong financial_task.",
    )
    parser.add_argument("--seed", type=int, default=0, help="only used when --plans-dir is omitted (build_plans fallback)")
    parser.add_argument("--max-sessions", type=int, help="cap total sessions regenerated this pass")
    parser.add_argument("--raw-output-dir", default=None)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8192,
        help="generation output cap; match the original run (default 8192). Too small "
        "starves thinking-on models (e.g. Sonnet 5) and yields empty output.",
    )
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--retry-label", default="judge_regen", help="filename label for raw outputs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="call the LLM API")
    mode.add_argument("--mock", action="store_true", help="deterministic template dialogues (ignores guidance)")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    records = list(read_jsonl(args.regeneration_file))
    grouped = group_by_trajectory(records)
    if not grouped:
        print("nothing to regenerate (empty or malformed regeneration file)")
        return 0

    if args.execute and not args.plans_dir:
        print(
            "WARNING: --execute without --plans-dir. Plans will be REBUILT via "
            f"build_plans(seed={args.seed}) and may not match the frozen plans the "
            "sessions were generated from / the judge scores against, silently "
            "regenerating against the wrong financial_task. Pass --plans-dir to load "
            "the frozen plans.",
            flush=True,
        )

    paths = RepoPaths.default()
    locale = load_locale(args.locale, paths)
    templates = load_life_event_templates(paths)
    planner = EvidencePlanner(templates, locale, paths)
    raw_output_dir = Path(args.raw_output_dir) if args.raw_output_dir else paths.raw_model_outputs / "dialogue"
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir = Path(args.sessions_dir)

    if args.execute:
        client = LLMClient.from_env(
            provider=args.provider,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            max_tokens=args.max_tokens,
        )
        if client.provider == "mock":
            raise SystemExit("--execute requires DEFAULT_LLM_PROVIDER=openai|anthropic in .env")
        generator = DialogueGenerator(
            mode="llm", client=client, paths=paths,
            raw_output_dir=raw_output_dir, raw_filename_suffix=f"_{args.retry_label}",
        )
    else:
        generator = DialogueGenerator(
            mode="mock", paths=paths,
            raw_output_dir=raw_output_dir, raw_filename_suffix=f"_{args.retry_label}",
        )

    budget = args.max_sessions
    results: list[dict[str, Any]] = []
    total_recovered = total_failed = 0

    for traj_file in tqdm(sorted(Path(args.trajectories_dir).glob("traj_*.json")), desc="judge-regen"):
        trajectory_id = traj_file.stem
        feedback_by_session = grouped.get(trajectory_id)
        if not feedback_by_session:
            continue
        trajectory = Trajectory.model_validate(json.loads(traj_file.read_text(encoding="utf-8")))
        if args.plans_dir:
            plan_path = Path(args.plans_dir) / f"plans_{trajectory_id}.jsonl"
            plans = {p.session_id: p for p in (DialogueGenerationPlan.model_validate(r) for r in read_jsonl(plan_path))}
        else:
            plans = {plan.session_id: plan for plan in planner.build_plans(trajectory, seed=args.seed)}
        sessions_path = sessions_dir / f"sessions_{trajectory_id}.jsonl"
        existing = _load_existing_sessions(sessions_path)

        recovered = 0
        for session_id, feedback in tqdm(feedback_by_session.items(), desc=trajectory_id, leave=False):
            if budget is not None and budget <= 0:
                break
            plan = plans.get(session_id)
            if plan is None:
                results.append({"trajectory_id": trajectory_id, "session_id": session_id, "status": "no_plan"})
                continue
            if budget is not None:
                budget -= 1
            try:
                session = generator.generate_session(plan, trajectory.persona, extra_guidance=feedback)
            except Exception as exc:  # noqa: BLE001 — keep regenerating the rest
                results.append({
                    "trajectory_id": trajectory_id, "session_id": session_id,
                    "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                })
                total_failed += 1
                continue
            if session is not None:
                existing[session.session_id] = session.model_dump(mode="json")
                recovered += 1
                total_recovered += 1
                results.append({"trajectory_id": trajectory_id, "session_id": session_id, "status": "regenerated"})

        if recovered:
            merged = [existing[key] for key in sorted(existing, key=lambda sid: _session_sort_key(existing[sid]))]
            write_jsonl(sessions_path, merged)
            print(f"{trajectory_id}: regenerated {recovered} -> {sessions_path}")

    result_path = Path(args.regeneration_file).with_name("regeneration_result.jsonl")
    write_jsonl(result_path, results)
    print(
        f"judge-regen: recovered {total_recovered}, failed {total_failed} "
        f"(validator-rejected regenerations count as failed) -> {result_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
