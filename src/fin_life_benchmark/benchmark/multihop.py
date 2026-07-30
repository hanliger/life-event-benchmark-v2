"""Build dialogue-grounded Stage 3 Multi-hop targets from PrefixGold."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..io import load_yaml


_PAYLOAD_FIELDS = (
    "gold_life_events",
    "gold_memory_updates",
    "gold_action_decisions",
    "gold_full_memory_state",
    "gold_full_action_state",
)


@dataclass(frozen=True)
class MultiHopFact:
    fact_id: str
    trajectory_id: str
    prefix_id: str
    checkpoint_session_count: int
    event_instance_id: str
    event_id: str
    event_label: str
    memory_path: str
    operation: str
    old_value: Any
    new_value: Any
    projected_value: Any
    evidence_sessions: tuple[str, ...]
    evidence_turns: tuple[str, ...]
    evidence_date: str


@dataclass(frozen=True)
class Stage3MultiHopTarget:
    canonical_target_id: str
    trajectory_id: str
    derivation_type: str
    memory_path: str
    question_label: str
    value_selector: str
    option_pool_type: str
    option_pool: tuple[Any, ...]
    hops: tuple[MultiHopFact, MultiHopFact]
    answer_value: Any
    first_visible_checkpoint: int
    prefix_id: str
    visible_session_ids: tuple[str, ...]


@dataclass(frozen=True)
class MultiHopBuildResult:
    targets: tuple[Stage3MultiHopTarget, ...]
    report: dict[str, Any]


def _value_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _semantic_value_key(memory_path: str, value: Any) -> str:
    """Collapse schema-level aliases that have the same user-facing meaning."""

    if memory_path == "housing.rent_amount" and (
        value is None
        or (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value == 0
        )
    ):
        return _value_key("__no_rent__")
    return _value_key(value)


def _session_number(session_id: str) -> int:
    value = str(session_id)
    if not value.startswith("S") or not value[1:].isdigit():
        raise ValueError(f"invalid session_id: {session_id!r}")
    return int(value[1:])


def _evidence_parts(raw_turns: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    sessions: list[str] = []
    turns: list[str] = []
    for raw in raw_turns:
        value = str(raw)
        session_id = value.split(":", 1)[0]
        if session_id not in sessions:
            sessions.append(session_id)
        if value not in turns:
            turns.append(value)
    return tuple(sessions), tuple(turns)


def _project_value(value: Any, selector: str, event_instance_id: str) -> Any:
    if selector == "amount_krw":
        if isinstance(value, dict):
            return value.get("amount_krw", value.get("amount"))
        return value
    if selector == "list_count":
        return len(value) if isinstance(value, list) else None
    if selector == "event_property_ownership":
        records = value if isinstance(value, list) else [value]
        for record in records:
            if not isinstance(record, dict):
                continue
            event_keys = {
                record.get("acquisition_event_instance_id"),
                record.get("disposal_event_instance_id"),
                record.get("event_instance_id"),
            }
            if event_instance_id in event_keys:
                return record.get("ownership_status")
        return None
    return copy.deepcopy(value)


def load_stage3_multihop_policy(path: Path | str) -> dict[str, dict[str, Any]]:
    """Load and validate the path-level Multi-hop policy."""

    raw = load_yaml(path)
    path_configs = raw.get("paths") if isinstance(raw, dict) else None
    if not isinstance(path_configs, dict) or not path_configs:
        raise ValueError("Stage 3 Multi-hop policy must define a non-empty paths map")

    allowed_derivations = {
        "state_sequence",
        "expense_aggregation",
        "count_sequence",
        "amount_comparison",
    }
    result: dict[str, dict[str, Any]] = {}
    for memory_path, raw_config in path_configs.items():
        if not isinstance(raw_config, dict):
            raise ValueError(f"Multi-hop policy for {memory_path!r} must be a mapping")
        config = dict(raw_config)
        derivation = str(config.get("derivation_type") or "")
        if derivation not in allowed_derivations:
            raise ValueError(
                f"unsupported Multi-hop derivation for {memory_path!r}: {derivation!r}"
            )
        option_pool = config.get("option_pool") or []
        if not isinstance(option_pool, list):
            raise ValueError(
                f"Multi-hop option_pool for {memory_path!r} must be a list"
            )
        excluded_values = config.get("excluded_values") or []
        if not isinstance(excluded_values, list):
            raise ValueError(
                f"Multi-hop excluded_values for {memory_path!r} must be a list"
            )
        result[str(memory_path)] = {
            **config,
            "derivation_type": derivation,
            "question_label": str(config.get("question_label") or memory_path),
            "value_selector": str(config.get("value_selector") or "value"),
            "option_pool_type": str(
                config.get("option_pool_type") or "categorical"
            ),
            "option_pool": tuple(option_pool),
            "excluded_values": tuple(excluded_values),
            "allow_same_value": bool(config.get("allow_same_value", False)),
            "allow_null": bool(config.get("allow_null", False)),
        }
    return result


def load_stage3_multihop_representative_policy(
    path: Path | str,
) -> dict[str, Any]:
    """Load the rules for selecting one representative per question axis."""

    raw = load_yaml(path)
    raw_selection = (
        raw.get("representative_selection") if isinstance(raw, dict) else None
    )
    raw_paths = raw.get("paths") if isinstance(raw, dict) else None
    if not isinstance(raw_selection, dict):
        raise ValueError(
            "Stage 3 Multi-hop policy must define representative_selection"
        )
    if not isinstance(raw_paths, dict):
        raise ValueError("Stage 3 Multi-hop policy must define paths")

    group_by = str(raw_selection.get("group_by") or "")
    if group_by != "trajectory_memory_path":
        raise ValueError(
            "representative_selection.group_by must be "
            "'trajectory_memory_path'"
        )

    raw_redundant = raw_selection.get("redundant_paths") or {}
    if not isinstance(raw_redundant, dict):
        raise ValueError(
            "representative_selection.redundant_paths must be a mapping"
        )
    redundant_paths: dict[str, str] = {}
    for redundant, preferred in raw_redundant.items():
        redundant_path = str(redundant)
        preferred_path = str(preferred)
        if redundant_path not in raw_paths or preferred_path not in raw_paths:
            raise ValueError(
                "representative_selection.redundant_paths contains a disabled "
                f"path: {redundant_path!r} -> {preferred_path!r}"
            )
        redundant_paths[redundant_path] = preferred_path

    return {
        "group_by": group_by,
        "omit_unchanged_sequences": bool(
            raw_selection.get("omit_unchanged_sequences", True)
        ),
        "redundant_paths": redundant_paths,
    }


def load_multihop_session_records(
    directory: Path | str,
) -> dict[str, list[dict[str, Any]]]:
    """Load canonical merged session JSONL files, scoped by trajectory."""

    directory = Path(directory)
    files = sorted(directory.glob("sessions_*.jsonl"))
    if not files:
        files = sorted(directory.glob("traj_*.jsonl"))
    records_by_trajectory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                trajectory_id = str(record.get("trajectory_id") or "")
                session_id = str(record.get("session_id") or "")
                if not trajectory_id or not re.fullmatch(r"S\d+", session_id):
                    raise ValueError(
                        f"invalid session identity at {path}:{line_number}: "
                        f"{trajectory_id!r}/{session_id!r}"
                    )
                key = (trajectory_id, session_id)
                if key in seen:
                    raise ValueError(f"duplicate session record: {key}")
                seen.add(key)
                records_by_trajectory[trajectory_id].append(record)
    for records in records_by_trajectory.values():
        records.sort(key=lambda row: _session_number(str(row["session_id"])))
    return dict(records_by_trajectory)


def _restore_prefix_payloads(
    prefixes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Accept either compact PrefixGold or loader-restored records."""

    restored: list[dict[str, Any]] = []
    last_by_trajectory: dict[str, dict[str, Any]] = {}
    for raw in prefixes:
        row = copy.deepcopy(raw)
        trajectory_id = str(row["trajectory_id"])
        if row.get("repeats_previous"):
            previous = last_by_trajectory.get(trajectory_id)
            if previous is None:
                raise ValueError(
                    f"PrefixGold starts with repeats_previous for {trajectory_id}"
                )
            for field in _PAYLOAD_FIELDS:
                if not row.get(field):
                    row[field] = copy.deepcopy(previous.get(field))
        else:
            last_by_trajectory[trajectory_id] = {
                field: copy.deepcopy(row.get(field)) for field in _PAYLOAD_FIELDS
            }
        restored.append(row)
    return restored


def _matching_grounded_cues(
    fact_update: dict[str, Any],
    source: str,
    session_records: dict[str, dict[str, Any]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return exact, visible user-turn grounding for one PrefixGold update."""

    evidence_sessions, evidence_turns = _evidence_parts(
        fact_update.get("evidence_turns") or []
    )
    valid_sessions: list[str] = []
    valid_turns: list[str] = []
    for session_id in evidence_sessions:
        session = session_records.get(session_id)
        if session is None:
            continue
        plan = session.get("plan") or {}
        linked = session.get(
            "linked_event_instance_id", plan.get("linked_event_instance_id")
        )
        status = session.get(
            "event_status_after_session", plan.get("event_status_after_session")
        )
        if linked != source or status != "occurred":
            continue
        turns = session.get("turns") or []
        matched = False
        for cue in session.get("cue_annotations") or []:
            if (
                cue.get("cue_type") != "memory_fact"
                or cue.get("linked_memory_path") != fact_update.get("path")
                or cue.get("linked_memory_operation")
                != fact_update.get("operation")
                or cue.get("linked_memory_value") != fact_update.get("new_value")
            ):
                continue
            index = int(cue.get("turn_index", -1))
            evidence_text = cue.get("evidence_text") or cue.get("cue_text") or ""
            if (
                0 <= index < len(turns)
                and turns[index].get("speaker") == "user"
                and evidence_text
                and evidence_text in turns[index].get("text", "")
            ):
                turn_ref = f"{session_id}:{index}"
                if turn_ref in evidence_turns and turn_ref not in valid_turns:
                    valid_turns.append(turn_ref)
                matched = True
        if matched and session_id not in valid_sessions:
            valid_sessions.append(session_id)
    return tuple(valid_sessions), tuple(valid_turns)


def _select_occurred_update(
    updates: list[dict[str, Any]],
    source: str,
    memory_path: str,
    session_records: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]] | None:
    precedence = {
        "update": 0,
        "archive": 1,
        "mark_stale": 2,
        "set_not_applicable": 3,
        "no_change": 4,
    }
    candidates: list[
        tuple[int, dict[str, Any], tuple[str, ...], tuple[str, ...]]
    ] = []
    seen: set[str] = set()
    for update in updates:
        if (
            str(update.get("source_event_instance_id") or "") != source
            or str(update.get("path") or "") != memory_path
        ):
            continue
        operation = str(update.get("operation") or "")
        if operation in {"", "set_pending", "no_update", "clear_pending"}:
            continue
        fingerprint = _value_key(
            [
                operation,
                update.get("old_value"),
                update.get("new_value"),
                update.get("evidence_turns"),
            ]
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        evidence_sessions, evidence_turns = _matching_grounded_cues(
            update, source, session_records
        )
        if not evidence_sessions or not evidence_turns:
            continue
        candidates.append(
            (
                precedence.get(operation, 99),
                update,
                evidence_sessions,
                evidence_turns,
            )
        )
    if not candidates:
        return None
    _, update, evidence_sessions, evidence_turns = sorted(
        candidates,
        key=lambda item: (item[0], _value_key(item[1].get("new_value"))),
    )[0]
    return update, evidence_sessions, evidence_turns


def _invalid_direction(fact: MultiHopFact) -> bool:
    """Reject transitions that contradict their event semantics."""

    if not isinstance(fact.old_value, (int, float)) or not isinstance(
        fact.new_value, (int, float)
    ):
        return False
    if fact.event_id == "relationship_dependent_addition":
        return fact.new_value <= fact.old_value
    if fact.event_id == "relationship_dependent_end":
        return fact.new_value >= fact.old_value
    return False


def _selector_for_path(memory_path: str) -> str:
    if memory_path == "cashflow.recent_one_off_expense":
        return "amount_krw"
    if memory_path == "household.children":
        return "list_count"
    if memory_path == "housing.properties":
        return "event_property_ownership"
    return "value"


def _session_user_text(session: dict[str, Any]) -> str:
    return " ".join(
        str(turn.get("text") or "")
        for turn in session.get("turns") or []
        if turn.get("speaker") == "user"
    )


def _fact_dialogue_text(
    fact: MultiHopFact,
    session_records: dict[str, dict[str, Any]],
) -> str:
    return " ".join(
        _session_user_text(session_records.get(session_id) or {})
        for session_id in fact.evidence_sessions
    )


def _sino_korean_number(value: int) -> str | None:
    """Return a compact Sino-Korean cardinal for values below 10,000."""

    if value < 0 or value >= 10_000:
        return None
    if value == 0:
        return "영"
    digits = "영일이삼사오육칠팔구"
    result: list[str] = []
    remainder = value
    for divisor, unit in ((1000, "천"), (100, "백"), (10, "십")):
        quotient, remainder = divmod(remainder, divisor)
        if quotient:
            if quotient > 1:
                result.append(digits[quotient])
            result.append(unit)
    if remainder:
        result.append(digits[remainder])
    return "".join(result)


def _amount_patterns(value: Any) -> tuple[re.Pattern[str], ...]:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or float(value) < 0
        or not float(value).is_integer()
    ):
        return ()
    amount = int(value)
    variants: list[str] = [re.escape(str(amount)), re.escape(f"{amount:,}")]
    patterns = [
        re.compile(rf"(?<![\d,]){variant}\s*원")
        for variant in dict.fromkeys(variants)
    ]
    if amount and amount % 10_000 == 0:
        man = amount // 10_000
        patterns.append(
            re.compile(rf"(?<![\d,]){re.escape(str(man))}\s*만\s*원")
        )
        korean = _sino_korean_number(man)
        if korean:
            patterns.append(re.compile(rf"{re.escape(korean)}\s*만\s*원"))
    if amount and amount % 100_000_000 == 0:
        eok = amount // 100_000_000
        patterns.append(
            re.compile(rf"(?<![\d,]){re.escape(str(eok))}\s*억\s*원")
        )
        korean = _sino_korean_number(eok)
        if korean:
            patterns.append(re.compile(rf"{re.escape(korean)}\s*억\s*원"))
    return tuple(patterns)


def _amount_evidence_status(text: str, value: Any) -> str:
    """Return exact, lower_bound, or missing for a KRW amount surface."""

    matched = False
    lower_bound_only = True
    for pattern in _amount_patterns(value):
        for match in pattern.finditer(text):
            matched = True
            context = text[max(0, match.start() - 8) : match.end() + 12]
            if not re.search(r"넘게|이상|초과|최소", context):
                lower_bound_only = False
    if not matched:
        return "missing"
    return "lower_bound" if lower_bound_only else "exact"


def _positive_income_stability_mention(text: str) -> bool:
    for match in re.finditer(
        r"(?<!불)안정(?:적|되|된|적으로)?|꾸준|꼬박|고정적|정기적|"
        r"(?:수입|소득|급여).{0,8}일정",
        text,
    ):
        context = text[match.start() : match.end() + 10]
        if not re.search(r"않|아니|없|못|줄|끊|불규칙", context):
            return True
    return False


def _income_stability_supported(value: Any, text: str) -> bool:
    patterns = {
        "variable": r"변동|들쭉날쭉|일정하지|매번\s*다르|달마다\s*다르|오락가락",
        "reduced": r"감소|줄었|줄어|줄어서|끊겼|끊겨|안\s*들어|못\s*받|소득.{0,6}없",
        "unstable": r"불안정|들쭉날쭉|일정하지|오락가락|정기적.{0,8}없",
    }
    if value == "stable":
        return _positive_income_stability_mention(text)
    pattern = patterns.get(str(value))
    return bool(pattern and re.search(pattern, text))


def _location_surface_mentioned(value: Any, text: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    compact_text = re.sub(r"\s+", "", text)
    parts = [part for part in re.split(r"\s+", value.strip()) if part]
    if not parts:
        return False
    locality = re.sub(r"(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구)$", "", parts[-1])
    return len(locality) >= 2 and locality in compact_text


def _employment_status_supported(value: Any, text: str) -> bool:
    patterns = {
        "employed": (
            r"재직|근무\s*중|(?:회사|직장).{0,8}다니|다니게\s*됐|"
            r"첫\s*(?:급여|월급)|(?:급여|월급).{0,8}(?:들어와|받고|받게)"
        ),
        "on_leave": (
            r"휴직|회사.{0,12}그만둔.{0,5}아니|소속.{0,8}그대로|"
            r"출근.{0,8}못.{0,12}소속|급여.{0,8}잠깐\s*끊"
        ),
        "unemployed": (
            r"무직|실직|퇴사|회사\s*다니는\s*(?:게|상태가)\s*아니|"
            r"다니던.{0,8}(?:안\s*다니|없어|끝났)|일하던.{0,8}없|"
            r"일이\s*없|요즘\s*일.{0,5}없|벌이.{0,5}없|"
            r"(?:예전|전)\s*회사|급여.{0,8}끊|월급.{0,8}끊"
        ),
        "self_employed": r"자영업|사업자\s*등록|사업.{0,8}운영|프리랜서|혼자\s*일감",
        "retired": r"은퇴|정년|직장.{0,8}그만두고.{0,8}연금|연금으로\s*생활|연금.{0,8}들어오",
    }
    pattern = patterns.get(str(value))
    return bool(pattern and re.search(pattern, text))


def _contract_type_supported(value: Any, text: str) -> bool:
    value = str(value)
    direct = {
        "jeonse": r"전세",
        "wolse": r"월세",
        "family_home": r"본가|부모님.{0,8}(?:집|같이)|가족.{0,8}(?:집|같이|거주|지내)|자녀.{0,8}(?:집|함께)",
    }
    if value in direct:
        return bool(re.search(direct[value], text))
    if value == "owner":
        if re.search(r"자가|내\s*집|제\s*명의\s*거주지", text):
            return True
        ownership = re.search(r"등기|소유권|제\s*명의|제\s*앞", text)
        residence = re.search(
            r"주소|거주지|사는\s*곳|살고|월세.{0,12}(?:없|안|정리|낼\s*일|나가)",
            text,
        )
        return bool(ownership and residence)
    if value == "other":
        recognized = r"자가|전세|월세|본가|부모님.{0,8}집|가족.{0,8}(?:집|거주|지내)"
        return not re.search(recognized, text)
    return False


def _rent_amount_supported(value: Any, text: str) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return _amount_evidence_status(text, value) != "missing"
    return bool(
        re.search(
            r"월세.{0,14}(?:없|안\s*(?:내|나가)|내지\s*않|낼\s*(?:필요|일).{0,4}없|정리|그럴\s*일.{0,4}없)",
            text,
        )
    )


def _marital_status_supported(value: Any, text: str) -> bool:
    patterns = {
        "single": r"미혼",
        "married": r"결혼|기혼|배우자|남편|아내",
        "separated": r"별거",
        "divorced": r"이혼",
        "widowed": r"사별|배우자.{0,8}(?:사망|장례|돌아가)|남편.{0,8}(?:사망|장례|돌아가)|아내.{0,8}(?:사망|장례|돌아가)|장례",
    }
    pattern = patterns.get(str(value))
    return bool(pattern and re.search(pattern, text))


def _value_surface_mentioned(memory_path: str, value: Any, text: str) -> bool:
    if not text:
        return False
    if value is None:
        return bool(re.search(r"해당\s*없|없(?:어요|습니다|다|고|는|음)", text))
    if memory_path == "housing.address":
        return _location_surface_mentioned(value, text)
    if memory_path in {
        "cashflow.recent_one_off_expense",
        "housing.rent_amount",
    }:
        return _amount_evidence_status(text, value) != "missing"
    if memory_path == "employment.salary_day" and isinstance(value, (int, float)):
        return bool(re.search(rf"(?<!\d){int(value)}\s*일(?!\d)", text))
    if memory_path in {"household.dependents", "household.children"} and isinstance(
        value, (int, float)
    ):
        count = int(value)
        korean = {0: "영", 1: "한", 2: "두", 3: "세", 4: "네"}.get(count)
        patterns = [rf"(?<!\d){count}\s*명(?!\d)"]
        if korean:
            patterns.append(rf"{korean}\s*명")
        if count == 0:
            patterns.append(r"자녀.{0,5}없|부양가족.{0,5}없")
        return any(re.search(pattern, text) for pattern in patterns)

    aliases: dict[str, dict[str, str]] = {
        "employment.employment_status": {
            "employed": (
                r"재직|직장.{0,6}다니|회사.{0,6}다니|다니던|근무\s*중|"
                r"(?:급여|월급).{0,5}들어오던|월급.{0,6}받|소속.{0,6}그대로|"
                r"그대로\s*다니"
            ),
            "on_leave": r"휴직",
            "unemployed": (
                r"무직|실직|퇴사|일이\s*없|직장이\s*없|회사.{0,5}안\s*다니|"
                r"(?:지난달까지|예전에는|그동안).{0,12}일(?:을)?\s*안\s*하|"
                r"일(?:을)?\s*안\s*하고\s*있었"
            ),
            "self_employed": r"자영업|프리랜서|사업(?:을|체|자|장)",
            "retired": r"은퇴|정년",
        },
        "employment.income_stability": {
            "stable": r"(?<!불)안정|꾸준|꼬박|고정적|정기적",
            "variable": r"변동|들쭉날쭉|일정하지|매번\s*다르|달마다\s*다르|오락가락",
            "reduced": r"감소|줄었|줄어|끊겼|끊겨|안\s*들어|못\s*받",
            "unstable": r"불안정|들쭉날쭉|일정하지|오락가락",
        },
        "housing.contract_type": {
            "owner": r"자가|제\s*집|내\s*집",
            "jeonse": r"전세",
            "wolse": r"월세",
            "family_home": r"본가|부모님.{0,5}집|가족.{0,5}집|가족과\s*거주",
        },
        "household.marital_status": {
            "single": r"미혼",
            "married": r"기혼|결혼|배우자|남편|아내",
            "separated": r"별거",
            "divorced": r"이혼",
            "widowed": r"사별|배우자.{0,5}사망|남편.{0,5}사망|아내.{0,5}사망",
        },
    }
    pattern = aliases.get(memory_path, {}).get(str(value))
    if pattern:
        if memory_path == "employment.income_stability" and value == "stable":
            return _positive_income_stability_mention(text)
        return bool(re.search(pattern, text))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(re.search(rf"(?<!\d){re.escape(str(value))}(?!\d)", text))
    if isinstance(value, str) and len(value.strip()) >= 2:
        compact_text = re.sub(r"\s+", "", text)
        compact_value = re.sub(r"\s+", "", value)
        return compact_value in compact_text
    return False


def _fact_surface_error(
    fact: MultiHopFact,
    session_records: dict[str, dict[str, Any]],
) -> str | None:
    """Return why a PrefixGold fact cannot be answered exactly from dialogue."""

    text = _fact_dialogue_text(fact, session_records)
    if fact.memory_path == "cashflow.recent_one_off_expense":
        amount_status = _amount_evidence_status(text, fact.projected_value)
        if amount_status == "missing":
            return "missing_expense_amount"
        if amount_status == "lower_bound":
            return "inexact_expense_amount"
    if fact.memory_path == "employment.income_stability":
        if fact.projected_value == "retired":
            return "income_stability_category_mismatch"
        if not _income_stability_supported(fact.projected_value, text):
            return "unsupported_income_stability"
    if fact.memory_path == "employment.employment_status" and not _employment_status_supported(
        fact.projected_value, text
    ):
        return "unsupported_employment_status"
    if fact.memory_path == "employment.employer":
        compact_text = re.sub(r"\s+", "", text)
        compact_value = re.sub(r"\s+", "", str(fact.projected_value or ""))
        if not compact_value or compact_value not in compact_text:
            return "unsupported_employer"
    if fact.memory_path == "employment.salary_day" and not _value_surface_mentioned(
        fact.memory_path, fact.projected_value, text
    ):
        return "unsupported_salary_day"
    if fact.memory_path == "housing.address":
        residence_context = re.search(
            r"주소|거주지|사는\s*곳|살고|살게|옮겼|이사|전세|월세|본가|가족.{0,8}지내",
            text,
        )
        if not residence_context or not _location_surface_mentioned(
            fact.projected_value, text
        ):
            return "unsupported_residential_address"
    if fact.memory_path == "housing.contract_type" and not _contract_type_supported(
        fact.projected_value, text
    ):
        return "unsupported_contract_type"
    if fact.memory_path == "housing.rent_amount" and not _rent_amount_supported(
        fact.projected_value, text
    ):
        return "unsupported_rent_amount"
    if fact.memory_path == "household.marital_status" and not _marital_status_supported(
        fact.projected_value, text
    ):
        return "ambiguous_marital_status"
    return None


def _initial_projected_value(
    initial_memory: dict[str, Any],
    memory_path: str,
    selector: str,
) -> tuple[bool, Any]:
    if memory_path not in initial_memory:
        return False, None
    cell = initial_memory[memory_path]
    value = cell.get("value") if isinstance(cell, dict) and "value" in cell else cell
    return True, _project_value(value, selector, "")


def _second_hop_shortcut(
    first: MultiHopFact,
    second: MultiHopFact,
    session_records: dict[str, dict[str, Any]],
) -> bool:
    """Return True when hop 2 alone reveals the answer for both dates."""

    same_value = _value_key(first.projected_value) == _value_key(
        second.projected_value
    )
    selector = _selector_for_path(first.memory_path)
    for session_id in second.evidence_sessions:
        session = session_records.get(session_id) or {}
        values: set[str] = set()
        for cue in session.get("cue_annotations") or []:
            if cue.get("linked_memory_path") != first.memory_path:
                continue
            value = _project_value(
                cue.get("linked_memory_value"),
                selector,
                second.event_instance_id,
            )
            values.add(_value_key(value))
        if not same_value and {
            _value_key(first.projected_value),
            _value_key(second.projected_value),
        } <= values:
            return True

    second_text = " ".join(
        _session_user_text(session_records.get(session_id) or {})
        for session_id in second.evidence_sessions
    )
    if same_value:
        return bool(
            re.search(r"여전히|그대로|계속|변함없이|마찬가지", second_text)
            and _value_surface_mentioned(
                first.memory_path, first.projected_value, second_text
            )
        )
    return _value_surface_mentioned(
        first.memory_path, first.projected_value, second_text
    )


def _derive_answer(
    derivation_type: str,
    first: MultiHopFact,
    second: MultiHopFact,
) -> Any:
    if derivation_type == "expense_aggregation":
        return int(first.projected_value) + int(second.projected_value)
    return [
        copy.deepcopy(first.projected_value),
        copy.deepcopy(second.projected_value),
    ]


def _target_id(
    trajectory_id: str,
    memory_path: str,
    derivation_type: str,
    first: MultiHopFact,
    second: MultiHopFact,
) -> str:
    payload = json.dumps(
        {
            "trajectory_id": trajectory_id,
            "memory_path": memory_path,
            "derivation_type": derivation_type,
            "facts": [first.fact_id, second.fact_id],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:14]
    safe_path = memory_path.replace(".", "_")
    return f"{trajectory_id}:mh:{safe_path}:{digest}"



def _is_meaningful_representative(target: Stage3MultiHopTarget) -> bool:
    """Return whether the pair captures a change rather than a repeated value."""

    if target.derivation_type == "expense_aggregation":
        return True
    first, second = target.hops
    return _semantic_value_key(
        target.memory_path, first.projected_value
    ) != _semantic_value_key(target.memory_path, second.projected_value)


def _intermediate_update_count(
    target: Stage3MultiHopTarget,
    path_updates: set[tuple[int, str]],
) -> int:
    first, second = target.hops
    return sum(
        first.checkpoint_session_count < checkpoint
        < second.checkpoint_session_count
        for checkpoint, _source_event_id in path_updates
    )


def _representative_rank(
    target: Stage3MultiHopTarget,
    path_updates: set[tuple[int, str]],
) -> tuple[int, int, int, int, str]:
    """Prefer direct, diverse, long-range pairs for a trajectory/path."""

    first, second = target.hops
    intermediate_updates = _intermediate_update_count(target, path_updates)
    same_event_type = int(first.event_id == second.event_id)
    checkpoint_span = second.checkpoint_session_count - first.checkpoint_session_count
    return (
        intermediate_updates,
        same_event_type,
        -checkpoint_span,
        -second.checkpoint_session_count,
        target.canonical_target_id,
    )


def build_stage3_multihop_targets(
    prefixes: list[dict[str, Any]],
    sessions_by_traj: dict[str, list[dict[str, Any]]],
    policy: dict[str, dict[str, Any]],
    *,
    initial_memory_by_traj: dict[str, dict[str, Any]] | None = None,
    representative_policy: dict[str, Any] | None = None,
    window_size: int = 15,
) -> MultiHopBuildResult:
    """Create a deterministic, quality-filtered Multi-hop target set."""

    if window_size <= 0:
        raise ValueError("window_size must be positive")
    initial_memory_by_traj = initial_memory_by_traj or {}
    rows = _restore_prefix_payloads(prefixes)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["trajectory_id"])].append(row)

    candidate_pool: list[Stage3MultiHopTarget] = []
    updates_by_group: dict[tuple[str, str], set[tuple[int, str]]] = defaultdict(set)
    exclusion_counts: Counter[str] = Counter()
    fact_counts: Counter[str] = Counter()

    for trajectory_id in sorted(grouped):
        trajectory_rows = sorted(
            grouped[trajectory_id],
            key=lambda row: int(
                row.get("checkpoint_session_count")
                or len(row.get("visible_sessions") or [])
            ),
        )
        session_records = {
            str(row["session_id"]): row
            for row in sessions_by_traj.get(trajectory_id, [])
        }
        if not session_records:
            raise ValueError(f"missing sessions for {trajectory_id}")
        facts_by_path: dict[str, list[MultiHopFact]] = defaultdict(list)
        observed_occurred: set[str] = set()

        for prefix in trajectory_rows:
            checkpoint = int(
                prefix.get("checkpoint_session_count")
                or len(prefix.get("visible_sessions") or [])
            )
            if checkpoint <= 0 or checkpoint % window_size:
                continue
            occurred_events = {
                str(event["event_instance_id"]): event
                for event in prefix.get("gold_life_events") or []
                if event.get("occurred") is True
            }
            newly_occurred = set(occurred_events) - observed_occurred
            observed_occurred.update(occurred_events)
            if len(newly_occurred) != 1:
                raise ValueError(
                    f"each checkpoint must add one occurred event: "
                    f"{trajectory_id}/S{checkpoint:03d}; new={sorted(newly_occurred)}"
                )
            source = next(iter(newly_occurred))
            event = occurred_events[source]
            updates = prefix.get("gold_memory_updates") or []

            for memory_path, config in policy.items():
                if any(
                    str(update.get("source_event_instance_id") or "") == source
                    and str(update.get("path") or "") == memory_path
                    for update in updates
                ):
                    updates_by_group[(trajectory_id, memory_path)].add(
                        (checkpoint, source)
                    )
                selected = _select_occurred_update(
                    updates, source, memory_path, session_records
                )
                if selected is None:
                    continue
                update, evidence_sessions, evidence_turns = selected
                evidence_session = sorted(
                    evidence_sessions, key=_session_number
                )[-1]
                session_date = session_records[evidence_session].get("session_date")
                if not session_date:
                    exclusion_counts["missing_evidence_date"] += 1
                    continue
                projected = _project_value(
                    update.get("new_value"),
                    str(config["value_selector"]),
                    source,
                )
                fact_payload = {
                    "trajectory_id": trajectory_id,
                    "event_instance_id": source,
                    "memory_path": memory_path,
                    "operation": update.get("operation"),
                    "new_value": update.get("new_value"),
                }
                fact_digest = hashlib.sha256(
                    json.dumps(
                        fact_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()[:12]
                fact = MultiHopFact(
                    fact_id=f"{source}:{memory_path}:{fact_digest}",
                    trajectory_id=trajectory_id,
                    prefix_id=str(prefix["prefix_id"]),
                    checkpoint_session_count=checkpoint,
                    event_instance_id=source,
                    event_id=str(event.get("event_id") or ""),
                    event_label=str(event.get("life_event_label") or ""),
                    memory_path=memory_path,
                    operation=str(update.get("operation") or ""),
                    old_value=copy.deepcopy(update.get("old_value")),
                    new_value=copy.deepcopy(update.get("new_value")),
                    projected_value=copy.deepcopy(projected),
                    evidence_sessions=evidence_sessions,
                    evidence_turns=evidence_turns,
                    evidence_date=str(session_date),
                )
                if any(
                    _value_key(projected) == _value_key(value)
                    for value in config.get("excluded_values", ())
                ):
                    exclusion_counts["excluded_value"] += 1
                    continue
                if _invalid_direction(fact):
                    exclusion_counts["event_direction_mismatch"] += 1
                    continue
                surface_error = _fact_surface_error(fact, session_records)
                if surface_error:
                    exclusion_counts[f"fact_{surface_error}"] += 1
                    continue
                facts_by_path[memory_path].append(fact)
                fact_counts[memory_path] += 1

        prefix_by_checkpoint = {
            int(
                row.get("checkpoint_session_count")
                or len(row.get("visible_sessions") or [])
            ): row
            for row in trajectory_rows
        }
        for memory_path, facts in facts_by_path.items():
            config = policy[memory_path]
            ordered = sorted(
                facts,
                key=lambda fact: (
                    fact.checkpoint_session_count,
                    fact.fact_id,
                ),
            )
            for first_index, first in enumerate(ordered):
                for second in ordered[first_index + 1 :]:
                    if first.event_instance_id == second.event_instance_id:
                        continue
                    if (
                        _value_key(first.projected_value)
                        != _value_key(second.projected_value)
                        and _semantic_value_key(memory_path, first.projected_value)
                        == _semantic_value_key(memory_path, second.projected_value)
                    ):
                        exclusion_counts["semantically_equivalent_values"] += 1
                        continue
                    has_initial, initial_value = _initial_projected_value(
                        initial_memory_by_traj.get(trajectory_id, {}),
                        memory_path,
                        str(config["value_selector"]),
                    )
                    if has_initial and _semantic_value_key(
                        memory_path, first.projected_value
                    ) == _semantic_value_key(memory_path, initial_value):
                        exclusion_counts["initial_memory_shortcut"] += 1
                        continue
                    if not config["allow_null"] and (
                        first.projected_value is None
                        or second.projected_value is None
                    ):
                        exclusion_counts["null_value"] += 1
                        continue
                    if (
                        not config["allow_same_value"]
                        and _value_key(first.projected_value)
                        == _value_key(second.projected_value)
                    ):
                        exclusion_counts["same_value"] += 1
                        continue
                    if _second_hop_shortcut(first, second, session_records):
                        exclusion_counts["single_session_shortcut"] += 1
                        continue
                    endpoint = prefix_by_checkpoint[second.checkpoint_session_count]
                    endpoint_updates = endpoint.get("gold_memory_updates") or []
                    if not all(
                        any(
                            str(update.get("source_event_instance_id") or "")
                            == fact.event_instance_id
                            and str(update.get("path") or "") == fact.memory_path
                            and str(update.get("operation") or "") == fact.operation
                            and update.get("new_value") == fact.new_value
                            for update in endpoint_updates
                        )
                        for fact in (first, second)
                    ):
                        exclusion_counts["prefix_missing_hop"] += 1
                        continue
                    derivation = str(config["derivation_type"])
                    candidate_pool.append(
                        Stage3MultiHopTarget(
                            canonical_target_id=_target_id(
                                trajectory_id,
                                memory_path,
                                derivation,
                                first,
                                second,
                            ),
                            trajectory_id=trajectory_id,
                            derivation_type=derivation,
                            memory_path=memory_path,
                            question_label=str(config["question_label"]),
                            value_selector=str(config["value_selector"]),
                            option_pool_type=str(config["option_pool_type"]),
                            option_pool=tuple(config["option_pool"]),
                            hops=(first, second),
                            answer_value=_derive_answer(
                                derivation, first, second
                            ),
                            first_visible_checkpoint=second.checkpoint_session_count,
                            prefix_id=str(endpoint["prefix_id"]),
                            visible_session_ids=tuple(
                                endpoint.get("visible_sessions") or []
                            ),
                        )
                    )

    selected: list[Stage3MultiHopTarget] = []
    candidates_by_path: dict[str, list[Stage3MultiHopTarget]] = defaultdict(list)
    candidates_by_group: dict[
        tuple[str, str], list[Stage3MultiHopTarget]
    ] = defaultdict(list)
    for target in candidate_pool:
        candidates_by_path[target.memory_path].append(target)
        candidates_by_group[(target.trajectory_id, target.memory_path)].append(target)
    pool_counts = {path: len(candidates_by_path.get(path, [])) for path in policy}
    representative_selection: dict[str, dict[str, str]] = defaultdict(dict)
    representative_intermediate_updates: dict[str, dict[str, int]] = (
        defaultdict(dict)
    )

    if representative_policy is None:
        representative_policy = {
            "omit_unchanged_sequences": True,
            "redundant_paths": {},
        }

    if representative_policy is not None:
        omit_unchanged = bool(
            representative_policy.get("omit_unchanged_sequences", True)
        )
        redundant_paths = dict(
            representative_policy.get("redundant_paths") or {}
        )
        eligible_by_group: dict[
            tuple[str, str], list[Stage3MultiHopTarget]
        ] = {}
        for group, candidates in candidates_by_group.items():
            eligible = list(candidates)
            if omit_unchanged:
                eligible = [
                    target
                    for target in eligible
                    if _is_meaningful_representative(target)
                ]
            if not eligible:
                exclusion_counts["representative_unchanged_group"] += 1
                continue
            eligible_by_group[group] = eligible

        for (trajectory_id, memory_path), candidates in sorted(
            eligible_by_group.items()
        ):
            preferred_path = redundant_paths.get(memory_path)
            if preferred_path and (trajectory_id, preferred_path) in eligible_by_group:
                exclusion_counts["representative_redundant_group"] += 1
                continue
            target = min(
                candidates,
                key=lambda candidate: _representative_rank(
                    candidate, updates_by_group[(trajectory_id, memory_path)]
                ),
            )
            selected.append(target)
            representative_selection[trajectory_id][memory_path] = (
                target.canonical_target_id
            )
            intermediate_count = _intermediate_update_count(
                target, updates_by_group[(trajectory_id, memory_path)]
            )
            if intermediate_count:
                representative_intermediate_updates[trajectory_id][
                    memory_path
                ] = intermediate_count

        exclusion_counts["representative_not_selected"] += (
            len(candidate_pool) - len(selected)
        )
        selection_mode = "representative"

    selected.sort(
        key=lambda target: (
            target.trajectory_id,
            target.first_visible_checkpoint,
            target.memory_path,
            target.canonical_target_id,
        )
    )
    selected_by_path = Counter(target.memory_path for target in selected)
    selected_by_type = Counter(target.derivation_type for target in selected)
    selected_by_trajectory = Counter(target.trajectory_id for target in selected)
    report = {
        "candidate_pairs_considered": len(candidate_pool),
        "selected_target_count": len(selected),
        "selection_mode": selection_mode,
        "representative_selection": {
            trajectory_id: dict(sorted(paths.items()))
            for trajectory_id, paths in sorted(representative_selection.items())
        },
        "representative_pairs_with_intermediate_updates": sum(
            len(paths) for paths in representative_intermediate_updates.values()
        ),
        "representative_intermediate_update_counts": {
            trajectory_id: dict(sorted(paths.items()))
            for trajectory_id, paths in sorted(
                representative_intermediate_updates.items()
            )
        },
        "fact_counts_by_path": dict(sorted(fact_counts.items())),
        "candidate_pairs_by_path": dict(sorted(pool_counts.items())),
        "selected_by_path": dict(sorted(selected_by_path.items())),
        "selected_by_derivation_type": dict(sorted(selected_by_type.items())),
        "selected_by_trajectory": dict(sorted(selected_by_trajectory.items())),
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
        "window_size": window_size,
    }
    return MultiHopBuildResult(targets=tuple(selected), report=report)


def _fact_from_gold_hop(
    trajectory_id: str,
    hop: dict[str, Any],
) -> MultiHopFact:
    return MultiHopFact(
        fact_id=str(hop.get("fact_id") or ""),
        trajectory_id=trajectory_id,
        prefix_id=str(hop.get("prefix_id") or ""),
        checkpoint_session_count=int(hop.get("checkpoint_session_count") or 0),
        event_instance_id=str(hop.get("event_instance_id") or ""),
        event_id=str(hop.get("event_id") or ""),
        event_label=str(hop.get("event_label") or ""),
        memory_path=str(hop.get("memory_path") or ""),
        operation=str(hop.get("operation") or ""),
        old_value=copy.deepcopy(hop.get("old_value")),
        new_value=copy.deepcopy(hop.get("new_value")),
        projected_value=copy.deepcopy(hop.get("projected_value")),
        evidence_sessions=tuple(hop.get("evidence_sessions") or []),
        evidence_turns=tuple(hop.get("evidence_turns") or []),
        evidence_date=str(hop.get("evidence_date") or ""),
    )


def _display_date(value: str) -> str:
    parts = value.split("-")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return f"{int(parts[0])}년 {int(parts[1])}월 {int(parts[2])}일"
    return value


def audit_stage3_multihop_items(
    items: list[dict[str, Any]],
    prefixes: list[dict[str, Any]],
    sessions_by_traj: dict[str, list[dict[str, Any]]],
    policy: dict[str, dict[str, Any]] | None = None,
    expected_representatives: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Independently validate item structure, derivation, and provenance."""

    restored = _restore_prefix_payloads(prefixes)
    prefix_by_id = {
        (str(row["trajectory_id"]), str(row["prefix_id"])): row
        for row in restored
    }
    failures: list[dict[str, Any]] = []
    derivations: Counter[str] = Counter()
    trajectories: Counter[str] = Counter()
    seen_item_ids: set[str] = set()

    for item in items:
        item_id = str(item.get("item_id") or "")
        errors: list[str] = []
        trajectory_id = str(item.get("trajectory_id") or "")
        gold = item.get("gold") or {}
        hops = gold.get("hops") or []
        derivation = str(gold.get("derivation_type") or "")
        derivations[derivation] += 1
        trajectories[trajectory_id] += 1

        if not item_id or item_id in seen_item_ids:
            errors.append("duplicate_or_missing_item_id")
        seen_item_ids.add(item_id)
        if item.get("stage") != "stage3_multi_hop_mcq":
            errors.append("invalid_stage")
        if (item.get("metadata") or {}).get("reasoning_type") != "multi_hop":
            errors.append("invalid_reasoning_type")
        if gold.get("hop_count") != 2 or len(hops) != 2:
            errors.append("invalid_hop_count")

        options = item.get("options") or []
        option_ids = [option.get("option_id") for option in options]
        option_texts = [str(option.get("text") or "") for option in options]
        correct_options = [option for option in options if option.get("correct") is True]
        if option_ids != list("ABCD") or len(set(option_texts)) != 4:
            errors.append("invalid_options")
        if len(correct_options) != 1:
            errors.append("invalid_correct_option_count")
        elif gold.get("correct_option") != correct_options[0].get("option_id"):
            errors.append("correct_option_mismatch")

        prefix = prefix_by_id.get((trajectory_id, str(item.get("prefix_id") or "")))
        if prefix is None:
            errors.append("missing_endpoint_prefix")
        elif list(item.get("visible_sessions") or []) != list(
            prefix.get("visible_sessions") or []
        ):
            errors.append("visible_prefix_mismatch")

        session_records = {
            str(record["session_id"]): record
            for record in sessions_by_traj.get(trajectory_id, [])
        }
        if len(hops) == 2:
            memory_path = str(gold.get("memory_path") or "")
            if derivation == "entity_lifecycle":
                errors.append("unsupported_entity_lifecycle")
            if policy is not None and memory_path not in policy:
                errors.append("path_not_enabled_by_policy")
            try:
                fact_objects = tuple(
                    _fact_from_gold_hop(trajectory_id, hop) for hop in hops
                )
            except (TypeError, ValueError):
                fact_objects = ()
                errors.append("invalid_hop_payload")
            if len(fact_objects) == 2:
                initial_memory = (item.get("metadata") or {}).get(
                    "initial_memory"
                ) or {}
                selector = str(gold.get("value_selector") or "value")
                has_initial, initial_value = _initial_projected_value(
                    initial_memory,
                    memory_path,
                    selector,
                )
                if has_initial and _semantic_value_key(
                    memory_path, fact_objects[0].projected_value
                ) == _semantic_value_key(memory_path, initial_value):
                    errors.append("initial_memory_shortcut")
                if (
                    _value_key(fact_objects[0].projected_value)
                    != _value_key(fact_objects[1].projected_value)
                    and _semantic_value_key(
                        memory_path, fact_objects[0].projected_value
                    )
                    == _semantic_value_key(
                        memory_path, fact_objects[1].projected_value
                    )
                ):
                    errors.append("semantically_equivalent_hop_values")
                if _second_hop_shortcut(
                    fact_objects[0], fact_objects[1], session_records
                ):
                    errors.append("single_session_shortcut")
                for fact in fact_objects:
                    surface_error = _fact_surface_error(fact, session_records)
                    if surface_error:
                        errors.append(f"dialogue_semantic_error:{surface_error}")
                if policy is not None and memory_path in policy:
                    excluded_values = policy[memory_path].get("excluded_values", ())
                    if any(
                        _value_key(fact.projected_value) == _value_key(value)
                        for fact in fact_objects
                        for value in excluded_values
                    ):
                        errors.append("excluded_policy_value")
            first_date = str(hops[0].get("evidence_date") or "")
            second_date = str(hops[1].get("evidence_date") or "")
            if not first_date or not second_date or first_date >= second_date:
                errors.append("non_chronological_hop_dates")
            question = str(item.get("question") or "")
            if re.search(r"\bS\d+\b", question):
                errors.append("session_id_leak_in_question")
            if any(_display_date(value) not in question for value in (first_date, second_date)):
                errors.append("question_missing_hop_date")
            if any(
                _display_date(value) in option_text
                for value in (first_date, second_date)
                for option_text in option_texts
            ):
                errors.append("option_contains_hop_date")
            event_ids = [str(hop.get("event_instance_id") or "") for hop in hops]
            if not all(event_ids) or len(set(event_ids)) != 2:
                errors.append("non_distinct_source_events")
            for hop in hops:
                event_label = str(hop.get("event_label") or "")
                if event_label and event_label in question:
                    errors.append("event_label_leak_in_question")
                    break

            projected = [copy.deepcopy(hop.get("projected_value")) for hop in hops]
            option_error_types = {
                option.get("error_type") for option in options
            }
            if derivation == "expense_aggregation":
                if not all(
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in projected
                ):
                    errors.append("non_numeric_expense_hop")
                    derived_answer = None
                else:
                    derived_answer = projected[0] + projected[1]
                    if projected[0] == projected[1]:
                        expected_errors = {
                            None,
                            "first_hop_only",
                            "underestimated_sum",
                            "overestimated_sum",
                        }
                        if option_error_types != expected_errors:
                            errors.append("invalid_equal_expense_distractor_pattern")
                    if any(
                        str(option.get("text") or "").replace(",", "") == "0원"
                        for option in options
                    ):
                        errors.append("zero_expense_distractor")
            else:
                derived_answer = projected
                same_value = _semantic_value_key(
                    memory_path, projected[0]
                ) == _semantic_value_key(memory_path, projected[1])
                expected_errors = {
                    None,
                    "wrong_first_hop",
                    "wrong_second_hop",
                    (
                        "wrong_both_hops"
                        if same_value
                        else "reversed_hop_order"
                    ),
                }
                if option_error_types != expected_errors:
                    errors.append("invalid_sequence_distractor_pattern")
            if _value_key(derived_answer) != _value_key(gold.get("answer_value")):
                errors.append("derived_answer_mismatch")

            endpoint_updates = (prefix or {}).get("gold_memory_updates") or []
            for hop in hops:
                source = str(hop.get("event_instance_id") or "")
                path = str(hop.get("memory_path") or "")
                operation = str(hop.get("operation") or "")
                new_value = hop.get("new_value")
                update_exists = any(
                    str(update.get("source_event_instance_id") or "") == source
                    and str(update.get("path") or "") == path
                    and str(update.get("operation") or "") == operation
                    and update.get("new_value") == new_value
                    for update in endpoint_updates
                )
                if not update_exists:
                    errors.append(f"prefix_missing_hop:{source}:{path}")
                    continue
                evidence_sessions, evidence_turns = _matching_grounded_cues(
                    {
                        "path": path,
                        "operation": operation,
                        "new_value": new_value,
                        "evidence_turns": hop.get("evidence_turns") or [],
                    },
                    source,
                    session_records,
                )
                if not evidence_sessions or not evidence_turns:
                    errors.append(f"dialogue_missing_hop:{source}:{path}")
                if not set(evidence_sessions) <= set(item.get("visible_sessions") or []):
                    errors.append(f"invisible_hop_evidence:{source}:{path}")

        if errors:
            failures.append({"item_id": item_id, "errors": sorted(set(errors))})

    selection_failures: list[dict[str, Any]] = []
    if expected_representatives is not None:
        expected_by_group = {
            (trajectory_id, memory_path): target_id
            for trajectory_id, paths in expected_representatives.items()
            for memory_path, target_id in paths.items()
        }
        items_by_group: dict[tuple[str, str], list[dict[str, Any]]] = (
            defaultdict(list)
        )
        for item in items:
            trajectory_id = str(item.get("trajectory_id") or "")
            memory_path = str((item.get("gold") or {}).get("memory_path") or "")
            items_by_group[(trajectory_id, memory_path)].append(item)

        unexpected = sorted(set(items_by_group) - set(expected_by_group))
        for trajectory_id, memory_path in unexpected:
            selection_failures.append(
                {
                    "error": "unexpected_representative_group",
                    "trajectory_id": trajectory_id,
                    "memory_path": memory_path,
                }
            )

        for (trajectory_id, memory_path), expected_target_id in sorted(
            expected_by_group.items()
        ):
            selected_items = items_by_group.get((trajectory_id, memory_path), [])
            if len(selected_items) != 1:
                selection_failures.append(
                    {
                        "error": "representative_item_count",
                        "trajectory_id": trajectory_id,
                        "memory_path": memory_path,
                        "expected": 1,
                        "actual": len(selected_items),
                    }
                )
                continue
            actual_target_id = str(
                (selected_items[0].get("gold") or {}).get(
                    "canonical_target_id"
                )
                or ""
            )
            if actual_target_id != expected_target_id:
                selection_failures.append(
                    {
                        "error": "representative_target_mismatch",
                        "trajectory_id": trajectory_id,
                        "memory_path": memory_path,
                        "expected_target_id": expected_target_id,
                        "actual_target_id": actual_target_id,
                    }
                )

    return {
        "passed": not failures and not selection_failures,
        "items": len(items),
        "passed_items": len(items) - len(failures),
        "failed_items": len(failures),
        "by_derivation_type": dict(sorted(derivations.items())),
        "by_trajectory": dict(sorted(trajectories.items())),
        "failures": failures,
        "selection_failures": selection_failures,
    }
