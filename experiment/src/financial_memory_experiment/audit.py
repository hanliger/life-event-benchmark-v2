from __future__ import annotations

from typing import Any


def branching_ingestion_audit(
    *,
    total_sessions: int = 6000,
    terminal_events: int = 451,
    masking_arms: int = 5,
    naive_ingestions: int = 367_100,
    prefix_trie_edges: int = 23_588,
    letta_variant_replay_passages: int = 369_355,
) -> dict[str, Any]:
    return {
        "naive_session_ingestions": naive_ingestions,
        "cloneable_method_prefix_trie_ingestions": prefix_trie_edges,
        "cloneable_method_reduction_factor": naive_ingestions
        / prefix_trie_edges,
        "letta_frozen_variant_replay_passages": letta_variant_replay_passages,
        "assumptions": {
            "total_sessions": total_sessions,
            "terminal_events": terminal_events,
            "masking_arms": masking_arms,
            "measurement": "five-arm variant files from HF snapshot d97e1acf",
            "clone_equivalence_required_for_mem0": True,
            "letta_agent_file_serializes_archival_passages": False,
            "automatic_replay_fallback": False,
        },
    }
