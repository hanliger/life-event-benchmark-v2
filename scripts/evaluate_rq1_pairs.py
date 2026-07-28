#!/usr/bin/env python
"""Evaluate a model on the temporary occurred-event pair pilot.

Stage ``stage1_occurred_event_evidence_pairs``. One condition is implemented:

    full_prefix   sessions D001..D(15k) at checkpoint k

The model sees only public session ids (D###), dialogue turns and the public
taxonomy. Gold -- one pair per occurred event instance, anchored on the earliest
visible establishing ``occurred_evidence`` session -- is derived from the
existing item's private PrefixGold payload and never rendered.

Without --execute the run is offline: a mock prediction ({"pairs": []})
exercises parsing, scoring and reporting without network calls.
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
from fin_life_benchmark.io import ensure_dialogue_sessions
from fin_life_benchmark.io.jsonl import read_jsonl
from fin_life_benchmark.io.paths import RepoPaths
from fin_life_benchmark.llm.client import LLMClient

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
    return {
        "input_tokens": (
            metadata.get("input_tokens")
            or metadata.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("prompt_tokens")
        ),
        "output_tokens": (
            metadata.get("output_tokens")
            or metadata.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("candidates_token_count")
            or usage.get("completion_tokens")
        ),
        "finish_reason": metadata.get("finish_reason") or metadata.get("stop_reason"),
        "request_duration_ms": metadata.get("request_duration_ms"),
    }


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True, help="progressive_items.jsonl")
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument(
        "--condition", default="full_prefix", choices=list(RQ1_PAIR_CONDITIONS)
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
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--trajectory-id", action="append", default=[])
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
    if args.max_items:
        records = records[: args.max_items]
    if not records:
        raise SystemExit("no items left after filtering")

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
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            reasoning_effort=args.reasoning_effort,
        )

    run_config = {
        "stage": RQ1_PAIR_STAGE,
        "protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "metrics_version": RQ1_PAIR_METRICS_VERSION,
        "provider": provider,
        "model": model,
        "condition": args.condition,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "reasoning_effort": args.reasoning_effort,
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

    with output_path.open("w", encoding="utf-8") as sink:
        for record in records:
            item = RQ1Item.model_validate(record)
            sessions = sessions_by_traj[item.trajectory_id]
            id_map = dict(item.gold.session_id_map)
            visible_ids = visible_ids_for_condition(item, args.condition)
            visible_map = {sid: id_map[sid] for sid in visible_ids}

            gold_pairs = gold_pairs_from_occurred_trajectory(
                item.gold.occurred_trajectory,
                session_id_map=visible_map,
                sessions=sessions,
                taxonomy_event_ids=taxonomy_ids,
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

            usage = _provider_usage(response_metadata)
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
                        "prompt_sha256": prompt_hash,
                        "taxonomy_hash": taxonomy_digest,
                        "provider": provider,
                        "model": model,
                        "requested_reasoning_effort": args.reasoning_effort,
                        "requested_max_tokens": args.max_tokens,
                        "requested_temperature": args.temperature,
                        "provider_input_tokens": usage["input_tokens"],
                        "provider_output_tokens": usage["output_tokens"],
                        "finish_reason": usage["finish_reason"],
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
                        "run_config": run_config,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

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
    print(f"predictions -> {output_path}")
    print(f"report -> {report_path}")


if __name__ == "__main__":
    main()
