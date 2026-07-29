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


class OracleRelevantContextMethod(FullContextMethod):
    """Full-context reader given only S000 and Gold support sessions.

    This is an analysis-only upper-bound arm. Gold is used solely to select
    dialogue sessions; the model never receives Gold state values.
    """

    def answer(self, item: dict[str, Any]) -> MethodAnswer:
        if item.get("stage") != STAGE2_2:
            raise ValueError(
                "oracle-relevant context is only defined for stage2_2_reconstruct"
            )
        support_ids = sorted(
            {
                str(public_id).replace("D", "S", 1)
                for cell in (item.get("gold") or {}).get("state", {}).values()
                for public_id in (cell.get("evidence_session_ids") or [])
            },
            key=lambda session_id: int(session_id[1:]),
        )
        available = {
            str(session["session_id"]): session for session in self.sessions
        }
        missing = [session_id for session_id in support_ids if session_id not in available]
        if missing:
            raise ValueError(
                f"oracle support sessions were not ingested: {missing[:5]}"
            )
        evidence = [available["S000"], *(available[session_id] for session_id in support_ids)]
        max_tokens = int(
            (item.get("metadata") or {}).get("max_output_tokens", 12000)
        )
        query = build_query(item, evidence)
        raw, metadata = self.reader.generate(
            system=self.system,
            user=query,
            max_tokens=max_tokens,
        )
        return MethodAnswer(
            raw_answer=raw,
            evidence_session_ids=[
                str(row["session_id"]) for row in evidence
            ],
            metadata={
                **metadata,
                "context_arm": "oracle_relevant",
                "oracle_support_session_count": len(support_ids),
            },
        )
