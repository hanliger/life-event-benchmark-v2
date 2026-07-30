#!/usr/bin/env python
"""Evaluate one counterfactual-canary condition (occurred-pair pilot).

Conditions are independently resumable. The five cases share one identical
``full`` context, so ``--condition full`` issues a single call and every paired
case references that one prediction:

    1 full call + 5 mask_terminal + 5 mask_all = 11 calls per model

Join the three prediction files with scripts/score_rq1_pair_counterfactual.py to
get the paired retraction diagnostics.

Provider failures are never hidden behind a mock fallback: without --execute the
run is explicitly offline, and with --execute a failing call is recorded as an
error row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.benchmark.lifecycle_masking import load_filler_bank
from fin_life_benchmark.benchmark.rq1_builder import (
    load_session_records,
    render_sessions_block,
    render_taxonomy_block,
)
from fin_life_benchmark.benchmark.rq1_pair_counterfactual import (
    CANARY_PROTOCOL_VERSION,
    CONDITIONS,
    case_gold_pairs,
    materialize_condition_sessions,
)
from fin_life_benchmark.benchmark.rq1_pair_metrics import pair_item_metrics
from fin_life_benchmark.benchmark.rq1_pair_models import (
    RQ1_PAIR_METRICS_VERSION,
    RQ1_PAIR_PROMPT_FILE,
    RQ1_PAIR_PROTOCOL_VERSION,
    RQ1_PAIR_STAGE,
)
from fin_life_benchmark.benchmark.rq1_pair_parser import parse_pair_prediction
from fin_life_benchmark.io.jsonl import read_jsonl
from fin_life_benchmark.io.paths import RepoPaths
from fin_life_benchmark.llm.client import LLMClient

DEFAULT_SYSTEM_PROMPT = "prompts/system/benchmark_evaluator_ko.txt"
MOCK_RESPONSE = json.dumps({"pairs": []}, ensure_ascii=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _usage_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    usage = metadata.get("usage") or {}

    def first(*keys: str) -> Any:
        for key in keys:
            for source in (metadata, usage):
                value = source.get(key)
                if value is not None:
                    return value
        return None

    return {
        "input_tokens": first("input_tokens", "prompt_tokens", "prompt_token_count"),
        "output_tokens": first(
            "output_tokens", "completion_tokens", "candidates_token_count"
        ),
        "thinking_tokens": metadata.get("thinking_tokens"),
        "thinking_tokens_source": metadata.get("thinking_tokens_source"),
        "finish_reason": first("finish_reason", "stop_reason"),
        "request_duration_ms": metadata.get("request_duration_ms"),
    }


def _applied_provider_params(client: LLMClient | None, args) -> dict[str, Any]:
    """Exactly what was asked of the provider, for the prediction row."""

    return {
        "provider": args.provider or "mock",
        "model": args.model or "mock",
        "max_tokens": args.max_tokens,
        "temperature_requested": args.temperature,
        "reasoning_effort_requested": args.reasoning_effort,
        "thinking_mode_requested": args.thinking_mode,
        "require_thinking_tokens": bool(args.require_thinking_tokens),
        "streaming_expected": bool(
            args.thinking_mode == "adaptive" and (args.provider == "anthropic")
        ),
    }


def _thinking_ok(metadata: dict[str, Any]) -> tuple[bool, str | None]:
    tokens = metadata.get("thinking_tokens")
    if tokens is None:
        return False, "thinking_tokens_unavailable"
    if int(tokens) <= 0:
        return False, "thinking_tokens_zero"
    if not metadata.get("streaming_used"):
        return False, "streaming_not_used"
    if metadata.get("thinking_mode_applied") != "adaptive":
        return False, "adaptive_thinking_not_applied"
    return True, None


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, help="cases.jsonl")
    parser.add_argument("--condition", required=True, choices=list(CONDITIONS))
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--fillers-dir", required=True)
    parser.add_argument("--taxonomy", required=True)
    parser.add_argument("--prompt", default=RQ1_PAIR_PROMPT_FILE)
    parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=65536)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument(
        "--thinking-mode",
        default=None,
        choices=("adaptive",),
        help="Anthropic adaptive thinking (Opus 4.8+)",
    )
    parser.add_argument(
        "--require-thinking-tokens",
        action="store_true",
        help="fail the run unless the provider reports thinking_tokens > 0",
    )
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument(
        "--reuse-full-prediction",
        default=None,
        help="full.jsonl whose single prediction is reused (bookkeeping only)",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    provider = args.provider
    model = args.model
    if args.execute:
        if not provider or not model:
            raise SystemExit("--execute requires --provider and --model")
        if provider == "mock":
            raise SystemExit("--execute cannot use the mock provider")
    else:
        provider = provider or "mock"
        model = model or "mock"
    args.provider, args.model = provider, model

    cases = list(read_jsonl(Path(args.cases)))
    if not cases:
        raise SystemExit(f"no cases in {args.cases}")
    if args.case_id:
        wanted = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in wanted]
    if args.max_cases:
        cases = cases[: args.max_cases]
    if not cases:
        raise SystemExit("no cases left after filtering")

    trajectory_id = cases[0]["trajectory_id"]
    checkpoint = int(cases[0]["checkpoint_session_count"])

    taxonomy_payload = json.loads(Path(args.taxonomy).read_text(encoding="utf-8"))
    taxonomy_event_ids = {row["event_id"] for row in taxonomy_payload["taxonomy"]}
    taxonomy_digest = taxonomy_payload["taxonomy_hash"]
    taxonomy_block = render_taxonomy_block(taxonomy_payload["taxonomy"])

    paths = RepoPaths.default()
    prompt_template = (paths.root / args.prompt).read_text(encoding="utf-8")
    system_prompt = (
        (paths.root / args.system_prompt).read_text(encoding="utf-8").strip()
    )
    prompt_hash = _sha256_text(prompt_template)

    sessions_by_traj = load_session_records(Path(args.sessions_dir), [trajectory_id])
    all_sessions = sessions_by_traj[trajectory_id]
    filler_bank = {
        row["session_id"]: row
        for row in load_filler_bank(
            Path(args.fillers_dir) / f"fillers_{trajectory_id}.jsonl"
        )
    }

    client: LLMClient | None = None
    if args.execute:
        client = LLMClient(
            provider=provider,
            model=model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            reasoning_effort=args.reasoning_effort,
            thinking_mode=args.thinking_mode,
        )

    def build_prompt(case: dict[str, Any]) -> tuple[str, list[str], dict[str, str]]:
        visible_ids = sorted(case["session_id_map"])
        sessions = [all_sessions[sid] for sid in visible_ids]
        variant = materialize_condition_sessions(
            case, sessions, filler_bank, args.condition
        )
        visible_map = {
            sid: case["session_id_map"][sid] for sid in visible_ids
        }
        block = render_sessions_block(variant, visible_map)
        return (
            prompt_template.replace("{{TAXONOMY}}", taxonomy_block).replace(
                "{{SESSIONS}}", block
            ),
            visible_ids,
            visible_map,
        )

    # The five cases must share one byte-identical full context; that is what
    # licenses reusing a single full prediction across all of them.
    full_prompt_hashes: dict[str, str] = {}
    if args.condition == "full":
        full_prompt_hashes = {
            case["case_id"]: _sha256_text(build_prompt(case)[0]) for case in cases
        }
        if len(set(full_prompt_hashes.values())) > 1:
            raise SystemExit(
                "full contexts differ across cases; they must be byte-identical: "
                + json.dumps(full_prompt_hashes, ensure_ascii=False)
            )

    run_config = {
        "stage": RQ1_PAIR_STAGE,
        "pair_protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "pair_metrics_version": RQ1_PAIR_METRICS_VERSION,
        "canary_protocol_version": CANARY_PROTOCOL_VERSION,
        "condition": args.condition,
        "cases_file": str(args.cases),
        "prompt_file": args.prompt,
        "prompt_sha256": prompt_hash,
        "system_prompt_sha256": _sha256_text(system_prompt),
        "taxonomy_hash": taxonomy_digest,
        "execute": bool(args.execute),
        "provider_params": _applied_provider_params(client, args),
        "reuse_full_prediction": args.reuse_full_prediction,
    }

    # For "full" the five cases share one call; for masked conditions each case
    # gets its own.
    call_units = [cases[0]] if args.condition == "full" else cases

    rows: list[dict[str, Any]] = []
    n_config_errors = 0
    n_parse_errors = 0
    for case in call_units:
        user_prompt, visible_ids, visible_map = build_prompt(case)
        context_hash = _sha256_text(user_prompt)

        raw = ""
        metadata: dict[str, Any] = {}
        call_error: str | None = None
        started = time.time()
        if client is None:
            raw = MOCK_RESPONSE
            metadata = {"provider": "mock", "model": "mock"}
        else:
            try:
                raw = client.generate(system=system_prompt, user=user_prompt)
                metadata = dict(client.last_response_metadata)
            except Exception as exc:
                call_error = f"{type(exc).__name__}: {exc}"
                metadata = dict(client.last_response_metadata or {})
        metadata.setdefault(
            "request_duration_ms", int((time.time() - started) * 1000)
        )

        config_error: str | None = None
        if args.require_thinking_tokens and client is not None and not call_error:
            ok, reason = _thinking_ok(metadata)
            if not ok:
                config_error = f"inference_configuration_error:{reason}"
                n_config_errors += 1

        prediction = parse_pair_prediction(
            raw,
            visible_public_ids=set(visible_map.values()),
            taxonomy_event_ids=taxonomy_event_ids,
        )
        if call_error:
            prediction.parse_error = prediction.parse_error or "call_error"
        if prediction.parse_error:
            n_parse_errors += 1

        gold = case_gold_pairs(case, args.condition)
        variant = materialize_condition_sessions(
            case,
            [all_sessions[sid] for sid in visible_ids],
            filler_bank,
            args.condition,
        )
        session_type_by_public_id = {
            visible_map[session["session_id"]]: session.get("session_type", "")
            for session in variant
        }
        metrics = pair_item_metrics(
            gold, prediction, session_type_by_public_id=session_type_by_public_id
        )

        usage = _usage_fields(metadata)
        rows.append(
            {
                "condition": args.condition,
                "shared_full_prediction": args.condition == "full",
                "prediction_id": f"{provider}__{model}__{args.condition}__"
                f"{context_hash[:12]}",
                "case_id": case["case_id"] if args.condition != "full" else None,
                "applies_to_case_ids": (
                    [c["case_id"] for c in cases] if args.condition == "full" else [case["case_id"]]
                ),
                "trajectory_id": case["trajectory_id"],
                "checkpoint_session_count": case["checkpoint_session_count"],
                "target_event_instance_id": (
                    case["target_event_instance_id"] if args.condition != "full" else None
                ),
                "n_visible_sessions": len(visible_ids),
                "context_prompt_sha256": context_hash,
                "prompt_sha256": prompt_hash,
                "taxonomy_hash": taxonomy_digest,
                "provider": provider,
                "model": model,
                "applied_provider_params": run_config["provider_params"],
                "provider_input_tokens": usage["input_tokens"],
                "provider_output_tokens": usage["output_tokens"],
                "provider_thinking_tokens": usage["thinking_tokens"],
                "thinking_tokens_source": usage["thinking_tokens_source"],
                "thinking_mode_applied": metadata.get("thinking_mode_applied"),
                "reasoning_effort_applied": metadata.get("reasoning_effort_applied"),
                "temperature_applied": metadata.get("temperature_applied"),
                "temperature_omission_reason": metadata.get(
                    "temperature_omission_reason"
                ),
                "streaming_used": metadata.get("streaming_used"),
                "truncated": metadata.get("truncated"),
                "finish_reason": usage["finish_reason"],
                "request_duration_ms": usage["request_duration_ms"],
                "raw_response": raw,
                "response_metadata": metadata,
                "call_error": call_error,
                "parse_error": prediction.parse_error,
                "inference_configuration_error": config_error,
                "scored": config_error is None and call_error is None,
                "predicted_pairs": [
                    pair.model_dump(mode="json") for pair in prediction.valid_pairs
                ],
                "invalid_record_count": prediction.invalid_record_count,
                "validation_errors": prediction.validation_errors,
                "rejected_records": prediction.rejected_records,
                # evaluator artifact only
                "gold_pairs": [
                    {"event_id": e, "evidence_session_id": s} for e, s in gold
                ],
                "metrics": metrics,
                "run_config": run_config,
            }
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as sink:
        for row in rows:
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")

    scored = [row for row in rows if row["scored"]]
    report = {
        "run_config": run_config,
        "condition": args.condition,
        "call_count": len(rows),
        "case_count": len(cases),
        "scored_call_count": len(scored),
        "parse_error_count": n_parse_errors,
        "inference_configuration_error_count": n_config_errors,
        "context_prompt_hashes": sorted({row["context_prompt_sha256"] for row in rows}),
        "per_call": [
            {
                "case_id": row["case_id"],
                "prediction_id": row["prediction_id"],
                "scored": row["scored"],
                "strict_f1": row["metrics"]["strict_occurred_event_evidence_f1"],
                "gold_pair_count": row["metrics"]["gold_pair_count"],
                "predicted_pair_count": row["metrics"]["predicted_pair_count"],
                "thinking_tokens": row["provider_thinking_tokens"],
                "inference_configuration_error": row["inference_configuration_error"],
            }
            for row in rows
        ],
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"rq1 pair counterfactual [{args.condition}] {provider}/{model}: "
        f"{len(rows)} calls ({len(scored)} scored), {n_parse_errors} parse errors, "
        f"{n_config_errors} inference configuration errors"
    )
    print(f"predictions -> {output_path}")
    print(f"report -> {report_path}")
    if n_config_errors:
        raise SystemExit(
            f"{n_config_errors} call(s) failed the --require-thinking-tokens contract"
        )


if __name__ == "__main__":
    main()
