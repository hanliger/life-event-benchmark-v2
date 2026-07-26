from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any, Callable

import numpy as np

from ..prompts import build_query, format_session, s000_as_session
from ..safety import assert_provider_construction_allowed
from .base import MemoryMethod, MethodAnswer
from .readers import Reader


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
    ):
        self.method_id = "bm25_gemini_3_6"
        self.reader = reader
        self.system = system
        self.k = k
        self.k1 = k1
        self.b = b
        self.tokenizer = tokenizer
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

    def snapshot(self) -> Any:
        return {"s000": self.s000, "sessions": self.sessions, "tokens": self.tokens}

    def restore(self, snapshot: Any) -> None:
        self.s000, self.sessions, self.tokens = (
            snapshot["s000"],
            snapshot["sessions"],
            snapshot["tokens"],
        )


class DenseMethod(MemoryMethod):
    def __init__(self, reader: Reader, system: str, embedder: Any, *, k: int):
        self.method_id = "dense_ge2_gemini_3_6"
        self.reader = reader
        self.system = system
        self.embedder = embedder
        self.k = k
        self.s000: dict[str, Any] | None = None
        self.sessions: list[dict[str, Any]] = []
        self.vectors: list[list[float]] = []

    @staticmethod
    def _normalize(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)

    def ingest_initial(self, s000: dict[str, Any]) -> None:
        self.s000 = s000
        self.ingest_session(s000_as_session(s000))

    def ingest_session(self, session: dict[str, Any]) -> None:
        vector = self.embedder([format_session(session)], "RETRIEVAL_DOCUMENT")[0]
        self.sessions.append(session)
        self.vectors.append(np.asarray(vector, dtype=np.float32).tolist())

    def answer(self, item: dict[str, Any]) -> MethodAnswer:
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

    def snapshot(self) -> Any:
        return {"s000": self.s000, "sessions": self.sessions, "vectors": self.vectors}

    def restore(self, snapshot: Any) -> None:
        self.s000, self.sessions, self.vectors = (
            snapshot["s000"],
            snapshot["sessions"],
            snapshot["vectors"],
        )
