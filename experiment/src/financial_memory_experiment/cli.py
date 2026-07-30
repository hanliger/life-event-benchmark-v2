from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .audit import branching_ingestion_audit
from .config import load_experiment_config
from .data_pipeline import (
    active_prepared_manifest,
    download_data,
    prepare_data,
    validate_raw_data,
)
from .evaluator import load_items, run_method
from .items import (
    build_canonical_items,
    build_prefix_gold_artifact,
    validate_canonical_items,
)
from .masking import build_masking_items, validate_masking_items
from .methods import method_ids
from .methods.full_context import FullContextMethod
from .methods.mem0_adapter import InMemoryMem0Double, Mem0Method
from .methods.readers import MockReader
from .methods.retrieval import BM25Method, DenseMethod, HashEmbedder, regex_tokenize
from .metrics import summarize_predictions, write_tables
from .paths import ExperimentPaths
from .safety import (
    APPROVAL_PHRASE,
    FULL_APPROVAL_PHRASE,
    build_full_plan,
    build_smoke_plan,
    load_verified_full_plan,
    load_verified_smoke_plan,
    reserve_smoke_budget,
)
from .stage2_2 import (
    download_stage2_2_data,
    prepare_stage2_2_data,
    stage2_2_item_path,
    validate_stage2_2_prepared,
    validate_stage2_2_raw_data,
    write_stage2_2_initial_copy_report,
)
from .util import read_jsonl, sha256_file, sha256_json, write_json


def _canonical_item_paths(paths: ExperimentPaths) -> list[Path]:
    root = Path(active_prepared_manifest(paths)["root"])
    return [
        root / "canonical_items" / "stage1_event_identification.jsonl",
        root / "canonical_items" / "stage2_memory_value.jsonl",
        root / "canonical_items" / "stage3_multi_hop_mcq.jsonl",
    ]


def _all_item_paths(paths: ExperimentPaths) -> list[Path]:
    root = Path(active_prepared_manifest(paths)["root"])
    result = _canonical_item_paths(paths) + [
        root / "masking_items" / "masking_questions.jsonl"
    ]
    try:
        result.append(stage2_2_item_path(paths))
    except FileNotFoundError:
        pass
    return result


def _selected_items(paths: ExperimentPaths, item_ids: list[str]) -> list[dict[str, Any]]:
    wanted = set(item_ids)
    rows = [row for path in _all_item_paths(paths) if path.exists() for row in read_jsonl(path)]
    selected = [row for row in rows if str(row["item_id"]) in wanted]
    missing = wanted - {str(row["item_id"]) for row in selected}
    if missing:
        raise ValueError(f"unknown item IDs: {sorted(missing)}")
    return selected


def _operation_limits(items: list[dict[str, Any]], methods: list[str]) -> dict[str, int]:
    canonical = [
        item for item in items if not str(item["stage"]).startswith("masking_")
    ]
    masking = [
        item for item in items if str(item["stage"]).startswith("masking_")
    ]
    canonical_by_trajectory: dict[str, int] = {}
    for item in canonical:
        trajectory_id = str(item["trajectory_id"])
        checkpoint = int(
            (item.get("metadata") or {}).get("query_checkpoint")
            or len(item.get("visible_sessions") or [])
        )
        canonical_by_trajectory[trajectory_id] = max(
            checkpoint, canonical_by_trajectory.get(trajectory_id, 0)
        )
    canonical_ingests = sum(canonical_by_trajectory.values()) + len(
        canonical_by_trajectory
    )

    class Node:
        def __init__(self) -> None:
            self.children: dict[str, "Node"] = {}

    masking_edges = 0
    masking_trajectories = 0
    masking_letta_passage_ingests = 0
    by_trajectory_files: dict[str, set[str]] = {}
    for item in masking:
        by_trajectory_files.setdefault(str(item["trajectory_id"]), set()).add(
            str((item.get("metadata") or {})["variant_sessions_file"])
        )
    for files in by_trajectory_files.values():
        masking_trajectories += 1
        root = Node()
        for filename in files:
            node = root
            for session in read_jsonl(Path(filename)):
                key = sha256_json(session)
                node = node.children.setdefault(key, Node())

        def count_edges(node: Node) -> int:
            return sum(
                1 + count_edges(child) for child in node.children.values()
            )

        masking_edges += count_edges(root)
        # Letta Agent File does not serialize archival passages. Replaying each
        # frozen variant is cheaper than cloning and re-embedding every prefix.
        masking_letta_passage_ingests += len(files) + sum(
            sum(1 for _ in read_jsonl(Path(filename))) for filename in files
        )

    state_ingests = canonical_ingests + masking_edges + masking_trajectories
    limits = {
        "answer_requests": len(items) * len(methods),
        "canonical_state_ingests_per_stateful_method": canonical_ingests,
        "masking_state_ingests_per_stateful_method": (
            masking_edges + masking_trajectories
        ),
    }
    if any(method.startswith("dense_") for method in methods):
        limits["embedding_documents"] = state_ingests
        limits["embedding_queries"] = len(items)
    if any(method.startswith("mem0_") for method in methods):
        limits["mem0_ingest_calls"] = state_ingests
        limits["mem0_search_calls"] = len(items)
    if any(method.startswith("letta_") for method in methods):
        limits["letta_passage_ingest_calls"] = (
            canonical_ingests + masking_letta_passage_ingests
        )
        limits["letta_embedding_documents"] = (
            canonical_ingests + masking_letta_passage_ingests
        )
        limits["letta_ingest_agent_steps"] = 0
        limits["letta_query_agent_steps"] = len(items) * 4
    return limits


def _load_approved_environment(paths: ExperimentPaths) -> None:
    """Load secrets only after the immutable plan and exact approval are verified."""

    load_dotenv(paths.root / ".env")
    load_dotenv(paths.repo_root / ".env")
    if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]


def _preflight_paid(methods: list[str]) -> None:
    missing_keys: list[str] = []
    if "fc_claude_opus_5" in methods and not os.environ.get("ANTHROPIC_API_KEY"):
        missing_keys.append("ANTHROPIC_API_KEY")
    if {
        "fc_gpt_5_6_sol",
        "oracle_rel_gpt_5_6_sol",
    } & set(methods) and not os.environ.get("OPENAI_API_KEY"):
        missing_keys.append("OPENAI_API_KEY")
    google_methods = {
        "fc_gemini_3_1_pro",
        "bm25_gemini_3_1_pro",
        "dense_ge2_gemini_3_1_pro",
        "mem0_gemini_3_1_pro",
        "letta_gemini_3_1_pro",
    }
    if google_methods & set(methods) and not (
        os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    ):
        missing_keys.append("GOOGLE_API_KEY or GEMINI_API_KEY")
    if missing_keys:
        raise RuntimeError(
            "paid preflight failed before provider construction; missing: "
            + ", ".join(missing_keys)
        )

    required_modules = {
        "fc_claude_opus_5": "anthropic",
        "fc_gpt_5_6_sol": "openai",
        "oracle_rel_gpt_5_6_sol": "openai",
        "fc_gemini_3_1_pro": "google.genai",
        "bm25_gemini_3_1_pro": "kiwipiepy",
        "dense_ge2_gemini_3_1_pro": "google.genai",
        "mem0_gemini_3_1_pro": "mem0",
        "letta_gemini_3_1_pro": "letta_client",
    }
    missing_modules: list[str] = []
    for method in methods:
        module = required_modules.get(method)
        if not module:
            continue
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError):
            available = False
        if not available:
            missing_modules.append(module)
    missing_modules = sorted(set(missing_modules))
    if missing_modules:
        raise RuntimeError(
            "paid preflight failed before provider construction; install missing modules: "
            + ", ".join(missing_modules)
        )

    if "letta_gemini_3_1_pro" in methods:
        try:
            with urllib.request.urlopen(
                "http://localhost:8283/v1/health", timeout=3
            ) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Letta health returned HTTP {response.status}")
        except Exception as exc:
            raise RuntimeError(
                "paid preflight failed before provider construction; "
                "Letta is not healthy at http://localhost:8283"
            ) from exc


def _write_environment_snapshot(paths: ExperimentPaths, output_dir: Path) -> None:
    freeze = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    output_dir.mkdir(parents=True, exist_ok=True)
    freeze_path = output_dir / "pip_freeze.txt"
    freeze_path.write_text(freeze, encoding="utf-8")
    docker_images: dict[str, dict[str, Any] | None] = {}
    for image_name in (
        "financial-memory-letta:0.16.8-googlecompat1",
        "letta/letta:0.16.8",
    ):
        try:
            inspected = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    image_name,
                    "--format",
                    "{{json .}}",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            docker_images[image_name] = json.loads(inspected)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
            docker_images[image_name] = None
    cfg = load_experiment_config(paths)
    write_json(
        output_dir / "environment.json",
        {
            "python": sys.version,
            "executable": sys.executable,
            "pip_freeze_sha256": sha256_file(freeze_path),
            "docker_images": docker_images,
            "models": cfg["models"],
        },
    )


def _dry_run(paths: ExperimentPaths) -> dict[str, Any]:
    session = {
        "trajectory_id": "traj_dry",
        "session_id": "S001",
        "session_date": "2026-01-01",
        "turns": [
            {"speaker": "user", "text": "새 직장에 입사했습니다."},
            {"speaker": "assistant", "text": "입사를 반영하겠습니다."},
        ],
    }
    s000 = {
        "trajectory_id": "traj_dry",
        "session_id": "S000",
        "session_date": "2025-12-31",
        "state": {},
    }
    item = {
        "item_id": "dry-stage2",
        "stage": "stage2_memory_value",
        "trajectory_id": "traj_dry",
        "question": "직장은?",
        "options": [
            {"option_id": option, "text": option, "correct": option == "A"}
            for option in "ABCD"
        ],
        "gold": {"correct_option": "A"},
        "metadata": {"query_checkpoint": 1, "answer_type": "mcq"},
    }
    system = "테스트"
    reader = MockReader()
    methods = [
        FullContextMethod("fc_mock", reader, system),
        BM25Method(reader, system, k=1, k1=1.5, b=0.75, tokenizer=regex_tokenize),
        DenseMethod(reader, system, HashEmbedder(), k=1),
        Mem0Method(InMemoryMem0Double, reader, system, trajectory_id="traj_dry", k=1),
    ]
    results: dict[str, Any] = {}
    for method in methods:
        method.ingest_initial(s000)
        method.ingest_session(session)
        before = method.state_fingerprint()
        answer = method.answer(item)
        results[method.method_id] = {
            "raw_answer": answer.raw_answer,
            "read_only": before == method.state_fingerprint(),
        }
    configured = method_ids(paths)
    if len(configured) != 8 or len(set(configured)) != 8:
        raise ValueError(
            "exactly eight unique core-plus-analysis methods must be configured"
        )
    return {
        "decision": "PASS",
        "configured_methods": configured,
        "component_results": results,
        "letta_contract": "official SDK adapter is importable; server not started",
        "branching_audit": branching_ingestion_audit(),
        "paid_calls": 0,
        "letta_docker_starts": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Financial memory benchmark experiment harness"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    download = sub.add_parser("download-data")
    download.add_argument("--source-dir", type=Path)
    download.add_argument("--revision")
    download_stage2_2 = sub.add_parser("download-stage2-2-data")
    download_stage2_2.add_argument("--revision")
    for name in (
        "validate-raw-data",
        "prepare-data",
        "validate-stage2-2-raw",
        "prepare-stage2-2",
        "validate-stage2-2-prepared",
        "stage2-2-initial-copy",
        "build-prefix-gold",
        "build-canonical-items",
        "build-masking-items",
        "validate-prepared-data",
        "dry-run",
    ):
        sub.add_parser(name)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--method", required=True)
    evaluate.add_argument("--items", type=Path, nargs="+")
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--top-k", type=int)
    evaluate.add_argument("--max-items", type=int)
    evaluate.add_argument("--mock", action="store_true")
    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("--predictions", type=Path, nargs="+", required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    aggregate.add_argument(
        "--expected-scope", choices=("canonical", "masking", "all")
    )
    aggregate.add_argument("--allow-partial", action="store_true")
    plan = sub.add_parser("plan-paid-smoke")
    plan.add_argument("--method", action="append", required=True)
    plan.add_argument("--item-id", action="append", required=True)
    plan.add_argument("--estimated-usd", type=float, required=True)
    execute = sub.add_parser("execute-paid-smoke")
    execute.add_argument("--plan-sha", required=True)
    execute.add_argument("--approval", required=True)
    execute.add_argument("--execute-paid", action="store_true")
    plan_full = sub.add_parser("plan-paid-full")
    plan_full.add_argument("--method", action="append", required=True)
    plan_full.add_argument(
        "--scope", choices=("canonical", "masking", "all"), required=True
    )
    plan_full.add_argument("--estimated-usd", type=float, required=True)
    execute_full = sub.add_parser("execute-paid-full")
    execute_full.add_argument("--plan-sha", required=True)
    execute_full.add_argument("--approval", required=True)
    execute_full.add_argument("--execute-paid", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = ExperimentPaths.discover()
    if args.command == "download-data":
        result: Any = download_data(paths, source_dir=args.source_dir, revision=args.revision)
    elif args.command == "download-stage2-2-data":
        result = download_stage2_2_data(paths, revision=args.revision)
    elif args.command == "validate-raw-data":
        result = validate_raw_data(paths)
    elif args.command == "validate-stage2-2-raw":
        result = validate_stage2_2_raw_data(paths)
    elif args.command == "prepare-data":
        result = prepare_data(paths)
    elif args.command == "prepare-stage2-2":
        result = prepare_stage2_2_data(paths)
    elif args.command == "validate-stage2-2-prepared":
        result = validate_stage2_2_prepared(paths)
    elif args.command == "stage2-2-initial-copy":
        result = write_stage2_2_initial_copy_report(paths)
    elif args.command == "build-prefix-gold":
        result = build_prefix_gold_artifact(paths)
    elif args.command == "build-canonical-items":
        result = build_canonical_items(paths)
    elif args.command == "build-masking-items":
        result = build_masking_items(paths)
    elif args.command == "validate-prepared-data":
        result = {
            "canonical": validate_canonical_items(paths),
            "masking": validate_masking_items(paths),
        }
    elif args.command == "dry-run":
        result = _dry_run(paths)
        write_json(paths.runs / "offline_dry_run.json", result)
    elif args.command == "evaluate":
        if not args.mock:
            raise SystemExit(
                "real evaluation is allowed only through execute-paid-smoke; use --mock here"
            )
        items = load_items(args.items or _canonical_item_paths(paths))
        if args.max_items is not None:
            if args.max_items <= 0:
                raise ValueError("--max-items must be positive")
            items = items[: args.max_items]
        result = run_method(
            paths,
            method_id=args.method,
            items=items,
            output=args.output,
            mock=True,
            top_k=args.top_k,
        )
    elif args.command == "aggregate":
        report = summarize_predictions(
            paths,
            args.predictions,
            allow_partial=args.allow_partial,
            expected_scope=args.expected_scope,
        )
        write_tables(report, args.output_dir)
        result = args.output_dir
    elif args.command == "plan-paid-smoke":
        unknown = set(args.method) - set(method_ids(paths))
        if unknown:
            raise ValueError(f"unknown methods: {sorted(unknown)}")
        selected = _selected_items(paths, args.item_id)
        result = build_smoke_plan(
            paths,
            method_ids=args.method,
            item_ids=args.item_id,
            estimated_usd=args.estimated_usd,
            operation_limits=_operation_limits(selected, args.method),
            input_items_sha256=sha256_json(selected),
        )
    elif args.command == "execute-paid-smoke":
        plan = load_verified_smoke_plan(
            paths,
            plan_sha=args.plan_sha,
            approval=args.approval,
            execute_paid=args.execute_paid,
        )
        # Keys are deliberately loaded only after all immutable-plan and approval
        # checks pass. The paid shell entrypoint is the sole route to this branch.
        selected = _selected_items(paths, list(plan["item_ids"]))
        if sha256_json(selected) != plan["input_items_sha256"]:
            raise RuntimeError(
                "frozen item payload changed after planning; refusing paid execution"
            )
        _load_approved_environment(paths)
        _preflight_paid(list(plan["method_ids"]))
        _write_environment_snapshot(
            paths, paths.runs / "paid_smoke" / plan["plan_sha256"]
        )
        reserve_smoke_budget(paths, plan)
        os.environ["FIN_MEMORY_DISABLE_PAID_APIS"] = "0"
        canonical = [
            item
            for item in selected
            if not str(item["stage"]).startswith("masking_")
        ]
        masking = [
            item for item in selected if str(item["stage"]).startswith("masking_")
        ]
        jobs: list[tuple[str, str, list[dict[str, Any]], Path]] = []
        for method_id in plan["method_ids"]:
            for label, subset in (("canonical", canonical), ("masking", masking)):
                if not subset:
                    continue
                output = (
                    paths.runs
                    / "paid_smoke"
                    / plan["plan_sha256"]
                    / f"{method_id}__{label}.jsonl"
                )
                jobs.append((method_id, label, subset, output))

        def run_paid_job(
            job: tuple[str, str, list[dict[str, Any]], Path],
        ) -> str:
            method_id, label, subset, output = job
            run_method(
                paths,
                method_id=method_id,
                items=subset,
                output=output,
                mock=False,
                query_concurrency=(
                    int(plan.get("checkpoint_concurrency", 1))
                    if label == "canonical"
                    and method_id
                    in {
                        "fc_claude_opus_5",
                        "fc_gemini_3_1_pro",
                        "fc_gpt_5_6_sol",
                    }
                    and all(
                        item.get("stage") == "stage2_2_reconstruct"
                        for item in subset
                    )
                    else 1
                ),
            )
            return str(output)

        with ThreadPoolExecutor(
            max_workers=int(plan.get("concurrency", 1))
        ) as executor:
            # map preserves the frozen plan order in the returned artifact list.
            outputs = list(executor.map(run_paid_job, jobs))
        result = {"plan_sha256": plan["plan_sha256"], "outputs": outputs}
    elif args.command == "plan-paid-full":
        unknown = set(args.method) - set(method_ids(paths))
        if unknown:
            raise ValueError(f"unknown methods: {sorted(unknown)}")
        item_paths = {
            "canonical": _canonical_item_paths(paths),
            "masking": _all_item_paths(paths)[-1:],
            "all": _all_item_paths(paths),
        }[args.scope]
        selected = load_items(item_paths)
        result = build_full_plan(
            paths,
            method_ids=args.method,
            item_ids=[str(item["item_id"]) for item in selected],
            estimated_usd=args.estimated_usd,
            operation_limits=_operation_limits(selected, args.method),
            input_items_sha256=sha256_json(selected),
        )
    elif args.command == "execute-paid-full":
        plan = load_verified_full_plan(
            paths,
            plan_sha=args.plan_sha,
            approval=args.approval,
            execute_paid=args.execute_paid,
        )
        selected = _selected_items(paths, list(plan["item_ids"]))
        if sha256_json(selected) != plan["input_items_sha256"]:
            raise RuntimeError(
                "frozen item payload changed after planning; refusing paid execution"
            )
        _load_approved_environment(paths)
        _preflight_paid(list(plan["method_ids"]))
        _write_environment_snapshot(
            paths, paths.runs / "paid_full" / plan["plan_sha256"]
        )
        os.environ["FIN_MEMORY_DISABLE_PAID_APIS"] = "0"
        canonical = [item for item in selected if not str(item["stage"]).startswith("masking_")]
        masking = [item for item in selected if str(item["stage"]).startswith("masking_")]
        outputs = []
        for method_id in plan["method_ids"]:
            for label, subset in (("canonical", canonical), ("masking", masking)):
                if not subset:
                    continue
                output = (
                    paths.runs
                    / "paid_full"
                    / plan["plan_sha256"]
                    / f"{method_id}__{label}.jsonl"
                )
                run_method(
                    paths,
                    method_id=method_id,
                    items=subset,
                    output=output,
                    mock=False,
                )
                outputs.append(str(output))
        result = {"plan_sha256": plan["plan_sha256"], "outputs": outputs}
    else:
        raise AssertionError(args.command)
    if isinstance(result, Path):
        print(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
