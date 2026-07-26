from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import load_experiment_config, load_method_config
from ..paths import ExperimentPaths
from .base import MemoryMethod
from .full_context import FullContextMethod
from .letta_adapter import LettaContractDouble, LettaMethod, official_letta_client
from .mem0_adapter import Mem0Method, build_official_mem0
from .readers import MockReader, ProviderReader, Reader
from .retrieval import BM25Method, DenseMethod, GeminiEmbedder, kiwi_tokenize, regex_tokenize


def method_ids(paths: ExperimentPaths | None = None) -> list[str]:
    return list(load_experiment_config(paths)["methods"])


def _system(paths: ExperimentPaths) -> str:
    return (paths.prompts / "system_ko.txt").read_text(encoding="utf-8").strip()


def _reader(
    provider: str,
    model: str,
    mock: bool,
    max_tokens: int,
    timeout_seconds: float,
) -> Reader:
    return (
        MockReader()
        if mock
        else ProviderReader(
            provider,
            model,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
    )


def create_method(
    method_id: str,
    *,
    trajectory_id: str,
    paths: ExperimentPaths | None = None,
    mock: bool = False,
    top_k: int | None = None,
) -> MemoryMethod:
    paths = paths or ExperimentPaths.discover()
    cfg = load_experiment_config(paths)
    method_cfg = load_method_config(paths)
    models = cfg["models"]
    k = int(top_k or cfg["benchmark"]["top_k_main"])
    max_tokens = int(cfg["models"]["final_answer_max_tokens"])
    timeout_seconds = float(cfg["models"]["request_timeout_seconds"])
    system = _system(paths)
    gemini = _reader(
        "google",
        str(models["gemini_reader"]),
        mock,
        max_tokens,
        timeout_seconds,
    )
    if method_id == "fc_claude_opus_5":
        return FullContextMethod(
            method_id,
            _reader(
                "anthropic",
                str(models["claude_full_context"]),
                mock,
                max_tokens,
                timeout_seconds,
            ),
            system,
        )
    if method_id == "fc_gemini_3_6_flash":
        return FullContextMethod(method_id, gemini, system)
    if method_id == "fc_gpt_5_6_sol":
        return FullContextMethod(
            method_id,
            _reader(
                "openai",
                str(models["openai_full_context"]),
                mock,
                max_tokens,
                timeout_seconds,
            ),
            system,
        )
    if method_id == "bm25_gemini_3_6":
        tokenizer = regex_tokenize if mock else kiwi_tokenize
        return BM25Method(
            gemini,
            system,
            k=k,
            k1=float(method_cfg["bm25"]["k1"]),
            b=float(method_cfg["bm25"]["b"]),
            tokenizer=tokenizer,
        )
    if method_id == "dense_ge2_gemini_3_6":
        if mock:
            from .retrieval import HashEmbedder

            embedder: Any = HashEmbedder()
        else:
            embedder = GeminiEmbedder(
                str(models["gemini_embedding"]),
                int(models["embedding_dimensions"]),
                timeout_seconds,
            )
        return DenseMethod(gemini, system, embedder, k=k)
    if method_id == "mem0_gemini_3_6":
        if mock:
            from .mem0_adapter import InMemoryMem0Double

            factory = InMemoryMem0Double
        else:
            import uuid

            store = paths.runs / "state" / "mem0" / trajectory_id

            def factory() -> Any:
                instance_id = uuid.uuid4().hex
                return build_official_mem0(
                    collection_name=f"financial_memory_{trajectory_id}_{instance_id}",
                    qdrant_path=store / instance_id,
                    llm_model=str(models["gemini_reader"]),
                    embedding_model=str(models["gemini_embedding"]),
                    embedding_dimensions=int(models["embedding_dimensions"]),
                    timeout_seconds=timeout_seconds,
                )

        return Mem0Method(factory, gemini, system, trajectory_id=trajectory_id, k=k)
    if method_id == "letta_gemini_3_6":
        if mock:
            return LettaContractDouble()
        return LettaMethod(
            lambda: official_letta_client(
                "http://localhost:8283", timeout_seconds
            ),
            trajectory_id=trajectory_id,
            model=f"google_ai/{models['gemini_reader']}",
            embedding=f"google_ai/{models['gemini_embedding']}",
            max_steps=int(method_cfg["letta"]["max_steps"]),
            max_tokens=max_tokens,
            top_k=k,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"unknown method_id: {method_id}")
