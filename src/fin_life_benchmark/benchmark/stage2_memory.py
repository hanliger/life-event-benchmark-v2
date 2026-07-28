"""Stage 2 memory-value item construction.

Stage 2 asks for the effective value of memory facts at dated 15-session
checkpoints. Targets come from occurred event deltas, while question wording
and answer format are determined by the memory path/selector policy.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..fsm.models import EventInstance, EventStatus
from ..gold.prefix_gold_exporter import serialize_memory_state
from ..io import RepoPaths, load_yaml
from ..memory.delta_engine import DeltaEngine
from ..memory.models import (
    FinancialMemoryState,
    MemoryOperation,
    MemoryUpdate,
)
from ..trajectory.models import Trajectory
from .models import BenchmarkItem, CounterfactualOption

_VALUE_OPERATIONS = {MemoryOperation.CREATE, MemoryOperation.UPDATE}
_DEACTIVATING_OPERATIONS = {
    MemoryOperation.ARCHIVE,
    MemoryOperation.MARK_STALE,
    MemoryOperation.NEEDS_VERIFICATION,
    MemoryOperation.SET_NOT_APPLICABLE,
}
_NONE_SURFACES = {
    "",
    "none",
    "null",
    "n/a",
    "없음",
    "해당없음",
    "해당 없음",
    "정보없음",
    "정보 없음",
    "등록없음",
    "등록 없음",
}


@dataclass(frozen=True)
class MemoryTarget:
    path: str
    selector: str
    selector_spec: dict[str, Any]
    entity_id: str | None
    introduced_by_event_instance_id: str
    introduced_by_event_id: str
    anchor_date: str

    @property
    def key(self) -> str:
        # A later event may update the same path. Keep each event's question
        # anchored to its own dated memory snapshot instead of overwriting it.
        return "::".join(
            (
                self.introduced_by_event_instance_id,
                self.path,
                self.selector,
                self.entity_id or "",
            )
        )


@dataclass
class TargetTouch:
    event_instance_id: str
    event_id: str
    source_operation: str
    before_value: Any
    after_value: Any

    @property
    def change_type(self) -> str:
        return "no_change" if self.before_value == self.after_value else "update"


class Stage2QuestionPolicy:
    def __init__(self, paths: RepoPaths | None = None):
        paths = paths or RepoPaths.default()
        raw = load_yaml(paths.registries / "stage2_memory_questions.yaml")
        self.version = int(raw.get("version", 1))
        self.checkpoint_stride = int(raw.get("checkpoint_stride", 15))
        self.choice_sets: dict[str, dict[str, str]] = raw.get("choice_sets") or {}
        self.paths: dict[str, dict[str, Any]] = raw.get("paths") or {}

    def path_policy(self, path: str) -> dict[str, Any]:
        try:
            return self.paths[path]
        except KeyError as exc:
            raise ValueError(
                f"Stage 2 question policy is missing memory path '{path}'"
            ) from exc

    def selector_specs(
        self,
        path: str,
        instance: EventInstance,
    ) -> list[tuple[str, dict[str, Any], str | None]]:
        policy = self.path_policy(path)
        if not policy.get("enabled", True):
            return []
        selectors = policy.get("selectors")
        if not selectors:
            return [("value", policy, None)]

        entity_ids: list[str] = []
        for param in policy.get("entity_param_candidates") or []:
            value = instance.params.get(param)
            if value is not None:
                entity_ids.append(str(value))

        expanded: list[tuple[str, dict[str, Any], str | None]] = []
        for selector, raw_spec in selectors.items():
            spec = {**policy, **(raw_spec or {})}
            spec.pop("selectors", None)
            if not spec.pop("enabled", True):
                continue
            if spec.get("scope") == "event_entity":
                expanded.extend((selector, spec, entity_id) for entity_id in entity_ids)
            else:
                expanded.append((selector, spec, None))
        return expanded

    def choices(self, spec: dict[str, Any]) -> dict[str, str]:
        if spec.get("choice_set"):
            name = spec["choice_set"]
            if name not in self.choice_sets:
                raise ValueError(f"unknown Stage 2 choice set: {name}")
            return self.choice_sets[name]
        return {str(key): str(value) for key, value in (spec.get("choices") or {}).items()}


def _cursor(key: str) -> tuple[int, int]:
    parts = str(key).split(":", 1)
    return int(parts[0]), int(parts[1]) if len(parts) == 2 else 0


def _snapshot_at(
    trajectory: Trajectory,
    month_index: int,
    transition_order: int,
    *,
    strictly_before: bool = False,
) -> FinancialMemoryState:
    snapshots = trajectory.ordered_memory_snapshots or trajectory.memory_snapshots
    target = (month_index, transition_order)
    candidates = [
        (key, _cursor(key))
        for key in snapshots
        if (_cursor(key) < target if strictly_before else _cursor(key) <= target)
    ]
    if not candidates:
        return trajectory.initial_financial_memory_state
    key = max(candidates, key=lambda item: item[1])[0]
    snapshot = snapshots[key]
    if isinstance(snapshot, FinancialMemoryState):
        return snapshot
    return FinancialMemoryState.model_validate(snapshot)


def _event_updates(
    trajectory: Trajectory,
    event_instance_id: str,
) -> list[MemoryUpdate]:
    return [
        update
        for step in trajectory.timeline_steps
        for update in step.memory_updates
        if update.source_event_instance_id == event_instance_id
        and update.event_status == EventStatus.OCCURRED.value
    ]


def _property(
    memory: FinancialMemoryState,
    entity_id: str | None,
) -> dict[str, Any] | None:
    if entity_id is None:
        return None
    properties = memory.current_value("housing.properties")
    if not isinstance(properties, list):
        return None
    return next(
        (
            item
            for item in properties
            if isinstance(item, dict) and str(item.get("property_id")) == entity_id
        ),
        None,
    )


def extract_target_value(memory: FinancialMemoryState, target: MemoryTarget) -> Any:
    raw = memory.current_value(target.path)
    selector_type = target.selector_spec.get("selector_type", "value")
    if selector_type == "value":
        return raw
    if selector_type == "dict_key":
        return raw.get(target.selector_spec["key"]) if isinstance(raw, dict) else None
    if selector_type == "list_count":
        return len(raw) if isinstance(raw, list) else 0
    if selector_type == "list_values":
        return list(raw) if isinstance(raw, list) else []
    if selector_type == "owned_property_count":
        if not isinstance(raw, list):
            return 0
        return sum(
            1
            for item in raw
            if isinstance(item, dict) and item.get("ownership_status") == "owned"
        )
    if selector_type == "property_field":
        item = _property(memory, target.entity_id)
        return item.get(target.selector_spec["field"]) if item else None
    if selector_type == "primary_residence_address":
        item = _property(memory, str(raw) if raw is not None else None)
        return item.get("address") if item else None
    raise ValueError(
        f"unknown Stage 2 selector_type '{selector_type}' for {target.path}"
    )


def _normal_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    return re.sub(r"\s+", "", text)


def _parse_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = unicodedata.normalize("NFKC", str(value)).strip().replace(",", "")
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else None


_KOREAN_DIGITS = {
    "영": 0,
    "공": 0,
    "일": 1,
    "이": 2,
    "삼": 3,
    "사": 4,
    "오": 5,
    "육": 6,
    "칠": 7,
    "팔": 8,
    "구": 9,
}
_KOREAN_SMALL_UNITS = {"십": 10, "백": 100, "천": 1_000}


def _parse_krw_component(text: str) -> float | None:
    """Parse a number component without large Korean units (억/만)."""
    if not text:
        return 1.0
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return float(text)

    if not any(unit in text for unit in _KOREAN_SMALL_UNITS):
        if text and all(char in _KOREAN_DIGITS for char in text):
            return float("".join(str(_KOREAN_DIGITS[char]) for char in text))
        return None

    total = 0.0
    pending: float | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if char.isdigit() or char == ".":
            match = re.match(r"\d+(?:\.\d+)?", text[index:])
            if match is None:
                return None
            pending = float(match.group())
            index += len(match.group())
            continue
        if char in _KOREAN_DIGITS:
            pending = float(_KOREAN_DIGITS[char])
        elif char in _KOREAN_SMALL_UNITS:
            total += (pending if pending is not None else 1.0) * _KOREAN_SMALL_UNITS[char]
            pending = None
        else:
            return None
        index += 1

    if pending is not None:
        total += pending
    return total


def _parse_krw(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = unicodedata.normalize("NFKC", str(value)).lower()
    text = text.replace(",", "").replace(" ", "").replace("원", "")
    text = re.sub(r"^(?:약|대략)", "", text)
    text = re.sub(r"(?:정도|쯤)$", "", text)
    if _normal_text(text) in _NONE_SURFACES:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return int(float(text))

    sign = -1 if text.startswith("-") else 1
    if sign < 0:
        text = text[1:]

    total = 0.0
    matched_large_unit = False
    remaining = text
    for unit, multiplier in (("억", 100_000_000), ("만", 10_000)):
        if unit not in remaining:
            continue
        component, remaining = remaining.split(unit, 1)
        parsed = _parse_krw_component(component)
        if parsed is None:
            return _parse_integer(text)
        total += parsed * multiplier
        matched_large_unit = True

    if matched_large_unit:
        if remaining:
            parsed = _parse_krw_component(remaining)
            if parsed is None:
                return _parse_integer(text)
            total += parsed
        return int(sign * total)

    parsed = _parse_krw_component(text)
    return int(sign * parsed) if parsed is not None else _parse_integer(text)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    text = str(value).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return parsed
    return [
        part.strip()
        for part in re.split(r"[,/·]|(?:\s+및\s+)|(?:\s+그리고\s+)", text)
        if part.strip()
    ]


def _canonical_text(
    value: Any,
    answer_aliases: dict[str, Any] | None,
) -> str:
    normalized = _normal_text(value)
    if not answer_aliases:
        return normalized
    aliases = {_normal_text(surface): canonical for surface, canonical in answer_aliases.items()}
    return _normal_text(aliases.get(normalized, value))


def normalize_stage2_answer(
    value: Any,
    normalizer: str | None,
    answer_aliases: dict[str, Any] | None = None,
) -> str:
    if value is None or _normal_text(value) in _NONE_SURFACES:
        return "__none__"
    if normalizer == "krw":
        parsed = _parse_krw(value)
        return "__none__" if parsed is None else str(parsed)
    if normalizer == "integer":
        parsed = _parse_integer(value)
        return "__none__" if parsed is None else str(parsed)
    if normalizer == "integer_list":
        parsed = sorted(
            item
            for item in (_parse_integer(part) for part in _as_list(value))
            if item is not None
        )
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    if normalizer == "string_list":
        parsed = sorted(
            _canonical_text(part, answer_aliases) for part in _as_list(value)
        )
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return _canonical_text(value, answer_aliases)


def _choice_key(value: Any, spec: dict[str, Any]) -> str:
    if value is None:
        key = "__none__"
    elif isinstance(value, bool):
        key = "true" if value else "false"
    else:
        key = str(value)
    aliases = {str(k): str(v) for k, v in (spec.get("value_aliases") or {}).items()}
    return aliases.get(key, key)


def _format_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.year}년 {parsed.month}월 {parsed.day}일"


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_")
    return slug or "value"


def _target_item_slug(target: MemoryTarget) -> str:
    parts = [_safe_slug(target.path)]
    if target.selector != "value":
        parts.append(_safe_slug(target.selector))
    event_digest = hashlib.sha256(
        target.introduced_by_event_instance_id.encode("utf-8")
    ).hexdigest()[:8]
    parts.append(f"event_{event_digest}")
    if target.entity_id:
        digest = hashlib.sha256(target.entity_id.encode("utf-8")).hexdigest()[:8]
        parts.append(f"entity_{digest}")
    return "_".join(parts)


class Stage2MemoryValueBuilder:
    """Build dated memory questions and evaluate them at longer prefixes."""

    def __init__(self, seed: int = 0, paths: RepoPaths | None = None):
        self.seed = seed
        self.paths = paths or RepoPaths.default()
        self.policy = Stage2QuestionPolicy(self.paths)
        self.delta_engine = DeltaEngine(self.paths)

    def _window_event_instance_id(
        self,
        prefix: dict[str, Any],
        visible_sessions: list[dict[str, Any]],
        previous_occurred: set[str],
    ) -> str | None:
        window = visible_sessions[-self.policy.checkpoint_stride :]
        anchors = {
            str(session["window_event_instance_id"])
            for session in window
            if session.get("window_event_instance_id")
        }
        if len(anchors) > 1:
            raise ValueError(
                f"{prefix['prefix_id']}: multiple window events in one checkpoint: "
                f"{sorted(anchors)}"
            )
        if anchors:
            return next(iter(anchors))

        occurred = {
            str(event["event_instance_id"])
            for event in prefix.get("gold_life_events") or []
            if event.get("event_status") == EventStatus.OCCURRED.value
        }
        new = occurred - previous_occurred
        return next(iter(new)) if len(new) == 1 else None

    @staticmethod
    def _session_lookup(sessions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(session["session_id"]): session for session in sessions}

    @staticmethod
    def _event_cursor(instance: EventInstance) -> tuple[int, int]:
        if instance.occurred_month is None:
            raise ValueError(f"{instance.event_instance_id}: occurred_month is missing")
        return instance.occurred_month, int(instance.occurred_transition_order or 0)

    def _resolved_value_updates(
        self,
        trajectory: Trajectory,
        instance: EventInstance,
        before: FinancialMemoryState,
    ) -> tuple[list[MemoryUpdate], list[MemoryUpdate]]:
        actual = _event_updates(trajectory, instance.event_instance_id)
        actual_values = [update for update in actual if update.operation in _VALUE_OPERATIONS]
        actual_status = [
            update for update in actual if update.operation in _DEACTIVATING_OPERATIONS
        ]

        candidates = self.delta_engine.resolve_transition_candidates(
            before,
            instance,
            EventStatus.OCCURRED,
            int(instance.occurred_month or 0),
        )
        value_paths = {update.path for update in actual_values}
        for candidate in candidates:
            if candidate.operation not in _VALUE_OPERATIONS:
                continue
            if candidate.path in value_paths or candidate.optional:
                continue
            if before.current_value(candidate.path) == candidate.new_value:
                candidate.old_value = before.current_value(candidate.path)
                actual_values.append(candidate)
                value_paths.add(candidate.path)
        return actual_values, actual_status

    def _targets_for_update(
        self,
        instance: EventInstance,
        update: MemoryUpdate,
        checkpoint_date: str,
    ) -> list[MemoryTarget]:
        return [
            MemoryTarget(
                path=update.path,
                selector=selector,
                selector_spec=spec,
                entity_id=entity_id,
                introduced_by_event_instance_id=instance.event_instance_id,
                introduced_by_event_id=instance.event_id,
                anchor_date=checkpoint_date,
            )
            for selector, spec, entity_id in self.policy.selector_specs(
                update.path, instance
            )
        ]

    @staticmethod
    def _question_context(
        target: MemoryTarget,
        current_memory: FinancialMemoryState,
    ) -> dict[str, str]:
        item = _property(current_memory, target.entity_id) or {}
        role_labels = {
            "primary_residence": "실거주 주택",
            "secondary_property": "비실거주 보유 주택",
        }
        return {
            "date": "",
            "anchor_date": _format_date(target.anchor_date),
            "entity_address": str(item.get("address") or "주소가 특정된 해당"),
            "entity_role_ko": role_labels.get(
                str(item.get("role")), "해당 주택"
            ),
        }

    def _build_options(
        self,
        prefix_id: str,
        target: MemoryTarget,
        answer_value: Any,
    ) -> tuple[list[CounterfactualOption], str]:
        choices = self.policy.choices(target.selector_spec)
        if len(choices) < 2:
            raise ValueError(f"{target.key}: MCQ requires at least two choices")
        correct_key = _choice_key(answer_value, target.selector_spec)
        if correct_key not in choices:
            raise ValueError(
                f"{target.key}: value {answer_value!r} is not in MCQ choices "
                f"{list(choices)}"
            )
        rows = list(choices.items())
        random.Random(f"{prefix_id}:{target.key}:{self.seed}").shuffle(rows)
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        options = [
            CounterfactualOption(
                option_id=letters[index],
                text=text,
                correct=key == correct_key,
            )
            for index, (key, text) in enumerate(rows)
        ]
        return options, next(option.option_id for option in options if option.correct)

    @staticmethod
    def _initial_memory(
        trajectory: Trajectory,
        target: MemoryTarget,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        state = trajectory.initial_financial_memory_state
        value = extract_target_value(state, target)
        cell = state.committed(target.path)
        status = cell.status.value if cell is not None else "unknown"
        if value is None and target.entity_id is not None:
            status = "not_applicable"
        display_key = (
            target.path
            if target.selector == "value"
            else f"{target.path}.{target.selector}"
        )
        source = serialize_memory_state(state)
        source_paths = {target.path}
        if target.selector_spec.get("selector_type") == "primary_residence_address":
            source_paths.add("housing.properties")
        return (
            {
                display_key: {
                    "value": value,
                    "status": status,
                    "historical_values": [],
                }
            },
            {path: source.get(path) for path in sorted(source_paths)},
        )

    def _build_item(
        self,
        prefix: dict[str, Any],
        target: MemoryTarget,
        target_memory: FinancialMemoryState,
        latest_touch: TargetTouch,
        checkpoint_change_type: str,
        trajectory: Trajectory,
        evaluation_checkpoint_date: str,
        evaluation_checkpoint_session_count: int,
        target_checkpoint_session_count: int,
    ) -> BenchmarkItem:
        answer_value = extract_target_value(target_memory, target)
        answer_type = str(target.selector_spec["answer_type"])
        normalizer = target.selector_spec.get("normalizer")
        answer_aliases = dict(target.selector_spec.get("answer_aliases") or {})
        context = self._question_context(target, target_memory)
        context["date"] = _format_date(target.anchor_date)
        question = str(target.selector_spec["question_ko"]).format(**context)
        options: list[CounterfactualOption] = []
        correct_option: str | None = None
        if answer_type == "mcq":
            options, correct_option = self._build_options(
                prefix["prefix_id"], target, answer_value
            )
        elif answer_type != "free_response":
            raise ValueError(f"{target.key}: unsupported answer_type {answer_type}")

        initial_memory, initial_memory_source = self._initial_memory(
            trajectory, target
        )
        gold: dict[str, Any] = {
            "answer_type": answer_type,
            "answer_value": answer_value,
            "normalized_answer": (
                _choice_key(answer_value, target.selector_spec)
                if answer_type == "mcq"
                else normalize_stage2_answer(answer_value, normalizer, answer_aliases)
            ),
            "memory_path": target.path,
            "value_selector": target.selector,
            "entity_id": target.entity_id,
            "checkpoint_date": target.anchor_date,
            "evaluation_checkpoint_date": evaluation_checkpoint_date,
            "target_checkpoint_session_count": target_checkpoint_session_count,
            "evaluation_checkpoint_session_count": evaluation_checkpoint_session_count,
            "target_event_instance_id": latest_touch.event_instance_id,
            "target_event_id": latest_touch.event_id,
            "source_operation": latest_touch.source_operation,
            "transition_before_value": latest_touch.before_value,
            "transition_after_value": latest_touch.after_value,
            "checkpoint_change_type": checkpoint_change_type,
        }
        if correct_option is not None:
            gold["correct_option"] = correct_option

        return BenchmarkItem(
            item_id=f"{prefix['prefix_id']}_{_target_item_slug(target)}_s2",
            stage="stage2_memory_value",
            trajectory_id=prefix["trajectory_id"],
            prefix_id=prefix["prefix_id"],
            visible_sessions=list(prefix["visible_sessions"]),
            question=question,
            options=options,
            gold=gold,
            metadata={
                "answer_type": answer_type,
                "normalizer": normalizer,
                "answer_aliases": answer_aliases,
                "checkpoint_date": target.anchor_date,
                "evaluation_checkpoint_date": evaluation_checkpoint_date,
                "checkpoint_session_count": evaluation_checkpoint_session_count,
                "target_checkpoint_session_count": target_checkpoint_session_count,
                "memory_path": target.path,
                "value_selector": target.selector,
                "question_policy_version": self.policy.version,
                "initial_memory": initial_memory,
                "initial_memory_source": initial_memory_source,
                "introduced_by_event_instance_id": (
                    target.introduced_by_event_instance_id
                ),
            },
        )

    def build(
        self,
        prefixes: list[dict[str, Any]],
        sessions_by_traj: dict[str, list[dict[str, Any]]],
        trajectories_by_traj: dict[str, Trajectory],
    ) -> list[BenchmarkItem]:
        items: list[BenchmarkItem] = []
        prefixes_by_traj: dict[str, list[dict[str, Any]]] = {}
        for prefix in prefixes:
            prefixes_by_traj.setdefault(prefix["trajectory_id"], []).append(prefix)

        for trajectory_id, trajectory_prefixes in sorted(prefixes_by_traj.items()):
            if trajectory_id not in trajectories_by_traj:
                raise ValueError(f"missing trajectory for {trajectory_id}")
            trajectory = trajectories_by_traj[trajectory_id]
            instances = {
                item.event_instance_id: item
                for item in trajectory.life_event_instances
            }
            sessions = sorted(
                sessions_by_traj.get(trajectory_id, []),
                key=lambda row: row["session_id"],
            )
            lookup = self._session_lookup(sessions)
            active: set[str] = set()
            targets: dict[str, MemoryTarget] = {}
            touches: dict[str, TargetTouch] = {}
            target_memories: dict[str, FinancialMemoryState] = {}
            target_checkpoint_counts: dict[str, int] = {}
            previous_occurred: set[str] = set()

            ordered_prefixes = sorted(
                trajectory_prefixes,
                key=lambda row: int(
                    row.get("checkpoint_session_count")
                    or len(row.get("visible_sessions") or [])
                ),
            )
            for prefix in ordered_prefixes:
                visible_ids = list(prefix.get("visible_sessions") or [])
                count = int(prefix.get("checkpoint_session_count") or len(visible_ids))
                if count == 0 or count % self.policy.checkpoint_stride != 0:
                    previous_occurred = {
                        str(event["event_instance_id"])
                        for event in prefix.get("gold_life_events") or []
                        if event.get("event_status") == EventStatus.OCCURRED.value
                    }
                    continue
                visible = [lookup[sid] for sid in visible_ids if sid in lookup]
                if len(visible) != len(visible_ids):
                    raise ValueError(
                        f"{prefix['prefix_id']}: missing visible sessions "
                        f"({len(visible)}/{len(visible_ids)})"
                    )
                checkpoint = visible[-1]
                checkpoint_date = checkpoint.get("session_date")
                if not checkpoint_date:
                    raise ValueError(
                        f"{trajectory_id}/{checkpoint['session_id']}: session_date is "
                        "required for Stage 2; run scripts/assign_session_dates.py "
                        "or use the dated HF dialogue dataset"
                    )
                current_memory = _snapshot_at(
                    trajectory,
                    int(checkpoint["month_index"]),
                    int(checkpoint.get("transition_order", 0)),
                )
                window_event_id = self._window_event_instance_id(
                    prefix, visible, previous_occurred
                )

                if window_event_id is not None:
                    instance = instances.get(window_event_id)
                    if instance is None:
                        raise ValueError(
                            f"{prefix['prefix_id']}: unknown window event "
                            f"{window_event_id}"
                        )
                    if instance.status != EventStatus.OCCURRED:
                        raise ValueError(
                            f"{window_event_id}: Stage 2 only accepts occurred events"
                        )
                    month, order = self._event_cursor(instance)
                    before = _snapshot_at(
                        trajectory, month, order, strictly_before=True
                    )
                    after = _snapshot_at(trajectory, month, order)
                    value_updates, _ = self._resolved_value_updates(
                        trajectory, instance, before
                    )

                    for update in value_updates:
                        for proposed in self._targets_for_update(
                            instance, update, str(checkpoint_date)
                        ):
                            target = targets.setdefault(proposed.key, proposed)
                            if proposed.key not in target_memories:
                                target_memories[proposed.key] = current_memory
                                target_checkpoint_counts[proposed.key] = count
                            before_value = extract_target_value(before, target)
                            after_value = extract_target_value(after, target)
                            touches[target.key] = TargetTouch(
                                event_instance_id=instance.event_instance_id,
                                event_id=instance.event_id,
                                source_operation=(
                                    "no_change"
                                    if before_value == after_value
                                    else update.operation.value
                                ),
                                before_value=before_value,
                                after_value=after_value,
                            )
                            active.add(target.key)

                for key in sorted(active):
                    target = targets[key]
                    touch = touches[key]
                    items.append(
                        self._build_item(
                            prefix=prefix,
                            target=target,
                            target_memory=target_memories[key],
                            latest_touch=touch,
                            checkpoint_change_type=touch.change_type,
                            trajectory=trajectory,
                            evaluation_checkpoint_date=str(checkpoint_date),
                            evaluation_checkpoint_session_count=count,
                            target_checkpoint_session_count=target_checkpoint_counts[key],
                        )
                    )

                previous_occurred = {
                    str(event["event_instance_id"])
                    for event in prefix.get("gold_life_events") or []
                    if event.get("event_status") == EventStatus.OCCURRED.value
                }
        return items
