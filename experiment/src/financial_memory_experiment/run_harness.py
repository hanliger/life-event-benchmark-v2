"""Stage-agnostic pieces of the paid grid runners.

Stage 2.2 and both Stage 1 execution profiles share immutable plans, provider
locks where applicable, per-method/trajectory attempt artifacts, and a
resumable grid. Only item selection, prompt auditing, and reporting differ, so
those stay in the stage runners.
"""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .evaluator import run_method
from .methods.readers import configure_generation_limits
from .paths import ExperimentPaths
from .util import read_jsonl, sha256_file, sha256_json, write_json


NINE_METHODS = (
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
OPENROUTER_MODEL_IDS = {
    "fc_openrouter_llama_4_maverick": "meta-llama/llama-4-maverick",
    "fc_openrouter_gpt_oss_120b": "openai/gpt-oss-120b",
    "fc_openrouter_qwen_3_5_122b_a10b": "qwen/qwen3.5-122b-a10b",
    "fc_openrouter_qwen_3_6_35b_a3b_fp8": "qwen/qwen3.6-35b-a3b",
}
OPENROUTER_METHODS = tuple(
    method for method in NINE_METHODS if method.startswith("fc_openrouter_")
)
ANTHROPIC_METHODS = tuple(
    method for method in NINE_METHODS if method not in OPENROUTER_METHODS
)
SNAPSHOT_METHODS = (
    "bm25_claude_opus_4_8",
    "dense_ge2_claude_opus_4_8",
    "mem0_claude_opus_4_8",
    "letta_claude_opus_4_8",
)
ALL_TRAJECTORIES = tuple(f"traj_{index:03d}" for index in range(1, 21))


def parse_selection(
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


def new_run_dir(paths: ExperimentPaths, task: str) -> Path:
    parent = paths.runs / task
    parent.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%m%d_%H%M")
    candidate = parent / stem
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{stem}_{suffix:02d}"
        suffix += 1
    candidate.mkdir()
    return candidate


def latest_run_dir(paths: ExperimentPaths, task: str) -> Path:
    parent = paths.runs / task
    candidates = sorted(
        path
        for path in parent.glob("*")
        if (path / "immutable_plan.json").exists()
    )
    if not candidates:
        raise FileNotFoundError(f"no {task} run plan exists")
    return candidates[-1]


def resolve_run_dir(
    paths: ExperimentPaths, task: str, value: str | None
) -> Path:
    return Path(value).resolve() if value else latest_run_dir(paths, task)


def execution_tree_sha256(paths: ExperimentPaths) -> str:
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


# OpenRouter reports endpoint throughput as a percentile object
# (`throughput_last_30m: {p50, p75, p90, p99}`), not a scalar. Rank on the
# median: p99 is a tail-optimistic number and would pick bursty providers.
THROUGHPUT_PERCENTILE = "p50"


def _reported_throughput(endpoint: dict[str, Any]) -> float | None:
    for key, value in endpoint.items():
        if "throughput" not in str(key).lower():
            continue
        if isinstance(value, dict):
            percentile = value.get(THROUGHPUT_PERCENTILE)
            if percentile is not None:
                return float(percentile)
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _endpoint_provider(endpoint: dict[str, Any]) -> str:
    provider = (
        endpoint.get("provider_slug")
        or endpoint.get("provider_name")
        or endpoint.get("provider")
        or (endpoint.get("provider_info") or {}).get("slug")
        or (endpoint.get("provider_info") or {}).get("name")
    )
    return str(provider or "")


def auto_provider_lock(
    methods: Iterable[str], *, schema_version: str
) -> dict[str, Any]:
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
    zdr_rows = [row for row in zdr_rows_raw if isinstance(row, dict)]
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
            # OpenRouter's endpoint payloads carry no id, so `endpoint_id` is ""
            # and an unguarded membership test matched every endpoint against
            # the {""} id set -- silently passing non-ZDR providers. Require a
            # real id before trusting the id-based match.
            is_zdr = bool(
                endpoint.get("zdr")
                or endpoint.get("is_zdr")
                or (endpoint_id and endpoint_id in zdr_ids)
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
            throughput = _reported_throughput(endpoint)
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
                    "throughput_percentile": THROUGHPUT_PERCENTILE,
                    "endpoint_status": endpoint.get("status"),
                    "uptime_last_30m": endpoint.get("uptime_last_30m"),
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
        "schema_version": schema_version,
        "status": "LOCKED",
        "selection_policy": (
            "highest reported throughput among ZDR-compatible endpoints, "
            f"ranked on throughput_last_30m.{THROUGHPUT_PERCENTILE}; "
            "Qwen3.6 additionally requires FP8"
        ),
        "resolved_at_kst": datetime.now(
            ZoneInfo("Asia/Seoul")
        ).isoformat(),
        "methods": result,
        "source_response_sha256": source_hashes,
    }


def read_provider_lock(
    path: str | None,
    selected_methods: Iterable[str],
    *,
    schema_version: str,
) -> dict[str, Any]:
    selected_openrouter = [
        method for method in selected_methods if method in OPENROUTER_METHODS
    ]
    if not selected_openrouter:
        return {"status": "NOT_APPLICABLE", "methods": {}}
    if not path:
        return auto_provider_lock(
            selected_openrouter, schema_version=schema_version
        )
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


def load_verified_plan(
    run_dir: Path,
    args: argparse.Namespace,
    *,
    approval_phrase: str,
) -> dict[str, Any]:
    if not args.execute_paid:
        raise RuntimeError("--execute-paid is required")
    if args.approval != approval_phrase:
        raise RuntimeError(f"--approval must exactly equal {approval_phrase}")
    path = run_dir / "immutable_plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    claimed = str(plan.pop("plan_sha256"))
    if sha256_json(plan) != claimed:
        raise RuntimeError("immutable plan SHA mismatch")
    plan["plan_sha256"] = claimed
    if float(plan["estimated_usd"]) > float(plan["budget_cap_usd"]):
        raise RuntimeError("plan estimate exceeds budget cap")
    if plan.get("execution_tree_sha256") != execution_tree_sha256(
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


def preflight_paid(plan: dict[str, Any]) -> None:
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
    if "fc_gpt_5_6_sol" in methods and not os.environ.get("OPENAI_API_KEY"):
        missing_keys.append("OPENAI_API_KEY")
    if "fc_gemini_3_1_pro" in methods and not (
        os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
    ):
        missing_keys.append("GOOGLE_API_KEY or GEMINI_API_KEY")
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
        "fc_gpt_5_6_sol": "openai",
        "fc_gemini_3_1_pro": "google.genai",
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


def load_approved_environment(paths: ExperimentPaths) -> None:
    """Load secrets only after the immutable plan is approved and verified."""

    load_dotenv(paths.root / ".env")
    load_dotenv(paths.repo_root / ".env")
    if not os.environ.get("GOOGLE_API_KEY") and os.environ.get(
        "GEMINI_API_KEY"
    ):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]


def attempt_path(
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


def run_grid(
    paths: ExperimentPaths,
    run_dir: Path,
    plan: dict[str, Any],
    *,
    items_by_id: dict[str, dict[str, Any]],
    top_k: int | None = None,
    reasoning_policy: str = "deployment_realistic_low",
    retrieval_env: dict[str, str] | None = None,
) -> None:
    selected = [items_by_id[item_id] for item_id in plan["item_ids"]]
    by_trajectory = {
        trajectory: [
            item
            for item in selected
            if str(item["trajectory_id"]) == trajectory
        ]
        for trajectory in plan["trajectories"]
    }
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
    os.environ["FIN_MEMORY_OPENROUTER_PROVIDER_LOCK"] = json.dumps(
        {
            method: entry["provider"]
            for method, entry in (lock.get("methods") or {}).items()
            if entry.get("provider")
        }
    )
    os.environ["FIN_MEMORY_REQUEST_TIMEOUT_SECONDS"] = str(
        plan["request_timeout_seconds"]
    )
    for name, value in (retrieval_env or {}).items():
        os.environ[name] = value

    def run_one(task: tuple[str, str]) -> str:
        method, trajectory = task
        complete, output = attempt_path(run_dir, method, trajectory)
        if complete is not None:
            return str(complete)
        run_method(
            paths,
            method_id=method,
            items=by_trajectory[trajectory],
            output=output,
            mock=False,
            top_k=top_k,
            query_concurrency=int(concurrency["checkpoint_workers"]),
            reasoning_policy=reasoning_policy,
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


def complete_prediction_paths(run_dir: Path) -> list[Path]:
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


def publish_stable_raw_paths(prediction_paths: list[Path]) -> None:
    for attempt in prediction_paths:
        method_dir = attempt.parents[1]
        trajectory = attempt.parent.name
        stable = method_dir / f"{trajectory}.jsonl"
        stable.write_bytes(attempt.read_bytes())
        attempt_manifest = attempt.with_suffix(".manifest.json")
        if attempt_manifest.exists():
            stable.with_suffix(".manifest.json").write_bytes(
                attempt_manifest.read_bytes()
            )


def validate_grid_shape(run_dir: Path, plan: dict[str, Any]) -> list[Path]:
    """Assert the frozen grid is complete, duplicate-free, and attempt-traced."""

    paths = complete_prediction_paths(run_dir)
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
    if sum(bool(row.get("attempts")) for row in rows) != len(rows):
        raise RuntimeError(
            "one or more predictions lack a preserved first attempt"
        )
    return paths


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]) if rows else ["method_id"]
        )
        writer.writeheader()
        writer.writerows(rows)


def parse_reliability_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "method_id": row["method_id"],
        "trajectory_id": row["trajectory_id"],
        "checkpoint": row["query_checkpoint"],
        "first_attempt_parse_error": row.get("first_attempt_parse_error"),
        "first_attempt_schema_failure": row.get(
            "first_attempt_schema_failure"
        ),
        "final_parse_error": bool(row.get("parse_error")),
        "final_schema_failure": row.get("final_schema_failure"),
        "retry_count": row.get("retry_count", 0),
    }


def cost_latency_row(
    row: dict[str, Any], provider_lock: dict[str, Any]
) -> dict[str, Any]:
    metadata = row.get("response_metadata") or {}
    usage = metadata.get("usage") or {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    memory_usage = metadata.get("memory_inference_usage") or []
    price = (
        (provider_lock.get("methods") or {})
        .get(str(row["method_id"]), {})
        .get("price")
    ) or {
        "fc_gpt_5_6_sol": {
            "prompt_usd_per_token": 5 / 1_000_000,
            "completion_usd_per_token": 30 / 1_000_000,
        },
        "fc_claude_opus_4_8": {
            "prompt_usd_per_token": 5 / 1_000_000,
            "completion_usd_per_token": 25 / 1_000_000,
        },
        "fc_gemini_3_1_pro": {
            "prompt_usd_per_token": 2 / 1_000_000,
            "completion_usd_per_token": 12 / 1_000_000,
        },
    }.get(str(row["method_id"]), {})
    estimated_cost = None
    if (
        input_tokens is not None
        and output_tokens is not None
        and price.get("prompt_usd_per_token") is not None
        and price.get("completion_usd_per_token") is not None
    ):
        estimated_cost = float(input_tokens) * float(
            price["prompt_usd_per_token"]
        ) + float(output_tokens) * float(price["completion_usd_per_token"])
    return {
        "method_id": row["method_id"],
        "trajectory_id": row["trajectory_id"],
        "checkpoint": row["query_checkpoint"],
        "latency_seconds": metadata.get("latency_seconds"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "memory_inference_input_tokens": (
            sum(int(record.get("input_tokens") or 0) for record in memory_usage)
            if memory_usage
            else None
        ),
        "memory_inference_output_tokens": (
            sum(
                int(record.get("output_tokens") or 0)
                for record in memory_usage
            )
            if memory_usage
            else None
        ),
        "memory_inference_usage_scope": (
            "cumulative_to_checkpoint" if memory_usage else None
        ),
        "estimated_cost_usd": estimated_cost,
        "routed_provider": metadata.get("routed_provider"),
        "embedding_document_calls": metadata.get("embedding_document_calls"),
        "embedding_query_calls": metadata.get("embedding_query_calls"),
        "memory_inference_calls": metadata.get("memory_inference_calls"),
        "memory_search_calls": metadata.get("memory_search_calls"),
        "letta_tool_calls": metadata.get("observed_search_calls"),
        "component_call_scope": (
            "cumulative_ingestion_to_checkpoint; "
            "query/search counts are per checkpoint"
        ),
    }


COLORBLIND_SAFE_COLORS = (
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


def line_chart_svg(
    *,
    title: str,
    x_values: list[int],
    series: dict[str, dict[int, float]],
) -> str:
    """Series-per-method curve over an ordered numeric axis, 0…1 on y."""

    width, height = 920, 520
    left, top, plot_w, plot_h = 75, 40, 600, 400
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="24" font-family="sans-serif" '
        f'font-size="16">{html.escape(title)}</text>',
    ]
    for tick in range(0, 11, 2):
        value = tick / 10
        y = top + plot_h * (1 - value)
        svg.extend(
            [
                f'<line x1="{left}" y1="{y:.1f}" '
                f'x2="{left + plot_w}" y2="{y:.1f}" stroke="#dddddd"/>',
                f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" '
                f'font-family="sans-serif" font-size="11">{value:.1f}</text>',
            ]
        )
    span = max(1, (x_values[-1] - x_values[0]) if x_values else 1)
    x_for = {
        value: left + plot_w * ((value - x_values[0]) / span)
        for value in x_values
    }
    for value in x_values:
        svg.append(
            f'<text x="{x_for[value]:.1f}" y="{top + plot_h + 20}" '
            'text-anchor="middle" font-family="sans-serif" '
            f'font-size="10">{value}</text>'
        )
    for index, name in enumerate(sorted(series)):
        points = [
            (x_for[value], top + plot_h * (1 - series[name][value]))
            for value in x_values
            if value in series[name]
        ]
        color = COLORBLIND_SAFE_COLORS[index % len(COLORBLIND_SAFE_COLORS)]
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
                f'<line x1="700" y1="{legend_y}" x2="724" y2="{legend_y}" '
                f'stroke="{color}" stroke-width="3"/>',
                f'<text x="732" y="{legend_y + 4}" font-family="sans-serif" '
                f'font-size="10">{html.escape(name)}</text>',
            ]
        )
    svg.append("</svg>")
    return "\n".join(svg) + "\n"


def heatmap_svg(
    *,
    title: str,
    columns: list[str],
    rows: list[str],
    values: dict[tuple[str, str], float | None],
) -> str:
    cell_w, cell_h = 74, 22
    left, top = 310, 170
    width = left + len(columns) * cell_w + 30
    height = top + len(rows) * cell_h + 30
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="20" y="28" font-family="sans-serif" font-size="16">'
        f"{html.escape(title)}</text>",
    ]
    for column, name in enumerate(columns):
        x = left + column * cell_w + cell_w / 2
        svg.append(
            f'<text x="{x}" y="{top - 8}" text-anchor="end" '
            f'transform="rotate(-55 {x} {top - 8})" '
            'font-family="sans-serif" font-size="9">'
            f"{html.escape(name)}</text>"
        )
    for row_index, row_name in enumerate(rows):
        y = top + row_index * cell_h
        svg.append(
            f'<text x="{left - 8}" y="{y + 15}" text-anchor="end" '
            'font-family="sans-serif" font-size="10">'
            f"{html.escape(row_name)}</text>"
        )
        for column, name in enumerate(columns):
            value = values.get((name, row_name))
            shade = 235 if value is None else int(245 - 185 * float(value))
            color = (
                f"rgb({shade},{shade},{255})" if value is not None else "#eeeeee"
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
    return "\n".join(svg) + "\n"


def write_run_manifest(
    run_dir: Path,
    *,
    schema_version: str,
    plan: dict[str, Any],
    provider_lock: dict[str, Any],
    prepared_manifest_path: Path,
) -> None:
    write_json(
        run_dir / "run_manifest.json",
        {
            "schema_version": schema_version,
            "status": "PLANNED",
            "run_id": run_dir.name,
            "plan_sha256": plan["plan_sha256"],
            "provider_lock_sha256": sha256_json(provider_lock),
            "prepared_manifest_sha256": sha256_file(prepared_manifest_path),
        },
    )
