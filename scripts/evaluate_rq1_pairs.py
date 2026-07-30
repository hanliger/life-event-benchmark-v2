#!/usr/bin/env python
"""Evaluate a model on the temporary occurred-event pair pilot.

Stage ``stage1_occurred_event_evidence_pairs``. Two conditions:

    no_prospective_substituted   (default) the prefix with its prospective
                     evidence removed by substitution: --sessions-dir must
                     point at a corpus where each weak-signal and upcoming
                     session was replaced in place by a neutral routine
                     filler, so all k sessions still render and only the
                     content changed. Occurred, cancellation, consequence,
                     stale-recall, hard-negative and routine sessions all
                     stay. The evaluator verifies the corpus really is
                     substituted and refuses to run otherwise.
    full_prefix      the untouched baseline: sessions D001..D(15k) at
                     checkpoint k.

The ablation runs at any checkpoint the items file carries, so it can be read
as a ladder; --checkpoint must be named explicitly.

The model sees only public session ids (D###), dialogue turns and the public
taxonomy. Gold -- one pair per occurred event instance, anchored on the earliest
visible establishing ``occurred_evidence`` session -- is derived from the
existing item's private PrefixGold payload and never rendered. Gold always comes
from the *full* prefix, so the ablation changes what the model sees and
nothing about what is correct.

Without --execute the run is offline: a mock prediction ({"pairs": []})
exercises parsing, scoring and reporting without network calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.evaluate_rq1_pairs in tests
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.benchmark.rq1_builder import (
    load_session_records,
    render_sessions_block,
    render_taxonomy_block,
    visible_ids_for_condition,
)
from fin_life_benchmark.benchmark.rq1_models import RQ1Item
from fin_life_benchmark.benchmark.rq1_pair_metrics import (
    aggregate_pair_results,
    pair_item_metrics,
)
from fin_life_benchmark.benchmark.rq1_pair_models import (
    RQ1_PAIR_CONDITIONS,
    RQ1_PAIR_METRICS_VERSION,
    RQ1_PAIR_PROMPT_FILE,
    RQ1_PAIR_PROTOCOL_VERSION,
    RQ1_PAIR_STAGE,
    gold_pairs_from_occurred_trajectory,
)
from fin_life_benchmark.benchmark.rq1_pair_parser import parse_pair_prediction
from fin_life_benchmark.benchmark.rq1_pair_no_prospective import (
    NO_PROSPECTIVE_DEFAULT_CHECKPOINT,
    NO_PROSPECTIVE_SUBSTITUTED_CONDITION,
    classify_pair_errors,
    compare_with_baseline,
    find_baseline_row,
    session_type_counts,
    surviving_prospective_sessions,
)
from fin_life_benchmark.io import ensure_dialogue_sessions
from fin_life_benchmark.io.jsonl import read_jsonl
from fin_life_benchmark.io.paths import RepoPaths
from fin_life_benchmark.llm.client import THINKING_MODES, LLMClient

DEFAULT_SYSTEM_PROMPT = "prompts/system/benchmark_evaluator_ko.txt"

MOCK_RESPONSE = json.dumps({"pairs": []}, ensure_ascii=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_taxonomy(path: Path) -> tuple[list[dict[str, str]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["taxonomy"], payload["taxonomy_hash"]


def _provider_usage(metadata: dict[str, Any]) -> dict[str, Any]:
    """Normalize the fields providers report under different names."""

    usage = metadata.get("usage") or {}

    def first(*keys: str) -> Any:
        for key in keys:
            for source in (metadata, usage):
                value = source.get(key)
                if value is not None:
                    return value
        return None

    # The Anthropic client reports thinking usage explicitly, including its
    # absence: an unavailable count stays None with source "unavailable" and is
    # never folded into 0.
    thinking_tokens = metadata.get("thinking_tokens")
    thinking_source = metadata.get("thinking_tokens_source")
    if thinking_tokens is None:
        fallback = first("thoughts_token_count", "reasoning_tokens")
        if fallback is not None:
            thinking_tokens, thinking_source = int(fallback), "provider_usage"

    finish_reason = first("finish_reason", "stop_reason")
    truncated = metadata.get("truncated")
    if truncated is None and finish_reason is not None:
        truncated = str(finish_reason).lower() in {"max_tokens", "length"}

    return {
        # anthropic: input/output_tokens; openai: prompt/completion_tokens;
        # gemini: prompt_token_count/candidates_token_count (+ thoughts)
        "input_tokens": first("input_tokens", "prompt_tokens", "prompt_token_count"),
        "output_tokens": first(
            "output_tokens", "completion_tokens", "candidates_token_count"
        ),
        "reasoning_tokens": first("thoughts_token_count", "reasoning_tokens"),
        "thinking_tokens": thinking_tokens,
        "thinking_tokens_source": thinking_source or "unavailable",
        "finish_reason": finish_reason,
        "truncated": truncated,
        "request_duration_ms": metadata.get("request_duration_ms"),
        # Whether --temperature actually reached the provider. Opus 5 and the
        # GPT-5.x frontier models reject the parameter, so a requested 0.0 is
        # dropped and the call samples at the provider default. With one call
        # per cell this is the difference between a reproducible number and an
        # unmeasured draw, so it travels with every row.
        "temperature_requested": metadata.get("temperature_requested"),
        "temperature_applied": metadata.get("temperature_applied"),
        "temperature_omission_reason": metadata.get("temperature_omission_reason"),
        "deterministic_sampling": (
            None
            if metadata.get("provider") in (None, "mock")
            else metadata.get("temperature_applied") == 0.0
        ),
    }


def _token_usage_rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Input / thinking / output token accounting for the run.

    Providers scope these differently and the difference is not cosmetic:
    OpenAI's ``completion_tokens`` and Anthropic's ``output_tokens`` *include*
    the reasoning/thinking tokens, while Gemini reports ``thoughts_token_count``
    *outside* ``candidates_token_count``. Summing output across providers
    therefore does not yield comparable "answer text" volume, so the note ships
    with the numbers rather than living in someone's head.

    A missing count stays ``None`` and is excluded from the total rather than
    folded in as 0, and ``items_missing_*`` says how many rows that was.
    """

    def total(key: str) -> int | None:
        values = [r[key] for r in rows if r.get(key) is not None]
        return sum(int(v) for v in values) if values else None

    def missing(key: str) -> int:
        return sum(1 for r in rows if r.get(key) is None)

    per_checkpoint: dict[str, Any] = {}
    for row in rows:
        per_checkpoint[str(row["checkpoint_session_count"])] = {
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "reasoning_tokens": row["reasoning_tokens"],
            "thinking_tokens": row["thinking_tokens"],
            "thinking_tokens_source": row["thinking_tokens_source"],
        }
    return {
        "call_count": len(rows),
        "totals": {
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "reasoning_tokens": total("reasoning_tokens"),
            "thinking_tokens": total("thinking_tokens"),
        },
        "items_missing_thinking_tokens": missing("thinking_tokens"),
        "items_missing_reasoning_tokens": missing("reasoning_tokens"),
        "per_checkpoint": per_checkpoint,
        "scope_note": (
            "openai/anthropic: reasoning+thinking are counted inside "
            "output_tokens; gemini: thoughts are reported outside "
            "candidates_token_count"
        ),
    }


def inference_contract_failures(
    metadata: dict[str, Any],
    usage: dict[str, Any],
    prediction: Any,
    *,
    provider: str,
    thinking_mode: str | None,
    reasoning_effort: str | None,
) -> tuple[list[str], list[str]]:
    """Split a call's contract violations into (fatal, metadata-gap) reason codes.

    This is the ``--require-thinking-tokens`` preflight gate. A *fatal* code means
    the call did not honor the requested inference config and is therefore not a
    measurement of the model: it is excluded from scoring and the run exits 1.

    The second list is the narrow exception. When the provider positively
    confirms it applied the requested config -- for an Anthropic adaptive request
    that is the applied mode, the applied effort and the streaming path, all
    three -- but reports no thinking-token *count*, nothing is wrong with the
    call; the provider's usage block simply does not break the number out.
    Charging that as a configuration failure discards a valid measurement over a
    reporting gap, so it is recorded as a gap and the item is still scored. A
    count that is present and non-positive is a different claim -- the provider
    is saying no thinking happened -- and stays fatal.
    """

    failures: list[str] = []
    gaps: list[str] = []

    config_confirmed = provider == "anthropic" and thinking_mode == "adaptive"
    if config_confirmed:
        if metadata.get("thinking_mode_applied") != "adaptive":
            failures.append("adaptive_thinking_not_applied")
        if reasoning_effort and metadata.get("reasoning_effort_applied") != (
            reasoning_effort
        ):
            failures.append(
                f"reasoning_effort_not_applied:"
                f"{metadata.get('reasoning_effort_applied')}"
            )
        if not metadata.get("streaming_used"):
            failures.append("streaming_not_used")
        # only an unblemished config confirmation earns the exception
        config_confirmed = not failures

    thinking_tokens = usage.get("thinking_tokens")
    if thinking_tokens is None:
        code = f"thinking_tokens_unavailable:{usage.get('thinking_tokens_source')}"
        (gaps if config_confirmed else failures).append(code)
    elif int(thinking_tokens) <= 0:
        failures.append(f"thinking_tokens_not_positive:{thinking_tokens}")

    if usage.get("truncated"):
        failures.append(f"response_truncated:{usage.get('finish_reason')}")
    if prediction.parse_error:
        failures.append(f"parse_error:{prediction.parse_error}")
    if prediction.invalid_record_count:
        failures.append(f"invalid_records:{prediction.invalid_record_count}")
    return failures, gaps


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True, help="progressive_items.jsonl")
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument(
        "--condition",
        default=NO_PROSPECTIVE_SUBSTITUTED_CONDITION,
        choices=list(RQ1_PAIR_CONDITIONS),
        help=(
            "default is the ablation; pass --condition full_prefix for the "
            "untouched baseline"
        ),
    )
    parser.add_argument(
        "--taxonomy", default=None, help="taxonomy.json (default: sibling of items)"
    )
    parser.add_argument("--prompt", default=RQ1_PAIR_PROMPT_FILE)
    parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--no-temperature",
        action="store_true",
        help=(
            "send no temperature at all, so the provider default applies. "
            "Distinct from --temperature 0.0, which is a request the frontier "
            "models refuse; this is not asking in the first place."
        ),
    )
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument(
        "--thinking-mode",
        default=None,
        choices=sorted(mode for mode in THINKING_MODES if mode),
        help="Anthropic thinking mode; omit for the non-thinking request shape",
    )
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help=(
            "total attempts = this + 1. 0 makes a failure final, which is what a "
            "single-replicate protocol wants: a retry would swap one unmeasured "
            "draw for another and report the second"
        ),
    )
    parser.add_argument(
        "--thinking-level", default=None, help="Gemini thinking level"
    )
    parser.add_argument(
        "--include-thoughts",
        dest="include_thoughts",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-include-thoughts", dest="include_thoughts", action="store_false"
    )
    parser.add_argument("--verbosity", default=None, help="OpenAI text verbosity")
    parser.add_argument("--store", dest="store", action="store_true", default=None)
    parser.add_argument("--no-store", dest="store", action="store_false")
    parser.add_argument(
        "--require-thinking-tokens",
        action="store_true",
        help=(
            "preflight gate: fail the run unless the provider reports a positive "
            "thinking-token count and honored the requested thinking config; the "
            "failed item is excluded from scored results and the exit code is 1"
        ),
    )
    parser.add_argument(
        "--baseline-predictions",
        default=None,
        help=(
            "existing full_prefix predictions JSONL to compare against; reuses "
            "the stored prediction and never calls the baseline model again"
        ),
    )
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--trajectory-id", action="append", default=[])
    parser.add_argument(
        "--checkpoint",
        action="append",
        type=int,
        default=[],
        help="evaluate only these checkpoints (repeatable); default all",
    )
    parser.add_argument(
        "--split",
        choices=("dev", "test", "all"),
        default="all",
        help="filter trajectories via the rq1 manifest split",
    )
    parser.add_argument("--manifest", default=None, help="rq1 manifest.json (--split)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    substituted = args.condition == NO_PROSPECTIVE_SUBSTITUTED_CONDITION
    if substituted and not args.checkpoint:
        # Any checkpoint (or ladder of them) is allowed, but each has to be named
        # explicitly: an unqualified run over every item in the file is never
        # what this diagnostic is for, and at ~1 long-context call per item it is
        # expensive to trigger by accident.
        raise SystemExit(
            f"--condition {args.condition} requires at least one --checkpoint "
            f"(e.g. --checkpoint {NO_PROSPECTIVE_DEFAULT_CHECKPOINT})"
        )

    provider = args.provider
    model = args.model
    if args.execute:
        if not provider or not model:
            provider = os.getenv("DEFAULT_LLM_PROVIDER")
            model = os.getenv("DEFAULT_GENERATION_MODEL")
        if not provider or not model or provider == "mock":
            raise SystemExit(
                "--execute requires --provider/--model or a non-mock .env default"
            )
    else:
        provider = provider or "mock"
        model = model or "mock"

    items_path = Path(args.items)
    records = list(read_jsonl(items_path))
    if not records:
        raise SystemExit(f"no items in {items_path}")
    if any("case_id" in record for record in records):
        raise SystemExit(f"{items_path} holds distractor cases, not natural items")

    wanted_traj = set(args.trajectory_id)
    if args.split != "all":
        manifest_path = (
            Path(args.manifest)
            if args.manifest
            else items_path.parent.parent / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        key = "dev_trajectories" if args.split == "dev" else "test_trajectories"
        split_ids = set(manifest.get(key) or [])
        wanted_traj = wanted_traj & split_ids if wanted_traj else split_ids
    if wanted_traj:
        records = [r for r in records if r.get("trajectory_id") in wanted_traj]
    if args.checkpoint:
        wanted_cp = set(args.checkpoint)
        records = [
            r for r in records if int(r["checkpoint_session_count"]) in wanted_cp
        ]
        missing = wanted_cp - {int(r["checkpoint_session_count"]) for r in records}
        if missing:
            raise SystemExit(f"no items for checkpoints: {sorted(missing)}")
    if args.max_items:
        records = records[: args.max_items]
    if not records:
        raise SystemExit("no items left after filtering")
    if substituted:
        # One item per (trajectory, checkpoint). More than one means the items
        # file holds duplicates, which would put two rows under the same rung of
        # the ladder and make the trend ambiguous.
        duplicated = sorted(
            key
            for key, count in Counter(
                (r["trajectory_id"], int(r["checkpoint_session_count"]))
                for r in records
            ).items()
            if count > 1
        )
        if duplicated:
            raise SystemExit(
                f"--condition {args.condition} needs one item per "
                f"trajectory/checkpoint; duplicates at {duplicated}"
            )

    taxonomy_path = (
        Path(args.taxonomy)
        if args.taxonomy
        else items_path.parent.parent / "taxonomy.json"
    )
    taxonomy, taxonomy_digest = _load_taxonomy(taxonomy_path)
    taxonomy_ids = {row["event_id"] for row in taxonomy}
    taxonomy_block = render_taxonomy_block(taxonomy)

    paths = RepoPaths.default()
    prompt_path = paths.root / args.prompt
    prompt_template = prompt_path.read_text(encoding="utf-8")
    system_prompt = (
        (paths.root / args.system_prompt).read_text(encoding="utf-8").strip()
    )
    prompt_hash = _sha256_text(prompt_template)
    system_prompt_hash = _sha256_text(system_prompt)
    if "{{TAXONOMY}}" not in prompt_template or "{{SESSIONS}}" not in prompt_template:
        raise SystemExit(f"prompt template missing placeholders: {prompt_path}")

    sessions_dir = Path(args.sessions_dir)
    ensure_dialogue_sessions(sessions_dir)
    trajectory_ids = sorted({r["trajectory_id"] for r in records})
    sessions_by_traj = load_session_records(sessions_dir, trajectory_ids)

    client: LLMClient | None = None
    if args.execute:
        client = LLMClient(
            provider=provider,
            model=model,
            temperature=None if args.no_temperature else args.temperature,
            max_tokens=args.max_tokens,
            reasoning_effort=args.reasoning_effort,
            thinking_mode=args.thinking_mode,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            thinking_level=args.thinking_level,
            include_thoughts=args.include_thoughts,
            verbosity=args.verbosity,
            store=args.store,
        )

    baseline_rows: list[dict[str, Any]] | None = None
    if args.baseline_predictions:
        baseline_rows = list(read_jsonl(Path(args.baseline_predictions)))

    run_config = {
        "stage": RQ1_PAIR_STAGE,
        "protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "metrics_version": RQ1_PAIR_METRICS_VERSION,
        "provider": provider,
        "model": model,
        "condition": args.condition,
        "temperature": None if args.no_temperature else args.temperature,
        "max_tokens": args.max_tokens,
        "reasoning_effort": args.reasoning_effort,
        "thinking_mode": args.thinking_mode,
        "require_thinking_tokens": bool(args.require_thinking_tokens),
        "timeout_seconds": args.timeout_seconds,
        "max_retries": args.max_retries,
        "thinking_level": args.thinking_level,
        "include_thoughts": args.include_thoughts,
        "verbosity": args.verbosity,
        "store": args.store,
        # This pilot runs exactly one call per (model, condition, checkpoint).
        # Stated in the artifact so a reader never has to infer it from the
        # row count, and so a future repeated design is distinguishable.
        "replicates_per_cell": 1,
        "baseline_predictions_file": args.baseline_predictions,
        "prompt_file": args.prompt,
        "prompt_sha256": prompt_hash,
        "system_prompt_file": args.system_prompt,
        "system_prompt_sha256": system_prompt_hash,
        "taxonomy_file": str(taxonomy_path),
        "taxonomy_hash": taxonomy_digest,
        "items_file": str(items_path),
        "execute": bool(args.execute),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    n_parse_errors = 0
    n_call_errors = 0
    n_invalid_records = 0
    contract_failures: list[dict[str, Any]] = []
    contract_gaps: list[dict[str, Any]] = []
    sampling_rows: list[dict[str, Any]] = []
    token_rows: list[dict[str, Any]] = []
    no_prospective_rungs: list[dict[str, Any]] = []

    with output_path.open("w", encoding="utf-8") as sink:
        for record in records:
            item = RQ1Item.model_validate(record)
            sessions = sessions_by_traj[item.trajectory_id]
            id_map = dict(item.gold.session_id_map)
            # Gold is projected over the *full* prefix in every condition, so
            # the ablation can only change what the model reads -- it can never
            # relabel an event or move an occurrence anchor.
            prefix_ids = visible_ids_for_condition(item, "full_prefix")
            prefix_map = {sid: id_map[sid] for sid in prefix_ids}
            gold_pairs = gold_pairs_from_occurred_trajectory(
                item.gold.occurred_trajectory,
                session_id_map=prefix_map,
                sessions=sessions,
                taxonomy_event_ids=taxonomy_ids,
            )

            if substituted:
                # Nothing is dropped: the whole prefix renders, and the corpus
                # itself is what carries the ablation. Verified, not assumed --
                # a stray prospective session means --sessions-dir is not the
                # substituted corpus and this is really a full_prefix run.
                visible_ids = list(prefix_ids)
                survivors = surviving_prospective_sessions(visible_ids, sessions)
                if survivors:
                    raise SystemExit(
                        f"{item.item_id}: --condition "
                        f"{NO_PROSPECTIVE_SUBSTITUTED_CONDITION} needs a corpus "
                        f"with every prospective session substituted, but "
                        f"{len(survivors)} survive in {sessions_dir} "
                        f"(e.g. {survivors[:5]}); point --sessions-dir at the "
                        "output of scripts/build_no_prospective_corpus.py"
                    )
            else:
                visible_ids = visible_ids_for_condition(item, args.condition)
            visible_map = {sid: id_map[sid] for sid in visible_ids}

            if substituted:
                rendered_public = set(visible_map.values())
                orphaned = [
                    f"{event_id}@{public}"
                    for event_id, public in gold_pairs
                    if public not in rendered_public
                ]
                if orphaned:
                    raise SystemExit(
                        f"{item.item_id}: gold occurrence anchors missing from the "
                        f"no-prospective context: {orphaned}"
                    )

            visible_records = [sessions[sid] for sid in visible_ids]
            sessions_block = render_sessions_block(visible_records, visible_map)
            user_prompt = prompt_template.replace(
                "{{TAXONOMY}}", taxonomy_block
            ).replace("{{SESSIONS}}", sessions_block)

            raw = ""
            response_metadata: dict[str, Any] = {}
            call_error: str | None = None
            started = time.time()
            if client is None:
                raw = MOCK_RESPONSE
                response_metadata = {"provider": "mock", "model": "mock"}
            else:
                try:
                    raw = client.generate(system=system_prompt, user=user_prompt)
                    response_metadata = dict(client.last_response_metadata)
                except Exception as exc:  # keep the run alive per item
                    call_error = f"{type(exc).__name__}: {exc}"
                    response_metadata = dict(client.last_response_metadata or {})
                    n_call_errors += 1
            response_metadata.setdefault(
                "request_duration_ms", int((time.time() - started) * 1000)
            )

            prediction = parse_pair_prediction(
                raw,
                visible_public_ids=set(visible_map.values()),
                taxonomy_event_ids=taxonomy_ids,
            )
            if call_error:
                prediction.parse_error = prediction.parse_error or "call_error"
            if prediction.parse_error:
                n_parse_errors += 1
            n_invalid_records += prediction.invalid_record_count

            session_type_by_public_id = {
                public: sessions[sid].get("session_type", "")
                for sid, public in visible_map.items()
            }
            metrics = pair_item_metrics(
                gold_pairs,
                prediction,
                session_type_by_public_id=session_type_by_public_id,
            )
            error_decomposition = classify_pair_errors(
                gold_pairs,
                prediction,
                session_type_by_public_id=session_type_by_public_id,
            )

            usage = _provider_usage(response_metadata)
            sampling_rows.append(usage)
            token_rows.append(
                {
                    "checkpoint_session_count": item.checkpoint_session_count,
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "reasoning_tokens": usage["reasoning_tokens"],
                    "thinking_tokens": usage["thinking_tokens"],
                    "thinking_tokens_source": usage["thinking_tokens_source"],
                }
            )
            failures: list[str] = []
            gaps: list[str] = []
            if args.require_thinking_tokens:
                failures, gaps = inference_contract_failures(
                    response_metadata,
                    usage,
                    prediction,
                    provider=provider,
                    thinking_mode=args.thinking_mode,
                    reasoning_effort=args.reasoning_effort,
                )
                if failures:
                    contract_failures.append(
                        {"item_id": item.item_id, "failures": failures}
                    )
                if gaps:
                    contract_gaps.append({"item_id": item.item_id, "gaps": gaps})

            comparison: dict[str, Any] | None = None
            if baseline_rows is not None:
                comparison = compare_with_baseline(
                    gold_pairs=gold_pairs,
                    prediction=prediction,
                    metrics=metrics,
                    baseline_row=find_baseline_row(
                        baseline_rows,
                        trajectory_id=item.trajectory_id,
                        checkpoint=item.checkpoint_session_count,
                    ),
                    session_type_by_public_id=session_type_by_public_id,
                )

            if substituted:
                no_prospective_rungs.append({
                    "item_id": item.item_id,
                    "trajectory_id": item.trajectory_id,
                    "condition": args.condition,
                    "checkpoint_session_count": item.checkpoint_session_count,
                    "visible_session_count": len(visible_ids),
                    "visible_session_type_counts": session_type_counts(
                        visible_ids, sessions
                    ),
                    # zero under the substituted arm, which is the point of it
                    "removed_session_count": len(prefix_ids) - len(visible_ids),
                    "metrics": metrics,
                    "error_decomposition": error_decomposition,
                    "baseline_comparison": comparison,
                    "inference_contract_failures": failures,
                    "inference_contract_gaps": gaps,
                })

            sink.write(
                json.dumps(
                    {
                        "item_id": item.item_id,
                        "stage": RQ1_PAIR_STAGE,
                        "protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
                        "metrics_version": RQ1_PAIR_METRICS_VERSION,
                        "trajectory_id": item.trajectory_id,
                        "checkpoint_session_count": item.checkpoint_session_count,
                        "condition": args.condition,
                        "n_visible_sessions": len(visible_ids),
                        # explicit: the item is still a cp300 item even when the
                        # rendered context holds fewer than 300 sessions
                        "visible_session_count": len(visible_ids),
                        "visible_session_type_counts": session_type_counts(
                            visible_ids, sessions
                        ),
                        "prompt_sha256": prompt_hash,
                        "taxonomy_hash": taxonomy_digest,
                        "provider": provider,
                        "model": model,
                        "requested_reasoning_effort": args.reasoning_effort,
                        "requested_thinking_mode": args.thinking_mode,
                        "requested_max_tokens": args.max_tokens,
                        "requested_temperature": (
                            None if args.no_temperature else args.temperature
                        ),
                        "provider_input_tokens": usage["input_tokens"],
                        "provider_output_tokens": usage["output_tokens"],
                        "provider_reasoning_tokens": usage["reasoning_tokens"],
                        "provider_thinking_tokens": usage["thinking_tokens"],
                        "thinking_tokens_source": usage["thinking_tokens_source"],
                        "finish_reason": usage["finish_reason"],
                        "truncated": usage["truncated"],
                        "temperature_applied": usage["temperature_applied"],
                        "temperature_omission_reason": usage[
                            "temperature_omission_reason"
                        ],
                        "deterministic_sampling": usage["deterministic_sampling"],
                        "request_duration_ms": usage["request_duration_ms"],
                        "raw_response": raw,
                        "response_metadata": response_metadata,
                        "call_error": call_error,
                        "parse_error": prediction.parse_error,
                        "predicted_pairs": [
                            pair.model_dump(mode="json")
                            for pair in prediction.valid_pairs
                        ],
                        "invalid_record_count": prediction.invalid_record_count,
                        "rejected_records": prediction.rejected_records,
                        "validation_errors": prediction.validation_errors,
                        # evaluator artifact only; never shown to the model
                        "gold_pairs": [
                            {"event_id": e, "evidence_session_id": s}
                            for e, s in gold_pairs
                        ],
                        "metrics": metrics,
                        "error_decomposition": error_decomposition,
                        "baseline_comparison": comparison,
                        "inference_configuration_error": failures or None,
                        # config confirmed applied, count simply not reported
                        "inference_metadata_gap": gaps or None,
                        "run_config": run_config,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

            if failures:
                # A call that did not honor the requested inference config is not
                # a measurement of the model; it is excluded from scoring.
                continue
            results.append(
                {
                    "trajectory_id": item.trajectory_id,
                    "checkpoint_session_count": item.checkpoint_session_count,
                    "metrics": metrics,
                }
            )

    aggregate = aggregate_pair_results(results)
    report = {
        "run_config": run_config,
        "stage": RQ1_PAIR_STAGE,
        "protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "metrics_version": RQ1_PAIR_METRICS_VERSION,
        "item_count": len(results),
        "trajectory_count": len(trajectory_ids),
        "parse_error_count": n_parse_errors,
        "call_error_count": n_call_errors,
        "invalid_record_count": n_invalid_records,
        "inference_configuration_errors": contract_failures,
        "inference_metadata_gaps": contract_gaps,
        "token_usage": _token_usage_rollup(token_rows),
        # Rollup of whether --temperature was honored, so a reader sees the
        # reproducibility caveat in the report without opening the rows.
        "sampling": {
            "replicates_per_cell": 1,
            "temperature_requested": (
                None if args.no_temperature else args.temperature
            ),
            "deterministic": sorted(
                {str(r["deterministic_sampling"]) for r in sampling_rows}
            ),
            "omission_reasons": sorted(
                {
                    r["temperature_omission_reason"]
                    for r in sampling_rows
                    if r["temperature_omission_reason"]
                }
            ),
        },
        # one entry per evaluated checkpoint, in ladder order
        "no_prospective_substituted": no_prospective_rungs or None,
        "checkpoints": aggregate["checkpoints"],
        "per_checkpoint": aggregate["per_checkpoint"],
        "checkpoint_macro_auc": aggregate["checkpoint_macro_auc"],
        "final_checkpoint": aggregate["final_checkpoint"],
        "final_at_300": aggregate["final_at_300"],
        "final_at_last_checkpoint": aggregate["final_at_last_checkpoint"],
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    auc = aggregate["checkpoint_macro_auc"].get(
        "strict_occurred_event_evidence_f1"
    )
    final = (aggregate["final_at_300"] or {}).get(
        "strict_occurred_event_evidence_f1"
    )
    print(
        f"rq1 occurred-pair evaluate [{args.condition}] {provider}/{model}: "
        f"{len(results)} items, {n_parse_errors} parse errors, "
        f"{n_invalid_records} invalid records, "
        f"F1@300={final if final is None else round(final, 4)}, "
        f"checkpoint macro AUC={auc if auc is None else round(auc, 4)}"
    )
    for rung in no_prospective_rungs:
        metrics = rung["metrics"]
        f1 = metrics.get("strict_occurred_event_evidence_f1")
        print(
            f"{args.condition} cp{rung['checkpoint_session_count']}: "
            f"{rung['visible_session_count']} visible sessions "
            f"(-{rung['removed_session_count']}), "
            f"{metrics['gold_pair_count']} gold pairs, "
            f"F1={f1 if f1 is None else round(f1, 4)}"
        )
        comparison = rung.get("baseline_comparison")
        if comparison:
            print(
                f"  vs full_prefix: delta F1={comparison['delta']['f1']}, "
                f"{len(comparison['full_correct_pairs_lost'])} full-correct pairs "
                f"lost, "
                f"{len(comparison['new_no_prospective_true_positives'])} new TPs"
            )
    print(f"predictions -> {output_path}")
    print(f"report -> {report_path}")
    if contract_failures:
        # artifacts are written first so the failure stays inspectable
        raise SystemExit(
            f"inference configuration error on {len(contract_failures)} item(s): "
            f"{contract_failures}"
        )


if __name__ == "__main__":
    main()
