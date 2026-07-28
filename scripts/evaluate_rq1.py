#!/usr/bin/env python
"""Evaluate a model on RQ1 (stage1_event_trajectory) items.

Natural conditions:
    full_prefix      sessions D001..D(15k) at checkpoint k
    last_15          only the newest 15 sessions, no prior ledger
    oracle_evidence  only gold core-evidence sessions (labels never exposed)

Distractor conditions (items = distractor cases from
build_rq1_distractor_cases.py; requires --fillers-dir):
    full             original prefix with the hard negative visible
    mask_distractor  target hard-negative slot replaced by a neutral donor
    sham             hard negative kept; a comparable routine slot replaced
                     by the same donor

The model sees only public session ids (D###) and turns. PrefixGold-derived
gold stays in the item payload and is used exclusively for scoring.

Without --execute the run is offline: a mock prediction ("events": [])
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
except ModuleNotFoundError:  # imported as scripts.evaluate_rq1 in tests
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.benchmark.rq1_builder import (
    apply_replacement_turns,
    load_session_records,
    render_sessions_block,
    render_taxonomy_block,
    visible_ids_for_condition,
)
from fin_life_benchmark.benchmark.rq1_metrics import (
    aggregate_item_results,
    item_metrics,
    progressive_metrics,
)
from fin_life_benchmark.benchmark.rq1_models import (
    DISTRACTOR_CONDITIONS,
    EVALUATION_CONDITIONS,
    RQ1GoldEventInstance,
    RQ1Item,
    session_number,
)
from fin_life_benchmark.benchmark.rq1_parser import parse_prediction
from fin_life_benchmark.io import ensure_dialogue_sessions
from fin_life_benchmark.io.jsonl import read_jsonl
from fin_life_benchmark.io.paths import RepoPaths
from fin_life_benchmark.llm.client import LLMClient

DEFAULT_PROMPT = "prompts/benchmark/rq1_event_trajectory_ko.md"
DEFAULT_SYSTEM_PROMPT = "prompts/system/benchmark_evaluator_ko.txt"

MOCK_RESPONSE = json.dumps({"events": []}, ensure_ascii=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_taxonomy(path: Path) -> tuple[list[dict[str, str]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["taxonomy"], payload["taxonomy_hash"]


def _load_filler_bank(fillers_dir: Path, trajectory_id: str) -> dict[str, dict[str, Any]]:
    path = fillers_dir / f"fillers_{trajectory_id}.jsonl"
    if not path.exists():
        raise SystemExit(f"missing filler bank file: {path}")
    return {row["session_id"]: row for row in read_jsonl(path)}


def _gold_from_payload(gold: dict[str, Any]) -> tuple[list[RQ1GoldEventInstance], list[RQ1GoldEventInstance]]:
    ledger = [RQ1GoldEventInstance.model_validate(e) for e in gold.get("full_observed_ledger", [])]
    occurred = [RQ1GoldEventInstance.model_validate(e) for e in gold.get("occurred_trajectory", [])]
    return ledger, occurred


def _distractor_flags(
    record: dict[str, Any],
    prediction,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Case-level indicators consumed by score_rq1_distractor.py."""

    near_miss = record.get("near_miss_event_id", "")
    target = record.get("target_session_id", "")
    unmatched = metrics.get("unmatched_pred_records", [])
    near_miss_hallucinated = any(p["event_id"] == near_miss for p in unmatched)
    cited = any(
        target in event.core_evidence_sessions or target in event.supporting_sessions
        for event in prediction.events
    )
    false_occurred = sum(1 for p in unmatched if p["status"] == "occurred")
    return {
        "near_miss_event_id": near_miss,
        "near_miss_hallucinated": near_miss_hallucinated,
        "hard_negative_cited": cited,
        "false_occurred_count": false_occurred,
        "hard_negative_type": record.get("hard_negative_type", ""),
    }


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True, help="items or cases jsonl")
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--fillers-dir", default=None)
    parser.add_argument(
        "--condition",
        required=True,
        choices=list(EVALUATION_CONDITIONS) + list(DISTRACTOR_CONDITIONS),
    )
    parser.add_argument("--taxonomy", default=None, help="taxonomy.json path (default: sibling of items)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
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
    parser.add_argument("--manifest", default=None, help="rq1 manifest.json (for --split)")
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

    is_distractor = args.condition in DISTRACTOR_CONDITIONS
    if is_distractor and not all("case_id" in r for r in records):
        raise SystemExit(
            f"condition {args.condition} requires distractor cases as --items"
        )
    if not is_distractor and any("case_id" in r for r in records):
        raise SystemExit(
            f"condition {args.condition} requires natural items as --items"
        )
    if args.condition == "mask_distractor" or args.condition == "sham":
        if not args.fillers_dir:
            raise SystemExit(f"condition {args.condition} requires --fillers-dir")

    # split filtering
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
    system_prompt = (paths.root / args.system_prompt).read_text(encoding="utf-8").strip()
    prompt_hash = _sha256_text(prompt_template)
    system_prompt_hash = _sha256_text(system_prompt)
    if "{{TAXONOMY}}" not in prompt_template or "{{SESSIONS}}" not in prompt_template:
        raise SystemExit(f"prompt template missing placeholders: {prompt_path}")

    sessions_dir = Path(args.sessions_dir)
    ensure_dialogue_sessions(sessions_dir)
    trajectory_ids = sorted({r["trajectory_id"] for r in records})
    sessions_by_traj = load_session_records(sessions_dir, trajectory_ids)
    filler_banks: dict[str, dict[str, dict[str, Any]]] = {}
    if args.fillers_dir:
        for traj_id in trajectory_ids:
            filler_banks[traj_id] = _load_filler_bank(Path(args.fillers_dir), traj_id)

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
        "taxonomy_hash": taxonomy_digest,
        "execute": bool(args.execute),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    first_recoverable: dict[str, dict[str, dict[str, Any]]] = {}
    n_parse_errors = 0
    n_call_errors = 0

    with output_path.open("w", encoding="utf-8") as sink:
        for record in records:
            trajectory_id = record["trajectory_id"]
            sessions = sessions_by_traj[trajectory_id]
            gold_payload = record["gold"]
            id_map: dict[str, str] = gold_payload.get("session_id_map") or {}

            if is_distractor:
                item_id = record["case_id"]
                checkpoint = int(record["checkpoint_session_count"])
                visible_ids = sorted(id_map, key=session_number)
                replacement_map: dict[str, str] = {}
                if args.condition == "mask_distractor":
                    slots = record.get("masked_session_ids") or []
                elif args.condition == "sham":
                    slots = record.get("sham_session_ids") or []
                else:
                    slots = []
                donor_by_slot = record.get("donor_by_slot") or {}
                for slot in slots:
                    replacement_map[slot] = donor_by_slot[slot]
            else:
                item = RQ1Item.model_validate(record)
                item_id = item.item_id
                checkpoint = item.checkpoint_session_count
                visible_ids = visible_ids_for_condition(item, args.condition)
                replacement_map = {}

            visible_records = [sessions[sid] for sid in visible_ids]
            if replacement_map:
                visible_records = apply_replacement_turns(
                    visible_records, replacement_map, filler_banks[trajectory_id]
                )
            visible_map = {sid: id_map[sid] for sid in visible_ids}
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

            public_to_canonical = {pub: sid for sid, pub in visible_map.items()}
            prediction = parse_prediction(
                raw,
                visible_public_ids=public_to_canonical,
                taxonomy_event_ids=taxonomy_ids,
            )
            if call_error:
                prediction.parse_error = prediction.parse_error or "call_error"
            if prediction.parse_error:
                n_parse_errors += 1

            ledger, occurred = _gold_from_payload(gold_payload)
            metrics = item_metrics(ledger, occurred, prediction.events)
            if not is_distractor:
                first_recoverable.setdefault(trajectory_id, {}).update(
                    {
                        iid: info
                        for iid, info in (
                            gold_payload.get("first_recoverable") or {}
                        ).items()
                    }
                )

            row: dict[str, Any] = {
                "item_id": item_id,
                "stage": record.get("stage", "stage1_event_trajectory"),
                "trajectory_id": trajectory_id,
                "checkpoint_session_count": checkpoint,
                "condition": args.condition,
                "n_visible_sessions": len(visible_ids),
                "prediction": prediction.model_dump(mode="json"),
                "call_error": call_error,
                "parse_error": prediction.parse_error,
                "validation_errors": prediction.validation_errors,
                "raw_response": raw,
                "response_metadata": response_metadata,
                "run_config": run_config,
                "metrics": {
                    k: v
                    for k, v in metrics.items()
                    if k
                    not in (
                        "instance_records",
                        "unmatched_pred_records",
                        "confidence_outcomes",
                    )
                },
            }
            if is_distractor:
                row["distractor"] = _distractor_flags(record, prediction, metrics)
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")

            results.append(
                {
                    "trajectory_id": trajectory_id,
                    "checkpoint_session_count": checkpoint,
                    "metrics": metrics,
                }
            )

    domain_by_event: dict[str, str] = {}
    try:
        from fin_life_benchmark.fsm.registry import load_life_event_templates

        domain_by_event = {
            event_id: getattr(t, "domain", "") or ""
            for event_id, t in load_life_event_templates(paths).items()
        }
    except Exception:
        pass

    report: dict[str, Any] = {
        **run_config,
        "items": len(results),
        "parse_errors": n_parse_errors,
        "call_errors": n_call_errors,
        "aggregate": aggregate_item_results(
            results, domain_by_event_id=domain_by_event or None
        ),
    }
    checkpoints = {r["checkpoint_session_count"] for r in results}
    if not is_distractor and len(checkpoints) > 1:
        report["progressive"] = progressive_metrics(
            results, first_recoverable=first_recoverable
        )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    headline = (
        report["aggregate"]["checkpoint_macro_auc"].get("ordered_occurred_event_f1")
    )
    print(
        f"rq1 evaluate [{args.condition}] {provider}/{model}: {len(results)} items, "
        f"{n_parse_errors} parse errors, ordered_occurred_event_f1 AUC="
        f"{headline if headline is None else round(headline, 4)}"
    )
    print(f"predictions -> {output_path}")
    print(f"report -> {report_path}")


if __name__ == "__main__":
    main()
