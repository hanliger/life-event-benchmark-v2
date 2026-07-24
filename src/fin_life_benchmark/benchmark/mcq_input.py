"""Load trajectory-level dialogue/gold data for MCQ construction.

The Hugging Face export stores dialogue and session gold in separate JSONL
files. This module joins them in memory and exposes the 15-session windows
used by the revised benchmark without changing the source files.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..io import load_yaml, read_jsonl
from ..trajectory.models import Trajectory


_SESSION_RE = re.compile(r"^S(\d+)$")


@dataclass(frozen=True)
class McqWindow:
    """One cumulative evaluation window and its occurred-event target."""

    trajectory_id: str
    window_index: int
    window_size: int
    target_session_start: str
    target_session_end: str
    visible_session_ids: tuple[str, ...]
    target_event_instance_id: str
    target_event_id: str
    target_event_label: str
    target_event_status: str


@dataclass(frozen=True)
class Stage2Target:
    """One canonical event-instance/memory-path transition target.

    The target is created once, at the first 15-session checkpoint where the
    occurred event and its dialogue-grounded memory update are both visible.
    Later checkpoints retain it as eligible context, while the item builder
    emits only the target newly created at the current checkpoint.
    """

    canonical_target_id: str
    trajectory_id: str
    target_event_instance_id: str
    target_event_id: str
    target_event_label: str
    memory_path: str
    operation: str
    first_visible_checkpoint: int
    evidence_sessions: tuple[str, ...]
    evidence_turns: tuple[str, ...]
    before_state: dict[str, Any]
    after_state: dict[str, Any]
    value_selector: str = "value"
    question_template: str | None = None
    question_label: str = "memory"
    question_scope: str = "current_prefix"
    option_pool_type: str = "categorical"
    option_pool: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Stage2Checkpoint:
    """A cumulative 15-session evaluation checkpoint."""

    trajectory_id: str
    prefix_id: str
    checkpoint_session_count: int
    visible_session_ids: tuple[str, ...]
    targets: tuple[Stage2Target, ...]


def _session_number(session_id: str) -> int:
    match = _SESSION_RE.fullmatch(str(session_id))
    if not match:
        raise ValueError(f"invalid session_id: {session_id!r}")
    return int(match.group(1))


def _session_sort_key(record: dict[str, Any]) -> int:
    return _session_number(str(record["session_id"]))


def _window_index(record: dict[str, Any]) -> int:
    plan = record.get("plan") or {}
    value = record.get("window_index", plan.get("window_index"))
    if value is None:
        raise ValueError(f"missing window_index for {record.get('session_id')}")
    return int(value)


def _position_in_window(record: dict[str, Any]) -> int:
    plan = record.get("plan") or {}
    value = record.get("position_in_window", plan.get("position_in_window"))
    if value is None:
        raise ValueError(f"missing position_in_window for {record.get('session_id')}")
    return int(value)


def _session_type(record: dict[str, Any]) -> str:
    plan = record.get("plan") or {}
    value = record.get("session_type", plan.get("session_type"))
    if value is None:
        raise ValueError(f"missing session_type for {record.get('session_id')}")
    return str(value)


def _record_field(record: dict[str, Any], field: str) -> Any:
    """Read a field from either exported top-level gold or the nested plan."""

    plan = record.get("plan") or {}
    return record[field] if field in record else plan.get(field)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_trajectory(trajectories_dir: Path, trajectory_id: str) -> Trajectory:
    path = trajectories_dir / f"{trajectory_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"missing trajectory file: {path}")
    return Trajectory.model_validate(_load_json(path))


def load_stage2_question_policy(path: Path | str) -> dict[str, dict[str, Any]]:
    """Load the event-to-question policy used by Stage 2 MCQ generation."""

    raw = load_yaml(path)
    policy: dict[str, dict[str, Any]] = {}
    for event_id, config in raw.items():
        if not isinstance(config, dict):
            raise ValueError(f"Stage 2 policy for {event_id!r} must be a mapping")
        option_pool = config.get("option_pool") or []
        if not isinstance(option_pool, list):
            raise ValueError(f"Stage 2 option_pool for {event_id!r} must be a list")
        target_memory_path = str(config.get("target_memory_path") or "")
        if not target_memory_path:
            raise ValueError(
                f"Stage 2 policy for {event_id!r} must define target_memory_path"
            )
        option_pool_type = str(config.get("option_pool_type") or "categorical")
        if option_pool_type not in {"categorical", "count", "numeric", "entity"}:
            raise ValueError(
                f"unsupported Stage 2 option_pool_type for {event_id!r}: "
                f"{option_pool_type!r}"
            )
        unique_pool = {
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            for value in option_pool
        }
        if len(unique_pool) < 4:
            raise ValueError(
                f"Stage 2 option_pool for {event_id!r} must contain at least four "
                "distinct values"
            )
        question_scope = str(config.get("question_scope") or "current_prefix")
        if question_scope not in {"current_prefix", "latest_window"}:
            raise ValueError(
                f"unsupported Stage 2 question_scope for {event_id!r}: "
                f"{question_scope!r}"
            )
        policy[str(event_id)] = {
            **config,
            "target_memory_path": target_memory_path,
            "option_pool_type": option_pool_type,
            "question_scope": question_scope,
            "option_pool": tuple(option_pool),
        }
    return policy


def _join_records(
    dialogues_dir: Path,
    gold_dir: Path,
    trajectory_id: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    dialogue_path = dialogues_dir / f"{trajectory_id}.jsonl"
    gold_path = gold_dir / f"{trajectory_id}.jsonl"
    if not dialogue_path.exists():
        raise FileNotFoundError(f"missing dialogue file: {dialogue_path}")
    if not gold_path.exists():
        raise FileNotFoundError(f"missing gold file: {gold_path}")

    dialogues = list(read_jsonl(dialogue_path))
    gold = list(read_jsonl(gold_path))
    dialogue_by_id = {row.get("session_id"): row for row in dialogues}
    gold_by_id = {row.get("session_id"): row for row in gold}
    if len(dialogue_by_id) != len(dialogues):
        raise ValueError(f"duplicate dialogue session_id in {dialogue_path}")
    if len(gold_by_id) != len(gold):
        raise ValueError(f"duplicate gold session_id in {gold_path}")
    if set(dialogue_by_id) != set(gold_by_id):
        missing_gold = sorted(set(dialogue_by_id) - set(gold_by_id), key=_session_number)
        missing_dialogue = sorted(set(gold_by_id) - set(dialogue_by_id), key=_session_number)
        raise ValueError(
            f"dialogue/gold session mismatch for {trajectory_id}: "
            f"missing_gold={missing_gold}, missing_dialogue={missing_dialogue}"
        )

    joined: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for session_id in sorted(dialogue_by_id, key=_session_number):
        dialogue = dialogue_by_id[session_id]
        session_gold = gold_by_id[session_id]
        for label, row in (("dialogue", dialogue), ("gold", session_gold)):
            if row.get("trajectory_id") != trajectory_id:
                raise ValueError(
                    f"{label} {session_id} has trajectory_id={row.get('trajectory_id')!r}, "
                    f"expected {trajectory_id!r}"
                )
        if dialogue.get("persona_id") != session_gold.get("persona_id"):
            raise ValueError(f"persona mismatch at {trajectory_id}/{session_id}")
        plan = session_gold.get("plan") or {}
        if plan.get("session_id") not in {None, session_id}:
            raise ValueError(f"gold plan/session mismatch at {trajectory_id}/{session_id}")
        joined.append((dialogue, session_gold))
    return joined


def load_mcq_windows(
    dialogues_dir: Path | str,
    gold_dir: Path | str,
    trajectories_dir: Path | str,
    *,
    trajectory_id: str | None = None,
    window_size: int = 15,
) -> list[McqWindow]:
    """Load cumulative windows and select each window's occurred target event."""

    if window_size <= 0:
        raise ValueError("window_size must be positive")
    dialogues_dir = Path(dialogues_dir)
    gold_dir = Path(gold_dir)
    trajectories_dir = Path(trajectories_dir)
    trajectory_ids = [trajectory_id] if trajectory_id else sorted(
        path.stem for path in dialogues_dir.glob("traj_*.jsonl")
    )
    if not trajectory_ids:
        raise ValueError(f"no trajectory dialogue files under {dialogues_dir}")

    windows: list[McqWindow] = []
    for current_trajectory_id in trajectory_ids:
        joined = _join_records(dialogues_dir, gold_dir, current_trajectory_id)
        trajectory = _load_trajectory(trajectories_dir, current_trajectory_id)
        instances = {item.event_instance_id: item for item in trajectory.life_event_instances}

        grouped: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for dialogue, session_gold in joined:
            grouped.setdefault(_window_index(session_gold), []).append((dialogue, session_gold))

        for current_window_index in sorted(grouped):
            block = sorted(grouped[current_window_index], key=lambda pair: _session_sort_key(pair[0]))
            positions = sorted(_position_in_window(gold) for _, gold in block)
            expected_positions = list(range(1, window_size + 1))
            if positions != expected_positions:
                raise ValueError(
                    f"window {current_trajectory_id}/{current_window_index} has positions "
                    f"{positions}, expected {expected_positions}"
                )

            target_dialogue, target_gold = block[-1]
            plan = target_gold.get("plan") or {}
            target_instance_id = (
                target_gold.get("window_event_instance_id")
                or plan.get("window_event_instance_id")
            )
            if not target_instance_id:
                raise ValueError(
                    f"window target missing window_event_instance_id: "
                    f"{current_trajectory_id}/{current_window_index}"
                )
            target_status = "occurred"
            instance = instances.get(target_instance_id)
            if instance is None:
                raise ValueError(
                    f"unknown target event instance {target_instance_id!r} in "
                    f"{current_trajectory_id}/{current_window_index}"
                )

            occurred_evidence = [
                gold
                for _, gold in block
                if _session_type(gold) == "occurred_evidence"
                and gold.get("event_status_after_session") == "occurred"
                and gold.get("linked_event_instance_id")
            ]
            occurred_instance_ids = {
                gold["linked_event_instance_id"] for gold in occurred_evidence
            }
            if occurred_instance_ids != {target_instance_id}:
                raise ValueError(
                    f"window must contain exactly one occurred event target: "
                    f"{current_trajectory_id}/{current_window_index}; "
                    f"target={target_instance_id}, "
                    f"occurred={sorted(occurred_instance_ids)}"
                )
            target_end_number = _session_number(str(target_dialogue["session_id"]))
            visible_session_ids = tuple(
                str(dialogue["session_id"])
                for dialogue, _ in joined
                if _session_number(str(dialogue["session_id"])) <= target_end_number
            )
            if len(visible_session_ids) != target_end_number:
                raise ValueError(
                    f"non-contiguous sessions before {target_dialogue['session_id']}: "
                    f"found {len(visible_session_ids)} sessions"
                )
            windows.append(
                McqWindow(
                    trajectory_id=current_trajectory_id,
                    window_index=current_window_index,
                    window_size=window_size,
                    target_session_start=str(block[0][0]["session_id"]),
                    target_session_end=str(target_dialogue["session_id"]),
                    visible_session_ids=visible_session_ids,
                    target_event_instance_id=target_instance_id,
                    target_event_id=instance.event_id,
                    target_event_label=instance.label_ko,
                    target_event_status=target_status,
                )
            )
    return windows


def _memory_cell_snapshot(cell: dict[str, Any] | None) -> dict[str, Any]:
    cell = cell or {}
    pending = cell.get("pending_proposal")
    return {
        "value": copy.deepcopy(cell.get("value")),
        "status": str(cell.get("status") or "unknown"),
        "pending_proposal": copy.deepcopy(pending) if isinstance(pending, dict) else None,
    }


def _update_fingerprint(
    source_event_instance_id: str,
    path: str,
    operation: str,
    old_value: Any,
    new_value: Any,
) -> str:
    payload = json.dumps(
        {
            "event_instance_id": source_event_instance_id,
            "path": path,
            "operation": operation,
            "old_value": old_value,
            "new_value": new_value,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    safe_path = re.sub(r"[^A-Za-z0-9_.-]+", "_", path)
    return f"{source_event_instance_id}:{safe_path}:{operation}:{digest}"


def _evidence_parts(evidence_turns: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    sessions: list[str] = []
    turns: list[str] = []
    for raw in evidence_turns:
        value = str(raw)
        session_id = value.split(":", 1)[0]
        if session_id not in sessions:
            sessions.append(session_id)
        if value not in turns:
            turns.append(value)
    return tuple(sessions), tuple(turns)


def _occurred_evidence_sessions(
    update: dict[str, Any],
    source_event_instance_id: str,
    sessions_by_id: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    evidence_sessions, _ = _evidence_parts(update.get("evidence_turns") or [])
    occurred: list[str] = []
    for session_id in evidence_sessions:
        record = sessions_by_id.get(session_id)
        if record is None:
            continue
        linked = _record_field(record, "linked_event_instance_id")
        status = _record_field(record, "event_status_after_session")
        if linked == source_event_instance_id and status == "occurred":
            occurred.append(session_id)
    return tuple(occurred)


def build_stage2_checkpoints(
    prefixes: list[dict[str, Any]],
    sessions_by_traj: dict[str, list[dict[str, Any]]] | None = None,
    initial_memory_by_traj: dict[str, dict[str, Any]] | None = None,
    question_policy: dict[str, dict[str, Any]] | None = None,
    strict_event_targets: bool = True,
    *,
    window_size: int = 15,
) -> list[Stage2Checkpoint]:
    """Build cumulative Stage 2 checkpoints from prefix gold.

    Prefix gold may contain every session or only stride-aligned checkpoints.
    Only checkpoints at 15, 30, ... sessions become evaluation windows. A
    memory update is eligible only when its source event is already occurred
    and the update has evidence in an occurred session for that event. This
    prevents pre-event "set_pending" proposals from becoming answer targets.
    """

    if window_size <= 0:
        raise ValueError("window_size must be positive")
    sessions_by_traj = sessions_by_traj or {}
    initial_memory_by_traj = initial_memory_by_traj or {}
    question_policy = question_policy or {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for prefix in prefixes:
        grouped.setdefault(str(prefix["trajectory_id"]), []).append(prefix)

    checkpoints: list[Stage2Checkpoint] = []
    for trajectory_id in sorted(grouped):
        rows = sorted(
            grouped[trajectory_id],
            key=lambda row: int(row.get("checkpoint_session_count") or len(row.get("visible_sessions") or [])),
        )
        session_records = {
            str(row["session_id"]): row
            for row in sessions_by_traj.get(trajectory_id, [])
        }
        targets: dict[str, Stage2Target] = {}
        previous_state = copy.deepcopy(initial_memory_by_traj.get(trajectory_id, {}))
        observed_occurred_ids: set[str] = set()

        for prefix in rows:
            checkpoint_count = int(
                prefix.get("checkpoint_session_count") or len(prefix.get("visible_sessions") or [])
            )
            if checkpoint_count <= 0 or checkpoint_count % window_size:
                continue

            current_state = prefix.get("gold_full_memory_state") or {}
            occurred_events = {
                str(event["event_instance_id"]): event
                for event in prefix.get("gold_life_events") or []
                if event.get("occurred") is True
            }
            newly_occurred = set(occurred_events) - observed_occurred_ids
            if len(newly_occurred) != 1:
                raise ValueError(
                    f"each {window_size}-session checkpoint must add exactly one "
                    f"occurred event: {trajectory_id}/S{checkpoint_count:03d}; "
                    f"new={sorted(newly_occurred)}"
                )
            observed_occurred_ids.update(occurred_events)

            updates_by_source: dict[str, list[dict[str, Any]]] = {}
            for update in prefix.get("gold_memory_updates") or []:
                operation = str(update.get("operation") or "")
                if operation in {"", "no_update", "set_pending"}:
                    continue
                source = str(update.get("source_event_instance_id") or "")
                path = str(update.get("path") or "")
                if not source or not path or source not in occurred_events:
                    continue
                updates_by_source.setdefault(source, []).append(update)

            # Only the newly occurred event can create a new canonical target
            # here. Older targets are reused below, so a late memory update
            # cannot make an old event appear for the first time at S+30.
            for source in sorted(newly_occurred):
                if any(target.target_event_instance_id == source for target in targets.values()):
                    continue
                event = occurred_events[source]
                event_id = str(event.get("event_id") or "")
                config = question_policy.get(event_id, {})
                preferred_path = str(config.get("target_memory_path") or "")
                candidates = updates_by_source.get(source, [])
                preferred = [
                    update for update in candidates
                    if str(update.get("path") or "") == preferred_path
                ]
                if preferred:
                    selected = sorted(
                        preferred,
                        key=lambda update: (
                            {"update": 0, "archive": 1, "mark_stale": 2}.get(
                                str(update.get("operation") or ""), 9
                            ),
                            str(update.get("path") or ""),
                        ),
                    )[0]
                elif (
                    config.get("allow_noop_current_value") is True
                    and preferred_path
                    and isinstance(current_state.get(preferred_path), dict)
                ):
                    # Some events keep the same value, so the delta engine
                    # correctly omits an update. The checkpoint memory remains
                    # authoritative for these explicitly configured policies.
                    previous_cell = previous_state.get(preferred_path) or {}
                    current_cell = current_state.get(preferred_path) or {}
                    selected = {
                        "path": preferred_path,
                        "operation": "no_change",
                        "old_value": copy.deepcopy(previous_cell.get("value")),
                        "new_value": copy.deepcopy(current_cell.get("value")),
                        "source_event_instance_id": source,
                        "evidence_turns": copy.deepcopy(
                            event.get("evidence_turns") or []
                        ),
                    }
                else:
                    # Keep the event-to-memory policy fixed. Do not silently
                    # replace a missing target path with another memory path.
                    continue
                operation = str(selected.get("operation") or "")
                path = str(selected.get("path") or "")

                occurred_evidence_sessions = _occurred_evidence_sessions(
                    selected,
                    source,
                    session_records,
                )
                if session_records and not occurred_evidence_sessions:
                    # Prefix gold is the authoritative checkpoint-level view.
                    # A stale/misaligned session-level event link must not
                    # erase an otherwise valid occurred memory update.
                    fallback_sessions, _ = _evidence_parts(
                        selected.get("evidence_turns") or []
                    )
                    occurred_evidence_sessions = tuple(
                        session_id
                        for session_id in fallback_sessions
                        if session_id in prefix.get("visible_sessions", [])
                    )
                if not session_records:
                    occurred_evidence_sessions, _ = _evidence_parts(
                        selected.get("evidence_turns") or []
                    )
                if not occurred_evidence_sessions:
                    continue

                before_state = _memory_cell_snapshot(previous_state.get(path))
                after_state = _memory_cell_snapshot(current_state.get(path))

                target_id = _update_fingerprint(
                    source,
                    path,
                    operation,
                    selected.get("old_value"),
                    selected.get("new_value"),
                )
                evidence_sessions, evidence_turns = _evidence_parts(
                    selected.get("evidence_turns") or []
                )
                evidence_sessions = tuple(
                    session_id
                    for session_id in evidence_sessions
                    if session_id in prefix.get("visible_sessions", [])
                )
                if occurred_evidence_sessions:
                    occurred_set = set(occurred_evidence_sessions)
                    evidence_sessions = tuple(
                        session_id for session_id in evidence_sessions if session_id in occurred_set
                    )
                    evidence_turns = tuple(
                        turn for turn in evidence_turns if turn.split(":", 1)[0] in occurred_set
                    )

                target_config = config
                targets[target_id] = Stage2Target(
                    canonical_target_id=target_id,
                    trajectory_id=trajectory_id,
                    target_event_instance_id=source,
                    target_event_id=event_id,
                    target_event_label=str(
                        event.get("life_event_label") or event_id or source
                    ),
                    memory_path=path,
                    operation=operation,
                    first_visible_checkpoint=checkpoint_count,
                    evidence_sessions=evidence_sessions,
                    evidence_turns=evidence_turns,
                    before_state=before_state,
                    after_state=after_state,
                    value_selector=str(target_config.get("value_selector") or "value"),
                    question_template=(
                        str(target_config["question_template"])
                        if target_config.get("question_template")
                        else None
                    ),
                    question_label=str(
                        target_config.get("question_label") or path
                    ),
                    question_scope=str(
                        target_config.get("question_scope") or "current_prefix"
                    ),
                    option_pool_type=str(
                        target_config.get("option_pool_type") or "categorical"
                    ),
                    option_pool=tuple(target_config.get("option_pool") or []),
                )

            missing_sources = [
                source
                for source in newly_occurred
                if not any(
                    target.target_event_instance_id == source
                    for target in targets.values()
                )
            ]
            if missing_sources and strict_event_targets:
                details = []
                for source in sorted(missing_sources):
                    event = occurred_events[source]
                    details.append(
                        f"{source}({event.get('event_id')})"
                    )
                raise ValueError(
                    f"occurred event has no configured memory-path update at "
                    f"its {window_size}-session checkpoint: "
                    f"{trajectory_id}/S{checkpoint_count:03d}; "
                    f"events={details}"
                )

            eligible = tuple(
                target
                for target in targets.values()
                if target.first_visible_checkpoint <= checkpoint_count
            )
            checkpoints.append(
                Stage2Checkpoint(
                    trajectory_id=trajectory_id,
                    prefix_id=str(prefix["prefix_id"]),
                    checkpoint_session_count=checkpoint_count,
                    visible_session_ids=tuple(prefix.get("visible_sessions") or []),
                    targets=eligible,
                )
            )
            previous_state = copy.deepcopy(current_state)

    return checkpoints
