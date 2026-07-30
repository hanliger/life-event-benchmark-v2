from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any, Callable

import numpy as np

from ..prompts import build_query, format_session, s000_as_session
from ..stage2_2 import STAGE2_2
from ..safety import assert_provider_construction_allowed
from .base import MemoryMethod, MethodAnswer
from .readers import Reader
from .stage2_2_retrieval import (
    deduplicate_ranked_sessions,
    pin_initial_state,
    stage2_2_output_tokens,
    stage2_2_retrieval_queries,
)


Tokenize = Callable[[str], list[str]]


def regex_tokenize(text: str) -> list[str]:
    return re.findall(r"[가-힣]+|[A-Za-z]+|\d+(?:[.,]\d+)*", text.lower())


def kiwi_tokenize(text: str) -> list[str]:
    try:
        from kiwipiepy import Kiwi
    except ModuleNotFoundError as exc:
        raise RuntimeError("main BM25 requires the kiwipiepy extra") from exc
    kiwi = Kiwi()
    morphs = [token.form.lower() for token in kiwi.tokenize(text)]
    preserved = re.findall(
        r"\d{4}[-./]\d{1,2}(?:[-./]\d{1,2})?"
        r"|[A-Za-z]+\d+[A-Za-z0-9_-]*"
        r"|\d+(?:[.,]\d+)*(?:원|만원|억원|%|일|월|년)?",
        text,
    )
    return morphs + [token.lower() for token in preserved]


class GeminiEmbedder:
    def __init__(
        self, model: str, dimensions: int, timeout_seconds: float = 120
    ):
        assert_provider_construction_allowed()
        from google import genai
        from google.genai import types

        self.client = genai.Client(
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            )
        )
        self.model = model
        self.dimensions = dimensions

    def __call__(self, texts: list[str], task_type: str) -> np.ndarray:
        from google.genai import types

        result = self.client.models.embed_content(
            model=self.model,
            contents=texts,
            config=types.EmbedContentConfig(
                output_dimensionality=self.dimensions,
                task_type=task_type,
            ),
        )
        return np.asarray([embedding.values for embedding in result.embeddings], dtype=np.float32)


class HashEmbedder:
    """Deterministic test-only embedder; never used in reported runs."""

    def __init__(self, dimensions: int = 64):
        self.dimensions = dimensions

    def __call__(self, texts: list[str], task_type: str) -> np.ndarray:
        rows = []
        for text in texts:
            vector = np.zeros(self.dimensions, dtype=np.float32)
            for token in regex_tokenize(text):
                digest = hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:2], "big") % self.dimensions] += 1
            rows.append(vector)
        return np.stack(rows)


class BM25Method(MemoryMethod):
    def __init__(
        self,
        reader: Reader,
        system: str,
        *,
        k: int,
        k1: float,
        b: float,
        tokenizer: Tokenize = kiwi_tokenize,
        method_id: str = "bm25_gemini_3_1_pro",
        retrieval_top_k_per_group: int = 5,
        retrieval_max_evidence: int = 20,
    ):
        self.method_id = method_id
        self.reader = reader
        self.system = system
        self.k = k
        self.k1 = k1
        self.b = b
        self.tokenizer = tokenizer
        self.retrieval_top_k_per_group = retrieval_top_k_per_group
        self.retrieval_max_evidence = retrieval_max_evidence
        self.s000: dict[str, Any] | None = None
        self.sessions: list[dict[str, Any]] = []
        self.tokens: list[list[str]] = []

    def ingest_initial(self, s000: dict[str, Any]) -> None:
        self.s000 = s000
        self.ingest_session(s000_as_session(s000))

    def ingest_session(self, session: dict[str, Any]) -> None:
        self.sessions.append(session)
        self.tokens.append(self.tokenizer(format_session(session)))

    def _scores(self, query: str) -> list[float]:
        query_counts = Counter(self.tokenizer(query))
        n = len(self.tokens)
        avgdl = sum(map(len, self.tokens)) / n if n else 0.0
        dfs = Counter(token for document in self.tokens for token in set(document))
        scores: list[float] = []
        for document in self.tokens:
            frequencies = Counter(document)
            score = 0.0
            for token, qf in query_counts.items():
                df = dfs[token]
                if not df:
                    continue
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                tf = frequencies[token]
                denominator = tf + self.k1 * (1 - self.b + self.b * len(document) / (avgdl or 1))
                score += qf * idf * (tf * (self.k1 + 1) / denominator)
            scores.append(score)
        return scores

    def answer(self, item: dict[str, Any]) -> MethodAnswer:
        if item.get("stage") == STAGE2_2:
            return self._answer_stage2_2(item)
        scores = self._scores(item["question"])
        ranked = sorted(
            range(len(self.sessions)),
            key=lambda index: (-scores[index], index),
        )[: self.k]
        evidence = [self.sessions[index] for index in sorted(ranked)]
        raw, metadata = self.reader.generate(
            system=self.system, user=build_query(item, evidence)
        )
        return MethodAnswer(
            raw_answer=raw,
            evidence_session_ids=[str(row["session_id"]) for row in evidence],
            metadata={
                **metadata,
                "top_k": self.k,
                "retriever": "bm25",
                "retrieval": [
                    {
                        "session_id": str(self.sessions[index]["session_id"]),
                        "score": scores[index],
                    }
                    for index in ranked
                ],
            },
        )

    def _answer_stage2_2(self, item: dict[str, Any]) -> MethodAnswer:
        if self.s000 is None:
            raise RuntimeError("BM25 Stage 2.2 query requires S000")
        retrieval_groups = []
        ranked_groups: list[list[tuple[int, float]]] = []
        for group in stage2_2_retrieval_queries():
            scores = self._scores(str(group["query"]))
            ranked = sorted(
                range(1, len(self.sessions)),
                key=lambda index: (-scores[index], index),
            )[: self.retrieval_top_k_per_group]
            ranked_groups.append(
                [(index, float(scores[index])) for index in ranked]
            )
            retrieval_groups.append(
                {
                    **group,
                    "results": [
                        {
                            "session_id": str(
                                self.sessions[index]["session_id"]
                            ),
                            "score": float(scores[index]),
                            "rank": rank,
                        }
                        for rank, index in enumerate(ranked, start=1)
                    ],
                }
            )
        selected = deduplicate_ranked_sessions(
            ranked_groups,
            max_evidence=self.retrieval_max_evidence,
        )
        evidence = pin_initial_state(
            self.s000, [self.sessions[index] for index in selected]
        )
        query = build_query(item, evidence)
        raw, metadata = self.reader.generate(
            system=self.system,
            user=query,
            max_tokens=stage2_2_output_tokens(item),
        )
        return MethodAnswer(
            raw_answer=raw,
            evidence_session_ids=[
                str(row["session_id"]) for row in evidence
            ],
            metadata={
                **metadata,
                "retriever": "bm25_stage2_2_path_groups",
                "retrieval_searches": len(retrieval_groups),
                "top_k_per_group": self.retrieval_top_k_per_group,
                "max_evidence": self.retrieval_max_evidence,
                "retrieval_groups": retrieval_groups,
                "rendered_user_prompt": query,
                "rendered_system_prompt": self.system,
            },
        )
    def snapshot(self) -> Any:
        return {"s000": self.s000, "sessions": self.sessions, "tokens": self.tokens}

    def restore(self, snapshot: Any) -> None:
        self.s000, self.sessions, self.tokens = (
            snapshot["s000"],
            snapshot["sessions"],
            snapshot["tokens"],
        )


class DenseMethod(MemoryMethod):
    def __init__(
        self,
        reader: Reader,
        system: str,
        embedder: Any,
        *,
        k: int,
        method_id: str = "dense_ge2_gemini_3_1_pro",
        retrieval_top_k_per_group: int = 5,
        retrieval_max_evidence: int = 20,
    ):
        self.method_id = method_id
        self.reader = reader
        self.system = system
        self.embedder = embedder
        self.k = k
        self.retrieval_top_k_per_group = retrieval_top_k_per_group
        self.retrieval_max_evidence = retrieval_max_evidence
        self.s000: dict[str, Any] | None = None
        self.sessions: list[dict[str, Any]] = []
        self.vectors: list[list[float]] = []
        self.embedding_document_calls = 0

    @staticmethod
    def _normalize(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)

    def ingest_initial(self, s000: dict[str, Any]) -> None:
        self.s000 = s000
        self.ingest_session(s000_as_session(s000))

    def ingest_session(self, session: dict[str, Any]) -> None:
        vector = self.embedder([format_session(session)], "RETRIEVAL_DOCUMENT")[0]
        self.embedding_document_calls += 1
        self.sessions.append(session)
        self.vectors.append(np.asarray(vector, dtype=np.float32).tolist())

    def answer(self, item: dict[str, Any]) -> MethodAnswer:
        if item.get("stage") == STAGE2_2:
            return self._answer_stage2_2(item)
        if not self.vectors:
            raise RuntimeError("dense retriever has no ingested sessions")
        query = self._normalize(
            np.asarray(self.embedder([item["question"]], "RETRIEVAL_QUERY"), dtype=np.float32)
        )[0]
        documents = self._normalize(np.asarray(self.vectors, dtype=np.float32))
        scores = documents @ query
        ranked = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), i))[: self.k]
        evidence = [self.sessions[index] for index in sorted(ranked)]
        raw, metadata = self.reader.generate(
            system=self.system, user=build_query(item, evidence)
        )
        return MethodAnswer(
            raw_answer=raw,
            evidence_session_ids=[str(row["session_id"]) for row in evidence],
            metadata={
                **metadata,
                "top_k": self.k,
                "retriever": "dense_cosine",
                "retrieval": [
                    {
                        "session_id": str(self.sessions[index]["session_id"]),
                        "score": float(scores[index]),
                    }
                    for index in ranked
                ],
            },
        )

    def _answer_stage2_2(self, item: dict[str, Any]) -> MethodAnswer:
        if self.s000 is None or not self.vectors:
            raise RuntimeError("Dense Stage 2.2 query requires S000 and vectors")
        documents = self._normalize(np.asarray(self.vectors, dtype=np.float32))
        groups = stage2_2_retrieval_queries()
        queries = self._normalize(
            np.asarray(
                self.embedder(
                    [str(group["query"]) for group in groups],
                    "RETRIEVAL_QUERY",
                ),
                dtype=np.float32,
            )
        )
        ranked_groups: list[list[tuple[int, float]]] = []
        retrieval_groups = []
        for group, query in zip(groups, queries, strict=True):
            scores = documents @ query
            ranked = sorted(
                range(1, len(scores)),
                key=lambda index: (-float(scores[index]), index),
            )[: self.retrieval_top_k_per_group]
            ranked_groups.append(
                [(index, float(scores[index])) for index in ranked]
            )
            retrieval_groups.append(
                {
                    **group,
                    "results": [
                        {
                            "session_id": str(
                                self.sessions[index]["session_id"]
                            ),
                            "score": float(scores[index]),
                            "rank": rank,
                        }
                        for rank, index in enumerate(ranked, start=1)
                    ],
                }
            )
        selected = deduplicate_ranked_sessions(
            ranked_groups,
            max_evidence=self.retrieval_max_evidence,
        )
        evidence = pin_initial_state(
            self.s000, [self.sessions[index] for index in selected]
        )
        rendered_query = build_query(item, evidence)
        raw, metadata = self.reader.generate(
            system=self.system,
            user=rendered_query,
            max_tokens=stage2_2_output_tokens(item),
        )
        return MethodAnswer(
            raw_answer=raw,
            evidence_session_ids=[
                str(row["session_id"]) for row in evidence
            ],
            metadata={
                **metadata,
                "retriever": "dense_stage2_2_path_groups",
                "retrieval_searches": len(retrieval_groups),
                "top_k_per_group": self.retrieval_top_k_per_group,
                "max_evidence": self.retrieval_max_evidence,
                "retrieval_groups": retrieval_groups,
                "rendered_user_prompt": rendered_query,
                "rendered_system_prompt": self.system,
                "embedding_document_calls": self.embedding_document_calls,
                "embedding_query_calls": 1,
                "embedding_query_inputs": len(groups),
            },
        )
    def snapshot(self) -> Any:
        return {
            "s000": self.s000,
            "sessions": self.sessions,
            "vectors": self.vectors,
            "embedding_document_calls": self.embedding_document_calls,
        }

    def restore(self, snapshot: Any) -> None:
        self.s000, self.sessions, self.vectors = (
            snapshot["s000"],
            snapshot["sessions"],
            snapshot["vectors"],
        )
        self.embedding_document_calls = int(
            snapshot.get("embedding_document_calls", len(self.vectors))
        )
