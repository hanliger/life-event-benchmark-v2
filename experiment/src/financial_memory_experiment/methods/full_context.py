from __future__ import annotations

from typing import Any

from ..prompts import build_query, s000_as_session
from ..stage2_2 import STAGE2_2
from .base import MemoryMethod, MethodAnswer
from .readers import Reader


class FullContextMethod(MemoryMethod):
    def __init__(self, method_id: str, reader: Reader, system: str):
        self.method_id = method_id
        self.reader = reader
        self.system = system
        self.s000: dict[str, Any] | None = None
        self.sessions: list[dict[str, Any]] = []

    def ingest_initial(self, s000: dict[str, Any]) -> None:
        self.s000 = s000
        self.sessions.append(s000_as_session(s000))

    def ingest_session(self, session: dict[str, Any]) -> None:
        self.sessions.append(session)

    def answer(self, item: dict[str, Any]) -> MethodAnswer:
        max_tokens = (
            int((item.get("metadata") or {}).get("max_output_tokens", 12000))
            if item.get("stage") == STAGE2_2
            else None
        )
        query = build_query(item, self.sessions)
        if max_tokens is None:
            raw, metadata = self.reader.generate(system=self.system, user=query)
        else:
            raw, metadata = self.reader.generate(
                system=self.system,
                user=query,
                max_tokens=max_tokens,
            )
        return MethodAnswer(
            raw_answer=raw,
            evidence_session_ids=[str(row["session_id"]) for row in self.sessions],
            metadata=metadata,
        )

    def snapshot(self) -> Any:
        return {"s000": self.s000, "sessions": self.sessions}

    def restore(self, snapshot: Any) -> None:
        self.s000 = snapshot["s000"]
        self.sessions = snapshot["sessions"]
