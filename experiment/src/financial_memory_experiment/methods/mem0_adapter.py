from __future__ import annotations

import copy
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from ..prompts import (
    answer_output_tokens,
    build_query,
    expose_rendered_prompt,
    format_s000,
    format_session,
)
from ..safety import assert_provider_construction_allowed
from ..stage2_2 import STAGE2_2
from ..util import sha256_json
from .base import MemoryMethod, MethodAnswer
from .readers import Reader, generation_slot
from .stage2_2_retrieval import (
    pin_initial_state,
    stage2_2_retrieval_queries,
)


def build_official_mem0(
    *,
    collection_name: str,
    qdrant_path: Path,
    llm_model: str,
    llm_provider: str = "gemini",
    embedding_model: str,
    embedding_dimensions: int,
    timeout_seconds: float = 120,
) -> Any:
    """Construct the official mem0ai Memory class with local Qdrant storage."""

    assert_provider_construction_allowed()
    qdrant_path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MEM0_DIR", str(qdrant_path.parent / "_mem0_config"))
    # Mem0 telemetry is unrelated to the benchmark and would create an
    # uncontrolled external request.
    os.environ["MEM0_TELEMETRY"] = "false"
    from mem0 import Memory
    from google import genai
    from google.genai import types

    config = {
        "history_db_path": str(qdrant_path / "history.db"),
        "llm": {
            "provider": llm_provider,
            "config": {
                "model": llm_model,
                "temperature": None,
                "top_p": None,
                "max_tokens": 4096,
            },
        },
        "embedder": {
            "provider": "gemini",
            "config": {
                "model": embedding_model,
                "embedding_dims": embedding_dimensions,
            },
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": collection_name,
                "path": str(qdrant_path),
                "embedding_model_dims": embedding_dimensions,
            },
        },
    }
    memory = Memory.from_config(config)
    memory._benchmark_inference_usage = []
    zero_retry_client = genai.Client(
        api_key=os.environ.get("GOOGLE_API_KEY"),
        http_options=types.HttpOptions(
            timeout=int(timeout_seconds * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    if llm_provider == "gemini":
        memory.llm.client = zero_retry_client
    elif llm_provider == "anthropic":
        import anthropic

        anthropic_client = anthropic.Anthropic(
            max_retries=0,
            timeout=timeout_seconds,
        )

        class _LowEffortMessages:
            def __init__(self, messages: Any):
                self._messages = messages

            def create(self, **kwargs: Any) -> Any:
                kwargs.pop("temperature", None)
                kwargs.pop("top_p", None)
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["output_config"] = {"effort": "low"}
                with generation_slot("anthropic"):
                    response = self._messages.create(**kwargs)
                usage = getattr(response, "usage", None)
                memory._benchmark_inference_usage.append(
                    {
                        field: int(value)
                        for field in (
                            "input_tokens",
                            "output_tokens",
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens",
                        )
                        if (value := getattr(usage, field, None))
                        is not None
                    }
                )
                return response

        class _LowEffortAnthropicClient:
            def __init__(self, client: Any):
                self._client = client
                self.messages = _LowEffortMessages(client.messages)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._client, name)

        memory.llm.client = _LowEffortAnthropicClient(anthropic_client)
    else:
        raise ValueError(f"unsupported Mem0 LLM provider: {llm_provider}")
    memory.embedding_model.client = zero_retry_client
    return memory


class Mem0Method(MemoryMethod):
    """Official Mem0 ingestion/search plus the shared answer reader."""

    method_id = "mem0_gemini_3_1_pro"

    def __init__(
        self,
        memory_factory: Callable[[], Any],
        reader: Reader,
        system: str,
        *,
        trajectory_id: str,
        k: int,
        method_id: str = "mem0_gemini_3_1_pro",
        retrieval_top_k_per_group: int = 5,
        retrieval_max_evidence: int = 20,
    ):
        self._memory_factory = memory_factory
        self.memory = memory_factory()
        self.reader = reader
        self.system = system
        self.trajectory_id = trajectory_id
        self.k = k
        self.method_id = method_id
        self.retrieval_top_k_per_group = retrieval_top_k_per_group
        self.retrieval_max_evidence = retrieval_max_evidence
        self.s000: dict[str, Any] | None = None
        self.ingestion_calls = 0

    def ingest_initial(self, s000: dict[str, Any]) -> None:
        self.s000 = copy.deepcopy(s000)
        self.memory.add(
            [{"role": "user", "content": format_s000(s000)}],
            user_id=self.trajectory_id,
            infer=True,
            metadata={"session_id": "S000", "record_type": "initial_state"},
        )
        self.ingestion_calls += 1

    def ingest_session(self, session: dict[str, Any]) -> None:
        self.memory.add(
            [{"role": "user", "content": format_session(session)}],
            user_id=self.trajectory_id,
            infer=True,
            metadata={
                "session_id": str(session["session_id"]),
                "session_date": str(session["session_date"]),
            },
        )
        self.ingestion_calls += 1

    @staticmethod
    def _results(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            payload = payload.get("results") or payload.get("memories") or []
        return [dict(row) for row in (payload or [])]

    def _all(self) -> list[dict[str, Any]]:
        rows = self._results(
            self.memory.get_all(
                filters={"user_id": self.trajectory_id},
                top_k=100_000,
            )
        )
        return sorted(
            rows,
            key=lambda row: (
                str((row.get("metadata") or {}).get("session_id") or ""),
                str(row.get("memory") or row.get("text") or ""),
            ),
        )

    @staticmethod
    def _canonical(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "memory": row.get("memory") or row.get("text"),
                "metadata": row.get("metadata") or {},
            }
            for row in rows
        ]

    def answer(self, item: dict[str, Any]) -> MethodAnswer:
        if item.get("stage") == STAGE2_2:
            return self._answer_stage2_2(item)
        before = self.state_fingerprint()
        rows = self._results(
            self.memory.search(
                query=str(item["question"]),
                filters={"user_id": self.trajectory_id},
                top_k=self.k,
            )
        )
        evidence = [
            {
                "session_id": str((row.get("metadata") or {}).get("session_id") or "memory"),
                "session_date": str(
                    (row.get("metadata") or {}).get("session_date")
                    or (self.s000 or {}).get("session_date")
                    or "0001-01-01"
                ),
                "turns": [{"speaker": "user", "text": row.get("memory") or row.get("text") or ""}],
            }
            for row in rows[: self.k]
        ]
        rendered_query = build_query(item, evidence)
        raw, metadata = self.reader.generate(
            system=self.system,
            user=rendered_query,
            max_tokens=answer_output_tokens(item),
        )
        if self.state_fingerprint() != before:
            raise RuntimeError("Mem0 query mutated persistent memory")
        return MethodAnswer(
            raw_answer=raw,
            evidence_session_ids=[str(row["session_id"]) for row in evidence],
            metadata={
                **metadata,
                "retriever": "official_mem0",
                "top_k": self.k,
                "retrieval": [
                    {
                        "memory_id": row.get("id"),
                        "session_id": (row.get("metadata") or {}).get("session_id"),
                        "score": row.get("score"),
                    }
                    for row in rows[: self.k]
                ],
                **(
                    {
                        "rendered_user_prompt": rendered_query,
                        "rendered_system_prompt": self.system,
                        "memory_inference_calls": self.ingestion_calls,
                        "embedding_document_calls": self.ingestion_calls,
                        "memory_search_calls": 1,
                        "memory_inference_usage": copy.deepcopy(
                            getattr(
                                self.memory, "_benchmark_inference_usage", []
                            )
                        ),
                    }
                    if expose_rendered_prompt(item)
                    else {}
                ),
            },
        )

    @staticmethod
    def _memory_key(row: dict[str, Any]) -> tuple[str, str]:
        return (
            str((row.get("metadata") or {}).get("session_id") or ""),
            str(row.get("memory") or row.get("text") or ""),
        )

    def _answer_stage2_2(self, item: dict[str, Any]) -> MethodAnswer:
        before = self.state_fingerprint()
        selected: dict[tuple[str, str], dict[str, Any]] = {}
        retrieval_groups = []
        for group in stage2_2_retrieval_queries():
            rows = self._results(
                self.memory.search(
                    query=str(group["query"]),
                    filters={"user_id": self.trajectory_id},
                    top_k=self.retrieval_top_k_per_group,
                )
            )[: self.retrieval_top_k_per_group]
            retrieval_groups.append(
                {
                    **group,
                    "results": [
                        {
                            "memory_id": row.get("id"),
                            "session_id": (
                                row.get("metadata") or {}
                            ).get("session_id"),
                            "score": row.get("score"),
                            "rank": rank,
                        }
                        for rank, row in enumerate(rows, start=1)
                    ],
                }
            )
            for row in rows:
                if str((row.get("metadata") or {}).get("session_id")) == "S000":
                    continue
                selected.setdefault(self._memory_key(row), row)
        ordered = sorted(
            selected.values(),
            key=lambda row: (
                str((row.get("metadata") or {}).get("session_id") or ""),
                str(row.get("memory") or row.get("text") or ""),
            ),
        )[: self.retrieval_max_evidence]
        evidence = pin_initial_state(
            self.s000,
            [
                {
                    "session_id": str(
                        (row.get("metadata") or {}).get("session_id")
                        or "memory"
                    ),
                    "session_date": str(
                        (row.get("metadata") or {}).get("session_date")
                        or (self.s000 or {}).get("session_date")
                        or "0001-01-01"
                    ),
                    "turns": [
                        {
                            "speaker": "user",
                            "text": row.get("memory")
                            or row.get("text")
                            or "",
                        }
                    ],
                }
                for row in ordered
            ],
        )
        rendered_query = build_query(item, evidence)
        raw, metadata = self.reader.generate(
            system=self.system,
            user=rendered_query,
            max_tokens=answer_output_tokens(item),
        )
        if self.state_fingerprint() != before:
            raise RuntimeError("Mem0 Stage 2.2 query mutated persistent memory")
        return MethodAnswer(
            raw_answer=raw,
            evidence_session_ids=[
                str(row["session_id"]) for row in evidence
            ],
            metadata={
                **metadata,
                "retriever": "official_mem0_stage2_2_path_groups",
                "retrieval_searches": len(retrieval_groups),
                "top_k_per_group": self.retrieval_top_k_per_group,
                "max_evidence": self.retrieval_max_evidence,
                "retrieval_groups": retrieval_groups,
                "rendered_user_prompt": rendered_query,
                "rendered_system_prompt": self.system,
                "memory_inference_calls": self.ingestion_calls,
                "embedding_document_calls": self.ingestion_calls,
                "memory_search_calls": len(retrieval_groups),
                "memory_inference_usage": copy.deepcopy(
                    getattr(
                        self.memory,
                        "_benchmark_inference_usage",
                        [],
                    )
                ),
            },
        )

    def snapshot(self) -> Any:
        return {
            "s000": self.s000,
            "memories": self._canonical(self._all()),
            "ingestion_calls": self.ingestion_calls,
            "memory_inference_usage": copy.deepcopy(
                getattr(
                    self.memory, "_benchmark_inference_usage", []
                )
            ),
        }

    def restore(self, snapshot: Any) -> None:
        self.memory = self._memory_factory()
        self.s000 = copy.deepcopy(snapshot["s000"])
        self.ingestion_calls = int(snapshot.get("ingestion_calls", 0))
        self.memory._benchmark_inference_usage = copy.deepcopy(
            snapshot.get("memory_inference_usage") or []
        )
        for row in snapshot["memories"]:
            self.memory.add(
                str(row["memory"]),
                user_id=self.trajectory_id,
                infer=False,
                metadata=copy.deepcopy(row["metadata"]),
            )
        if self.state_fingerprint() != sha256_json(snapshot):
            raise RuntimeError("Mem0 import is not clone-equivalent; replay fallback is forbidden")

    def clone(self) -> "Mem0Method":
        clone = object.__new__(Mem0Method)
        clone._memory_factory = self._memory_factory
        clone.reader = self.reader
        clone.system = self.system
        clone.trajectory_id = f"{self.trajectory_id}__clone_{uuid.uuid4().hex}"
        clone.k = self.k
        clone.method_id = self.method_id
        clone.retrieval_top_k_per_group = self.retrieval_top_k_per_group
        clone.retrieval_max_evidence = self.retrieval_max_evidence
        clone.ingestion_calls = 0
        snapshot = self.snapshot()
        # The logical user id is metadata only in an isolated store; retaining it
        # makes the imported state queryable under the clone's own namespace.
        clone.trajectory_id = self.trajectory_id
        clone.restore(snapshot)
        return clone

    def state_fingerprint(self) -> str:
        return sha256_json(self.snapshot())


class InMemoryMem0Double:
    """Contract test double with the subset of the official API used above."""

    def __init__(self):
        self.rows: list[dict[str, Any]] = []

    def add(self, messages: Any, *, user_id: str, infer: bool, metadata: dict[str, Any]) -> None:
        if isinstance(messages, list):
            text = "\n".join(str(row.get("content") or "") for row in messages)
        else:
            text = str(messages)
        self.rows.append({"memory": text, "metadata": copy.deepcopy(metadata)})

    def get_all(
        self, *, filters: dict[str, Any], top_k: int = 20
    ) -> dict[str, Any]:
        return {"results": copy.deepcopy(self.rows)}

    def search(
        self, *, query: str, filters: dict[str, Any], top_k: int
    ) -> dict[str, Any]:
        return {"results": copy.deepcopy(self.rows[-top_k:])}
