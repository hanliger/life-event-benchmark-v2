from __future__ import annotations

import json
import os
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
    method_cfg = load_method_config(paths)
    models = cfg["models"]
    retrieval = method_cfg["stage2_2_retrieval"]
    contract = {
        "embedding_model": str(models["gemini_embedding"]),
        "embedding_dimensions": int(models["embedding_dimensions"]),
        "retrieval_top_k_per_group": int(retrieval["top_k_per_group"]),
        "retrieval_max_evidence": int(
            retrieval["max_evidence_sessions"]
        ),
        "methods": [
            "bm25_claude_opus_4_8",
            "dense_ge2_claude_opus_4_8",
            "mem0_claude_opus_4_8",
            "letta_claude_opus_4_8",
        ],
    }
    if contract["embedding_model"] != "gemini-embedding-2":
        raise ValueError("comparison contract requires gemini-embedding-2")
    if contract["embedding_dimensions"] != 768:
        raise ValueError(
            "Dense/Mem0/Letta comparison contract requires 768-dimensional embeddings"
        )
    if (
        contract["retrieval_top_k_per_group"] != 5
        or contract["retrieval_max_evidence"] != 20
    ):
        raise ValueError(
            "Stage 2.2 comparison contract requires top-5/group and "
            "max 20 evidence sessions"
        )
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


def _stage1_system(paths: ExperimentPaths) -> str:
    path = paths.prompts / "stage1_system_ko.txt"
    return (
        path.read_text(encoding="utf-8").strip()
        if path.exists()
        else _system(paths)
    )


def _env_override(default: Any, *names: str) -> str:
    """First set environment variable wins; runners set the stage-neutral name.

    The `STAGE2_2_*` spellings stay accepted so a Stage 2.2 run planned before
    the Stage 1 harness landed still resolves the same values.
    """

    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return str(default)


_REQUEST_TIMEOUT_ENV = (
    "FIN_MEMORY_REQUEST_TIMEOUT_SECONDS",
    "STAGE2_2_REQUEST_TIMEOUT_SECONDS",
)
_PROVIDER_LOCK_ENV = (
    "FIN_MEMORY_OPENROUTER_PROVIDER_LOCK",
    "STAGE2_2_OPENROUTER_PROVIDER_LOCK",
)


def _reader(
    provider: str,
    model: str,
    mock: bool,
    max_tokens: int,
    timeout_seconds: float,
    generation_settings: dict[str, Any],
    *,
    api_surface: str | None = None,
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
            api_surface=api_surface,
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
    stage: str | None = None,
) -> MemoryMethod:
    paths = paths or ExperimentPaths.discover()
    cfg = load_experiment_config(paths)
    comparison_contract(paths)
    method_cfg = load_method_config(paths)
    models = cfg["models"]
    k = int(top_k or cfg["benchmark"]["top_k_main"])
    max_tokens = int(cfg["models"]["final_answer_max_tokens"])
    timeout_seconds = float(
        _env_override(
            cfg["models"]["request_timeout_seconds"], *_REQUEST_TIMEOUT_ENV
        )
    )
    opus_timeout_seconds = float(
        _env_override(
            models["claude_opus_4_8_request_timeout_seconds"],
            *_REQUEST_TIMEOUT_ENV,
        )
    )
    generation_settings = generation_settings_for_policy(
        models, reasoning_policy
    )
    system = _system(paths)
    stage1_system = _stage1_system(paths)
    stage2_2_retrieval = method_cfg.get("stage2_2_retrieval") or {}
    group_k = int(
        os.environ.get(
            "STAGE2_2_RETRIEVAL_TOP_K_PER_GROUP",
            stage2_2_retrieval.get("top_k_per_group", 5),
        )
    )
    max_evidence = int(
        os.environ.get(
            "STAGE2_2_RETRIEVAL_MAX_EVIDENCE",
            stage2_2_retrieval.get("max_evidence_sessions", 20),
        )
    )

    def gemini_reader() -> Reader:
        return _reader(
            "google",
            str(models["gemini_reader"]),
            mock,
            max_tokens,
            timeout_seconds,
            dict(generation_settings["google"]),
        )

    def opus_4_8_reader() -> Reader:
        return _reader(
            "anthropic",
            str(models["claude_opus_4_8"]),
            mock,
            max_tokens,
            opus_timeout_seconds,
            dict(generation_settings["anthropic"]),
        )

    def direct_full_context(
        method: str,
        provider: str,
        model: str,
    ) -> FullContextMethod:
        settings = dict(generation_settings[provider])
        api_surface = None
        if (
            provider == "openai"
            and stage == "stage1_occurred_event_evidence_pairs"
        ):
            settings = {
                "reasoning_effort": settings["reasoning"]["effort"],
                "verbosity": settings["text"]["verbosity"],
                "store": settings["store"],
            }
            api_surface = "chat_completions"
        return FullContextMethod(
            method,
            _reader(
                provider,
                model,
                mock,
                max_tokens,
                timeout_seconds,
                settings,
                api_surface=api_surface,
            ),
            system,
            stage1_system=stage1_system,
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
            stage1_system=stage1_system,
        )
    if method_id == "fc_claude_opus_4_8":
        return FullContextMethod(
            method_id,
            opus_4_8_reader(),
            system,
            stage1_system=stage1_system,
        )
    if method_id == "fc_claude_sonnet_4_6":
        return direct_full_context(
            method_id,
            "anthropic",
            str(models["claude_sonnet_4_6"]),
        )
    openrouter_models = models.get("openrouter") or {}
    openrouter_timeout_seconds = float(
        _env_override(
            openrouter_models.get("request_timeout_seconds", timeout_seconds),
            *_REQUEST_TIMEOUT_ENV,
        )
    )
    openrouter_map = {
        "fc_openrouter_llama_4_maverick": (
            "llama_4_maverick",
            False,
            None,
        ),
        "fc_openrouter_gpt_oss_120b": ("gpt_oss_120b", True, None),
        "fc_openrouter_qwen_3_5_122b_a10b": (
            "qwen_3_5_122b_a10b",
            True,
            None,
        ),
        "fc_openrouter_qwen_3_6_35b_a3b_fp8": (
            "qwen_3_6_35b_a3b",
            True,
            ["fp8"],
        ),
    }
    if method_id in openrouter_map:
        model_key, reasoning, quantizations = openrouter_map[method_id]
        settings: dict[str, Any] = {
            "provider": dict(openrouter_models["provider"]),
        }
        provider_lock = json.loads(_env_override("{}", *_PROVIDER_LOCK_ENV))
        locked_provider = provider_lock.get(method_id)
        if locked_provider:
            settings["provider"]["order"] = [str(locked_provider)]
            settings["provider"]["only"] = [str(locked_provider)]
        if reasoning:
            settings["reasoning"] = {"effort": "low", "exclude": True}
        if quantizations is not None:
            settings["provider"]["quantizations"] = quantizations
        return FullContextMethod(
            method_id,
            _reader(
                "openrouter",
                str(openrouter_models[model_key]),
                mock,
                max_tokens,
                openrouter_timeout_seconds,
                settings,
            ),
            system,
            stage1_system=stage1_system,
        )
    if method_id == "fc_gemini_3_1_pro":
        return FullContextMethod(
            method_id,
            gemini_reader(),
            system,
            stage1_system=stage1_system,
        )
    if method_id == "fc_gemini_3_5_flash":
        return direct_full_context(
            method_id,
            "google",
            str(models["gemini_3_5_flash"]),
        )
    if method_id == "fc_gpt_5_6_sol":
        return FullContextMethod(
            method_id,
            _reader(
                "openai",
                str(models["openai_full_context"]),
                mock,
                max_tokens,
                timeout_seconds,
                (
                    {
                        "reasoning_effort": generation_settings["openai"][
                            "reasoning"
                        ]["effort"],
                        "verbosity": generation_settings["openai"]["text"][
                            "verbosity"
                        ],
                        "store": generation_settings["openai"]["store"],
                    }
                    if stage == "stage1_occurred_event_evidence_pairs"
                    else dict(generation_settings["openai"])
                ),
                api_surface=(
                    "chat_completions"
                    if stage == "stage1_occurred_event_evidence_pairs"
                    else "responses"
                ),
            ),
            system,
            stage1_system=stage1_system,
        )
    if method_id in {"fc_gpt_5_6_terra", "fc_gpt_5_6_luna"}:
        model_key = (
            "openai_terra"
            if method_id == "fc_gpt_5_6_terra"
            else "openai_luna"
        )
        return direct_full_context(
            method_id,
            "openai",
            str(models[model_key]),
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
            gemini_reader(),
            system,
            k=k,
            k1=float(method_cfg["bm25"]["k1"]),
            b=float(method_cfg["bm25"]["b"]),
            tokenizer=tokenizer,
        )
    if method_id == "bm25_claude_opus_4_8":
        tokenizer = regex_tokenize if mock else kiwi_tokenize
        return BM25Method(
            opus_4_8_reader(),
            system,
            k=k,
            k1=float(method_cfg["bm25"]["k1"]),
            b=float(method_cfg["bm25"]["b"]),
            tokenizer=tokenizer,
            method_id=method_id,
            retrieval_top_k_per_group=group_k,
            retrieval_max_evidence=max_evidence,
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
        return DenseMethod(gemini_reader(), system, embedder, k=k)
    if method_id == "dense_ge2_claude_opus_4_8":
        if mock:
            from .retrieval import HashEmbedder

            opus_embedder: Any = HashEmbedder()
        else:
            opus_embedder = GeminiEmbedder(
                str(models["gemini_embedding"]),
                int(models["embedding_dimensions"]),
                timeout_seconds,
            )
        return DenseMethod(
            opus_4_8_reader(),
            system,
            opus_embedder,
            k=k,
            method_id=method_id,
            retrieval_top_k_per_group=group_k,
            retrieval_max_evidence=max_evidence,
        )
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

        return Mem0Method(
            factory,
            gemini_reader(),
            system,
            trajectory_id=trajectory_id,
            k=k,
        )
    if method_id == "mem0_claude_opus_4_8":
        if mock:
            from .mem0_adapter import InMemoryMem0Double

            opus_memory_factory = InMemoryMem0Double
        else:
            import uuid

            store = paths.runs / "state" / "mem0_opus_4_8" / trajectory_id

            def opus_memory_factory() -> Any:
                instance_id = uuid.uuid4().hex
                return build_official_mem0(
                    collection_name=(
                        f"financial_memory_opus48_{trajectory_id}_{instance_id}"
                    ),
                    qdrant_path=store / instance_id,
                    llm_model=str(models["claude_opus_4_8"]),
                    llm_provider="anthropic",
                    embedding_model=str(models["gemini_embedding"]),
                    embedding_dimensions=int(models["embedding_dimensions"]),
                    timeout_seconds=opus_timeout_seconds,
                )

        return Mem0Method(
            opus_memory_factory,
            opus_4_8_reader(),
            system,
            trajectory_id=trajectory_id,
            k=k,
            method_id=method_id,
            retrieval_top_k_per_group=group_k,
            retrieval_max_evidence=max_evidence,
        )
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
            system=system,
            timeout_seconds=timeout_seconds,
        )
    if method_id == "letta_claude_opus_4_8":
        if mock:
            double = LettaContractDouble()
            double.method_id = method_id
            return double
        return LettaMethod(
            lambda: official_letta_client(
                "http://localhost:8283",
                opus_timeout_seconds,
            ),
            trajectory_id=trajectory_id,
            model=f"anthropic/{models['claude_opus_4_8']}",
            embedding=f"google_ai/{models['gemini_embedding']}",
            max_steps=int(method_cfg["letta"]["max_steps"]),
            max_tokens=20_000,
            # Stage 2.2 leaves top_k unset and uses the per-group budget; Stage 1
            # passes its frozen single-query top_k explicitly.
            top_k=int(top_k) if top_k else group_k,
            method_id=method_id,
            stage2_2_search_calls=int(
                method_cfg["letta"]["archival_search_calls_per_query"]
            ),
            system=system,
            timeout_seconds=opus_timeout_seconds,
        )
    raise ValueError(f"unknown method_id: {method_id}")
