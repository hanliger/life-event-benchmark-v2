#!/usr/bin/env python
"""Default LLM judge QA gate over generated dialogue sessions.

This is the default dialogue-quality reviewer. It runs an LLM evaluator over
dialogue sessions and emits:

- ``judge_review_decision.json`` — authoritative PASS/FAIL gate decision,
  consumable by ``generate_dialogue_sessions.py --require-review-pass``
- ``judge_report.{json,md}``   — per-dimension pass rates and gate view
- ``judged_sessions.jsonl``    — one packet-shaped record per session
- ``suggested_regeneration.jsonl`` — sessions the judge flagged for regeneration

The judge shares the same rubric and gate logic (``score_records``) as the human
review packet, so ``judge_review_decision.json`` is interchangeable with
``human_review_decision.json`` at the production gate. The human-review branch
remains available as an optional cross-check with the identical rubric — build
the packet, fill it, and score it — but the default pipeline gates on this
judge. Note the trust caveat: the most sensitive dimensions (memory grounding,
semantic leakage, high-risk safety) are where an LLM judge is least reliable, so
a human spot-check via the shared rubric is recommended for high-stakes runs.

Example:

  python scripts/judge_dialogue_sessions.py \
    --plans-dir data/runs/v4/dialogues/plans \
    --sessions-dir data/runs/v4/dialogues/sessions \
    --output-dir data/runs/v4/reports/dialogue_judge \
    --provider anthropic --model claude-opus-4-8
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.judge_dialogue_sessions in tests
    from scripts import _bootstrap  # type: ignore # noqa: F401

from fin_life_benchmark.io import RepoPaths, ensure_dialogue_sessions, read_jsonl, write_jsonl
from fin_life_benchmark.llm.client import LLMClient

try:
    from score_dialogue_review_packet import CRITICAL_FIELDS, RATE_THRESHOLDS, score_records
except ModuleNotFoundError:  # imported as scripts.judge_dialogue_sessions in tests
    from scripts.score_dialogue_review_packet import (  # type: ignore
        CRITICAL_FIELDS,
        RATE_THRESHOLDS,
        score_records,
    )

REVIEWER_BOOL_FIELDS = (
    "natural_korean_dialogue",
    "event_task_alignment",
    "lifecycle_calibration",
    "memory_grounding",
    "assistant_semantic_leakage",
    "high_risk_safety",
    "event_implicit_but_recoverable",
)

SYSTEM_PROMPT = (
    RepoPaths.default().prompts / "judge" / "judge_dialogue_sessions_ko.md"
).read_text(encoding="utf-8")

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict[str, Any]:
    stripped = _JSON_FENCE.sub("", text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"pass", "passed", "true", "yes", "1"}:
            return True
        if normalized in {"fail", "failed", "false", "no", "0"}:
            return False
    return None


def build_user_prompt(plan: dict[str, Any], session: dict[str, Any]) -> str:
    structured = plan.get("structured_context") or {}
    event = structured.get("event") or {}
    evaluator = {
        "session_type": plan.get("session_type"),
        "lifecycle_status": plan.get("event_status_after_session"),
        "event_id": event.get("event_id"),
        "financial_task": plan.get("financial_task"),
        "planned_cues": plan.get("planned_cues") or [],
        "expected_memory_updates": structured.get("session_memory_updates") or [],
        "action_resolution": session.get("action_resolution"),
    }
    dialogue = "\n".join(
        f"- {turn.get('speaker')}: {turn.get('text')}"
        for turn in session.get("turns") or []
    )
    return (
        "## Evaluator metadata (정답 기준; benchmark 대화에 복사 금지)\n"
        + json.dumps(evaluator, ensure_ascii=False, indent=2)
        + "\n\n## Dialogue\n"
        + dialogue
    )


def _iter_trajectory_ids(sessions_dir: Path, trajectory_id: str | None) -> list[str]:
    if trajectory_id:
        return [trajectory_id]
    ids = [
        path.name[len("sessions_") : -len(".jsonl")]
        for path in sorted(sessions_dir.glob("sessions_*.jsonl"))
    ]
    if not ids:
        raise SystemExit(f"no sessions_*.jsonl found in {sessions_dir}")
    return ids


def _load_pairs(
    plans_dir: Path, sessions_dir: Path, trajectory_ids: list[str]
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for traj in trajectory_ids:
        plans = {p["session_id"]: p for p in read_jsonl(plans_dir / f"plans_{traj}.jsonl")}
        for session in read_jsonl(sessions_dir / f"sessions_{traj}.jsonl"):
            sid = session["session_id"]
            plan = plans.get(sid)
            if plan is None:
                continue
            pairs.append((traj, plan, session))
    return pairs


def _judge_one(
    client: LLMClient, traj: str, plan: dict[str, Any], session: dict[str, Any]
) -> dict[str, Any]:
    structured = plan.get("structured_context") or {}
    event = structured.get("event") or {}
    record: dict[str, Any] = {
        "evaluator_only": {
            "trajectory_id": traj,
            "session_id": session["session_id"],
            "session_type": plan.get("session_type"),
            "lifecycle_status": plan.get("event_status_after_session"),
            "event_id": event.get("event_id"),
            "financial_task": plan.get("financial_task"),
        },
        "generated_dialogue": session.get("turns") or [],
        "reviewer": {field: None for field in REVIEWER_BOOL_FIELDS} | {"comments": ""},
    }
    raw: str | None = None
    try:
        raw = client.generate(SYSTEM_PROMPT, build_user_prompt(plan, session))
        parsed = _extract_json(raw)
        reviewer = {field: _coerce_bool(parsed.get(field)) for field in REVIEWER_BOOL_FIELDS}
        reviewer["comments"] = str(parsed.get("comments") or "")
        record["reviewer"] = reviewer
        parse_ok = all(reviewer[field] is not None for field in REVIEWER_BOOL_FIELDS)
        record["judge_meta"] = {
            "parse_ok": parse_ok,
            "usage": dict((client.last_response_metadata or {}).get("usage") or {}),
        }
        if not parse_ok:
            record["judge_meta"]["raw"] = raw
    except Exception as exc:  # noqa: BLE001 — advisory tool: record, never abort the run
        meta: dict[str, Any] = {"parse_ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if raw is not None:
            meta["raw"] = raw
        record["judge_meta"] = meta
    return record


def _soft_pass_rates(records: list[dict[str, Any]]) -> dict[str, float]:
    """Population pass rate per soft (rate-gated) dimension, over judged verdicts."""
    rates: dict[str, float] = {}
    for field in RATE_THRESHOLDS:
        judged = [r for r in records if (r.get("reviewer") or {}).get(field) in (True, False)]
        if not judged:
            rates[field] = 1.0
            continue
        passed = sum(1 for r in judged if (r.get("reviewer") or {}).get(field) is True)
        rates[field] = passed / len(judged)
    return rates


def _flagged_for_regeneration(record: dict[str, Any], below_threshold: set[str]) -> list[str]:
    """Reasons to regenerate one session, given which soft dims miss the gate.

    Critical dims are per-session absolute gates: any fail is always flagged.
    Soft dims are population *rate* gates: an individual soft fail is only worth
    regenerating when that dimension's aggregate pass rate is below its threshold
    (``below_threshold``). This mirrors the scoring gate rather than treating
    every soft fail as a hard per-session failure.
    """
    reviewer = record.get("reviewer") or {}
    reasons: list[str] = []
    for field in CRITICAL_FIELDS:
        if reviewer.get(field) is False:
            reasons.append(f"critical:{field}")
    for field in RATE_THRESHOLDS:
        if field in below_threshold and reviewer.get(field) is False:
            reasons.append(f"soft:{field}")
    return reasons


def _aggregate_usage(records: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for record in records:
        for key, value in ((record.get("judge_meta") or {}).get("usage") or {}).items():
            if isinstance(value, int):
                totals[key] += value
    return dict(totals)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trajectory-id", help="judge one trajectory; default: all in --sessions-dir")
    parser.add_argument("--provider", help="LLM provider (default: DEFAULT_LLM_PROVIDER)")
    parser.add_argument("--model", help="judge model (default: DEFAULT_GENERATION_MODEL)")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--max-sessions", type=int, help="cap sessions judged (cost control / smoke test)")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--session-ids-file",
        help="judge ONLY the session_ids listed in this file (jsonl with a "
        "session_id field, or one id per line). Use for cheap subset re-judging "
        "after regeneration; pair with --merge-baseline to keep a full-corpus decision.",
    )
    parser.add_argument(
        "--merge-baseline",
        help="a prior judged_sessions.jsonl. Verdicts for sessions NOT re-judged "
        "this run are carried over from here, and the decision / suggested "
        "regeneration are recomputed over the merged full set. Requires --session-ids-file.",
    )
    parser.add_argument(
        "--cache-prompt",
        action="store_true",
        help="mark the fixed rubric system prompt with cache_control so it bills at "
        "cache-read rates across the run (Anthropic).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build prompts and write them, but make no API calls",
    )
    args = parser.parse_args(argv)

    plans_dir, sessions_dir = Path(args.plans_dir), Path(args.sessions_dir)
    ensure_dialogue_sessions(sessions_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.merge_baseline and not args.session_ids_file:
        raise SystemExit("--merge-baseline requires --session-ids-file")

    trajectory_ids = _iter_trajectory_ids(sessions_dir, args.trajectory_id)
    pairs = _load_pairs(plans_dir, sessions_dir, trajectory_ids)

    if args.session_ids_file:
        # session_id is unique only WITHIN a trajectory (S001..S300 repeat across
        # trajectories), so key on (trajectory_id, session_id).
        wanted: set[tuple[str, str]] = set()
        for line in Path(args.session_ids_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                wanted.add((rec["trajectory_id"], rec["session_id"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                raise SystemExit(
                    "--session-ids-file lines must be JSON with trajectory_id + session_id "
                    "(e.g. suggested_regeneration.jsonl)"
                )
        pairs = [(t, p, s) for (t, p, s) in pairs if (t, s["session_id"]) in wanted]
        print(f"subset: judging {len(pairs)} of {len(wanted)} requested (trajectory, session) ids")

    if args.max_sessions is not None:
        pairs = pairs[: args.max_sessions]
    if not pairs:
        raise SystemExit("no (plan, session) pairs to judge")

    if args.dry_run:
        prompts = [
            {
                "session_id": session["session_id"],
                "trajectory_id": traj,
                "system": SYSTEM_PROMPT,
                "user": build_user_prompt(plan, session),
            }
            for traj, plan, session in pairs
        ]
        count = write_jsonl(output_dir / "judge_prompts.jsonl", prompts)
        print(f"dry-run: wrote {count} prompts to {output_dir / 'judge_prompts.jsonl'} (no API calls)")
        return 0

    local = threading.local()

    def client_for_thread() -> LLMClient:
        client = getattr(local, "client", None)
        if client is None:
            client = LLMClient.from_env(
                provider=args.provider,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                max_tokens=args.max_tokens,
                cache_prompt=args.cache_prompt,
            )
            local.client = client
        return client

    records: list[dict[str, Any]] = []
    if args.concurrency > 1:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [
                pool.submit(lambda t=t, p=p, s=s: _judge_one(client_for_thread(), t, p, s))
                for (t, p, s) in pairs
            ]
            for future in as_completed(futures):
                records.append(future.result())
    else:
        client = client_for_thread()
        for traj, plan, session in pairs:
            records.append(_judge_one(client, traj, plan, session))

    # (trajectory_id, session_id) — session_id alone is not unique across trajectories.
    def _key(r: dict[str, Any]) -> tuple[str, str]:
        e = r["evaluator_only"]
        return (e["trajectory_id"], e["session_id"])

    records.sort(key=_key)
    # Token spend for THIS run only (before merging in carried-over baseline verdicts).
    round_usage = _aggregate_usage(records)

    if args.merge_baseline:
        merged = {_key(r): r for r in read_jsonl(Path(args.merge_baseline))}
        rejudged = len(records)
        for r in records:  # override baseline with freshly re-judged verdicts
            merged[_key(r)] = r
        records = sorted(merged.values(), key=_key)
        print(f"merged: {len(records)} total ({rejudged} re-judged this run + baseline carryover)")

    write_jsonl(output_dir / "judged_sessions.jsonl", records)

    soft_rates = _soft_pass_rates(records)
    below_threshold = {field for field, threshold in RATE_THRESHOLDS.items() if soft_rates.get(field, 1.0) < threshold}
    regeneration = [
        {
            "session_id": record["evaluator_only"]["session_id"],
            "trajectory_id": record["evaluator_only"]["trajectory_id"],
            "session_type": record["evaluator_only"]["session_type"],
            "reasons": reasons,
            "comments": (record.get("reviewer") or {}).get("comments") or "",
        }
        for record in records
        if (reasons := _flagged_for_regeneration(record, below_threshold))
    ]
    write_jsonl(output_dir / "suggested_regeneration.jsonl", regeneration)

    scored = [r for r in records if (r.get("judge_meta") or {}).get("parse_ok")]
    scoring = score_records(scored) if scored else {"decision": "N/A"}
    usage = round_usage  # report THIS run's API spend, not the merged corpus
    parse_failures = sum(1 for r in records if not (r.get("judge_meta") or {}).get("parse_ok"))

    # Authoritative review decision. Uses the same rubric/gate (score_records) as
    # the human-review packet, so judge_review_decision.json and
    # human_review_decision.json are interchangeable at the production gate. Any
    # unparseable judgement is a hard failure — we must not pass a session the
    # judge could not actually score.
    decision = dict(scoring)
    decision["producer"] = "llm_judge"
    decision["provider"] = args.provider
    decision["model"] = args.model
    decision["judged_session_count"] = len(records)
    decision["parse_failure_count"] = parse_failures
    if parse_failures:
        decision["decision"] = "FAIL"
        decision.setdefault("hard_gate_failures", []).append(
            {"gate": "judge_parse_failures", "actual": parse_failures, "threshold": 0}
        )
    (output_dir / "judge_review_decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = {
        "authoritative": True,
        "note": (
            "Default dialogue-quality QA gate. Shares the rubric (score_records) with "
            "the optional human-review packet, so judge_review_decision.json is "
            "interchangeable with human_review_decision.json at the production gate. "
            "For a human cross-check, fill the review packet and score it with the "
            "same rubric."
        ),
        "provider": args.provider,
        "model": args.model,
        "judged_session_count": len(records),
        "parsed_session_count": len(scored),
        "parse_failure_count": parse_failures,
        "suggested_regeneration_count": len(regeneration),
        "review_decision": decision["decision"],
        "scoring": scoring,
        "token_usage_total": usage,
    }
    (output_dir / "judge_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    rates = (scoring.get("pass_rates") or {}) if isinstance(scoring, dict) else {}
    rate_lines = [f"- {field}: {rate:.3f}" for field, rate in rates.items()] or ["- (none)"]
    usage_lines = [f"- {key}: {value}" for key, value in sorted(usage.items())] or ["- (none)"]
    lines = [
        "# Dialogue LLM-judge QA report (default gate)",
        "",
        "> Default dialogue-quality gate. Same rubric as the optional human-review "
        "packet; a human cross-check is recommended for the sensitive dimensions "
        "(memory-grounding / leakage / high-risk-safety).",
        "",
        f"- model: `{args.provider or 'env'}` / `{args.model or 'env'}`",
        f"- judged sessions: {len(records)} (parse ok: {len(scored)}, parse failures: {parse_failures})",
        f"- review decision: **{decision['decision']}**",
        f"- suggested for regeneration: {len(regeneration)}",
        "",
        "## Pass rates",
        "",
        *rate_lines,
        "",
        "## Token usage (total)",
        "",
        *usage_lines,
    ]
    (output_dir / "judge_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"judge QA gate: {len(records)} sessions, "
        f"{len(regeneration)} flagged for regeneration, "
        f"decision={decision['decision']} -> {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
