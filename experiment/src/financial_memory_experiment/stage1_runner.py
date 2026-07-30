"""Stage 1 nine-method paid grid runner.

Stage 1 asks which Life Event most recently occurred inside each 15-session
target window, so the frozen grid is 20 trajectories × 20 window checkpoints.
The comparison surface, immutable plan, provider lock, and resume semantics are
shared with Stage 2.2 through `run_harness`; only item selection, the prompt
leakage audit, and reporting are Stage 1 specific.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .corpus import corpus_manifest_path, corpus_root
from .evaluator import _load_s000, _load_sessions
from .methods import create_method, method_ids
from .metrics import summarize_predictions, write_tables
from .paths import ExperimentPaths
from .prompts import build_query
from .run_harness import (
    ALL_TRAJECTORIES,
    NINE_METHODS,
    OPENROUTER_METHODS,
    complete_prediction_paths,
    cost_latency_row,
    execution_tree_sha256,
    heatmap_svg,
    line_chart_svg,
    load_approved_environment,
    load_verified_plan,
    new_run_dir,
    parse_reliability_row,
    parse_selection,
    preflight_paid,
    publish_stable_raw_paths,
    read_provider_lock,
    resolve_run_dir,
    run_grid,
    validate_grid_shape,
    write_csv,
    write_run_manifest,
)
from .stage1 import (
    STAGE1,
    STAGE1_MAX_OUTPUT_TOKENS,
    STAGE1_TOP_K,
    audit_rendered_prompt,
    generation_item,
    query_checkpoint,
    stage1_contract,
    stage1_item_path,
    target_window_recall,
)
from .util import read_jsonl, sha256_json, write_json


TASK = "stage1"
APPROVAL_PHRASE = "I_APPROVE_STAGE1_PAID"
PROVIDER_LOCK_SCHEMA = "stage1_openrouter_provider_lock-v1"
LETTA_METHOD = "letta_claude_opus_4_8"


def _all_items(paths: ExperimentPaths) -> list[dict[str, Any]]:
    return list(read_jsonl(stage1_item_path(paths)))


def _selected_items(
    paths: ExperimentPaths,
    *,
    trajectories: list[str],
    checkpoint_start: int,
    checkpoint_end: int,
    checkpoint_stride: int,
) -> list[dict[str, Any]]:
    if checkpoint_stride <= 0:
        raise ValueError("checkpoint stride must be positive")
    checkpoints = set(
        range(checkpoint_start, checkpoint_end + 1, checkpoint_stride)
    )
    rows = [
        item
        for item in _all_items(paths)
        if str(item["trajectory_id"]) in trajectories
        and query_checkpoint(item) in checkpoints
    ]
    rows.sort(
        key=lambda item: (
            str(item["trajectory_id"]),
            query_checkpoint(item),
            str(item["item_id"]),
        )
    )
    expected = len(trajectories) * len(checkpoints)
    if len(rows) != expected:
        raise ValueError(
            f"selected Stage 1 grid is incomplete: {len(rows)} != {expected}"
        )
    return rows


def _render_prompt_offline(
    paths: ExperimentPaths,
    *,
    method_id: str,
    trajectory_id: str,
    checkpoint: int,
) -> dict[str, Any]:
    items = [
        item
        for item in _all_items(paths)
        if str(item["trajectory_id"]) == trajectory_id
        and query_checkpoint(item) == checkpoint
    ]
    if len(items) != 1:
        raise ValueError(
            f"expected one item for {trajectory_id}/cp{checkpoint:03d}"
        )
    item = items[0]
    generation = generation_item(item)
    root = corpus_root(paths)
    system_prompt = (
        paths.prompts / "system_ko.txt"
    ).read_text(encoding="utf-8").strip()
    s000 = _load_s000(root, trajectory_id)
    sessions = _load_sessions(root, trajectory_id)[:checkpoint]
    if method_id == LETTA_METHOD:
        # Letta answers end-to-end from archival memory, so the auditable text
        # is the pre-search query the agent receives.
        prompt = (
            f"archival search는 최대 1회, 각 결과는 최대 {STAGE1_TOP_K}개만 "
            "사용하라.\n\n" + build_query(generation, [])
        )
        return {
            "method_id": method_id,
            "item": item,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "retrieval_groups": [],
            "evidence_session_ids": [],
            "note": (
                "Letta search results are agent-selected at paid runtime; this "
                "is the exact pre-search query."
            ),
        }
    method = create_method(
        method_id,
        trajectory_id=trajectory_id,
        paths=paths,
        mock=True,
        top_k=STAGE1_TOP_K,
    )
    try:
        method.ingest_initial(s000)
        for session in sessions:
            method.ingest_session(session)
        answer = method.answer(generation)
    finally:
        method.close()
    prompt = str(answer.metadata.get("rendered_user_prompt") or "")
    if not prompt:
        raise RuntimeError(f"{method_id} did not expose its rendered prompt")
    return {
        "method_id": method_id,
        "item": item,
        "prompt": prompt,
        "system_prompt": str(
            answer.metadata.get("rendered_system_prompt") or system_prompt
        ),
        "retrieval_groups": answer.metadata.get("retrieval_groups") or [],
        "evidence_session_ids": answer.evidence_session_ids,
    }


def command_plan(args: argparse.Namespace) -> None:
    paths = ExperimentPaths.discover()
    contract = stage1_contract(paths)
    configured = method_ids(paths)
    methods = parse_selection(
        args.methods, all_values=NINE_METHODS, label="methods"
    )
    if not set(methods) <= set(configured):
        raise ValueError("selected methods are not in the frozen config")
    trajectories = parse_selection(
        args.trajectories, all_values=ALL_TRAJECTORIES, label="trajectories"
    )
    items = _selected_items(
        paths,
        trajectories=trajectories,
        checkpoint_start=args.checkpoint_start,
        checkpoint_end=args.checkpoint_end,
        checkpoint_stride=args.checkpoint_stride,
    )
    if args.budget_cap_usd <= 0:
        raise ValueError("--budget-cap-usd must be positive")
    if args.provider_retries != 0:
        raise ValueError("provider retries are frozen at 0")
    estimated = (
        args.estimated_usd
        if args.estimated_usd is not None
        else args.budget_cap_usd
    )
    if estimated <= 0 or estimated > args.budget_cap_usd:
        raise ValueError("estimated USD must be in (0, budget cap]")
    provider_lock = read_provider_lock(
        args.provider_lock_file, methods, schema_version=PROVIDER_LOCK_SCHEMA
    )
    context_precheck: dict[str, Any] = {
        "estimator": "ceil(rendered_unicode_characters/2)+max_output_tokens",
        "max_estimated_total_tokens": None,
        "passed": None,
    }
    selected_openrouter = set(methods) & set(OPENROUTER_METHODS)
    if selected_openrouter and provider_lock["status"] == "LOCKED":
        prompt_lengths = [
            len(
                _render_prompt_offline(
                    paths,
                    method_id="fc_openrouter_llama_4_maverick",
                    trajectory_id=trajectory,
                    checkpoint=args.checkpoint_end,
                )["prompt"]
            )
            for trajectory in trajectories
        ]
        estimated_total = (
            math.ceil(max(prompt_lengths) / 2) + STAGE1_MAX_OUTPUT_TOKENS
        )
        failures = [
            method
            for method in selected_openrouter
            if int(provider_lock["methods"][method]["context_window"])
            < estimated_total
        ]
        context_precheck.update(
            {
                "max_estimated_total_tokens": estimated_total,
                "passed": not failures,
                "failed_methods": sorted(failures),
            }
        )
        if failures:
            raise ValueError(
                "provider context precheck failed for "
                f"{sorted(failures)}: estimated total={estimated_total}"
            )
    run_dir = new_run_dir(paths, TASK)
    plan_body = {
        "schema_version": "stage1_nine_method_plan-v1",
        "task_id": STAGE1,
        "run_id": run_dir.name,
        "created_at_kst": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "methods": methods,
        "trajectories": trajectories,
        "checkpoints": sorted({query_checkpoint(item) for item in items}),
        "item_ids": [str(item["item_id"]) for item in items],
        "item_grid_sha256": sha256_json(
            [str(item["item_id"]) for item in items]
        ),
        "prediction_count": len(methods) * len(items),
        "budget_cap_usd": round(args.budget_cap_usd, 6),
        "estimated_usd": round(estimated, 6),
        "concurrency": {
            "model_workers": args.model_workers,
            "trajectory_workers": args.trajectory_workers,
            "checkpoint_workers": args.checkpoint_workers,
            "max_in_flight": args.max_in_flight,
            "anthropic_max_in_flight": args.anthropic_max_in_flight,
            "openrouter_max_in_flight": args.openrouter_max_in_flight,
        },
        "retrieval": {
            "strategy": contract["retrieval_strategy"],
            "top_k": contract["retrieval_top_k"],
        },
        "request_timeout_seconds": args.request_timeout_seconds,
        "provider_retries": args.provider_retries,
        "parse_retries": args.parse_retries,
        "provider_lock_status": provider_lock["status"],
        "context_precheck": context_precheck,
        "prompt_audit_required": True,
        "max_output_tokens": contract["max_output_tokens"],
        "execution_tree_sha256": execution_tree_sha256(paths),
    }
    plan = {**plan_body, "plan_sha256": sha256_json(plan_body)}
    write_json(run_dir / "immutable_plan.json", plan)
    write_json(run_dir / "provider_lock.json", provider_lock)
    write_run_manifest(
        run_dir,
        schema_version="stage1_run_manifest-v1",
        plan=plan,
        provider_lock=provider_lock,
        prepared_manifest_path=corpus_manifest_path(paths),
    )
    print(json.dumps({"run_dir": str(run_dir), **plan}, ensure_ascii=False))


def command_show_prompt(args: argparse.Namespace) -> None:
    paths = ExperimentPaths.discover()
    rendered = _render_prompt_offline(
        paths,
        method_id=args.method,
        trajectory_id=args.trajectory,
        checkpoint=args.checkpoint,
    )
    print("[final user prompt]")
    print(rendered["prompt"])
    print("\n[system prompt]")
    print(rendered["system_prompt"])


def command_audit_prompt(args: argparse.Namespace) -> None:
    paths = ExperimentPaths.discover()
    run_dir = resolve_run_dir(paths, TASK, args.run_dir)
    plan = json.loads(
        (run_dir / "immutable_plan.json").read_text(encoding="utf-8")
    )
    checks = []
    for method in plan["methods"]:
        for checkpoint in (
            min(plan["checkpoints"]),
            max(plan["checkpoints"]),
        ):
            checks.append(
                audit_rendered_prompt(
                    _render_prompt_offline(
                        paths,
                        method_id=method,
                        trajectory_id=str(plan["trajectories"][0]),
                        checkpoint=int(checkpoint),
                    )
                )
            )
    if "traj_001" in plan["trajectories"]:
        for checkpoint in (min(plan["checkpoints"]), max(plan["checkpoints"])):
            rendered = _render_prompt_offline(
                paths,
                method_id=str(plan["methods"][0]),
                trajectory_id="traj_001",
                checkpoint=int(checkpoint),
            )
            sample_path = (
                run_dir
                / "prompts"
                / "audit_examples"
                / f"traj_001_cp_{int(checkpoint):03d}.txt.gz"
            )
            sample_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(sample_path, "wt", encoding="utf-8") as handle:
                handle.write(
                    "[SYSTEM]\n"
                    + rendered["system_prompt"]
                    + "\n\n[USER]\n"
                    + rendered["prompt"]
                )
    write_json(run_dir / "prompt_audit.json", {"checks": checks})
    source = paths.root / "docs" / "stage1_prompt_leakage_audit.md"
    (run_dir / "prompt_leakage_audit.md").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    if not checks or not all(check["passed"] for check in checks):
        raise RuntimeError("prompt leakage audit failed")
    print(
        json.dumps(
            {"run_dir": str(run_dir), "checks": checks}, ensure_ascii=False
        )
    )


def _validate_complete_grid(
    run_dir: Path, plan: dict[str, Any]
) -> list[Path]:
    paths = validate_grid_shape(run_dir, plan)
    for row in (row for path in paths for row in read_jsonl(path)):
        if str(row.get("stage")) != STAGE1:
            raise RuntimeError(
                f"{row.get('item_id')}: unexpected stage {row.get('stage')}"
            )
        if not str(row.get("gold") or ""):
            raise RuntimeError(f"{row.get('item_id')}: missing Gold event_id")
    return paths


def _materialize_answer_pairs(
    paths: ExperimentPaths, run_dir: Path, prediction_paths: list[Path]
) -> None:
    items = {str(item["item_id"]): item for item in _all_items(paths)}
    for prediction_path in prediction_paths:
        for row in read_jsonl(prediction_path):
            method = str(row["method_id"])
            trajectory = str(row["trajectory_id"])
            checkpoint = int(row["query_checkpoint"])
            item = items[str(row["item_id"])]
            metadata = row.get("response_metadata") or {}
            item_metadata = row.get("item_metadata") or {}
            write_json(
                run_dir
                / "answer_pairs"
                / method
                / trajectory
                / f"cp_{checkpoint:03d}.json",
                {
                    "schema_version": "stage1_answer_pair-v1",
                    "method_id": method,
                    "trajectory_id": trajectory,
                    "checkpoint": checkpoint,
                    "window_index": item_metadata.get("window_index"),
                    "target_window": [
                        item_metadata.get("target_session_start"),
                        item_metadata.get("target_session_end"),
                    ],
                    "question": item.get("question"),
                    "candidate_event_count": len(
                        item_metadata.get("candidate_events") or []
                    ),
                    "prediction_event_id": row["prediction"],
                    "gold_event_id": row["gold"],
                    "gold_event_label": (item.get("gold") or {}).get(
                        "event_label"
                    ),
                    # Gold-shaped prediction plus its field-level diff.
                    "answer_record": row.get("answer_record"),
                    "correct": bool(row["correct"]),
                    "parse_error": bool(row.get("parse_error")),
                    "retrieval_evidence": {
                        "session_ids": row.get("evidence_session_ids") or [],
                        **target_window_recall(
                            item_metadata=item_metadata,
                            evidence_session_ids=row.get(
                                "evidence_session_ids"
                            )
                            or [],
                        ),
                    },
                    "prompt_sha256": metadata.get("prompt_sha256"),
                    "prompt_artifact_path": metadata.get(
                        "prompt_artifact_path"
                    ),
                    "raw_response_sha256": sha256_json(row["raw_answer"]),
                    "provider_usage": metadata.get("usage"),
                    "latency_seconds": metadata.get("latency_seconds"),
                    "attempts": row.get("attempts") or [],
                },
            )


def _write_auxiliary_metrics(
    run_dir: Path, prediction_paths: list[Path]
) -> list[dict[str, Any]]:
    raw_rows = [row for path in prediction_paths for row in read_jsonl(path)]
    provider_lock = json.loads(
        (run_dir / "provider_lock.json").read_text(encoding="utf-8")
    )
    checkpoint_rows: list[dict[str, Any]] = []
    parse_rows: list[dict[str, Any]] = []
    cost_rows: list[dict[str, Any]] = []
    retrieval_rows: list[dict[str, Any]] = []
    for row in raw_rows:
        item_metadata = row.get("item_metadata") or {}
        record = row.get("answer_record") or {}
        checkpoint_rows.append(
            {
                "method_id": row["method_id"],
                "trajectory_id": row["trajectory_id"],
                "checkpoint": row["query_checkpoint"],
                "window_index": item_metadata.get("window_index"),
                "correct": int(bool(row["correct"])),
                "prediction_event_id": row["prediction"],
                "gold_event_id": row["gold"],
                # Labels make the row readable without joining the item file.
                "prediction_event_label": (record.get("prediction") or {}).get(
                    "event_label"
                ),
                "gold_event_label": (record.get("gold") or {}).get(
                    "event_label"
                ),
                "prediction_in_candidate_set": record.get(
                    "prediction_in_candidate_set"
                ),
                "parse_error": int(bool(row.get("parse_error"))),
            }
        )
        parse_rows.append(parse_reliability_row(row))
        cost_rows.append(cost_latency_row(row, provider_lock))
        retrieval_rows.append(
            {
                "method_id": row["method_id"],
                "trajectory_id": row["trajectory_id"],
                "checkpoint": row["query_checkpoint"],
                **target_window_recall(
                    item_metadata=item_metadata,
                    evidence_session_ids=row.get("evidence_session_ids") or [],
                ),
            }
        )
    trajectory_rows = []
    grouped: dict[tuple[str, str], list[int]] = {}
    for row in raw_rows:
        grouped.setdefault(
            (str(row["method_id"]), str(row["trajectory_id"])), []
        ).append(int(bool(row["correct"])))
    for (method, trajectory), values in sorted(grouped.items()):
        trajectory_rows.append(
            {
                "method_id": method,
                "trajectory_id": trajectory,
                "items": len(values),
                "accuracy": sum(values) / len(values),
            }
        )
    write_csv(run_dir / "metrics" / "checkpoint_metrics.csv", checkpoint_rows)
    write_csv(run_dir / "metrics" / "trajectory_metrics.csv", trajectory_rows)
    write_csv(run_dir / "metrics" / "parse_reliability.csv", parse_rows)
    write_csv(run_dir / "metrics" / "cost_latency.csv", cost_rows)
    write_csv(run_dir / "metrics" / "retrieval_recall.csv", retrieval_rows)
    _write_figures(run_dir, checkpoint_rows, trajectory_rows)
    return checkpoint_rows


def _write_figures(
    run_dir: Path,
    checkpoint_rows: list[dict[str, Any]],
    trajectory_rows: list[dict[str, Any]],
) -> None:
    figure_dir = run_dir / "report" / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    checkpoints = sorted({int(row["checkpoint"]) for row in checkpoint_rows})
    series: dict[str, dict[int, float]] = {}
    counts: dict[tuple[str, int], list[int]] = {}
    for row in checkpoint_rows:
        counts.setdefault(
            (str(row["method_id"]), int(row["checkpoint"])), []
        ).append(int(row["correct"]))
    for (method, checkpoint), values in counts.items():
        series.setdefault(method, {})[checkpoint] = sum(values) / len(values)
    (figure_dir / "checkpoint_event_identification_accuracy.svg").write_text(
        line_chart_svg(
            title="Stage 1 event identification accuracy by checkpoint",
            x_values=checkpoints,
            series=series,
        ),
        encoding="utf-8",
    )
    methods = sorted({str(row["method_id"]) for row in trajectory_rows})
    trajectories = sorted(
        {str(row["trajectory_id"]) for row in trajectory_rows}
    )
    (figure_dir / "method_trajectory_accuracy_heatmap.svg").write_text(
        heatmap_svg(
            title="Method × trajectory Stage 1 accuracy",
            columns=methods,
            rows=trajectories,
            values={
                (str(row["method_id"]), str(row["trajectory_id"])): float(
                    row["accuracy"]
                )
                for row in trajectory_rows
            },
        ),
        encoding="utf-8",
    )


def command_report(args: argparse.Namespace) -> None:
    paths = ExperimentPaths.discover()
    run_dir = resolve_run_dir(paths, TASK, args.run_dir)
    prediction_paths = complete_prediction_paths(run_dir)
    if not prediction_paths:
        raise RuntimeError("no complete prediction artifacts to report")
    publish_stable_raw_paths(prediction_paths)
    report = summarize_predictions(paths, prediction_paths, allow_partial=True)
    write_json(run_dir / "metrics" / "metrics.json", report)
    write_tables(report, run_dir / "metrics")
    _write_auxiliary_metrics(run_dir, prediction_paths)
    _materialize_answer_pairs(paths, run_dir, prediction_paths)
    lines = [
        "# Stage 1 Event Identification — 9-Method Comparison",
        "",
        "Primary metric is trajectory-macro accuracy: each 15-session window "
        "checkpoint is scored, averaged within a trajectory, then trajectories "
        "are averaged with equal weight. Retrieval and memory arms share one "
        "question query at top_k=10; Full Context receives every session up to "
        "the checkpoint.",
        "",
        "## Result artifacts",
        "",
        "- `metrics/main_results.csv` — method × stage score with "
        "trajectory bootstrap CI",
        "- `metrics/paired_method_deltas.csv`",
        "- `metrics/checkpoint_metrics.csv`",
        "- `metrics/trajectory_metrics.csv`",
        "- `metrics/parse_reliability.csv`",
        "- `metrics/retrieval_recall.csv` — target-window coverage of the "
        "evidence each method actually used",
        "- `metrics/cost_latency.csv`",
        "- `answer_pairs/<method>/<trajectory>/cp_XXX.json`",
        "",
        "`retrieval_recall.csv` is Gold-independent: it measures whether the "
        "target window was in context at all, so Full Context scores 1.0 by "
        "construction and the number separates the retrieval arms from each "
        "other.",
        "",
        "The run remains partial unless all frozen method × trajectory jobs "
        "have a COMPLETE immutable output manifest.",
    ]
    report_path = run_dir / "report" / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), "report": str(report_path)}))


def command_execute(args: argparse.Namespace) -> None:
    paths = ExperimentPaths.discover()
    run_dir = resolve_run_dir(paths, TASK, args.run_dir)
    plan = load_verified_plan(run_dir, args, approval_phrase=APPROVAL_PHRASE)
    load_approved_environment(paths)
    preflight_paid(plan)
    os.environ["FIN_MEMORY_DISABLE_PAID_APIS"] = "0"
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "status": "RUNNING",
            "started_at_kst": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        }
    )
    write_json(manifest_path, manifest)
    try:
        run_grid(
            paths,
            run_dir,
            plan,
            items_by_id={
                str(item["item_id"]): item for item in _all_items(paths)
            },
            top_k=int(plan["retrieval"]["top_k"]),
        )
        complete_paths = _validate_complete_grid(run_dir, plan)
    except BaseException as exc:
        manifest.update(
            {
                "status": "FAILED",
                "failure": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }
        )
        write_json(manifest_path, manifest)
        raise
    manifest.update(
        {
            "status": "GENERATED",
            "completed_at_kst": datetime.now(
                ZoneInfo("Asia/Seoul")
            ).isoformat(),
            "complete_prediction_files": len(complete_paths),
        }
    )
    write_json(manifest_path, manifest)
    command_report(argparse.Namespace(run_dir=str(run_dir)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--methods", default="all")
    plan.add_argument("--trajectories", default="all")
    plan.add_argument("--checkpoint-start", type=int, default=15)
    plan.add_argument("--checkpoint-end", type=int, default=300)
    plan.add_argument("--checkpoint-stride", type=int, default=15)
    plan.add_argument("--model-workers", type=int, default=9)
    plan.add_argument("--trajectory-workers", type=int, default=20)
    plan.add_argument("--checkpoint-workers", type=int, default=20)
    plan.add_argument("--max-in-flight", type=int, default=60)
    plan.add_argument("--anthropic-max-in-flight", type=int, default=20)
    plan.add_argument("--openrouter-max-in-flight", type=int, default=40)
    plan.add_argument("--request-timeout-seconds", type=int, default=300)
    plan.add_argument("--provider-retries", type=int, default=0)
    plan.add_argument("--parse-retries", type=int, default=1)
    plan.add_argument("--budget-cap-usd", type=float, required=True)
    plan.add_argument("--estimated-usd", type=float)
    plan.add_argument("--provider-lock-file")
    plan.set_defaults(handler=command_plan)

    show = commands.add_parser("show-prompt")
    show.add_argument("--method", required=True, choices=NINE_METHODS)
    show.add_argument("--trajectory", required=True)
    show.add_argument("--checkpoint", type=int, required=True)
    show.set_defaults(handler=command_show_prompt)

    audit = commands.add_parser("audit-prompt")
    audit.add_argument("--run-dir")
    audit.set_defaults(handler=command_audit_prompt)

    for name in ("execute", "resume"):
        execute = commands.add_parser(name)
        execute.add_argument("--run-dir")
        execute.add_argument("--execute-paid", action="store_true")
        execute.add_argument("--approval", required=True)
        execute.set_defaults(handler=command_execute)

    report = commands.add_parser("report")
    report.add_argument("--run-dir")
    report.set_defaults(handler=command_report)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
