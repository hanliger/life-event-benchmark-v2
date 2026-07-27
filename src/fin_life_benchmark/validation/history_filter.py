"""History-necessity filter (finance-adapted consensus filter).

Rejects (tags) items solvable without the long-horizon history:

  single_session      — show only the latest session; if validators still pick
                        the correct option, the item is too_easy.
  partial_prefix      — show the prefix WITHOUT the earlier critical-evidence
                        sessions; success => too_easy / leakage_suspected.
  no_history_option   — show only the question + options; success => the
                        options themselves give the answer away.

Validators are "provider:model" specs. A "mock:*" validator answers
deterministically pseudo-randomly so the pipeline runs without API keys —
its verdicts are placeholders, not measurements.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..io import RepoPaths
from ..llm.client import LLMClient

MODES = ("single_session", "partial_prefix", "no_history_option")


def _format_sessions(sessions: list[dict[str, Any]]) -> str:
    blocks = []
    for s in sessions:
        lines = [f"[세션 {s['session_id']}]"]
        for t in s.get("turns", []):
            speaker = "고객" if t["speaker"] == "user" else "상담원"
            lines.append(f"{speaker}: {t['text']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _format_initial_memory(memory: dict[str, Any]) -> str:
    lines = ["다음은 문제와 관련된 초기 금융 메모리입니다."]
    for path, cell in sorted(memory.items()):
        cell = cell or {}
        lines.append(f"- {path}: 상태={cell.get('status')}, 값={cell.get('value')}")
    return "\n".join(lines)


def _visible_for_mode(
    item: dict[str, Any],
    sessions_by_id: dict[tuple[str, str], dict[str, Any]],
    mode: str,
) -> list[dict[str, Any]]:
    ids = item["visible_sessions"]
    if mode == "no_history_option":
        return []
    if mode == "single_session":
        ids = ids[-1:]
    elif mode == "partial_prefix":
        # drop the evidence sessions of the gold action's source event: keep
        # only the last third of the prefix
        cut = max(1, len(ids) // 3)
        ids = ids[-cut:]
    trajectory_id = item["trajectory_id"]
    return [
        sessions_by_id[(trajectory_id, session_id)]
        for session_id in ids
        if (trajectory_id, session_id) in sessions_by_id
    ]


def build_validator_prompt(item: dict[str, Any], visible: list[dict[str, Any]]) -> str:
    lines = []
    if visible:
        lines.append("다음은 은행 상담 세션 이력입니다.\n")
        lines.append(_format_sessions(visible))
        lines.append("")
    initial_memory = (item.get("metadata") or {}).get("initial_memory") or {}
    if initial_memory:
        lines.append(_format_initial_memory(initial_memory))
        lines.append("")
    lines.append(item["question"])
    lines.append("")
    for opt in item.get("options", []):
        lines.append(f"{opt['option_id']}. {opt['text']}")
    lines.append("")
    lines.append('정답 선택지 하나만 JSON으로 답하시오: {"answer": "A"}')
    return "\n".join(lines)


class MockValidator:
    """Deterministic pseudo-random answers keyed on (item, mode, name)."""

    def __init__(self, name: str = "mock-validator"):
        self.name = name
        self.provider = "mock"

    def answer(self, item: dict[str, Any], prompt: str, mode: str) -> str:
        options = [o["option_id"] for o in item.get("options", [])] or ["A"]
        digest = hashlib.sha256(f"{item['item_id']}:{mode}:{self.name}".encode()).hexdigest()
        return options[int(digest[:8], 16) % len(options)]


class LLMValidator:
    def __init__(self, provider: str, model: str):
        self.name = f"{provider}:{model}"
        self.provider = provider
        self.client = LLMClient(provider=provider, model=model, temperature=0.0, max_tokens=64)
        self.system = (
            RepoPaths.default().prompts / "system" / "history_filter_validator_ko.txt"
        ).read_text(encoding="utf-8").strip()

    def answer(self, item: dict[str, Any], prompt: str, mode: str) -> str:
        raw = self.client.generate(
            system=self.system,
            user=prompt,
        )
        try:
            start, end = raw.find("{"), raw.rfind("}")
            return str(json.loads(raw[start : end + 1]).get("answer", "")).strip().upper()[:1]
        except Exception:
            return ""


def parse_validators(spec: str) -> list[Any]:
    validators: list[Any] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        provider, _, model = part.partition(":")
        if provider == "mock":
            validators.append(MockValidator(model or "mock-validator"))
        else:
            validators.append(LLMValidator(provider, model))
    if not validators:
        raise ValueError("no validators parsed")
    return validators


def run_filter(
    items: list[dict[str, Any]],
    sessions_by_id: dict[tuple[str, str], dict[str, Any]],
    validators: list[Any],
    mode: str,
) -> list[dict[str, Any]]:
    if mode not in MODES:
        raise ValueError(f"unknown filter mode: {mode} (choose from {MODES})")
    results = []
    for item in items:
        gold = (item.get("gold") or {}).get("correct_option")
        visible = _visible_for_mode(item, sessions_by_id, mode)
        prompt = build_validator_prompt(item, visible)
        votes = []
        for validator in validators:
            answer = validator.answer(item, prompt, mode)
            votes.append({"validator": validator.name, "mode": mode, "answer": answer, "correct": answer == gold})
        n_correct = sum(1 for v in votes if v["correct"])
        majority_correct = n_correct > len(votes) / 2
        if majority_correct and mode == "no_history_option":
            status = "leakage_suspected"
        elif majority_correct:
            status = "too_easy"
        else:
            status = "keep"
        item = dict(item)
        item["filter_status"] = status
        item["filter_votes"] = item.get("filter_votes", []) + votes
        item["filter_meta"] = {
            "mode": mode,
            "mock_only": all(getattr(v, "provider", "") == "mock" for v in validators),
        }
        results.append(item)
    return results
