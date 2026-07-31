from __future__ import annotations

import argparse
import gzip
import html
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .evaluator import _load_s000, _load_sessions
from .methods import create_method, method_ids
from .methods.stage2_2_retrieval import stage2_2_retrieval_queries
from .metrics import summarize_predictions, write_tables
from .paths import ExperimentPaths
from .corpus import corpus_manifest_path, corpus_root
from .prompts import build_query, s000_as_session
from .stage2_2 import write_stage2_2_initial_copy_report
from .run_harness import (
    ALL_TRAJECTORIES,
    ANTHROPIC_METHODS,
    NINE_METHODS,
    OPENROUTER_METHODS,
    OPENROUTER_MODEL_IDS,
    attempt_path as _attempt_path,
    complete_prediction_paths as _complete_prediction_paths,
    cost_latency_row,
    execution_tree_sha256 as _execution_tree_sha256,
    load_approved_environment as _load_approved_environment,
    load_verified_plan,
    new_run_dir,
    parse_reliability_row,
    parse_selection as _parse_selection,
    preflight_paid as _preflight_paid,
    publish_stable_raw_paths as _publish_stable_raw_paths,
    read_provider_lock,
    resolve_run_dir,
    run_grid,
    validate_grid_shape,
    write_csv as _write_csv,
    write_run_manifest,
)
from .stage2_2 import (
    STAGE2_2,
    stage2_2_item_path,
)
from .util import (
    read_jsonl,
    sha256_file,
    sha256_json,
    write_json,
    write_jsonl,
)


DEFAULT_METHODS = NINE_METHODS
DIRECT_API_METHODS = (
    "fc_gpt_5_6_sol",
    "fc_claude_opus_4_8",
    "fc_gemini_3_5_flash",
)
ADDITIONAL_DIRECT_API_METHODS = (
    "fc_gemini_3_1_pro",
    "fc_gpt_5_6_terra",
    "fc_gpt_5_6_luna",
    "fc_claude_sonnet_4_6",
)
ALL_DIRECT_API_METHODS = tuple(
    dict.fromkeys((*DIRECT_API_METHODS, *ADDITIONAL_DIRECT_API_METHODS))
)
SELECTABLE_METHODS = tuple(
    dict.fromkeys((*DEFAULT_METHODS, *ALL_DIRECT_API_METHODS))
)
TASK = "stage2_2"
APPROVAL_PHRASE = "I_APPROVE_STAGE2_2_PAID"
PROVIDER_LOCK_SCHEMA = "stage2_2_openrouter_provider_lock-v1"


def _checkpoint(item: dict[str, Any]) -> int:
    return int((item.get("metadata") or {})["query_checkpoint"])


def _selected_methods(value: str) -> list[str]:
    # Preserve `all` as the independently runnable nine-method comparison.
    # The three direct-API models remain independently runnable.
    if value == "all":
        return list(DEFAULT_METHODS)
    if value == "direct3":
        return list(DIRECT_API_METHODS)
    return _parse_selection(
        value, all_values=SELECTABLE_METHODS, label="methods"
    )


def _all_items(paths: ExperimentPaths) -> list[dict[str, Any]]:
    return list(read_jsonl(stage2_2_item_path(paths)))


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
        and _checkpoint(item) in checkpoints
    ]
    rows.sort(
        key=lambda item: (
            str(item["trajectory_id"]),
            _checkpoint(item),
            str(item["item_id"]),
        )
    )
    expected = len(trajectories) * len(checkpoints)
    if len(rows) != expected:
        raise ValueError(
            f"selected Stage 2.2 grid is incomplete: {len(rows)} != {expected}"
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
        and _checkpoint(item) == checkpoint
    ]
    if len(items) != 1:
        raise ValueError(
            f"expected one item for {trajectory_id}/cp{checkpoint:03d}"
        )
    item = items[0]
    generation_item = {
        key: value for key, value in item.items() if key != "gold"
    }
    generation_item["metadata"] = {
        key: value
        for key, value in (item.get("metadata") or {}).items()
        if key
        not in {"dynamic_paths", "evidence_sessions", "gold_evidence"}
    }
    root = corpus_root(paths)
    system_prompt = (
        paths.prompts / "system_ko.txt"
    ).read_text(encoding="utf-8").strip()
    s000 = _load_s000(root, trajectory_id)
    sessions = _load_sessions(root, trajectory_id)[:checkpoint]
    if method_id == "letta_claude_opus_4_8":
        groups = stage2_2_retrieval_queries()
        group_text = "\n".join(
            f"{index}. {group['group_id']}: {group['query']}"
            for index, group in enumerate(groups, start=1)
        )
        prompt = (
            "archival search는 최대 4회, 각 결과는 최대 5개만 사용하라.\n"
            "다음 네 Gold-independent state 그룹을 순서대로 검색한 뒤 "
            f"전체 상태를 복원하라.\n{group_text}\n\n"
            + build_query(generation_item, [s000_as_session(s000)])
        )
        return {
            "method_id": method_id,
            "item": item,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "retrieval_groups": groups,
            "evidence_session_ids": ["S000"],
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
    )
    try:
        method.ingest_initial(s000)
        for session in sessions:
            method.ingest_session(session)
        answer = method.answer(generation_item)
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
            answer.metadata.get("rendered_system_prompt")
            or system_prompt
        ),
        "retrieval_groups": answer.metadata.get("retrieval_groups") or [],
        "evidence_session_ids": answer.evidence_session_ids,
    }


def _audit_rendered_prompt(rendered: dict[str, Any]) -> dict[str, Any]:
    item = rendered["item"]
    checkpoint = _checkpoint(item)
    prompt = rendered["prompt"]
    dialogue_ids = [
        int(value)
        for value in re.findall(r"\[D(\d{3})\s*\|", prompt)
    ]
    groups_json = json.dumps(
        rendered["retrieval_groups"], ensure_ascii=False, sort_keys=True
    )
    return {
        "method_id": rendered["method_id"],
        "trajectory_id": item["trajectory_id"],
        "checkpoint": checkpoint,
        "s000_present": "[S000" in prompt,
        "future_session_ids": [
            value for value in dialogue_ids if value > checkpoint
        ],
        "max_visible_dialogue_id": max(dialogue_ids, default=0),
        "gold_field_name_in_prompt": '"gold"' in prompt,
        "dynamic_paths_field_name_in_prompt": "dynamic_paths" in prompt,
        "retrieval_query_contains_gold_field": '"gold"' in groups_json,
        "retrieval_query_contains_dynamic_paths_field": (
            "dynamic_paths" in groups_json
        ),
        "candidate_values_exposed_by_task_prompt": True,
        "passed": (
            "[S000" in prompt
            and not any(value > checkpoint for value in dialogue_ids)
            and '"gold"' not in prompt
            and "dynamic_paths" not in prompt
            and '"gold"' not in groups_json
            and "dynamic_paths" not in groups_json
        ),
    }


def command_plan(args: argparse.Namespace) -> None:
    paths = ExperimentPaths.discover()
    configured = method_ids(paths)
    methods = _selected_methods(args.methods)
    allowed = set(configured) | set(ALL_DIRECT_API_METHODS)
    if not set(methods) <= allowed:
        raise ValueError("selected methods are not in the frozen config")
    trajectories = _parse_selection(
        args.trajectories,
        all_values=ALL_TRAJECTORIES,
        label="trajectories",
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
        estimated_total = math.ceil(max(prompt_lengths) / 2) + 20_000
        failures = [
            method
            for method in selected_openrouter
            if int(
                provider_lock["methods"][method]["context_window"]
            )
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
    direct_api_plan = set(methods) <= set(ALL_DIRECT_API_METHODS)
    plan_body = {
        "schema_version": (
            "stage2_2_direct_api_plan-v1"
            if direct_api_plan
            else "stage2_2_nine_method_plan-v1"
        ),
        "run_id": run_dir.name,
        "created_at_kst": datetime.now(
            ZoneInfo("Asia/Seoul")
        ).isoformat(),
        "methods": methods,
        "trajectories": trajectories,
        "checkpoints": sorted({_checkpoint(item) for item in items}),
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
            "openai_max_in_flight": args.openai_max_in_flight,
            "google_max_in_flight": args.google_max_in_flight,
            "openrouter_max_in_flight": args.openrouter_max_in_flight,
        },
        "retrieval": {
            "top_k_per_group": args.retrieval_top_k_per_group,
            "max_evidence": args.retrieval_max_evidence,
        },
        "request_timeout_seconds": args.request_timeout_seconds,
        "provider_retries": args.provider_retries,
        "parse_retries": args.parse_retries,
        "reasoning_policy": args.reasoning_policy,
        "provider_lock_status": provider_lock["status"],
        "context_precheck": context_precheck,
        "prompt_audit_required": True,
        "max_output_tokens": 20_000,
        "execution_tree_sha256": _execution_tree_sha256(paths),
    }
    plan = {**plan_body, "plan_sha256": sha256_json(plan_body)}
    write_json(run_dir / "immutable_plan.json", plan)
    write_json(run_dir / "provider_lock.json", provider_lock)
    write_run_manifest(
        run_dir,
        schema_version="stage2_2_run_manifest-v1",
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
    print("[retrieval queries]")
    print(
        json.dumps(
            rendered["retrieval_groups"],
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n[final user prompt]")
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
    samples = []
    audit_methods = list(plan["methods"])
    for method in audit_methods:
        for checkpoint in (
            min(plan["checkpoints"]),
            max(plan["checkpoints"]),
        ):
            rendered = _render_prompt_offline(
                paths,
                method_id=method,
                trajectory_id=str(plan["trajectories"][0]),
                checkpoint=int(checkpoint),
            )
            checks.append(_audit_rendered_prompt(rendered))
            samples.append(rendered)
    if "traj_001" in plan["trajectories"]:
        for checkpoint in (15, 300):
            if checkpoint not in plan["checkpoints"]:
                continue
            rendered = _render_prompt_offline(
                paths,
                method_id=str(plan["methods"][0]),
                trajectory_id="traj_001",
                checkpoint=checkpoint,
            )
            sample_path = (
                run_dir
                / "prompts"
                / "audit_examples"
                / f"traj_001_cp_{checkpoint:03d}.txt.gz"
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
    source = paths.root / "docs" / "stage2_2_prompt_leakage_audit.md"
    target = run_dir / "prompt_leakage_audit.md"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    if not checks or not all(check["passed"] for check in checks):
        raise RuntimeError("prompt leakage audit failed")
    print(json.dumps({"run_dir": str(run_dir), "checks": checks}, ensure_ascii=False))


def _run_grid(
    paths: ExperimentPaths,
    run_dir: Path,
    plan: dict[str, Any],
) -> None:
    run_grid(
        paths,
        run_dir,
        plan,
        items_by_id={
            str(item["item_id"]): item for item in _all_items(paths)
        },
        retrieval_env={
            "STAGE2_2_RETRIEVAL_TOP_K_PER_GROUP": str(
                plan["retrieval"]["top_k_per_group"]
            ),
            "STAGE2_2_RETRIEVAL_MAX_EVIDENCE": str(
                plan["retrieval"]["max_evidence"]
            ),
        },
        reasoning_policy=str(
            plan.get("reasoning_policy", "deployment_realistic_low")
        ),
    )


def _validate_complete_grid(
    run_dir: Path, plan: dict[str, Any]
) -> list[Path]:
    paths = validate_grid_shape(run_dir, plan)
    for row in (row for path in paths for row in read_jsonl(path)):
        path_count = len(
            (row.get("metrics") or {}).get("path_outcomes") or {}
        )
        if path_count != 34:
            raise RuntimeError(
                f"{row.get('item_id')}: expected 34 path outcomes, "
                f"found {path_count}"
            )
    return paths


def _diff_states(
    prediction: dict[str, Any], gold: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        {"path": path, "prediction": prediction.get(path), "gold": gold.get(path)}
        for path in sorted(set(prediction) | set(gold))
        if prediction.get(path) != gold.get(path)
    ]


def _materialize_state_pairs(
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
            prompt = str(metadata.get("rendered_user_prompt") or "")
            system_prompt = str(
                metadata.get("rendered_system_prompt") or ""
            )
            complete_prompt = (
                "[SYSTEM]\n"
                + system_prompt
                + "\n\n[USER]\n"
                + prompt
                if prompt
                else ""
            )
            prompt_path = (
                run_dir
                / "prompts"
                / method
                / trajectory
                / f"cp_{checkpoint:03d}.txt.gz"
            )
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            if prompt and not metadata.get("prompt_artifact_path"):
                with gzip.open(prompt_path, "wt", encoding="utf-8") as handle:
                    handle.write(complete_prompt)
            retrieval = metadata.get("retrieval_groups") or []
            retrieval_path = (
                run_dir
                / "retrieval"
                / method
                / trajectory
                / f"cp_{checkpoint:03d}.json"
            )
            write_json(
                retrieval_path,
                {
                    "retrieval_groups": retrieval,
                    "evidence_session_ids": row.get(
                        "evidence_session_ids"
                    )
                    or [],
                },
            )
            state_pair = {
                "schema_version": "stage2_2_state_pair-v1",
                "method_id": method,
                "trajectory_id": trajectory,
                "checkpoint": checkpoint,
                "initial_state": item["gold"]["initial_state"],
                "prediction_state": row["prediction"],
                "gold_state": row["gold"],
                "prediction_gold_diff": _diff_states(
                    row["prediction"], row["gold"]
                ),
                "confusion_classification": {
                    path: outcome["classification"]
                    for path, outcome in (
                        row.get("metrics", {}).get("path_outcomes") or {}
                    ).items()
                },
                "metrics": row["metrics"],
                "prompt_sha256": (
                    metadata.get("prompt_sha256")
                    or (sha256_json(complete_prompt) if prompt else None)
                ),
                # attempts[] already carries each attempt's text, but the
                # scored response was only recoverable by reasoning about which
                # attempt broke the retry loop. Record it directly.
                "raw_answer": row["raw_answer"],
                "raw_response_sha256": sha256_json(row["raw_answer"]),
                "retrieval_evidence": {
                    "session_ids": row.get("evidence_session_ids") or [],
                    "groups": retrieval,
                },
                "provider_usage": metadata.get("usage"),
                "latency_seconds": metadata.get("latency_seconds"),
                "attempts": row.get("attempts") or [],
            }
            write_json(
                run_dir
                / "state_pairs"
                / method
                / trajectory
                / f"cp_{checkpoint:03d}.json",
                state_pair,
            )


def _write_svg_figures(
    run_dir: Path,
    checkpoint_rows: list[dict[str, Any]],
    report: dict[str, Any],
) -> None:
    figure_dir = run_dir / "report" / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    colors = (
        "#0072B2",
        "#D55E00",
        "#009E73",
        "#CC79A7",
        "#E69F00",
        "#56B4E9",
        "#F0E442",
        "#000000",
        "#7A5195",
    )
    for metric, filename in (
        (
            "dynamic_path_final_state_accuracy",
            "checkpoint_dynamic_path_final_state_accuracy.svg",
        ),
        ("correct_change_f1", "checkpoint_correct_change_f1.svg"),
    ):
        methods = sorted({str(row["method_id"]) for row in checkpoint_rows})
        checkpoints = sorted(
            {int(row["checkpoint"]) for row in checkpoint_rows}
        )
        width, height = 920, 520
        left, top, plot_w, plot_h = 75, 40, 600, 400
        minimum, maximum = 0.0, 1.0
        svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="white"/>',
            f'<text x="{left}" y="24" font-family="sans-serif" '
            f'font-size="16">{html.escape(metric)}</text>',
        ]
        for tick in range(0, 11, 2):
            value = tick / 10
            y = top + plot_h * (1 - value)
            svg.extend(
                [
                    f'<line x1="{left}" y1="{y:.1f}" '
                    f'x2="{left + plot_w}" y2="{y:.1f}" '
                    'stroke="#dddddd"/>',
                    f'<text x="{left - 8}" y="{y + 4:.1f}" '
                    'text-anchor="end" font-family="sans-serif" '
                    f'font-size="11">{value:.1f}</text>',
                ]
            )
        x_for = {
            checkpoint: left
            + plot_w
            * (
                (checkpoint - checkpoints[0])
                / max(1, checkpoints[-1] - checkpoints[0])
            )
            for checkpoint in checkpoints
        }
        for checkpoint in checkpoints:
            x = x_for[checkpoint]
            svg.append(
                f'<text x="{x:.1f}" y="{top + plot_h + 20}" '
                'text-anchor="middle" font-family="sans-serif" '
                f'font-size="10">{checkpoint}</text>'
            )
        for index, method in enumerate(methods):
            by_checkpoint: dict[int, list[float]] = {}
            for row in checkpoint_rows:
                if row["method_id"] != method or row.get(metric) is None:
                    continue
                by_checkpoint.setdefault(int(row["checkpoint"]), []).append(
                    float(row[metric])
                )
            points = [
                (
                    x_for[checkpoint],
                    top
                    + plot_h
                    * (
                        maximum
                        - mean_value
                    )
                    / (maximum - minimum),
                )
                for checkpoint in checkpoints
                if (values := by_checkpoint.get(checkpoint))
                and (mean_value := sum(values) / len(values)) is not None
            ]
            color = colors[index % len(colors)]
            if points:
                svg.append(
                    '<polyline fill="none" '
                    f'stroke="{color}" stroke-width="2" points="'
                    + " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
                    + '"/>'
                )
            legend_y = 55 + index * 24
            svg.extend(
                [
                    f'<line x1="700" y1="{legend_y}" x2="724" '
                    f'y2="{legend_y}" stroke="{color}" stroke-width="3"/>',
                    f'<text x="732" y="{legend_y + 4}" '
                    'font-family="sans-serif" font-size="10">'
                    f"{html.escape(method)}</text>",
                ]
            )
        svg.append("</svg>")
        (figure_dir / filename).write_text(
            "\n".join(svg) + "\n", encoding="utf-8"
        )

    path_values: dict[tuple[str, str], float | None] = {}
    methods = []
    all_paths = set()
    for method, stages in report["methods"].items():
        methods.append(method)
        metrics = (
            stages.get(STAGE2_2, {})
            .get("state_reconstruction", {})
            .get("path_macro", {})
            .get("path_metrics", {})
        )
        for path, values in metrics.items():
            all_paths.add(path)
            path_values[(method, path)] = values.get(
                "final_state_accuracy"
            )
    paths = sorted(all_paths)
    cell_w, cell_h = 74, 22
    left, top = 310, 170
    width = left + len(methods) * cell_w + 30
    height = top + len(paths) * cell_h + 30
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="20" y="28" font-family="sans-serif" font-size="16">'
        "Method × path Final State Accuracy</text>",
    ]
    for column, method in enumerate(methods):
        x = left + column * cell_w + cell_w / 2
        svg.append(
            f'<text x="{x}" y="{top - 8}" text-anchor="end" '
            f'transform="rotate(-55 {x} {top - 8})" '
            'font-family="sans-serif" font-size="9">'
            f"{html.escape(method)}</text>"
        )
    for row_index, path in enumerate(paths):
        y = top + row_index * cell_h
        svg.append(
            f'<text x="{left - 8}" y="{y + 15}" text-anchor="end" '
            'font-family="sans-serif" font-size="10">'
            f"{html.escape(path)}</text>"
        )
        for column, method in enumerate(methods):
            value = path_values.get((method, path))
            shade = 235 if value is None else int(245 - 185 * float(value))
            color = (
                f"rgb({shade},{shade},{255})"
                if value is not None
                else "#eeeeee"
            )
            x = left + column * cell_w
            label = "—" if value is None else f"{100 * float(value):.0f}"
            svg.extend(
                [
                    f'<rect x="{x}" y="{y}" width="{cell_w}" '
                    f'height="{cell_h}" fill="{color}" stroke="white"/>',
                    f'<text x="{x + cell_w / 2}" y="{y + 15}" '
                    'text-anchor="middle" font-family="sans-serif" '
                    f'font-size="9">{label}</text>',
                ]
            )
    svg.append("</svg>")
    (figure_dir / "method_path_final_state_accuracy_heatmap.svg").write_text(
        "\n".join(svg) + "\n", encoding="utf-8"
    )


def _write_auxiliary_metrics(
    run_dir: Path, prediction_paths: list[Path], report: dict[str, Any]
) -> None:
    raw_rows = [row for path in prediction_paths for row in read_jsonl(path)]
    checkpoint_rows = []
    parse_rows = []
    cost_rows = []
    semantic_rows = []
    retrieval_rows = []
    provider_lock = json.loads(
        (run_dir / "provider_lock.json").read_text(encoding="utf-8")
    )
    for row in raw_rows:
        metrics = row.get("metrics") or {}
        checkpoint_rows.append(
            {
                "method_id": row["method_id"],
                "trajectory_id": row["trajectory_id"],
                "checkpoint": row["query_checkpoint"],
                **{
                    key: value
                    for key, value in metrics.items()
                    if not isinstance(value, (dict, list))
                },
            }
        )
        semantic_rows.append(
            {
                "method_id": row["method_id"],
                "trajectory_id": row["trajectory_id"],
                "checkpoint": row["query_checkpoint"],
                **{
                    key: metrics.get(key)
                    for key in (
                        "value_accuracy",
                        "status_accuracy",
                        "evidence_hit_rate",
                        "evidence_citation_precision",
                        "exact_state_match",
                    )
                },
            }
        )
        gold_evidence = {
            str(event_id).replace("D", "S", 1)
            for cell in (row.get("gold") or {}).values()
            for event_id in (cell.get("evidence_session_ids") or [])
        }
        retrieved = set(map(str, row.get("evidence_session_ids") or []))
        retrieval_rows.append(
            {
                "method_id": row["method_id"],
                "trajectory_id": row["trajectory_id"],
                "checkpoint": row["query_checkpoint"],
                "gold_evidence_support": len(gold_evidence),
                "retrieved_evidence_count": len(
                    retrieved - {"S000"}
                ),
                "gold_evidence_recall": (
                    len(gold_evidence & retrieved) / len(gold_evidence)
                    if gold_evidence
                    else None
                ),
                "complete_gold_evidence_recall": (
                    gold_evidence <= retrieved if gold_evidence else None
                ),
            }
        )
        parse_rows.append(parse_reliability_row(row))
        cost_rows.append(cost_latency_row(row, provider_lock))
    _write_csv(run_dir / "metrics" / "checkpoint_metrics.csv", checkpoint_rows)
    _write_csv(run_dir / "metrics" / "parse_reliability.csv", parse_rows)
    _write_csv(run_dir / "metrics" / "cost_latency.csv", cost_rows)
    _write_csv(run_dir / "metrics" / "semantic_quality.csv", semantic_rows)
    _write_csv(run_dir / "metrics" / "retrieval_recall.csv", retrieval_rows)
    _write_svg_figures(run_dir, checkpoint_rows, report)
    trajectory_rows = []
    for method, stages in report["methods"].items():
        reconstruction = (
            stages.get(STAGE2_2, {}).get("state_reconstruction") or {}
        )
        for trajectory, metrics in (
            reconstruction.get("trajectory_metrics") or {}
        ).items():
            trajectory_rows.append(
                {
                    "method_id": method,
                    "trajectory_id": trajectory,
                    **metrics,
                }
            )
    _write_csv(
        run_dir / "metrics" / "trajectory_metrics.csv", trajectory_rows
    )


def command_report(args: argparse.Namespace) -> None:
    paths = ExperimentPaths.discover()
    run_dir = resolve_run_dir(paths, TASK, args.run_dir)
    prediction_paths = _complete_prediction_paths(run_dir)
    if not prediction_paths:
        raise RuntimeError("no complete prediction artifacts to report")
    _publish_stable_raw_paths(prediction_paths)
    report = summarize_predictions(
        paths, prediction_paths, allow_partial=True
    )
    write_json(run_dir / "metrics" / "metrics.json", report)
    prepared_root = corpus_root(paths)
    baseline_path = prepared_root / "baselines" / "initial_copy.json"
    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.exists()
        else None
    )
    if not baseline or not baseline.get("gca15"):
        baseline = write_stage2_2_initial_copy_report(paths)
    write_json(
        run_dir / "metrics" / "initial_copy_baseline.json",
        baseline,
    )
    write_tables(report, run_dir / "metrics")
    _write_auxiliary_metrics(run_dir, prediction_paths, report)
    _materialize_state_pairs(paths, run_dir, prediction_paths)
    (run_dir / "report" / "figures").mkdir(parents=True, exist_ok=True)
    plan_path = run_dir / "immutable_plan.json"
    plan = (
        json.loads(plan_path.read_text(encoding="utf-8"))
        if plan_path.exists()
        else {}
    )
    plan_methods = set(plan.get("methods") or [])
    title = (
        "Direct-API Low-Reasoning Models"
        if plan_methods and plan_methods <= set(ALL_DIRECT_API_METHODS)
        else "9-Method Comparison"
    )
    lines = [
        f"# Stage 2.2 Reconstruction — {title}",
        "",
        "The Stage 2 headline is GCA@15: the published GCA C/W/O/M and "
        "weighted-harmonic formula applied to 15-session checkpoint "
        "transitions, with S000 as an unscored seed. Confidence intervals "
        "use a trajectory-cluster bootstrap.",
        "",
        "## Result artifacts",
        "",
        "- `metrics/checkpoint_metrics.csv`",
        "- `metrics/trajectory_metrics.csv`",
        "- `metrics/path_trajectory_metrics.csv`",
        "- `metrics/path_trajectory_macro.csv`",
        "- `metrics/parse_reliability.csv`",
        "- `metrics/semantic_quality.csv`",
        "- `metrics/retrieval_recall.csv`",
        "- `metrics/cost_latency.csv`",
        "- `metrics/initial_copy_baseline.json`",
        "",
        "The run remains partial unless all frozen method × trajectory jobs "
        "have a COMPLETE immutable output manifest.",
    ]
    report_path = run_dir / "report" / "report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), "report": str(report_path)}))


def command_combine(args: argparse.Namespace) -> None:
    """Combine disjoint completed runs without hiding reused smoke cells."""

    paths = ExperimentPaths.discover()
    source_dirs = [Path(value).resolve() for value in args.source_run_dir]
    if len(source_dirs) < 2:
        raise ValueError("combine requires at least two --source-run-dir values")

    source_plans = []
    source_rows: list[dict[str, Any]] = []
    source_records = []
    for source_dir in source_dirs:
        plan_path = source_dir / "immutable_plan.json"
        manifest_path = source_dir / "run_manifest.json"
        if not plan_path.exists() or not manifest_path.exists():
            raise FileNotFoundError(f"incomplete source run metadata: {source_dir}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "GENERATED":
            raise RuntimeError(f"source run is not GENERATED: {source_dir}")
        prediction_paths = _complete_prediction_paths(source_dir)
        rows = [row for path in prediction_paths for row in read_jsonl(path)]
        if len(rows) != int(plan["prediction_count"]):
            raise RuntimeError(
                f"source grid incomplete for {source_dir}: "
                f"{len(rows)} != {plan['prediction_count']}"
            )
        source_plans.append(plan)
        source_rows.extend(rows)
        source_records.append(
            {
                "run_dir": str(source_dir),
                "run_id": plan["run_id"],
                "plan_sha256": plan["plan_sha256"],
                "methods": plan["methods"],
                "trajectories": plan["trajectories"],
                "prediction_count": plan["prediction_count"],
            }
        )

    methods = list(
        dict.fromkeys(
            method
            for plan in source_plans
            for method in plan["methods"]
        )
    )
    checkpoints = list(source_plans[0]["checkpoints"])
    for plan in source_plans[1:]:
        if list(plan["checkpoints"]) != checkpoints:
            raise RuntimeError("source checkpoint grids differ")
        for field in (
            "max_output_tokens",
            "provider_retries",
        ):
            if plan.get(field) != source_plans[0].get(field):
                raise RuntimeError(f"source plans differ on {field}")
        if (
            plan.get("reasoning_policy") or "deployment_realistic_low"
        ) != (
            source_plans[0].get("reasoning_policy")
            or "deployment_realistic_low"
        ):
            raise RuntimeError("source plans differ on reasoning_policy")

    parse_retry_policies = sorted(
        {int(plan.get("parse_retries", 0)) for plan in source_plans}
    )
    parse_retry_disclosure = None
    if len(parse_retry_policies) > 1:
        retried_rows = [
            (
                str(row["method_id"]),
                str(row["trajectory_id"]),
                int(row["query_checkpoint"]),
                int(row.get("retry_count", 0)),
            )
            for row in source_rows
            if int(row.get("retry_count", 0)) != 0
        ]
        if retried_rows:
            raise RuntimeError(
                "source plans differ on parse_retries and some source rows "
                f"used retries: {retried_rows[:5]}"
            )
        parse_retry_disclosure = (
            "Source plans declared different parse-retry limits "
            f"({parse_retry_policies}), but every combined row has "
            "retry_count=0."
        )

    keys = [
        (
            str(row["method_id"]),
            str(row["trajectory_id"]),
            int(row["query_checkpoint"]),
        )
        for row in source_rows
    ]
    if len(keys) != len(set(keys)):
        raise RuntimeError("source runs overlap on method/trajectory/checkpoint")
    trajectories = sorted({trajectory for _, trajectory, _ in keys})
    expected = {
        (method, trajectory, int(checkpoint))
        for method in methods
        for trajectory in trajectories
        for checkpoint in checkpoints
    }
    if set(keys) != expected:
        missing = sorted(expected - set(keys))
        extra = sorted(set(keys) - expected)
        raise RuntimeError(
            f"combined grid is not rectangular: missing={missing[:5]}, "
            f"extra={extra[:5]}"
        )

    fingerprints: dict[str, set[str]] = {}
    for row in source_rows:
        metadata = row.get("response_metadata") or {}
        fingerprint = sha256_json(
            {
                "model": metadata.get("model"),
                "provider": metadata.get("provider"),
                "api_surface": metadata.get("api_surface"),
                "max_output_tokens": metadata.get("max_output_tokens"),
                "generation_settings": metadata.get("generation_settings"),
            }
        )
        fingerprints.setdefault(str(row["method_id"]), set()).add(fingerprint)
    drift = {
        method: sorted(values)
        for method, values in fingerprints.items()
        if len(values) != 1
    }
    if drift:
        raise RuntimeError(f"source inference payloads differ: {drift}")

    run_dir = new_run_dir(paths, "stage2_2_combined")
    plan_body = {
        "schema_version": "stage2_2_combined_plan-v1",
        "run_id": run_dir.name,
        "created_at_kst": datetime.now(
            ZoneInfo("Asia/Seoul")
        ).isoformat(),
        "methods": methods,
        "trajectories": trajectories,
        "checkpoints": checkpoints,
        "prediction_count": len(source_rows),
        "reasoning_policy": (
            source_plans[0].get("reasoning_policy")
            or "deployment_realistic_low"
        ),
        "parse_retry_policies": parse_retry_policies,
        "parse_retry_disclosure": parse_retry_disclosure,
        "sources": source_records,
        "reuse_disclosure": (
            "Rows come from completed source runs and were not rerun during "
            "combination."
        ),
    }
    plan = {**plan_body, "plan_sha256": sha256_json(plan_body)}
    write_json(run_dir / "immutable_plan.json", plan)
    write_json(
        run_dir / "provider_lock.json",
        {"status": "NOT_APPLICABLE", "methods": {}},
    )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in source_rows:
        grouped.setdefault(
            (str(row["method_id"]), str(row["trajectory_id"])), []
        ).append(row)
    for (method, trajectory), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: int(row["query_checkpoint"]))
        output = (
            run_dir / "raw" / method / trajectory / "attempt_01.jsonl"
        )
        write_jsonl(output, rows)
        write_json(
            output.with_suffix(".manifest.json"),
            {
                "schema_version": "stage2_2_combined_attempt_manifest-v1",
                "status": "COMPLETE",
                "method_id": method,
                "trajectory_id": trajectory,
                "input_item_ids": [str(row["item_id"]) for row in rows],
                "completed_items": len(rows),
                "output_sha256": sha256_file(output),
                "source_plan_sha256": next(
                    record["plan_sha256"]
                    for record in source_records
                    if method in record["methods"]
                    and trajectory in record["trajectories"]
                ),
            },
        )

    combined_manifest = {
        "schema_version": "stage2_2_combined_run_manifest-v1",
        "status": "COMBINING",
        "run_id": run_dir.name,
        "plan_sha256": plan["plan_sha256"],
        "source_runs": source_records,
        "complete_prediction_files": len(grouped),
    }
    write_json(run_dir / "run_manifest.json", combined_manifest)
    _validate_complete_grid(run_dir, plan)
    command_report(argparse.Namespace(run_dir=str(run_dir)))
    write_json(
        run_dir / "run_manifest.json",
        {**combined_manifest, "status": "GENERATED"},
    )
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "plan_sha256": plan["plan_sha256"],
                "prediction_count": len(source_rows),
                "sources": source_records,
            },
            ensure_ascii=False,
        )
    )


def command_execute(args: argparse.Namespace) -> None:
    paths = ExperimentPaths.discover()
    run_dir = resolve_run_dir(paths, TASK, args.run_dir)
    plan = load_verified_plan(run_dir, args, approval_phrase=APPROVAL_PHRASE)
    _load_approved_environment(paths)
    _preflight_paid(plan)
    os.environ["FIN_MEMORY_DISABLE_PAID_APIS"] = "0"
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "status": "RUNNING",
            "started_at_kst": datetime.now(
                ZoneInfo("Asia/Seoul")
            ).isoformat(),
        }
    )
    write_json(manifest_path, manifest)
    try:
        _run_grid(paths, run_dir, plan)
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
            "complete_prediction_files": len(
                complete_paths
            ),
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
    plan.add_argument("--openai-max-in-flight", type=int, default=20)
    plan.add_argument("--google-max-in-flight", type=int, default=20)
    plan.add_argument("--openrouter-max-in-flight", type=int, default=40)
    plan.add_argument("--retrieval-top-k-per-group", type=int, default=5)
    plan.add_argument("--retrieval-max-evidence", type=int, default=20)
    plan.add_argument("--request-timeout-seconds", type=int, default=300)
    plan.add_argument("--provider-retries", type=int, default=0)
    plan.add_argument("--parse-retries", type=int, default=1)
    plan.add_argument(
        "--reasoning-policy",
        choices=(
            "deployment_realistic_low",
            "deployment_realistic_medium",
        ),
        default="deployment_realistic_low",
    )
    plan.add_argument("--budget-cap-usd", type=float, required=True)
    plan.add_argument("--estimated-usd", type=float)
    plan.add_argument("--provider-lock-file")
    plan.set_defaults(handler=command_plan)

    show = commands.add_parser("show-prompt")
    show.add_argument("--method", required=True, choices=SELECTABLE_METHODS)
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

    combine = commands.add_parser("combine")
    combine.add_argument(
        "--source-run-dir",
        action="append",
        required=True,
        help="Completed Stage 2.2 source run; pass once per disjoint run.",
    )
    combine.set_defaults(handler=command_combine)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
