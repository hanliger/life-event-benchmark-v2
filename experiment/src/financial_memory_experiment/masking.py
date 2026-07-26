from __future__ import annotations

import copy
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

from fin_life_benchmark.benchmark.item_builder import ItemBuilder
from fin_life_benchmark.benchmark.mcq_input import Stage2Target, load_stage2_question_policy
from fin_life_benchmark.gold.prefix_gold_exporter import export_prefix_gold
from fin_life_benchmark.trajectory.models import Trajectory
from scripts.mask_lifecycle_experiment import (
    _filler_provenance,
    _is_neutral_filler,
    _neutralize,
)

from .config import load_experiment_config
from .data_pipeline import ANSWER_FREE_FIELDS, active_prepared_manifest
from .paths import ExperimentPaths
from .util import read_jsonl, session_number, sha256_file, write_json, write_jsonl


LEVELS = ("full", "mask_terminal", "mask_upcoming", "mask_all", "placebo_all")
LIFECYCLE_STATUSES = ("no_event", "weak_signal", "upcoming", "occurred", "cancelled")


def _prepared_root(paths: ExperimentPaths) -> Path:
    return Path(active_prepared_manifest(paths)["root"])


def _fillers_dir(raw_root: Path) -> Path:
    candidates = (
        raw_root / "counterfactual_fillers" / "v1" / "sessions",
        raw_root / "counterfactual_fillers" / "sessions",
        raw_root / "counterfactual_fillers",
    )
    for candidate in candidates:
        if list(candidate.glob("*.jsonl")):
            return candidate
    raise FileNotFoundError("counterfactual filler session files are absent")


def _load_trajectory(path: Path) -> Trajectory:
    return Trajectory.model_validate_json(path.read_text(encoding="utf-8"))


def _answer_free(session: dict[str, Any]) -> dict[str, Any]:
    return {field: copy.deepcopy(session[field]) for field in ANSWER_FREE_FIELDS if field in session}


def _gold_status(prefix_gold: dict[str, Any], event_instance_id: str) -> str:
    for event in prefix_gold.get("gold_life_events") or []:
        if event.get("event_instance_id") == event_instance_id:
            return str(event.get("event_status") or "no_event")
    return "no_event"


def _load_filler_map(path: Path) -> dict[str, dict[str, Any]]:
    return {str(row["session_id"]): row for row in read_jsonl(path)}


def _placebo_slots(
    sessions: list[dict[str, Any]],
    *,
    checkpoint: int,
    target_event_id: str,
    target_slot_ids: list[str],
) -> list[dict[str, Any]]:
    target_numbers = [session_number(session_id) for session_id in target_slot_ids]
    candidates = [
        session
        for session in sessions[:checkpoint]
        if session.get("linked_event_instance_id") != target_event_id
        and (
            _is_neutral_filler(session)
            or (
                not session.get("linked_event_instance_id")
                and session.get("event_status_after_session") == "no_event"
            )
        )
        and session["session_id"] not in target_slot_ids
    ]
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    for target_number in target_numbers:
        compatible = [
            session
            for session in candidates
            if session["session_id"] not in used
            and len(session.get("turns") or []) == 8
        ]
        if not compatible:
            raise ValueError(
                f"insufficient placebo slots for {target_event_id}: "
                f"required={len(target_slot_ids)}, selected={len(selected)}"
            )
        chosen = min(
            compatible,
            key=lambda session: (
                abs(session_number(str(session["session_id"])) - target_number),
                str(session["session_id"]),
            ),
        )
        selected.append(chosen)
        used.add(str(chosen["session_id"]))
    return selected


def _materialize_from_recipe(
    sessions: list[dict[str, Any]],
    fillers: dict[str, dict[str, Any]],
    record: dict[str, Any],
) -> list[dict[str, Any]]:
    checkpoint = int(record["checkpoint_session_count"])
    replacements = {
        str(replacement["slot_session_id"]): fillers[str(replacement["donor_session_id"])]
        for replacement in record.get("replacements") or []
    }
    return [
        _neutralize(session, replacements[str(session["session_id"])])
        if str(session["session_id"]) in replacements
        else copy.deepcopy(session)
        for session in sessions[:checkpoint]
    ]


def _create_placebo_record(
    *,
    full_record: dict[str, Any],
    mask_all_record: dict[str, Any],
    sessions: list[dict[str, Any]],
    fillers: dict[str, dict[str, Any]],
    trajectory: Trajectory,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checkpoint = int(full_record["checkpoint_session_count"])
    event_instance_id = str(full_record["event_instance_id"])
    mask_replacements = list(mask_all_record.get("replacements") or [])
    target_slot_ids = [str(item["slot_session_id"]) for item in mask_replacements]
    placebo_slots = _placebo_slots(
        sessions,
        checkpoint=checkpoint,
        target_event_id=event_instance_id,
        target_slot_ids=target_slot_ids,
    )
    prefix_ids = {str(session["session_id"]) for session in sessions[:checkpoint]}
    variant = [copy.deepcopy(session) for session in sessions[:checkpoint]]
    index_by_id = {str(session["session_id"]): index for index, session in enumerate(variant)}
    provenance: list[dict[str, Any]] = []
    for slot, mask_replacement in zip(placebo_slots, mask_replacements):
        filler = fillers[str(mask_replacement["donor_session_id"])]
        variant[index_by_id[str(slot["session_id"])]] = _neutralize(slot, filler)
        provenance.append(_filler_provenance(slot, filler, prefix_ids))
    prefix = export_prefix_gold(
        trajectory,
        variant,
        checkpoint_stride=checkpoint,
    )[-1].model_dump(mode="json")
    if prefix != full_record["prefix_gold"]:
        raise ValueError(
            f"placebo changed gold for {event_instance_id}; neutral slot contract is invalid"
        )
    record = {
        **copy.deepcopy(full_record),
        "case_id": f"{full_record['trajectory_id']}__{event_instance_id}__placebo_all",
        "level": "placebo_all",
        "masked_session_ids": [str(slot["session_id"]) for slot in placebo_slots],
        "replacements": provenance,
        "prefix_gold": prefix,
        "placebo_target_slot_ids": target_slot_ids,
        "placebo_slot_contract": "unlinked_no_event_background",
    }
    return record, variant


def _run_four_arm_builder(paths: ExperimentPaths, root: Path, fillers_dir: Path) -> tuple[Path, Path]:
    output_dir = root / "masking"
    ladder = output_dir / "masking_ladder_four_arm.json"
    prefix_gold = output_dir / "masking_prefix_gold_four_arm.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(paths.repo_root / "scripts" / "mask_lifecycle_experiment.py"),
        "--trajectories-dir",
        str(root / "trajectories_fixed"),
        "--sessions-dir",
        str(root / "sessions_joined"),
        "--fillers-dir",
        str(fillers_dir),
        "--out",
        str(ladder),
        "--prefix-gold-out",
        str(prefix_gold),
        "--max-events",
        "10000",
        "--quiet",
    ]
    subprocess.run(command, cwd=paths.repo_root, check=True)
    return ladder, prefix_gold


def _value_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _project_value(
    *,
    cell: dict[str, Any],
    selector: str,
    memory_path: str,
    event_instance_id: str,
    operation: str,
) -> Any:
    target = Stage2Target(
        canonical_target_id="masking_projection",
        trajectory_id="masking",
        target_event_instance_id=event_instance_id,
        target_event_id="masking",
        target_event_label="masking",
        memory_path=memory_path,
        operation=operation,
        first_visible_checkpoint=0,
        evidence_sessions=(),
        evidence_turns=(),
        before_state=cell,
        after_state=cell,
        value_selector=selector,
    )
    return ItemBuilder._project_value(target, cell)


def _family_options(
    *,
    trajectory_id: str,
    event_instance_id: str,
    path: str,
    policy: dict[str, Any],
    values: list[Any],
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values + list(policy.get("option_pool") or []):
        key = _value_key(value)
        if key not in seen:
            unique.append(value)
            seen.add(key)
    if len(unique) < 4:
        unique.extend(value for value in ("미정", "변경 없음", "기타", None) if _value_key(value) not in seen)
        unique = list({_value_key(value): value for value in unique}.values())
    gold_keys = {_value_key(value) for value in values}
    selected = [value for value in unique if _value_key(value) in gold_keys]
    selected.extend(value for value in unique if _value_key(value) not in gold_keys)
    selected = selected[:4]
    missing_gold = gold_keys - {_value_key(value) for value in selected}
    if missing_gold:
        raise ValueError(f"masking family has more than four distinct gold values: {event_instance_id}")

    target = Stage2Target(
        canonical_target_id=event_instance_id,
        trajectory_id=trajectory_id,
        target_event_instance_id=event_instance_id,
        target_event_id="masking",
        target_event_label="masking",
        memory_path=path,
        operation="update",
        first_visible_checkpoint=0,
        evidence_sessions=(),
        evidence_turns=(),
        before_state={},
        after_state={},
        value_selector=str(policy.get("value_selector") or "value"),
        option_pool_type=str(policy.get("option_pool_type") or "categorical"),
        option_pool=tuple(policy.get("option_pool") or []),
    )
    payload = f"{seed}:{trajectory_id}:{event_instance_id}:memory".encode()
    random.Random(int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")).shuffle(selected)
    options = [
        {
            "option_id": "ABCD"[index],
            "text": ItemBuilder._format_option_value(target, value),
            "value": value,
        }
        for index, value in enumerate(selected)
    ]
    answer_by_key = {_value_key(option["value"]): option["option_id"] for option in options}
    return options, answer_by_key


def _lifecycle_options(seed: int, trajectory_id: str, event_instance_id: str) -> list[dict[str, Any]]:
    labels = {
        "no_event": "관련 근거 없음",
        "weak_signal": "약한 신호",
        "upcoming": "예정",
        "occurred": "발생",
        "cancelled": "취소",
    }
    statuses = list(LIFECYCLE_STATUSES)
    payload = f"{seed}:{trajectory_id}:{event_instance_id}:lifecycle".encode()
    random.Random(int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")).shuffle(statuses)
    return [
        {"option_id": "ABCDE"[index], "text": labels[status], "value": status}
        for index, status in enumerate(statuses)
    ]


def _build_masking_questions(
    paths: ExperimentPaths,
    root: Path,
    records: list[dict[str, Any]],
    variants: dict[str, Path],
) -> list[dict[str, Any]]:
    cfg = load_experiment_config(paths)
    seed = int(cfg["benchmark"]["option_seed"])
    policy = load_stage2_question_policy(
        paths.repo_root / "configs" / "registries" / "stage2_question_policy.yaml"
    )
    trajectories = {
        trajectory.trajectory_id: trajectory
        for path in sorted((root / "trajectories_fixed").glob("traj_*.json"))
        for trajectory in [_load_trajectory(path)]
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(
            (str(record["trajectory_id"]), str(record["event_instance_id"])), []
        ).append(record)

    questions: list[dict[str, Any]] = []
    for (trajectory_id, event_instance_id), family in sorted(grouped.items()):
        by_level = {str(record["level"]): record for record in family}
        if set(by_level) != set(LEVELS):
            raise ValueError(f"incomplete masking family {trajectory_id}/{event_instance_id}")
        trajectory = trajectories[trajectory_id]
        instance = next(
            item for item in trajectory.life_event_instances
            if item.event_instance_id == event_instance_id
        )
        event_policy = policy[instance.event_id]
        memory_path = str(event_policy["target_memory_path"])
        selector = str(event_policy.get("value_selector") or "value")
        operation = "update"
        full_updates = [
            update for update in by_level["full"]["prefix_gold"].get("gold_memory_updates") or []
            if update.get("source_event_instance_id") == event_instance_id
            and update.get("path") == memory_path
        ]
        if full_updates:
            operation = str(full_updates[-1].get("operation") or "update")

        values_by_level = {
            level: _project_value(
                cell=(by_level[level]["prefix_gold"].get("gold_full_memory_state") or {}).get(memory_path) or {},
                selector=selector,
                memory_path=memory_path,
                event_instance_id=event_instance_id,
                operation=operation,
            )
            for level in LEVELS
        }
        memory_options, answer_by_key = _family_options(
            trajectory_id=trajectory_id,
            event_instance_id=event_instance_id,
            path=memory_path,
            policy=event_policy,
            values=list(values_by_level.values()),
            seed=seed,
        )
        lifecycle_options = _lifecycle_options(seed, trajectory_id, event_instance_id)
        lifecycle_answer = {option["value"]: option["option_id"] for option in lifecycle_options}
        full_sessions = list(
            read_jsonl(root / "sessions_joined" / f"sessions_{trajectory_id}.jsonl")
        )
        linked = [
            session for session in full_sessions
            if session.get("linked_event_instance_id") == event_instance_id
        ]
        target_start = min(str(session["session_date"]) for session in linked)
        checkpoint = int(by_level["full"]["checkpoint_session_count"])
        target_end = str(full_sessions[checkpoint - 1]["session_date"])
        visible_ids = [str(session["session_id"]) for session in full_sessions[:checkpoint]]
        label = str(instance.label_ko)
        memory_label = str(event_policy.get("question_label") or memory_path).removeprefix("현재 ")

        for level in LEVELS:
            record = by_level[level]
            case_id = str(record["case_id"])
            common_metadata = {
                "case_id": case_id,
                "masking_level": level,
                "event_instance_id": event_instance_id,
                "event_id": instance.event_id,
                "target_date_start": target_start,
                "target_date_end": target_end,
                "query_checkpoint": checkpoint,
                "variant_sessions_file": str(variants[case_id]),
                "initial_state_protocol": "S000_ingest_once",
            }
            status = _gold_status(record["prefix_gold"], event_instance_id)
            questions.append(
                {
                    "item_id": f"{case_id}__lifecycle",
                    "stage": "masking_lifecycle_mcq",
                    "trajectory_id": trajectory_id,
                    "prefix_id": record["prefix_gold"]["prefix_id"],
                    "visible_sessions": visible_ids,
                    "question": (
                        f"{target_start}~{target_end} 기간에 관찰되는 "
                        f"'{label}' 관련 Life Event의 가장 강한 근거 상태는 무엇인가?"
                    ),
                    "options": [
                        {**option, "correct": option["option_id"] == lifecycle_answer[status]}
                        for option in lifecycle_options
                    ],
                    "gold": {"correct_option": lifecycle_answer[status], "status": status},
                    "metadata": common_metadata,
                }
            )
            value = values_by_level[level]
            correct_id = answer_by_key[_value_key(value)]
            questions.append(
                {
                    "item_id": f"{case_id}__memory",
                    "stage": "masking_memory_mcq",
                    "trajectory_id": trajectory_id,
                    "prefix_id": record["prefix_gold"]["prefix_id"],
                    "visible_sessions": visible_ids,
                    "question": (
                        f"{target_start}~{target_end} 기간 종료 시점의 "
                        f"{memory_label}은 무엇인가?"
                    ),
                    "options": [
                        {**option, "correct": option["option_id"] == correct_id}
                        for option in memory_options
                    ],
                    "gold": {
                        "correct_option": correct_id,
                        "answer_value": value,
                        "memory_path": memory_path,
                    },
                    "metadata": common_metadata,
                }
            )
    return questions


def build_masking_items(paths: ExperimentPaths) -> Path:
    cfg = load_experiment_config(paths)
    expected = cfg["dataset"]["expected"]
    root = _prepared_root(paths)
    raw_root = Path(active_prepared_manifest(paths)["raw_manifest"]["root"])
    fillers_dir = _fillers_dir(raw_root)
    ladder_path, four_arm_path = _run_four_arm_builder(paths, root, fillers_dir)
    ladder = json.loads(ladder_path.read_text(encoding="utf-8"))
    four_arm_records = list(read_jsonl(four_arm_path))
    if len(ladder) != int(expected["terminal_events"]):
        raise ValueError(f"expected 451 masking events, got {len(ladder)}")

    records_by_family: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for record in four_arm_records:
        key = (str(record["trajectory_id"]), str(record["event_instance_id"]))
        records_by_family.setdefault(key, {})[str(record["level"])] = record

    all_records: list[dict[str, Any]] = []
    variant_paths: dict[str, Path] = {}
    variants_dir = root / "masking" / "variant_sessions"
    for (trajectory_id, event_instance_id), family in sorted(records_by_family.items()):
        sessions_path = root / "sessions_joined" / f"sessions_{trajectory_id}.jsonl"
        sessions = list(read_jsonl(sessions_path))
        trajectory = _load_trajectory(root / "trajectories_fixed" / f"{trajectory_id}.json")
        filler_path = fillers_dir / f"fillers_{trajectory_id}.jsonl"
        fillers = _load_filler_map(filler_path)
        for level in ("full", "mask_terminal", "mask_upcoming", "mask_all"):
            record = family[level]
            variant = _materialize_from_recipe(sessions, fillers, record)
            case_id = str(record["case_id"])
            variant_path = variants_dir / f"{case_id}.jsonl"
            write_jsonl(variant_path, (_answer_free(session) for session in variant))
            variant_paths[case_id] = variant_path
            all_records.append(record)
        placebo, placebo_variant = _create_placebo_record(
            full_record=family["full"],
            mask_all_record=family["mask_all"],
            sessions=sessions,
            fillers=fillers,
            trajectory=trajectory,
        )
        placebo_path = variants_dir / f"{placebo['case_id']}.jsonl"
        write_jsonl(placebo_path, (_answer_free(session) for session in placebo_variant))
        variant_paths[str(placebo["case_id"])] = placebo_path
        all_records.append(placebo)

    cases_path = root / "masking" / "masking_cases_five_arm.jsonl"
    write_jsonl(cases_path, all_records)
    questions = _build_masking_questions(paths, root, all_records, variant_paths)
    questions_path = root / "masking_items" / "masking_questions.jsonl"
    write_jsonl(questions_path, questions)
    if len(all_records) != int(expected["masking_cases"]):
        raise ValueError(f"expected {expected['masking_cases']} masking cases, got {len(all_records)}")
    if len(questions) != int(expected["masking_questions"]):
        raise ValueError(
            f"expected {expected['masking_questions']} masking questions, got {len(questions)}"
        )
    write_json(
        root / "masking_items" / "manifest.json",
        {
            "schema_version": "masking-items-manifest-v1",
            "arms": list(LEVELS),
            "events": len(ladder),
            "cases": len(all_records),
            "questions": len(questions),
            "cases_sha256": sha256_file(cases_path),
            "questions_sha256": sha256_file(questions_path),
        },
    )
    return questions_path


def validate_masking_items(paths: ExperimentPaths) -> dict[str, Any]:
    cfg = load_experiment_config(paths)
    expected = cfg["dataset"]["expected"]
    root = _prepared_root(paths)
    rows = list(read_jsonl(root / "masking_items" / "masking_questions.jsonl"))
    errors: list[str] = []
    if len(rows) != int(expected["masking_questions"]):
        errors.append(f"question count {len(rows)}")
    by_family: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        metadata = row.get("metadata") or {}
        key = (
            str(row["trajectory_id"]),
            str(metadata.get("event_instance_id")),
            str(row["stage"]),
        )
        by_family.setdefault(key, set()).add(str(metadata.get("masking_level")))
        options = row.get("options") or []
        if sum(bool(option.get("correct")) for option in options) != 1:
            errors.append(f"{row['item_id']}: expected exactly one correct option")
    incomplete = [key for key, levels in by_family.items() if levels != set(LEVELS)]
    if incomplete:
        errors.append(f"incomplete masking families: {incomplete[:5]}")
    report = {
        "decision": "PASS" if not errors else "FAIL",
        "questions": len(rows),
        "families": len(by_family),
        "errors": errors,
    }
    if errors:
        raise ValueError("masking validation failed:\n- " + "\n- ".join(errors[:30]))
    return report
