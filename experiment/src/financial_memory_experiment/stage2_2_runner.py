from __future__ import annotations

import argparse
import csv
import gzip
import html
import importlib.util
import json
import math
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .evaluator import _load_s000, _load_sessions, run_method
from .methods import create_method, method_ids
from .methods.readers import configure_generation_limits
from .methods.stage2_2_retrieval import stage2_2_retrieval_queries
from .metrics import summarize_predictions, write_tables
from .paths import ExperimentPaths
from .prompts import build_query, s000_as_session
from .stage2_2 import (
    STAGE2_2,
    active_stage2_2_prepared_manifest,
    stage2_2_item_path,
)
from .util import (
    read_jsonl,
    sha256_file,
    sha256_json,
    write_json,
)


DEFAULT_METHODS = (
    "fc_claude_opus_4_8",
    "bm25_claude_opus_4_8",
    "dense_ge2_claude_opus_4_8",
    "mem0_claude_opus_4_8",
    "letta_claude_opus_4_8",
    "fc_openrouter_llama_4_maverick",
    "fc_openrouter_gpt_oss_120b",
    "fc_openrouter_qwen_3_5_122b_a10b",
    "fc_openrouter_qwen_3_6_35b_a3b_fp8",
)
OPENROUTER_METHODS = tuple(
    method for method in DEFAULT_METHODS if method.startswith("fc_openrouter_")
)
OPENROUTER_MODEL_IDS = {
    "fc_openrouter_llama_4_maverick": "meta-llama/llama-4-maverick",
    "fc_openrouter_gpt_oss_120b": "openai/gpt-oss-120b",
    "fc_openrouter_qwen_3_5_122b_a10b": "qwen/qwen3.5-122b-a10b",
    "fc_openrouter_qwen_3_6_35b_a3b_fp8": "qwen/qwen3.6-35b-a3b",
}
ANTHROPIC_METHODS = tuple(
    method
    for method in DEFAULT_METHODS
    if method not in OPENROUTER_METHODS
)
APPROVAL_PHRASE = "I_APPROVE_STAGE2_2_PAID"


def _checkpoint(item: dict[str, Any]) -> int:
    return int((item.get("metadata") or {})["query_checkpoint"])


def _all_items(paths: ExperimentPaths) -> list[dict[str, Any]]:
    return list(read_jsonl(stage2_2_item_path(paths)))


def _parse_selection(
    value: str,
    *,
    all_values: Iterable[str],
    label: str,
) -> list[str]:
    available = list(all_values)
    if value == "all":
        return available
    selected = [part.strip() for part in value.split(",") if part.strip()]
    unknown = set(selected) - set(available)
    if unknown:
        raise ValueError(f"unknown {label}: {sorted(unknown)}")
    return selected


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


def _new_run_dir(paths: ExperimentPaths) -> Path:
    parent = paths.runs / "stage2_2"
    parent.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%m%d_%H%M")
    candidate = parent / stem
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{stem}_{suffix:02d}"
        suffix += 1
    candidate.mkdir()
    return candidate


def _execution_tree_sha256(paths: ExperimentPaths) -> str:
    roots = (
        paths.root / "src",
        paths.root / "configs",
        paths.root / "prompts",
        paths.root / "scripts",
    )
    hashes = {
        str(path.relative_to(paths.root)): sha256_file(path)
        for root in roots
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    return sha256_json(hashes)


def _latest_run_dir(paths: ExperimentPaths) -> Path:
    parent = paths.runs / "stage2_2"
    candidates = sorted(
        path for path in parent.glob("*") if (path / "immutable_plan.json").exists()
    )
    if not candidates:
        raise FileNotFoundError("no Stage 2.2 run plan exists")
    return candidates[-1]


def _resolve_run_dir(paths: ExperimentPaths, value: str | None) -> Path:
    return Path(value).resolve() if value else _latest_run_dir(paths)


def _openrouter_json(url: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected OpenRouter response: {url}")
    return payload


def _nested_numeric(value: Any, key_fragment: str) -> float | None:
    candidates: list[float] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key_fragment in str(key).lower():
                try:
                    candidates.append(float(child))
                except (TypeError, ValueError):
                    pass
            nested = _nested_numeric(child, key_fragment)
            if nested is not None:
                candidates.append(nested)
    elif isinstance(value, list):
        for child in value:
            nested = _nested_numeric(child, key_fragment)
            if nested is not None:
                candidates.append(nested)
    return max(candidates) if candidates else None


def _endpoint_provider(endpoint: dict[str, Any]) -> str:
    provider = (
        endpoint.get("provider_slug")
        or endpoint.get("provider_name")
        or endpoint.get("provider")
        or (endpoint.get("provider_info") or {}).get("slug")
        or (endpoint.get("provider_info") or {}).get("name")
    )
    return str(provider or "")


def _auto_provider_lock(methods: Iterable[str]) -> dict[str, Any]:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required to resolve and freeze providers "
            "during plan; alternatively pass --provider-lock-file"
        )
    zdr_payload = _openrouter_json(
        "https://openrouter.ai/api/v1/endpoints/zdr", api_key
    )
    zdr_rows_raw = zdr_payload.get("data") or []
    if isinstance(zdr_rows_raw, dict):
        zdr_rows_raw = (
            zdr_rows_raw.get("endpoints")
            or zdr_rows_raw.get("data")
            or []
        )
    zdr_rows = [
        row for row in zdr_rows_raw if isinstance(row, dict)
    ]
    if not zdr_rows:
        raise RuntimeError("OpenRouter returned no ZDR endpoint metadata")
    zdr_ids = {
        str(row.get("id") or row.get("endpoint_id") or "")
        for row in zdr_rows
    }
    zdr_pairs = {
        (
            str(row.get("model_id") or row.get("model") or ""),
            _endpoint_provider(row),
        )
        for row in zdr_rows
    }
    result: dict[str, Any] = {}
    source_hashes: dict[str, str] = {
        "zdr_endpoints": sha256_json(zdr_payload)
    }
    for method in methods:
        model = OPENROUTER_MODEL_IDS[method]
        author, slug = model.split("/", 1)
        payload = _openrouter_json(
            "https://openrouter.ai/api/v1/models/"
            f"{author}/{slug}/endpoints",
            api_key,
        )
        source_hashes[method] = sha256_json(payload)
        data = payload.get("data") or {}
        endpoints = (
            data.get("endpoints") if isinstance(data, dict) else []
        ) or []
        candidates = []
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                continue
            provider = _endpoint_provider(endpoint)
            endpoint_id = str(
                endpoint.get("id") or endpoint.get("endpoint_id") or ""
            )
            is_zdr = bool(
                endpoint.get("zdr")
                or endpoint.get("is_zdr")
                or endpoint_id in zdr_ids
                or (model, provider) in zdr_pairs
            )
            if not is_zdr or not provider:
                continue
            quantization = str(
                endpoint.get("quantization")
                or endpoint.get("quant")
                or ""
            ).lower()
            if method.endswith("_qwen_3_6_35b_a3b_fp8") and (
                quantization != "fp8"
            ):
                continue
            throughput = _nested_numeric(endpoint, "throughput")
            if throughput is None:
                continue
            context_window = int(
                endpoint.get("context_length")
                or endpoint.get("context_window")
                or data.get("context_length")
                or 0
            )
            pricing = endpoint.get("pricing") or data.get("pricing") or {}
            if (
                context_window <= 0
                or pricing.get("prompt") is None
                or pricing.get("completion") is None
            ):
                continue
            candidates.append(
                {
                    "provider": provider,
                    "throughput_tokens_per_second": throughput,
                    "price": {
                        "prompt_usd_per_token": pricing.get("prompt"),
                        "completion_usd_per_token": pricing.get(
                            "completion"
                        ),
                    },
                    "context_window": context_window,
                    "quantizations": (
                        ["fp8"]
                        if method.endswith("_qwen_3_6_35b_a3b_fp8")
                        else ([quantization] if quantization else None)
                    ),
                    "endpoint_id": endpoint_id or None,
                }
            )
        if not candidates:
            raise RuntimeError(
                "no throughput-ranked ZDR endpoint satisfies the frozen "
                f"routing contract for {method}/{model}"
            )
        result[method] = max(
            candidates,
            key=lambda row: float(row["throughput_tokens_per_second"]),
        )
    return {
        "schema_version": "stage2_2_openrouter_provider_lock-v1",
        "status": "LOCKED",
        "selection_policy": (
            "highest reported throughput among ZDR-compatible endpoints; "
            "Qwen3.6 additionally requires FP8"
        ),
        "resolved_at_kst": datetime.now(
            ZoneInfo("Asia/Seoul")
        ).isoformat(),
        "methods": result,
        "source_response_sha256": source_hashes,
    }


def _read_provider_lock(
    path: str | None, selected_methods: Iterable[str]
) -> dict[str, Any]:
    selected_openrouter = [
        method for method in selected_methods if method in OPENROUTER_METHODS
    ]
    if not selected_openrouter:
        return {"status": "NOT_APPLICABLE", "methods": {}}
    if not path:
        return _auto_provider_lock(selected_openrouter)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    locked = payload.get("methods") or {}
    missing = [
        method
        for method in selected_openrouter
        if not (locked.get(method) or {}).get("provider")
        or not (locked.get(method) or {}).get("context_window")
        or (
            ((locked.get(method) or {}).get("price") or {}).get(
                "prompt_usd_per_token"
            )
            is None
        )
        or (
            ((locked.get(method) or {}).get("price") or {}).get(
                "completion_usd_per_token"
            )
            is None
        )
    ]
    if missing:
        raise ValueError(
            "provider lock must include provider, price, and context_window for "
            f"{missing}"
        )
    qwen_fp8 = locked.get("fc_openrouter_qwen_3_6_35b_a3b_fp8")
    if (
        "fc_openrouter_qwen_3_6_35b_a3b_fp8" in selected_openrouter
        and [str(value).lower() for value in qwen_fp8.get("quantizations") or []]
        != ["fp8"]
    ):
        raise ValueError("Qwen3.6 provider lock must require exactly FP8")
    return {**payload, "status": "LOCKED"}


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
    root = Path(active_stage2_2_prepared_manifest(paths)["root"])
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
    methods = _parse_selection(
        args.methods, all_values=DEFAULT_METHODS, label="methods"
    )
    if not set(methods) <= set(configured):
        raise ValueError("selected methods are not in the frozen config")
    all_trajectories = [f"traj_{index:03d}" for index in range(1, 21)]
    trajectories = _parse_selection(
        args.trajectories,
        all_values=all_trajectories,
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
    provider_lock = _read_provider_lock(args.provider_lock_file, methods)
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
    run_dir = _new_run_dir(paths)
    plan_body = {
        "schema_version": "stage2_2_nine_method_plan-v1",
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
            "openrouter_max_in_flight": args.openrouter_max_in_flight,
        },
        "retrieval": {
            "top_k_per_group": args.retrieval_top_k_per_group,
            "max_evidence": args.retrieval_max_evidence,
        },
        "request_timeout_seconds": args.request_timeout_seconds,
        "provider_retries": args.provider_retries,
        "parse_retries": args.parse_retries,
        "provider_lock_status": provider_lock["status"],
        "context_precheck": context_precheck,
        "prompt_audit_required": True,
        "max_output_tokens": 20_000,
        "execution_tree_sha256": _execution_tree_sha256(paths),
    }
    plan = {**plan_body, "plan_sha256": sha256_json(plan_body)}
    write_json(run_dir / "immutable_plan.json", plan)
    write_json(run_dir / "provider_lock.json", provider_lock)
    write_json(
        run_dir / "run_manifest.json",
        {
            "schema_version": "stage2_2_run_manifest-v1",
            "status": "PLANNED",
            "run_id": run_dir.name,
            "plan_sha256": plan["plan_sha256"],
            "provider_lock_sha256": sha256_json(provider_lock),
            "prepared_manifest_sha256": sha256_file(
                paths.stage2_2_prepared / "active_manifest.json"
            ),
        },
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
    run_dir = _resolve_run_dir(paths, args.run_dir)
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


def _load_verified_plan(run_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute_paid:
        raise RuntimeError("--execute-paid is required")
    if args.approval != APPROVAL_PHRASE:
        raise RuntimeError(f"--approval must exactly equal {APPROVAL_PHRASE}")
    path = run_dir / "immutable_plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    claimed = str(plan.pop("plan_sha256"))
    if sha256_json(plan) != claimed:
        raise RuntimeError("immutable plan SHA mismatch")
    plan["plan_sha256"] = claimed
    if float(plan["estimated_usd"]) > float(plan["budget_cap_usd"]):
        raise RuntimeError("plan estimate exceeds budget cap")
    if plan.get("execution_tree_sha256") != _execution_tree_sha256(
        ExperimentPaths.discover()
    ):
        raise RuntimeError(
            "code/config/prompt changed after planning; create a new plan"
        )
    if not (run_dir / "prompt_audit.json").exists():
        raise RuntimeError("run audit-prompt before paid execution")
    audit = json.loads(
        (run_dir / "prompt_audit.json").read_text(encoding="utf-8")
    )
    if not all(check.get("passed") for check in audit.get("checks") or []):
        raise RuntimeError("prompt audit is not passing")
    lock = json.loads(
        (run_dir / "provider_lock.json").read_text(encoding="utf-8")
    )
    run_manifest = json.loads(
        (run_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    if run_manifest.get("provider_lock_sha256") != sha256_json(lock):
        raise RuntimeError("provider lock changed after immutable planning")
    selected_openrouter = set(plan["methods"]) & set(OPENROUTER_METHODS)
    if selected_openrouter:
        if lock.get("status") != "LOCKED":
            raise RuntimeError(
                "OpenRouter provider lock is unresolved; create a new plan "
                "with --provider-lock-file"
            )
        for method in selected_openrouter:
            entry = (lock.get("methods") or {}).get(method) or {}
            if not entry.get("provider") or not entry.get("context_window"):
                raise RuntimeError(f"incomplete provider lock for {method}")
        if not (plan.get("context_precheck") or {}).get("passed"):
            raise RuntimeError(
                "OpenRouter context precheck was not frozen as passing"
            )
    return plan


def _preflight_paid(plan: dict[str, Any]) -> None:
    methods = set(map(str, plan["methods"]))
    missing_keys = []
    if methods & set(ANTHROPIC_METHODS) and not os.environ.get(
        "ANTHROPIC_API_KEY"
    ):
        missing_keys.append("ANTHROPIC_API_KEY")
    if methods & set(OPENROUTER_METHODS) and not os.environ.get(
        "OPENROUTER_API_KEY"
    ):
        missing_keys.append("OPENROUTER_API_KEY")
    if methods & {
        "dense_ge2_claude_opus_4_8",
        "mem0_claude_opus_4_8",
        "letta_claude_opus_4_8",
    } and not (
        os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
    ):
        missing_keys.append("GOOGLE_API_KEY or GEMINI_API_KEY")
    if missing_keys:
        raise RuntimeError(
            "missing paid API credentials: " + ", ".join(missing_keys)
        )
    required_modules = {
        "fc_claude_opus_4_8": "anthropic",
        "bm25_claude_opus_4_8": "kiwipiepy",
        "dense_ge2_claude_opus_4_8": "google.genai",
        "mem0_claude_opus_4_8": "mem0",
        "letta_claude_opus_4_8": "letta_client",
        **{method: "openai" for method in OPENROUTER_METHODS},
    }
    missing_modules = sorted(
        {
            module
            for method, module in required_modules.items()
            if method in methods
            and importlib.util.find_spec(module) is None
        }
    )
    if missing_modules:
        raise RuntimeError(
            "missing runtime modules: " + ", ".join(missing_modules)
        )
    if "letta_claude_opus_4_8" in methods:
        try:
            with urllib.request.urlopen(
                "http://localhost:8283/v1/health", timeout=3
            ) as response:
                if response.status >= 400:
                    raise RuntimeError(
                        f"Letta health returned {response.status}"
                    )
        except Exception as exc:
            raise RuntimeError(
                "Letta server is not healthy at localhost:8283"
            ) from exc


def _load_approved_environment(paths: ExperimentPaths) -> None:
    """Load secrets only after the immutable plan is approved and verified."""

    load_dotenv(paths.root / ".env")
    load_dotenv(paths.repo_root / ".env")
    if not os.environ.get("GOOGLE_API_KEY") and os.environ.get(
        "GEMINI_API_KEY"
    ):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]


def _attempt_path(
    run_dir: Path, method: str, trajectory: str
) -> tuple[Path | None, Path]:
    parent = run_dir / "raw" / method / trajectory
    parent.mkdir(parents=True, exist_ok=True)
    attempts = sorted(parent.glob("attempt_*.jsonl"))
    for path in reversed(attempts):
        manifest = path.with_suffix(".manifest.json")
        if manifest.exists():
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if payload.get("status") == "COMPLETE":
                return path, path
    return None, parent / f"attempt_{len(attempts) + 1:02d}.jsonl"


def _run_grid(
    paths: ExperimentPaths,
    run_dir: Path,
    plan: dict[str, Any],
) -> None:
    item_map = {
        str(item["item_id"]): item for item in _all_items(paths)
    }
    selected = [item_map[item_id] for item_id in plan["item_ids"]]
    by_trajectory: dict[str, list[dict[str, Any]]] = {}
    for trajectory in plan["trajectories"]:
        by_trajectory[trajectory] = [
            item
            for item in selected
            if str(item["trajectory_id"]) == trajectory
        ]
    tasks = [
        (method, trajectory)
        for method in plan["methods"]
        for trajectory in plan["trajectories"]
    ]
    concurrency = plan["concurrency"]
    configure_generation_limits(
        max_in_flight=int(concurrency["max_in_flight"]),
        provider_limits={
            "anthropic": int(concurrency["anthropic_max_in_flight"]),
            "openrouter": int(concurrency["openrouter_max_in_flight"]),
        },
    )
    lock = json.loads(
        (run_dir / "provider_lock.json").read_text(encoding="utf-8")
    )
    os.environ["STAGE2_2_OPENROUTER_PROVIDER_LOCK"] = json.dumps(
        {
            method: entry["provider"]
            for method, entry in (lock.get("methods") or {}).items()
            if entry.get("provider")
        }
    )
    os.environ["STAGE2_2_RETRIEVAL_TOP_K_PER_GROUP"] = str(
        plan["retrieval"]["top_k_per_group"]
    )
    os.environ["STAGE2_2_RETRIEVAL_MAX_EVIDENCE"] = str(
        plan["retrieval"]["max_evidence"]
    )
    os.environ["STAGE2_2_REQUEST_TIMEOUT_SECONDS"] = str(
        plan["request_timeout_seconds"]
    )

    def run_one(task: tuple[str, str]) -> str:
        method, trajectory = task
        complete, output = _attempt_path(run_dir, method, trajectory)
        if complete is not None:
            return str(complete)
        run_method(
            paths,
            method_id=method,
            items=by_trajectory[trajectory],
            output=output,
            mock=False,
            query_concurrency=int(concurrency["checkpoint_workers"]),
            reasoning_policy="deployment_realistic_low",
            parse_retries=int(plan["parse_retries"]),
            prompt_artifact_root=run_dir / "prompts",
        )
        return str(output)

    outer_workers = min(
        len(tasks),
        int(concurrency["model_workers"])
        * int(concurrency["trajectory_workers"]),
    )
    with ThreadPoolExecutor(max_workers=outer_workers) as executor:
        list(executor.map(run_one, tasks))


def _complete_prediction_paths(run_dir: Path) -> list[Path]:
    complete = []
    for path in sorted((run_dir / "raw").glob("*/*/attempt_*.jsonl")):
        manifest = path.with_suffix(".manifest.json")
        if not manifest.exists():
            continue
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("status") == "COMPLETE":
            complete.append(path)
    latest: dict[tuple[str, str], Path] = {}
    for path in complete:
        latest[(path.parents[1].name, path.parent.name)] = path
    return sorted(latest.values())


def _validate_complete_grid(
    run_dir: Path, plan: dict[str, Any]
) -> list[Path]:
    paths = _complete_prediction_paths(run_dir)
    rows = [row for path in paths for row in read_jsonl(path)]
    keys = [
        (
            str(row["method_id"]),
            str(row["trajectory_id"]),
            int(row["query_checkpoint"]),
        )
        for row in rows
    ]
    if len(rows) != int(plan["prediction_count"]):
        raise RuntimeError(
            f"prediction grid incomplete: {len(rows)} != "
            f"{plan['prediction_count']}"
        )
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate method/trajectory/checkpoint prediction")
    first_attempts = sum(bool(row.get("attempts")) for row in rows)
    if first_attempts != len(rows):
        raise RuntimeError("one or more predictions lack a preserved first attempt")
    for row in rows:
        path_count = len(
            (row.get("metrics") or {}).get("path_outcomes") or {}
        )
        if path_count != 34:
            raise RuntimeError(
                f"{row.get('item_id')}: expected 34 path outcomes, "
                f"found {path_count}"
            )
    return paths


def _publish_stable_raw_paths(prediction_paths: list[Path]) -> None:
    for attempt_path in prediction_paths:
        method_dir = attempt_path.parents[1]
        trajectory = attempt_path.parent.name
        stable = method_dir / f"{trajectory}.jsonl"
        stable.write_bytes(attempt_path.read_bytes())
        attempt_manifest = attempt_path.with_suffix(".manifest.json")
        if attempt_manifest.exists():
            stable.with_suffix(".manifest.json").write_bytes(
                attempt_manifest.read_bytes()
            )


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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]) if rows else ["method_id"]
        )
        writer.writeheader()
        writer.writerows(rows)


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
        parse_rows.append(
            {
                "method_id": row["method_id"],
                "trajectory_id": row["trajectory_id"],
                "checkpoint": row["query_checkpoint"],
                "first_attempt_parse_error": row.get(
                    "first_attempt_parse_error"
                ),
                "first_attempt_schema_failure": row.get(
                    "first_attempt_schema_failure"
                ),
                "final_parse_error": bool(row.get("parse_error")),
                "final_schema_failure": row.get(
                    "final_schema_failure"
                ),
                "retry_count": row.get("retry_count", 0),
            }
        )
        metadata = row.get("response_metadata") or {}
        usage = metadata.get("usage") or {}
        input_tokens = usage.get(
            "input_tokens", usage.get("prompt_tokens")
        )
        output_tokens = usage.get(
            "output_tokens", usage.get("completion_tokens")
        )
        memory_usage = metadata.get("memory_inference_usage") or []
        memory_input_tokens = sum(
            int(record.get("input_tokens") or 0)
            for record in memory_usage
        )
        memory_output_tokens = sum(
            int(record.get("output_tokens") or 0)
            for record in memory_usage
        )
        price = (
            (
                (provider_lock.get("methods") or {})
                .get(str(row["method_id"]), {})
                .get("price")
            )
            or {}
        )
        estimated_cost = None
        if (
            input_tokens is not None
            and output_tokens is not None
            and price.get("prompt_usd_per_token") is not None
            and price.get("completion_usd_per_token") is not None
        ):
            estimated_cost = (
                float(input_tokens)
                * float(price["prompt_usd_per_token"])
                + float(output_tokens)
                * float(price["completion_usd_per_token"])
            )
        cost_rows.append(
            {
                "method_id": row["method_id"],
                "trajectory_id": row["trajectory_id"],
                "checkpoint": row["query_checkpoint"],
                "latency_seconds": metadata.get("latency_seconds"),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "memory_inference_input_tokens": (
                    memory_input_tokens if memory_usage else None
                ),
                "memory_inference_output_tokens": (
                    memory_output_tokens if memory_usage else None
                ),
                "memory_inference_usage_scope": (
                    "cumulative_to_checkpoint"
                    if memory_usage
                    else None
                ),
                "estimated_cost_usd": estimated_cost,
                "routed_provider": metadata.get("routed_provider"),
                "embedding_document_calls": metadata.get(
                    "embedding_document_calls"
                ),
                "embedding_query_calls": metadata.get(
                    "embedding_query_calls"
                ),
                "memory_inference_calls": metadata.get(
                    "memory_inference_calls"
                ),
                "memory_search_calls": metadata.get("memory_search_calls"),
                "letta_tool_calls": metadata.get(
                    "observed_search_calls"
                ),
                "component_call_scope": (
                    "cumulative_ingestion_to_checkpoint; "
                    "query/search counts are per checkpoint"
                ),
            }
        )
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
    run_dir = _resolve_run_dir(paths, args.run_dir)
    prediction_paths = _complete_prediction_paths(run_dir)
    if not prediction_paths:
        raise RuntimeError("no complete prediction artifacts to report")
    _publish_stable_raw_paths(prediction_paths)
    report = summarize_predictions(
        paths, prediction_paths, allow_partial=True
    )
    write_json(run_dir / "metrics" / "metrics.json", report)
    prepared_root = Path(
        active_stage2_2_prepared_manifest(paths)["root"]
    )
    baseline_path = prepared_root / "baselines" / "initial_copy.json"
    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.exists()
        else None
    )
    if baseline is not None:
        write_json(
            run_dir / "metrics" / "initial_copy_baseline.json",
            baseline,
        )
    write_tables(report, run_dir / "metrics")
    _write_auxiliary_metrics(run_dir, prediction_paths, report)
    _materialize_state_pairs(paths, run_dir, prediction_paths)
    (run_dir / "report" / "figures").mkdir(parents=True, exist_ok=True)
    lines = [
        "# Stage 2.2 Reconstruction — 9-Method Comparison",
        "",
        "This report uses checkpoint-then-trajectory macro aggregation. "
        "Path metrics first aggregate 20 checkpoints within each "
        "path × trajectory unit, then average trajectories with equal weight.",
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


def command_execute(args: argparse.Namespace) -> None:
    paths = ExperimentPaths.discover()
    run_dir = _resolve_run_dir(paths, args.run_dir)
    plan = _load_verified_plan(run_dir, args)
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
    plan.add_argument("--openrouter-max-in-flight", type=int, default=40)
    plan.add_argument("--retrieval-top-k-per-group", type=int, default=5)
    plan.add_argument("--retrieval-max-evidence", type=int, default=20)
    plan.add_argument("--request-timeout-seconds", type=int, default=300)
    plan.add_argument("--provider-retries", type=int, default=0)
    plan.add_argument("--parse-retries", type=int, default=1)
    plan.add_argument("--budget-cap-usd", type=float, required=True)
    plan.add_argument("--estimated-usd", type=float)
    plan.add_argument("--provider-lock-file")
    plan.set_defaults(handler=command_plan)

    show = commands.add_parser("show-prompt")
    show.add_argument("--method", required=True, choices=DEFAULT_METHODS)
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
