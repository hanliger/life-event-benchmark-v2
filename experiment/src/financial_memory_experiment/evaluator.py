from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .data_pipeline import active_prepared_manifest, assert_answer_free_record
from .methods import create_method
from .methods.base import CloneEquivalenceError
from .paths import ExperimentPaths
from .prompts import gold_answer, parse_answer
from .stage2_2 import (
    STAGE2_2,
    active_stage2_2_prepared_manifest,
    parse_stage2_2_prediction,
    score_stage2_2,
)
from .util import read_jsonl, session_number, sha256_file, sha256_json, write_json


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_provenance(paths: ExperimentPaths) -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=paths.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    try:
        return {
            "commit": run("rev-parse", "HEAD"),
            "dirty": bool(run("status", "--porcelain")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def _package_versions() -> dict[str, str | None]:
    packages = (
        "financial-memory-benchmark-experiment",
        "anthropic",
        "openai",
        "google-genai",
        "mem0ai",
        "letta-client",
        "qdrant-client",
    )
    result: dict[str, str | None] = {}
    for package in packages:
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    return result


class _RunRecorder:
    def __init__(
        self,
        paths: ExperimentPaths,
        *,
        output: Path,
        method_id: str,
        items: list[dict[str, Any]],
        mock: bool,
        top_k: int | None,
        reasoning_policy: str | None = None,
    ):
        self.output = output
        self.manifest_path = output.with_suffix(".manifest.json")
        if output.exists() or self.manifest_path.exists():
            raise FileExistsError(
                f"immutable evaluation output already exists: {output}"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.touch(exist_ok=False)
        item_ids = sorted(str(item["item_id"]) for item in items)
        prepared = (
            active_stage2_2_prepared_manifest(paths)
            if items and all(item.get("stage") == STAGE2_2 for item in items)
            else active_prepared_manifest(paths)
        )
        self.manifest: dict[str, Any] = {
            "schema_version": "evaluation-output-manifest-v2",
            "status": "RUNNING",
            "started_at": _utc_now(),
            "method_id": method_id,
            "mock": mock,
            "top_k": top_k,
            "reasoning_policy": reasoning_policy,
            "expected_items": len(item_ids),
            "completed_items": 0,
            "input_item_ids_sha256": sha256_json(item_ids),
            "input_item_ids": item_ids,
            "prepared_manifest": prepared,
            "config_sha256": {
                name: sha256_file(path)
                for name, path in {
                    "experiment": paths.configs / "experiment.yaml",
                    "methods": paths.configs / "methods.yaml",
                    "paid_safety": paths.configs / "paid_safety.yaml",
                    "system_prompt": paths.prompts / "system_ko.txt",
                }.items()
            },
            "git": _git_provenance(paths),
            "package_versions": _package_versions(),
        }
        write_json(self.manifest_path, self.manifest)

    def append(self, row: dict[str, Any]) -> None:
        with self.output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
        self.manifest["completed_items"] += 1
        write_json(self.manifest_path, self.manifest)

    def complete(self, **extra: Any) -> None:
        self.manifest.update(extra)
        self.manifest.update(
            {
                "status": "COMPLETE",
                "completed_at": _utc_now(),
                "output_sha256": sha256_file(self.output),
            }
        )
        if self.manifest["completed_items"] != self.manifest["expected_items"]:
            raise RuntimeError("completed item count differs from frozen input")
        write_json(self.manifest_path, self.manifest)

    def fail(self, exc: BaseException) -> None:
        self.manifest.update(
            {
                "status": "FAILED",
                "failed_at": _utc_now(),
                "failure": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                },
                "output_sha256": sha256_file(self.output),
            }
        )
        write_json(self.manifest_path, self.manifest)


def _checkpoint(item: dict[str, Any]) -> int:
    metadata = item.get("metadata") or {}
    return int(
        metadata.get("query_checkpoint")
        or metadata.get("checkpoint_session_count")
        or len(item.get("visible_sessions") or [])
    )


def _load_sessions(root: Path, trajectory_id: str) -> list[dict[str, Any]]:
    candidates = (
        root / "sessions_answer_free" / f"traj_{trajectory_id.removeprefix('traj_')}.jsonl",
        root / "sessions_answer_free" / f"{trajectory_id}.jsonl",
    )
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        raise FileNotFoundError(f"answer-free sessions absent for {trajectory_id}")
    rows = list(read_jsonl(path))
    for row in rows:
        assert_answer_free_record(row)
    return rows


def _load_s000(root: Path, trajectory_id: str) -> dict[str, Any]:
    path = root / "initial_state_s000" / f"{trajectory_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_items(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows = [row for path in paths for row in read_jsonl(path)]
    ids = [str(row["item_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate item IDs in evaluation input")
    return rows


def run_method(
    paths: ExperimentPaths,
    *,
    method_id: str,
    items: list[dict[str, Any]],
    output: Path,
    mock: bool,
    top_k: int | None = None,
    query_concurrency: int = 1,
    reasoning_policy: str | None = None,
) -> Path:
    if query_concurrency <= 0:
        raise ValueError("query_concurrency must be positive")
    is_masking = [str(item.get("stage", "")).startswith("masking_") for item in items]
    if any(is_masking):
        if not all(is_masking):
            raise ValueError("canonical and masking items must run in separate invocations")
        return _run_masking_method(
            paths,
            method_id=method_id,
            items=items,
            output=output,
            mock=mock,
            top_k=top_k,
        )
    recorder = _RunRecorder(
        paths,
        output=output,
        method_id=method_id,
        items=items,
        mock=mock,
        top_k=top_k,
        reasoning_policy=reasoning_policy,
    )
    is_stage2_2 = [item.get("stage") == STAGE2_2 for item in items]
    if any(is_stage2_2) and not all(is_stage2_2):
        raise ValueError("Stage 2.2 items must run in a separate invocation")
    root = Path(
        (
            active_stage2_2_prepared_manifest(paths)
            if all(is_stage2_2)
            else active_prepared_manifest(paths)
        )["root"]
    )
    by_trajectory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_trajectory[str(item["trajectory_id"])].append(item)

    try:
        if query_concurrency > 1:
            supported = {
                "fc_claude_opus_5",
                "fc_gemini_3_1_pro",
                "fc_gpt_5_6_sol",
            }
            if not all(is_stage2_2) or method_id not in supported:
                raise ValueError(
                    "parallel queries are limited to Stage 2.2 full-context methods"
                )
            tasks: list[tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
            for trajectory_id in sorted(by_trajectory):
                s000 = _load_s000(root, trajectory_id)
                sessions = _load_sessions(root, trajectory_id)
                for item in sorted(
                    by_trajectory[trajectory_id],
                    key=lambda row: (_checkpoint(row), str(row["item_id"])),
                ):
                    checkpoint = _checkpoint(item)
                    if checkpoint < 0 or checkpoint > len(sessions):
                        raise ValueError(
                            f"invalid checkpoint {trajectory_id}/{checkpoint}; "
                            f"sessions={len(sessions)}"
                        )
                    tasks.append((trajectory_id, item, s000, sessions[:checkpoint]))

            def answer_independent(
                task: tuple[
                    str,
                    dict[str, Any],
                    dict[str, Any],
                    list[dict[str, Any]],
                ]
            ) -> dict[str, Any]:
                trajectory_id, item, s000, prefix = task
                method = create_method(
                    method_id,
                    trajectory_id=trajectory_id,
                    paths=paths,
                    mock=mock,
                    top_k=top_k,
                    reasoning_policy=reasoning_policy,
                )
                try:
                    method.ingest_initial(s000)
                    for session in prefix:
                        method.ingest_session(session)
                    checkpoint = _checkpoint(item)
                    answer = _answer_with_query_isolation(method, item)
                    return _prediction(
                        method_id=method_id,
                        item=item,
                        checkpoint=checkpoint,
                        answer=answer,
                    )
                finally:
                    method.close()

            with ThreadPoolExecutor(max_workers=query_concurrency) as executor:
                # executor.map runs requests concurrently but yields results in
                # frozen task order, keeping the JSONL artifact deterministic.
                for row in executor.map(answer_independent, tasks):
                    recorder.append(row)
            recorder.complete(
                query_execution={
                    "strategy": "parallel_independent_prefix",
                    "max_workers": query_concurrency,
                    "fresh_method_and_client_per_item": True,
                }
            )
            return output

        for trajectory_id in sorted(by_trajectory):
            method = create_method(
                method_id,
                trajectory_id=trajectory_id,
                paths=paths,
                mock=mock,
                top_k=top_k,
                reasoning_policy=reasoning_policy,
            )
            try:
                method.ingest_initial(_load_s000(root, trajectory_id))
                sessions = _load_sessions(root, trajectory_id)
                grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
                for item in by_trajectory[trajectory_id]:
                    grouped[_checkpoint(item)].append(item)
                cursor = 0
                for checkpoint in sorted(grouped):
                    if checkpoint < cursor or checkpoint > len(sessions):
                        raise ValueError(
                            f"invalid checkpoint {trajectory_id}/{checkpoint}; cursor={cursor}"
                        )
                    while cursor < checkpoint:
                        method.ingest_session(sessions[cursor])
                        cursor += 1
                    for item in sorted(
                        grouped[checkpoint], key=lambda row: str(row["item_id"])
                    ):
                        answer = _answer_with_query_isolation(method, item)
                        recorder.append(
                            _prediction(
                                method_id=method_id,
                                item=item,
                                checkpoint=checkpoint,
                                answer=answer,
                            )
                        )
            finally:
                method.close()
        recorder.complete()
    except BaseException as exc:
        recorder.fail(exc)
        raise
    return output


def _assert_no_future_evidence(evidence_ids: list[str], checkpoint: int) -> None:
    future = [
        session_id
        for session_id in evidence_ids
        if session_id.startswith("S") and session_id[1:].isdigit()
        and session_number(session_id) > checkpoint
    ]
    if future:
        raise RuntimeError(f"future leakage in retrieved evidence: {future[:5]}")


def _prediction(
    *,
    method_id: str,
    item: dict[str, Any],
    checkpoint: int,
    answer: Any,
) -> dict[str, Any]:
    if item.get("stage") == STAGE2_2:
        parsed = parse_stage2_2_prediction(
            answer.raw_answer, checkpoint=checkpoint
        )
        metrics = score_stage2_2(
            prediction=parsed,
            initial_state=item["gold"]["initial_state"],
            gold_state=item["gold"]["state"],
            dynamic_paths=(item.get("metadata") or {}).get("dynamic_paths"),
        )
        _assert_no_future_evidence(
            [
                public_id.replace("D", "S", 1)
                for cell in (parsed.get("state") or {}).values()
                for public_id in (cell.get("evidence_session_ids") or [])
            ],
            checkpoint,
        )
        return {
            "schema_version": "financial-memory-prediction-v3",
            "method_id": method_id,
            "item_id": item["item_id"],
            "stage": item["stage"],
            "trajectory_id": item["trajectory_id"],
            "prefix_id": item.get("prefix_id"),
            "query_checkpoint": checkpoint,
            "prediction": parsed.get("state") or {},
            "gold": item["gold"]["state"],
            "correct": bool(metrics["exact_state_match"]),
            "parse_error": parsed.get("parse_error"),
            "validation_errors": parsed.get("validation_errors") or [],
            "metrics": metrics,
            "evidence_session_ids": answer.evidence_session_ids,
            "response_metadata": answer.metadata,
            "item_metadata": item.get("metadata") or {},
            "raw_answer": answer.raw_answer,
        }
    prediction = parse_answer(item, answer.raw_answer)
    gold = gold_answer(item)
    _assert_no_future_evidence(answer.evidence_session_ids, checkpoint)
    return {
        "schema_version": "financial-memory-prediction-v1",
        "method_id": method_id,
        "item_id": item["item_id"],
        "stage": item["stage"],
        "trajectory_id": item["trajectory_id"],
        "prefix_id": item.get("prefix_id"),
        "query_checkpoint": checkpoint,
        "prediction": prediction,
        "gold": gold,
        "correct": prediction == gold,
        "parse_error": not bool(prediction),
        "evidence_session_ids": answer.evidence_session_ids,
        "response_metadata": answer.metadata,
        "item_metadata": item.get("metadata") or {},
        "raw_answer": answer.raw_answer,
    }


def _answer_with_query_isolation(method: Any, item: dict[str, Any]) -> Any:
    """Answer without allowing query history to contaminate later items."""

    before = method.state_fingerprint()
    if not bool(getattr(method, "query_on_clone", False)):
        answer = method.answer(item)
        if method.state_fingerprint() != before:
            raise RuntimeError(
                f"{method.method_id}/{item['item_id']}: query changed state"
            )
        return answer

    query_method = method.clone()
    try:
        if query_method.state_fingerprint() != before:
            raise CloneEquivalenceError(
                f"{method.method_id}/{item['item_id']}: query clone differs"
            )
        answer = query_method.answer(item)
    finally:
        query_method.close()
    if method.state_fingerprint() != before:
        raise RuntimeError(
            f"{method.method_id}/{item['item_id']}: isolated query changed base state"
        )
    return answer


class _TrieNode:
    def __init__(self) -> None:
        self.children: dict[str, tuple[dict[str, Any], "_TrieNode"]] = {}
        self.items: list[dict[str, Any]] = []


def _run_masking_method(
    paths: ExperimentPaths,
    *,
    method_id: str,
    items: list[dict[str, Any]],
    output: Path,
    mock: bool,
    top_k: int | None,
) -> Path:
    if method_id == "letta_gemini_3_1_pro" and not mock:
        return _run_masking_letta_replay(
            paths,
            method_id=method_id,
            items=items,
            output=output,
            top_k=top_k,
        )
    recorder = _RunRecorder(
        paths,
        output=output,
        method_id=method_id,
        items=items,
        mock=mock,
        top_k=top_k,
    )
    root = Path(active_prepared_manifest(paths)["root"])
    by_trajectory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_trajectory[str(item["trajectory_id"])].append(item)
    ingestion_operations = 0
    clone_operations = 0

    try:
        for trajectory_id in sorted(by_trajectory):
            trie = _TrieNode()
            by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for item in by_trajectory[trajectory_id]:
                by_case[str((item.get("metadata") or {})["case_id"])].append(item)
            for case_items in by_case.values():
                variant_path = Path(
                    str((case_items[0].get("metadata") or {})["variant_sessions_file"])
                )
                sessions = list(read_jsonl(variant_path))
                node = trie
                for session in sessions:
                    key = sha256_json(session)
                    if key not in node.children:
                        node.children[key] = (session, _TrieNode())
                    node = node.children[key][1]
                node.items.extend(case_items)

            base = create_method(
                method_id,
                trajectory_id=trajectory_id,
                paths=paths,
                mock=mock,
                top_k=top_k,
            )
            try:
                base.ingest_initial(_load_s000(root, trajectory_id))

                def visit(node: _TrieNode, method: Any, depth: int) -> None:
                    nonlocal ingestion_operations, clone_operations
                    for item in sorted(
                        node.items, key=lambda row: str(row["item_id"])
                    ):
                        answer = _answer_with_query_isolation(method, item)
                        recorder.append(
                            _prediction(
                                method_id=method_id,
                                item=item,
                                checkpoint=depth,
                                answer=answer,
                            )
                        )
                    children = list(node.children.values())
                    if len(children) == 1:
                        session, child = children[0]
                        method.ingest_session(session)
                        ingestion_operations += 1
                        visit(child, method, depth + 1)
                    elif children:
                        parent_fingerprint = method.state_fingerprint()
                        for session, child in children:
                            branch = method.clone()
                            try:
                                clone_operations += 1
                                if branch.state_fingerprint() != parent_fingerprint:
                                    raise CloneEquivalenceError(
                                        f"{method_id}/{trajectory_id}: "
                                        "branch clone fingerprint differs"
                                    )
                                branch.ingest_session(session)
                                ingestion_operations += 1
                                visit(child, branch, depth + 1)
                            finally:
                                branch.close()

                visit(trie, base, 0)
            finally:
                base.close()
        recorder.complete(
            branching={
                "strategy": "prefix_trie_clone",
                "ingestion_operations": ingestion_operations,
                "clone_operations": clone_operations,
                "automatic_replay_fallback": False,
            }
        )
    except BaseException as exc:
        recorder.fail(exc)
        raise
    return output


def _run_masking_letta_replay(
    paths: ExperimentPaths,
    *,
    method_id: str,
    items: list[dict[str, Any]],
    output: Path,
    top_k: int | None,
) -> Path:
    """Replay each frozen masking variant for Letta archival memory.

    Letta Agent File intentionally omits archival passages, so prefix clones
    would silently lose memory or require more re-embedding than exact replay.
    """

    recorder = _RunRecorder(
        paths,
        output=output,
        method_id=method_id,
        items=items,
        mock=False,
        top_k=top_k,
    )
    root = Path(active_prepared_manifest(paths)["root"])
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        metadata = item.get("metadata") or {}
        grouped[
            (
                str(item["trajectory_id"]),
                str(metadata["variant_sessions_file"]),
            )
        ].append(item)

    passage_ingestions = 0
    variants = 0
    try:
        for (trajectory_id, filename), variant_items in sorted(
            grouped.items()
        ):
            method = create_method(
                method_id,
                trajectory_id=trajectory_id,
                paths=paths,
                mock=False,
                top_k=top_k,
            )
            if hasattr(method, "_delete_on_close"):
                method._delete_on_close = True
            try:
                method.ingest_initial(_load_s000(root, trajectory_id))
                passage_ingestions += 1
                sessions = list(read_jsonl(Path(filename)))
                for session in sessions:
                    method.ingest_session(session)
                    passage_ingestions += 1
                checkpoint = len(sessions)
                for item in sorted(
                    variant_items, key=lambda row: str(row["item_id"])
                ):
                    answer = _answer_with_query_isolation(method, item)
                    recorder.append(
                        _prediction(
                            method_id=method_id,
                            item=item,
                            checkpoint=checkpoint,
                            answer=answer,
                        )
                    )
            finally:
                method.close()
            variants += 1
        recorder.complete(
            branching={
                "strategy": "frozen_variant_replay",
                "reason": "letta_agent_file_omits_archival_passages",
                "variants": variants,
                "passage_ingestion_operations": passage_ingestions,
                "automatic_replay_fallback": False,
            }
        )
    except BaseException as exc:
        recorder.fail(exc)
        raise
    return output
