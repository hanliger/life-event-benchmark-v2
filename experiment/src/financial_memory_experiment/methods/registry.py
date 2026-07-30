from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import load_experiment_config, load_method_config
from ..paths import ExperimentPaths
from .base import MemoryMethod
from .full_context import FullContextMethod, OracleRelevantContextMethod
from .letta_adapter import LettaContractDouble, LettaMethod, official_letta_client
from .mem0_adapter import Mem0Method, build_official_mem0
from .readers import MockReader, ProviderReader, Reader
from .retrieval import BM25Method, DenseMethod, GeminiEmbedder, kiwi_tokenize, regex_tokenize


def comparison_contract(paths: ExperimentPaths | None = None) -> dict[str, Any]:
    cfg = load_experiment_config(paths)
    models = cfg["models"]
    contract = {
        "embedding_model": str(models["gemini_embedding"]),
        "embedding_dimensions": int(models["embedding_dimensions"]),
        "top_k": int(cfg["benchmark"]["top_k_main"]),
        "methods": [
            "dense_ge2_gemini_3_1_pro",
            "mem0_gemini_3_1_pro",
            "letta_gemini_3_1_pro",
        ],
    }
    if contract["embedding_model"] != "gemini-embedding-2":
        raise ValueError("comparison contract requires gemini-embedding-2")
    if contract["embedding_dimensions"] != 768:
        raise ValueError(
            "Dense/Mem0/Letta comparison contract requires 768-dimensional embeddings"
        )
    if contract["top_k"] != 10:
        raise ValueError("Dense/Mem0/Letta comparison contract requires top_k=10")
    return contract


def method_ids(paths: ExperimentPaths | None = None) -> list[str]:
    comparison_contract(paths)
    cfg = load_experiment_config(paths)
    return [
        *map(str, cfg["methods"]),
        *map(str, cfg.get("analysis_methods") or []),
    ]


def _system(paths: ExperimentPaths) -> str:
    return (paths.prompts / "system_ko.txt").read_text(encoding="utf-8").strip()


def _reader(
    provider: str,
    model: str,
    mock: bool,
    max_tokens: int,
    timeout_seconds: float,
    generation_settings: dict[str, Any],
) -> Reader:
    return (
        MockReader()
        if mock
        else ProviderReader(
            provider,
            model,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            generation_settings=generation_settings,
        )
    )


def generation_settings_for_policy(
    models: dict[str, Any],
    reasoning_policy: str | None,
) -> dict[str, Any]:
    default_policy = str(models["reasoning_policy"])
    selected = reasoning_policy or default_policy
    if selected == default_policy:
        return dict(models["generation_settings"])
    profiles = models.get("generation_profiles") or {}
    settings = profiles.get(selected)
    if not isinstance(settings, dict):
        raise ValueError(f"unknown reasoning policy: {selected}")
    return dict(settings)


def create_method(
    method_id: str,
    *,
    trajectory_id: str,
    paths: ExperimentPaths | None = None,
    mock: bool = False,
    top_k: int | None = None,
    reasoning_policy: str | None = None,
) -> MemoryMethod:
    paths = paths or ExperimentPaths.discover()
    cfg = load_experiment_config(paths)
    comparison_contract(paths)
    method_cfg = load_method_config(paths)
    models = cfg["models"]
    k = int(top_k or cfg["benchmark"]["top_k_main"])
    max_tokens = int(cfg["models"]["final_answer_max_tokens"])
    timeout_seconds = float(cfg["models"]["request_timeout_seconds"])
    generation_settings = generation_settings_for_policy(
        models, reasoning_policy
    )
    system = _system(paths)
    gemini = _reader(
        "google",
        str(models["gemini_reader"]),
        mock,
        max_tokens,
        timeout_seconds,
        dict(generation_settings["google"]),
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
                dict(generation_settings["anthropic"]),
            ),
            system,
        )
    if method_id == "fc_claude_opus_4_8":
        return FullContextMethod(
            method_id,
            _reader(
                "anthropic",
                str(models["claude_opus_4_8"]),
                mock,
                max_tokens,
                float(models["claude_opus_4_8_request_timeout_seconds"]),
                dict(generation_settings["anthropic"]),
            ),
            system,
        )
    if method_id == "fc_gemini_3_1_pro":
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
                dict(generation_settings["openai"]),
            ),
            system,
        )
    if method_id == "oracle_rel_gpt_5_6_sol":
        return OracleRelevantContextMethod(
            method_id,
            _reader(
                "openai",
                str(models["openai_full_context"]),
                mock,
                max_tokens,
                timeout_seconds,
                dict(generation_settings["openai"]),
            ),
            system,
        )
    if method_id == "bm25_gemini_3_1_pro":
        tokenizer = regex_tokenize if mock else kiwi_tokenize
        return BM25Method(
            gemini,
            system,
            k=k,
            k1=float(method_cfg["bm25"]["k1"]),
            b=float(method_cfg["bm25"]["b"]),
            tokenizer=tokenizer,
        )
    if method_id == "dense_ge2_gemini_3_1_pro":
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
    if method_id == "mem0_gemini_3_1_pro":
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
    if method_id == "letta_gemini_3_1_pro":
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
