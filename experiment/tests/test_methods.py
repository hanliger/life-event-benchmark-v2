from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from financial_memory_experiment import evaluator
from financial_memory_experiment.evaluator import (
    _answer_with_query_isolation,
    run_method,
)
from financial_memory_experiment.methods.base import MethodAnswer
from financial_memory_experiment.methods.full_context import (
    FullContextMethod,
    OracleRelevantContextMethod,
)
from financial_memory_experiment.methods.letta_adapter import LettaMethod
from financial_memory_experiment.methods.mem0_adapter import InMemoryMem0Double, Mem0Method
from financial_memory_experiment.methods.readers import MockReader
from financial_memory_experiment.methods.retrieval import (
    BM25Method,
    DenseMethod,
    HashEmbedder,
    regex_tokenize,
)
from financial_memory_experiment.paths import ExperimentPaths


S000 = {
    "trajectory_id": "traj_test",
    "session_id": "S000",
    "session_date": "2025-12-31",
    "state": {},
}
SESSION = {
    "trajectory_id": "traj_test",
    "session_id": "S001",
    "session_date": "2026-01-01",
    "turns": [
        {"speaker": "user", "text": "한빛테크에 입사했어요."},
        {"speaker": "assistant", "text": "입사 정보를 반영합니다."},
    ],
}
ITEM = {
    "item_id": "q1",
    "stage": "stage2_memory_value",
    "trajectory_id": "traj_test",
    "question": "직장은?",
    "options": [
        {"option_id": option, "text": option, "correct": option == "A"}
        for option in "ABCD"
    ],
    "gold": {"correct_option": "A"},
    "metadata": {"answer_type": "mcq"},
}


class _CapturingReader:
    def __init__(self):
        self.user = ""

    def generate(self, *, system, user, max_tokens=None):
        self.user = user
        return "{}", {"provider": "capture", "model": "capture", "paid": False}


def test_local_methods_are_query_read_only_and_cloneable():
    reader = MockReader()
    methods = [
        FullContextMethod("fc", reader, "system"),
        BM25Method(reader, "system", k=1, k1=1.5, b=0.75, tokenizer=regex_tokenize),
        DenseMethod(reader, "system", HashEmbedder(), k=1),
        Mem0Method(InMemoryMem0Double, reader, "system", trajectory_id="traj_test", k=1),
    ]
    for method in methods:
        method.ingest_initial(S000)
        method.ingest_session(SESSION)
        before = method.state_fingerprint()
        assert method.answer(ITEM).raw_answer == "<answer>A</answer>"
        assert method.state_fingerprint() == before
        clone = method.clone()
        assert clone.state_fingerprint() == before
        clone.ingest_session({**SESSION, "session_id": "S002"})
        assert method.state_fingerprint() == before
        assert clone.state_fingerprint() != before


def test_oracle_relevant_context_uses_only_s000_and_gold_support_sessions():
    reader = _CapturingReader()
    method = OracleRelevantContextMethod("oracle", reader, "system")
    method.ingest_initial(S000)
    for number in range(1, 4):
        method.ingest_session(
            {
                **SESSION,
                "session_id": f"S{number:03d}",
                "turns": [
                    {
                        "speaker": "user",
                        "text": f"SESSION-{number}",
                    }
                ],
            }
        )
    state = {
        "employment.employer": {
            "value": "한빛테크",
            "status": "current",
            "evidence_session_ids": ["D002"],
        }
    }
    item = {
        "item_id": "oracle-q",
        "stage": "stage2_2_reconstruct",
        "trajectory_id": "traj_test",
        "question": "현재 상태를 복원하세요.",
        "gold": {"state": state},
        "metadata": {"max_output_tokens": 12000},
    }

    answer = method.answer(item)

    assert "SESSION-2" in reader.user
    assert "SESSION-1" not in reader.user
    assert "SESSION-3" not in reader.user
    assert answer.evidence_session_ids == ["S000", "S002"]
    assert answer.metadata["context_arm"] == "oracle_relevant"
    assert answer.metadata["oracle_support_session_count"] == 1


def test_stage2_2_parallel_checkpoints_use_fresh_independent_methods(
    tmp_path, monkeypatch
):
    experiment_root = tmp_path / "experiment"
    for relative in (
        "configs/experiment.yaml",
        "configs/methods.yaml",
        "configs/paid_safety.yaml",
        "prompts/system_ko.txt",
    ):
        path = experiment_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test\n", encoding="utf-8")
    paths = ExperimentPaths(root=experiment_root, repo_root=tmp_path)
    prepared = tmp_path / "prepared"
    barrier = threading.Barrier(5)
    created = []

    class _IndependentMethod:
        method_id = "fc_gpt_5_6_sol"
        query_on_clone = False

        def __init__(self):
            self.ingested = []
            created.append(self)

        def ingest_initial(self, s000):
            self.ingested.append(s000["session_id"])

        def ingest_session(self, session):
            self.ingested.append(session["session_id"])

        def state_fingerprint(self):
            return json.dumps(self.ingested)

        def answer(self, item):
            barrier.wait(timeout=2)
            return MethodAnswer(raw_answer="{}")

        def close(self):
            return None

    items = [
        {
            "item_id": f"q{checkpoint}",
            "stage": "stage2_2_reconstruct",
            "trajectory_id": "traj_test",
            "gold": {"initial_state": {}, "state": {}},
            "metadata": {"query_checkpoint": checkpoint},
        }
        for checkpoint in range(1, 6)
    ]
    sessions = [
        {"session_id": f"S{checkpoint:03d}"}
        for checkpoint in range(1, 6)
    ]

    monkeypatch.setattr(
        evaluator,
        "active_stage2_2_prepared_manifest",
        lambda _paths: {"root": str(prepared)},
    )
    monkeypatch.setattr(
        evaluator,
        "_load_s000",
        lambda _root, _trajectory_id: {"session_id": "S000"},
    )
    monkeypatch.setattr(
        evaluator,
        "_load_sessions",
        lambda _root, _trajectory_id: sessions,
    )
    monkeypatch.setattr(
        evaluator,
        "create_method",
        lambda *_args, **_kwargs: _IndependentMethod(),
    )
    monkeypatch.setattr(
        evaluator,
        "_prediction",
        lambda *, method_id, item, checkpoint, answer: {
            "method_id": method_id,
            "item_id": item["item_id"],
            "query_checkpoint": checkpoint,
        },
    )

    output = experiment_root / "runs" / "parallel.jsonl"
    run_method(
        paths,
        method_id="fc_gpt_5_6_sol",
        items=items,
        output=output,
        mock=True,
        query_concurrency=5,
    )

    rows = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    manifest = json.loads(
        output.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert [row["query_checkpoint"] for row in rows] == [1, 2, 3, 4, 5]
    assert len(created) == 5
    assert sorted(method.ingested for method in created) == [
        ["S000", *[f"S{number:03d}" for number in range(1, checkpoint + 1)]]
        for checkpoint in range(1, 6)
    ]
    assert manifest["query_execution"] == {
        "strategy": "parallel_independent_prefix",
        "max_workers": 5,
        "fresh_method_and_client_per_item": True,
    }


class _FakeAgentsMessages:
    def __init__(self, server):
        self.server = server

    def create(self, *, agent_id, messages, max_steps, streaming, timeout):
        assert streaming is False
        self.server.states[agent_id].append(messages[0]["content"])
        return SimpleNamespace(messages=[])


class _FakeAgents:
    def __init__(self, server):
        self.server = server
        self.messages = _FakeAgentsMessages(server)
        self.passages = _FakePassages(server)

    def create(self, **kwargs):
        assert kwargs["tools"] == ["archival_memory_search"]
        assert kwargs["include_base_tools"] is False
        assert kwargs["include_base_tool_rules"] is False
        assert kwargs["tool_rules"] == [
            {
                "type": "run_first",
                "tool_name": "archival_memory_search",
                "args": {"top_k": 10},
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
        ]
        agent_id = f"a{len(self.server.states)}"
        self.server.states[agent_id] = []
        self.server.passages[agent_id] = []
        return SimpleNamespace(id=agent_id)

    def export_file(self, *, agent_id, scrub_messages, timeout):
        import json

        return json.dumps(
            {
                "agents": [
                    {
                        "name": "financial-memory-traj_test",
                        "agent_type": "letta_v3_agent",
                        "messages": (
                            [] if scrub_messages else self.server.states[agent_id]
                        ),
                    }
                ]
            }
        )

    def import_file(self, *, file, timeout):
        import json

        agent_id = f"a{len(self.server.states)}"
        data = json.loads(file.decode())
        self.server.states[agent_id] = list(data["agents"][0]["messages"])
        self.server.passages[agent_id] = []
        return SimpleNamespace(agent_ids=[agent_id])

    def delete(self, agent_id, *, timeout):
        del self.server.states[agent_id]
        del self.server.passages[agent_id]


class _FakePassages:
    def __init__(self, server):
        self.server = server

    def create(
        self, agent_id, *, text, tags, timeout, created_at=None
    ):
        passage = SimpleNamespace(
            id=f"p{len(self.server.passages[agent_id])}",
            text=text,
            tags=list(tags),
        )
        self.server.passages[agent_id].append(passage)
        return passage

    def list(self, agent_id, **kwargs):
        return list(self.server.passages[agent_id])


class _FakeConversationMessages:
    def __init__(self, server):
        self.server = server
        self.recorded = {}

    def create(self, **kwargs):
        agent_id = str(kwargs["conversation_id"]).removeprefix("c-")
        self.server.states[agent_id].append("QUERY_HISTORY")
        messages = [
            # The real list endpoint returns newest messages first.
            SimpleNamespace(
                message_type="assistant_message",
                content="<answer>A</answer>",
            ),
            SimpleNamespace(
                content=None,
                tool_call={
                    "name": "archival_memory_search",
                    "arguments": '{"query":"직장","top_k":10}',
                },
                tool_calls=[],
            ),
            SimpleNamespace(
                message_type="tool_return_message",
                name="archival_memory_search",
                tool_return=(
                    "[{'content': '[S001 | 상담일]', "
                    "'tags': ['traj_test', 'S001']}]"
                ),
            ),
            SimpleNamespace(
                message_type="user_message",
                content="설명 없이 <answer>...</answer> 형식으로 답하세요.",
            ),
        ]
        self.recorded[str(kwargs["conversation_id"])] = messages
        # The real conversation create response can omit the typed message list;
        # the adapter must verify the server-recorded conversation instead.
        return SimpleNamespace(
            messages=[],
            usage=None,
            stop_reason="end_turn",
        )

    def list(self, conversation_id, **kwargs):
        return SimpleNamespace(
            items=self.recorded[conversation_id],
        )


class _FakeConversations:
    def __init__(self, server):
        self.messages = _FakeConversationMessages(server)

    def create(self, *, agent_id, timeout):
        return SimpleNamespace(id=f"c-{agent_id}")


class _FakeLetta:
    def __init__(self):
        self.states = {}
        self.passages = {}
        self.agents = _FakeAgents(self)
        self.conversations = _FakeConversations(self)


def test_letta_archival_passages_clone_and_query_history_is_read_only():
    server = _FakeLetta()
    method = LettaMethod(
        lambda: server,
        trajectory_id="traj_test",
        model="google_ai/mock",
        embedding="google_ai/mock-embedding",
        max_steps=4,
        max_tokens=4096,
        top_k=10,
    )
    method.ingest_initial(S000)
    method.ingest_session(SESSION)
    before = method.state_fingerprint()
    answer = _answer_with_query_isolation(method, ITEM)
    assert answer.raw_answer == "<answer>A</answer>"
    assert answer.evidence_session_ids == ["S001"]
    assert method.state_fingerprint() == before
    clone = method.clone()
    assert clone.state_fingerprint() == before
    clone.close()
