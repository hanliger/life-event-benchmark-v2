from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MethodAnswer:
    raw_answer: str
    evidence_session_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryMethod(ABC):
    method_id: str
    query_on_clone: bool = False

    @abstractmethod
    def ingest_initial(self, s000: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def ingest_session(self, session: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def answer(self, item: dict[str, Any]) -> MethodAnswer:
        raise NotImplementedError

    def snapshot(self) -> Any:
        return copy.deepcopy(self.__dict__)

    def restore(self, snapshot: Any) -> None:
        self.__dict__ = copy.deepcopy(snapshot)

    def clone(self) -> "MemoryMethod":
        result = copy.copy(self)
        result.restore(copy.deepcopy(self.snapshot()))
        return result

    def state_fingerprint(self) -> str:
        from ..util import sha256_json

        return sha256_json(self.snapshot())

    def close(self) -> None:
        return None


class CloneEquivalenceError(RuntimeError):
    pass


def assert_clone_equivalent(
    original: MemoryMethod,
    clone: MemoryMethod,
    probe_item: dict[str, Any],
) -> None:
    original_before = original.state_fingerprint()
    clone_before = clone.state_fingerprint()
    if original_before != clone_before:
        raise CloneEquivalenceError("clone state fingerprint differs before query")
    first = original.answer(probe_item)
    second = clone.answer(probe_item)
    if first.raw_answer != second.raw_answer or first.evidence_session_ids != second.evidence_session_ids:
        raise CloneEquivalenceError("clone probe answer/evidence differs")
    if original.state_fingerprint() != original_before:
        raise CloneEquivalenceError("original query mutated persistent state")
    if clone.state_fingerprint() != clone_before:
        raise CloneEquivalenceError("clone query mutated persistent state")
