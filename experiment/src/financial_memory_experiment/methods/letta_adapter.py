from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from typing import Any, Callable

from ..prompts import build_query, format_s000, format_session
from ..safety import assert_provider_construction_allowed
from ..util import sha256_json
from .base import MemoryMethod, MethodAnswer


READ_ONLY_INSTRUCTIONS = """
질의 시 archival_memory_search를 정확히 한 번만 사용하고 질의에서 지정한 top_k를 지킨다.
질의 중 core memory, archival memory, block, tool, agent 설정을 생성·수정·삭제하지 않는다.
설명 없이 요청된 <answer>...</answer>만 반환한다.
""".strip()


def official_letta_client(base_url: str, timeout_seconds: float = 120) -> Any:
    assert_provider_construction_allowed()
    from letta_client import Letta

    return Letta(
        base_url=base_url,
        max_retries=0,
        timeout=timeout_seconds,
    )


class LettaMethod(MemoryMethod):
    """Official Letta server adapter using native archival passages."""

    method_id = "letta_gemini_3_6"
    query_on_clone = False

    def __init__(
        self,
        client_factory: Callable[[], Any],
        *,
        trajectory_id: str,
        model: str,
        embedding: str,
        max_steps: int,
        max_tokens: int,
        top_k: int,
        timeout_seconds: float = 120,
    ):
        self._client_factory = client_factory
        self.client = client_factory()
        self.trajectory_id = trajectory_id
        self.model = model
        self.embedding = embedding
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.top_k = top_k
        self.timeout_seconds = timeout_seconds
        self._delete_on_close = False
        agent = self.client.agents.create(
            name=f"financial-memory-{trajectory_id}",
            model=model,
            embedding=embedding,
            max_tokens=max_tokens,
            memory_blocks=[
                {"label": "protocol", "value": READ_ONLY_INSTRUCTIONS},
            ],
            tools=["archival_memory_search"],
            include_base_tools=False,
            include_base_tool_rules=False,
            tool_rules=[
                {
                    "type": "run_first",
                    "tool_name": "archival_memory_search",
                    "args": {"top_k": top_k},
                },
                {
                    "type": "max_count_per_step",
                    "tool_name": "archival_memory_search",
                    "max_count_limit": 1,
                },
                {
                    "type": "continue_loop",
                    "tool_name": "archival_memory_search",
                },
            ],
            timeout=self.timeout_seconds,
        )
        self.agent_id = str(agent.id)
        self._ingested: list[str] = []

    def _insert_passage(
        self,
        *,
        text: str,
        session_id: str,
        created_at: str | None,
    ) -> None:
        self.client.agents.passages.create(
            agent_id=self.agent_id,
            text=text,
            created_at=created_at,
            tags=[self.trajectory_id, session_id],
            timeout=self.timeout_seconds,
        )

    def ingest_initial(self, s000: dict[str, Any]) -> None:
        self._insert_passage(
            text=format_s000(s000),
            session_id="S000",
            created_at=str(s000.get("session_date") or "") or None,
        )
        self._ingested.append("S000")

    def ingest_session(self, session: dict[str, Any]) -> None:
        session_id = str(session["session_id"])
        self._insert_passage(
            text=format_session(session),
            session_id=session_id,
            created_at=str(session.get("session_date") or "") or None,
        )
        self._ingested.append(session_id)

    @classmethod
    def _response_text(cls, response: Any) -> str:
        tagged: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, str):
                tagged.extend(
                    match.group(0)
                    for match in re.finditer(
                        r"<answer>.*?</answer>", value, flags=re.DOTALL
                    )
                )
            elif isinstance(value, dict):
                for child in value.values():
                    collect(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    collect(child)
            elif hasattr(value, "model_dump"):
                collect(value.model_dump(mode="json"))
            elif hasattr(value, "__dict__"):
                collect(vars(value))

        messages = getattr(
            response,
            "messages",
            response if isinstance(response, list) else [],
        )
        for message in messages or []:
            payload = cls._jsonable(message)
            if isinstance(payload, dict):
                message_type = payload.get("message_type")
                role = payload.get("role")
                if message_type == "assistant_message" or role == "assistant":
                    collect(payload.get("content", payload.get("text")))
            elif (
                getattr(message, "message_type", None) == "assistant_message"
                or getattr(message, "role", None) == "assistant"
            ):
                collect(
                    getattr(
                        message,
                        "content",
                        getattr(message, "text", None),
                    )
                )
        return tagged[-1].strip() if tagged else ""

    @classmethod
    def _tool_calls(cls, response: Any) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        messages = getattr(
            response,
            "messages",
            response if isinstance(response, list) else [],
        )
        for message in messages or []:
            single = getattr(message, "tool_call", None)
            tool_calls = getattr(message, "tool_calls", None) or []
            if not isinstance(tool_calls, list):
                tool_calls = [tool_calls]
            for call in ([single] if single else []) + tool_calls:
                if isinstance(call, dict):
                    function = call.get("function") or {}
                    name = call.get("name") or (
                        function.get("name")
                        if isinstance(function, dict)
                        else getattr(function, "name", None)
                    )
                    arguments = call.get("arguments") or (
                        function.get("arguments")
                        if isinstance(function, dict)
                        else getattr(function, "arguments", None)
                    )
                else:
                    function = getattr(call, "function", None)
                    name = getattr(call, "name", None) or getattr(
                        function, "name", None
                    )
                    arguments = getattr(call, "arguments", None) or getattr(
                        function, "arguments", None
                    )
                if name:
                    calls.append({"name": str(name), "arguments": arguments})

        payload = cls._jsonable(response)

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                name = value.get("name")
                if isinstance(name, str) and (
                    "arguments" in value or "args" in value
                ):
                    calls.append(
                        {
                            "name": name,
                            "arguments": value.get(
                                "arguments", value.get("args")
                            ),
                        }
                    )
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(payload)
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for call in calls:
            arguments = call.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    pass
            normalized = {
                "name": str(call["name"]),
                "arguments": arguments,
            }
            key = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique.append(normalized)
        return unique

    @classmethod
    def _evidence_session_ids(cls, response: Any) -> list[str]:
        session_ids: list[str] = []
        messages = getattr(
            response,
            "messages",
            response if isinstance(response, list) else [],
        )

        def add_from_return(value: Any) -> None:
            if isinstance(value, str):
                try:
                    value = ast.literal_eval(value)
                except (ValueError, SyntaxError):
                    for match in re.finditer(r"\[(S\d{3,})\b", value):
                        session_ids.append(match.group(1))
                    return
            if isinstance(value, dict):
                tags = value.get("tags") or []
                for tag in tags:
                    tag = str(tag)
                    if re.fullmatch(r"S\d{3,}", tag):
                        session_ids.append(tag)
                for key in ("tool_return", "tool_returns"):
                    if key in value:
                        add_from_return(value[key])
            elif isinstance(value, (list, tuple)):
                for child in value:
                    add_from_return(child)

        for message in messages or []:
            payload = cls._jsonable(message)
            if not isinstance(payload, dict) and hasattr(message, "__dict__"):
                payload = vars(message)
            if not isinstance(payload, dict):
                continue
            if (
                payload.get("message_type") == "tool_return_message"
                and payload.get("name") == "archival_memory_search"
            ):
                add_from_return(payload.get("tool_return"))
                add_from_return(payload.get("tool_returns"))

        return list(dict.fromkeys(session_ids))

    def _export_bytes(self, *, scrub_messages: bool = True) -> bytes:
        exported = self.client.agents.export_file(
            agent_id=self.agent_id,
            scrub_messages=scrub_messages,
            timeout=self.timeout_seconds,
        )
        if isinstance(exported, bytes):
            return exported
        if isinstance(exported, str):
            return exported.encode("utf-8")
        if hasattr(exported, "read"):
            payload = exported.read()
            return payload.encode("utf-8") if isinstance(payload, str) else payload
        raise TypeError(f"unsupported Letta export payload: {type(exported).__name__}")

    @staticmethod
    def _logical_export(payload: bytes) -> Any:
        """Remove server-generated identifiers while retaining logical memory."""

        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"opaque_sha256": hashlib.sha256(payload).hexdigest()}

        volatile = {
            "id",
            "agent_id",
            "message_id",
            "conversation_id",
            "created_at",
            "updated_at",
            "last_run_completion",
            "otid",
        }

        def normalize(value: Any, path: tuple[Any, ...] = ()) -> Any:
            if isinstance(value, dict):
                normalized: dict[str, Any] = {}
                for key, child in sorted(value.items()):
                    string_key = str(key)
                    if string_key in volatile:
                        continue
                    if (
                        string_key == "name"
                        and len(path) == 2
                        and path[0] == "agents"
                        and isinstance(child, str)
                    ):
                        while child.endswith("_copy"):
                            child = child[: -len("_copy")]
                    normalized[string_key] = normalize(
                        child, (*path, string_key)
                    )
                return normalized
            if isinstance(value, list):
                return [
                    normalize(child, (*path, index))
                    for index, child in enumerate(value)
                ]
            return value

        return normalize(decoded)

    def _passage_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        after: str | None = None
        seen_ids: set[str] = set()
        while True:
            kwargs: dict[str, Any] = {
                "limit": 200,
                "ascending": True,
                "timeout": self.timeout_seconds,
            }
            if after:
                kwargs["after"] = after
            batch = list(
                self.client.agents.passages.list(self.agent_id, **kwargs)
            )
            fresh = [
                passage
                for passage in batch
                if str(getattr(passage, "id", "")) not in seen_ids
            ]
            for passage in fresh:
                passage_id = str(getattr(passage, "id", ""))
                if passage_id:
                    seen_ids.add(passage_id)
                rows.append(
                    {
                        "text": str(getattr(passage, "text", "")),
                        "tags": sorted(
                            str(tag)
                            for tag in (getattr(passage, "tags", None) or [])
                        ),
                    }
                )
            if len(batch) < 200 or not fresh:
                break
            after = str(getattr(batch[-1], "id", ""))
            if not after:
                break
        return sorted(rows, key=lambda row: (row["text"], row["tags"]))

    def snapshot(self) -> Any:
        return {
            "agent_file": self._export_bytes(scrub_messages=True),
            "passages": self._passage_rows(),
            "ingested": list(self._ingested),
        }

    def restore(self, snapshot: Any) -> None:
        imported = self.client.agents.import_file(
            file=snapshot["agent_file"],
            timeout=self.timeout_seconds,
        )
        agent_ids = list(getattr(imported, "agent_ids", None) or [])
        self.agent_id = str(
            agent_ids[0]
            if agent_ids
            else getattr(imported, "id", getattr(imported, "agent_id", ""))
        )
        if not self.agent_id:
            raise RuntimeError("Letta import did not return an agent id")
        for passage in snapshot["passages"]:
            self.client.agents.passages.create(
                agent_id=self.agent_id,
                text=passage["text"],
                tags=passage["tags"],
                timeout=self.timeout_seconds,
            )
        self._ingested = list(snapshot["ingested"])

    def clone(self) -> "LettaMethod":
        clone = object.__new__(LettaMethod)
        clone._client_factory = self._client_factory
        clone.client = self._client_factory()
        clone.trajectory_id = self.trajectory_id
        clone.model = self.model
        clone.embedding = self.embedding
        clone.max_steps = self.max_steps
        clone.max_tokens = self.max_tokens
        clone.top_k = self.top_k
        clone.timeout_seconds = self.timeout_seconds
        clone._delete_on_close = True
        clone.restore(self.snapshot())
        if clone.state_fingerprint() != self.state_fingerprint():
            raise RuntimeError("Letta export/import is not clone-equivalent")
        return clone

    def state_fingerprint(self) -> str:
        snapshot = self.snapshot()
        return sha256_json(
            {
                "logical_export": self._logical_export(snapshot["agent_file"]),
                "passages": snapshot["passages"],
                "ingested": snapshot["ingested"],
            }
        )

    def answer(self, item: dict[str, Any]) -> MethodAnswer:
        conversation = self.client.conversations.create(
            agent_id=self.agent_id,
            timeout=self.timeout_seconds,
        )
        response = self.client.conversations.messages.create(
            conversation_id=str(conversation.id),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"archival search는 최대 1회, 결과는 최대 {self.top_k}개만 사용하라.\n\n"
                        + build_query(item, [])
                    ),
                    "otid": f"financial-memory:query:{item['item_id']}",
                }
            ],
            max_steps=self.max_steps,
            streaming=False,
            timeout=self.timeout_seconds,
        )
        recorded_messages: Any = response
        if hasattr(self.client.conversations.messages, "list"):
            page = self.client.conversations.messages.list(
                str(conversation.id),
                limit=max(20, self.max_steps * 8),
                timeout=self.timeout_seconds,
            )
            recorded_messages = getattr(page, "items", page)

        tool_calls = self._tool_calls(recorded_messages)
        tool_names = [str(call["name"]) for call in tool_calls]
        if tool_names != ["archival_memory_search"]:
            raise RuntimeError(
                "Letta must use archival_memory_search exactly once; "
                f"observed={tool_names}"
            )
        arguments = tool_calls[0].get("arguments")
        if not isinstance(arguments, dict):
            raise RuntimeError("Letta archival search arguments were not JSON")
        effective_top_k = int(arguments.get("top_k", 10))
        if effective_top_k != self.top_k:
            raise RuntimeError(
                f"Letta archival top_k mismatch: {effective_top_k} != {self.top_k}"
            )
        raw = self._response_text(recorded_messages) or self._response_text(
            response
        )
        if not raw:
            raise RuntimeError("Letta returned no final answer")
        evidence_session_ids = self._evidence_session_ids(recorded_messages)
        if not evidence_session_ids:
            raise RuntimeError(
                "Letta archival search returned no attributable session IDs"
            )
        return MethodAnswer(
            raw_answer=raw,
            evidence_session_ids=evidence_session_ids,
            metadata={
                "agent": "official_letta",
                "max_steps": self.max_steps,
                "archival_search_limit": 1,
                "top_k": self.top_k,
                "fresh_conversation": True,
                "conversation_id": str(conversation.id),
                "query_isolation": "fresh_conversation_passage_fingerprint",
                "tool_names": tool_names,
                "observed_search_calls": 1,
                "usage": self._jsonable(getattr(response, "usage", None)),
                "stop_reason": self._jsonable(
                    getattr(response, "stop_reason", None)
                ),
                "automatic_retries": 0,
            },
        )

    def close(self) -> None:
        if getattr(self, "_delete_on_close", False) and getattr(
            self, "agent_id", None
        ):
            self.client.agents.delete(
                self.agent_id,
                timeout=self.timeout_seconds,
            )
            self.agent_id = ""

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value


class LettaContractDouble(MemoryMethod):
    """Offline-only stateful double. It is never a reported benchmark method."""

    method_id = "letta_gemini_3_6"

    def __init__(self):
        self.s000: dict[str, Any] | None = None
        self.sessions: list[dict[str, Any]] = []

    def ingest_initial(self, s000: dict[str, Any]) -> None:
        self.s000 = copy.deepcopy(s000)

    def ingest_session(self, session: dict[str, Any]) -> None:
        self.sessions.append(copy.deepcopy(session))

    def answer(self, item: dict[str, Any]) -> MethodAnswer:
        return MethodAnswer(
            raw_answer="<answer>A</answer>",
            metadata={"provider": "mock", "adapter_contract": "letta", "paid": False},
        )

    def snapshot(self) -> Any:
        return {"s000": self.s000, "sessions": self.sessions}

    def restore(self, snapshot: Any) -> None:
        self.s000 = copy.deepcopy(snapshot["s000"])
        self.sessions = copy.deepcopy(snapshot["sessions"])
