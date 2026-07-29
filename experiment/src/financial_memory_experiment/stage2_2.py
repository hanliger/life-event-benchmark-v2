from __future__ import annotations

import copy
import json
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fin_life_benchmark.gold.loader import read_prefix_gold
from fin_life_benchmark.gold.prefix_gold_exporter import (
    export_prefix_gold,
    serialize_memory_state,
)
from fin_life_benchmark.trajectory.models import Trajectory

from .config import load_experiment_config
from .data_pipeline import (
    ANSWER_FREE_FIELDS,
    _fix_trajectory,
    _hash_tree,
    _load_by_key,
    _slug,
)
from .paths import ExperimentPaths
from .util import read_jsonl, sha256_file, sha256_json, write_json, write_jsonl


STAGE2_2 = "stage2_2_reconstruct"
SCHEMA_VERSION = "stage2_2_reconstruct-v2"
PROJECTOR_VERSION = "stage2_2_observable_state-v2"

SCALAR_CLOSED_VALUES: dict[str, tuple[Any, ...]] = {
    "household.marital_status": (
        "single",
        "married",
        "separated",
        "divorced",
        "widowed",
    ),
    "household.spouse_or_partner": ("spouse", None),
    "employment.employment_status": (
        "employed",
        "self_employed",
        "unemployed",
        "on_leave",
        "retired",
        "student",
        "homemaker",
    ),
    "employment.income_stability": (
        "stable",
        "variable",
        "reduced",
        "unstable",
        "retired",
        None,
    ),
    "employment.salary_day": (10, 15, 21, 25, None),
    "employment.salary_account": ("main_checking", None),
    "housing.residence_status": (
        "owner",
        "jeonse",
        "wolse",
        "family_home",
        "other",
    ),
    "housing.contract_type": (
        "owner",
        "jeonse",
        "wolse",
        "family_home",
        "other",
        None,
    ),
    "housing.mortgage_status": ("none", "active", "closed"),
    "education.self_education_status": ("none", "enrolled", "study_abroad"),
    "education.child_education_stage": (
        "preschool",
        "primary",
        "middle",
        "high",
        "adult",
        None,
    ),
    "financial_products.pension_or_irp": ("irp", "receiving", None),
    "goals.emergency_fund": ("building", None),
    "goals.housing_deposit_goal": ("active", None),
    "goals.child_education_goal": ("active", None),
    "goals.retirement_goal": ("active", None),
}

LIST_ELEMENT_CLOSED_VALUES: dict[str, tuple[str, ...]] = {
    "financial_products.checking_accounts": ("main_checking",),
    "financial_products.savings_accounts": ("savings_1",),
    "financial_products.loans": ("mortgage", "jeonse_loan", "credit"),
}

OBJECT_FIELD_CLOSED_VALUES: dict[str, dict[str, tuple[str, ...]]] = {
    "housing.properties": {
        "role": ("primary_residence", "secondary_property"),
        "mortgage_status": ("none", "active", "closed"),
        "ownership_status": ("owned", "sold"),
    },
    "housing.primary_residence_property_id": {
        "role": ("primary_residence",),
        "mortgage_status": ("none", "active", "closed"),
        "ownership_status": ("owned",),
    },
    "cashflow.recent_one_off_expense": {
        "category": (
            "medical",
            "accident_or_disaster",
            "fraud_loss",
            "funeral",
        ),
    },
}

VALUE_KINDS: dict[str, str] = {
    "profile.age": "integer",
    "profile.locale": "string",
    "profile.region": "string",
    "household.marital_status": "closed enum",
    "household.spouse_or_partner": "closed enum or null",
    "household.children": "array of integer ages",
    "household.dependents": "integer",
    "household.child_support_arrangement": "integer KRW or null",
    "employment.employment_status": "closed enum",
    "employment.employer": "open string or null",
    "employment.occupation": "open string or null",
    "employment.income_stability": "closed enum or null",
    "employment.salary_day": "closed integer enum or null",
    "employment.salary_account": "closed enum or null",
    "housing.residence_status": "closed enum",
    "housing.address": "open string or null",
    "housing.contract_type": "closed enum or null",
    "housing.rent_amount": "integer KRW or null",
    "housing.rent_payee": "string or null",
    "housing.maintenance_fee_payee": "string or null",
    "housing.mortgage_status": "closed enum",
    "housing.properties": (
        "array of {address:open string, role:closed enum, "
        "mortgage_status:closed enum, ownership_status:closed enum}"
    ),
    "housing.primary_residence_property_id": (
        "observable primary property object "
        "{address:open string, role:closed enum, "
        "mortgage_status:closed enum, ownership_status:closed enum} or null"
    ),
    "education.self_education_status": "closed enum",
    "education.child_education_stage": "closed enum or null",
    "financial_products.checking_accounts": "array of closed enum strings",
    "financial_products.savings_accounts": "array of closed enum strings",
    "financial_products.loans": "array of closed enum strings",
    "financial_products.pension_or_irp": "closed enum or null",
    "goals.emergency_fund": "closed enum or null",
    "goals.housing_deposit_goal": "closed enum or null",
    "goals.child_education_goal": "closed enum or null",
    "goals.retirement_goal": "closed enum or null",
    "cashflow.recent_one_off_expense": (
        "object {category:string, amount_krw:integer} or null"
    ),
}

ALLOWED_STATUSES = (
    "current",
    "historical",
    "stale",
    "needs_verification",
    "unknown",
    "not_applicable",
)

_PROPERTY_FIELDS = (
    "address",
    "role",
    "mortgage_status",
    "ownership_status",
)


def _candidate_text(values: tuple[Any, ...]) -> str:
    return " | ".join(
        "null" if value is None else json.dumps(value, ensure_ascii=False)
        for value in values
    )


def value_schema_description(path: str) -> str:
    description = VALUE_KINDS[path]
    if path in SCALAR_CLOSED_VALUES:
        return (
            f"{description}; allowed values: "
            f"{_candidate_text(SCALAR_CLOSED_VALUES[path])}"
        )
    if path in LIST_ELEMENT_CLOSED_VALUES:
        return (
            f"{description}; allowed element values: "
            f"{_candidate_text(LIST_ELEMENT_CLOSED_VALUES[path])}"
        )
    if path in OBJECT_FIELD_CLOSED_VALUES:
        fields = "; ".join(
            f"{field}: {_candidate_text(values)}"
            for field, values in OBJECT_FIELD_CLOSED_VALUES[path].items()
        )
        return f"{description}; allowed object fields: {fields}"
    return description


def active_stage2_2_raw_manifest(paths: ExperimentPaths) -> dict[str, Any]:
    path = paths.stage2_2_raw / "active_manifest.json"
    if not path.exists():
        raise FileNotFoundError("Stage 2.2 raw data is absent")
    return json.loads(path.read_text(encoding="utf-8"))


def active_stage2_2_prepared_manifest(paths: ExperimentPaths) -> dict[str, Any]:
    path = paths.stage2_2_prepared / "active_manifest.json"
    if not path.exists():
        raise FileNotFoundError("Stage 2.2 prepared data is absent")
    return json.loads(path.read_text(encoding="utf-8"))


def download_stage2_2_data(
    paths: ExperimentPaths, *, revision: str | None = None
) -> Path:
    from huggingface_hub import HfApi, snapshot_download

    cfg = load_experiment_config(paths)
    stage_cfg = cfg[STAGE2_2]
    repo_id = str(cfg["dataset"]["repo_id"])
    requested_revision = revision or str(stage_cfg["revision"])
    pinned_destination = (
        paths.stage2_2_raw / "hf" / _slug(repo_id) / requested_revision
    )
    if pinned_destination.exists():
        resolved_revision = requested_revision
        destination = pinned_destination
    else:
        info = HfApi().dataset_info(repo_id, revision=requested_revision)
        resolved_revision = str(info.sha)
        destination = (
            paths.stage2_2_raw / "hf" / _slug(repo_id) / resolved_revision
        )
    if not destination.exists():
        destination.mkdir(parents=True)
        snapshot_download(
            repo_id=repo_id,
            repo_type="dataset",
            revision=resolved_revision,
            local_dir=destination,
            allow_patterns=[
                "dialogues_no_prospective/*.jsonl",
                "dialogues_no_prospective/substitution_manifest.json",
                "gold_no_prospective/*.jsonl",
                "gold/*.jsonl",
            ],
        )
    hashes = _hash_tree(
        destination,
        exclude_relative_paths={
            "manifest.json",
            "stage2_2_validation.json",
        },
    )
    manifest = {
        "schema_version": "stage2_2_raw_manifest-v1",
        "repo_id": repo_id,
        "requested_revision": requested_revision,
        "resolved_revision": resolved_revision,
        "root": str(destination),
        "files": hashes,
        "tree_hash": sha256_json(hashes),
    }
    write_json(destination / "manifest.json", manifest)
    write_json(paths.stage2_2_raw / "active_manifest.json", manifest)
    return destination


def _memory_fact_count(row: dict[str, Any]) -> int:
    return sum(
        cue.get("cue_type") == "memory_fact"
        for cue in (row.get("cue_annotations") or [])
    )


def validate_stage2_2_raw_data(paths: ExperimentPaths) -> dict[str, Any]:
    cfg = load_experiment_config(paths)[STAGE2_2]
    expected = cfg["expected"]
    manifest = active_stage2_2_raw_manifest(paths)
    root = Path(manifest["root"])
    dialogues = _load_by_key(root / str(cfg["dialogues_dir"]))
    gold = _load_by_key(root / str(cfg["gold_dir"]))
    source_gold = _load_by_key(root / str(cfg["source_gold_dir"]))
    errors: list[str] = []
    if str(manifest.get("resolved_revision")) != str(cfg["revision"]):
        errors.append(
            "resolved revision differs from the pinned Stage 2.2 revision"
        )
    if str(manifest.get("tree_hash")) != str(cfg["tree_hash"]):
        errors.append("raw tree differs from the pinned Stage 2.2 tree hash")

    if set(dialogues) != set(gold):
        errors.append("no-prospective dialogue/gold keys differ")
    if set(gold) != set(source_gold):
        errors.append("source/no-prospective gold keys differ")
    if len(gold) != int(expected["sessions"]):
        errors.append(f"expected {expected['sessions']} sessions, got {len(gold)}")

    substitutions = {
        key
        for key, row in source_gold.items()
        if row.get("session_type")
        in {"weak_signal_evidence", "upcoming_evidence"}
    }
    if len(substitutions) != int(expected["substitutions"]):
        errors.append(
            f"expected {expected['substitutions']} substitutions, "
            f"got {len(substitutions)}"
        )
    for key in sorted(substitutions):
        replacement = gold.get(key) or {}
        if replacement.get("event_status_after_session") != "no_event":
            errors.append(f"{key}: replacement is not no_event")
        if replacement.get("linked_event_instance_id") is not None:
            errors.append(f"{key}: replacement remains event-linked")
        if _memory_fact_count(replacement):
            errors.append(f"{key}: replacement contains memory_fact")

    occurred = {
        key for key, row in source_gold.items()
        if row.get("event_status_after_session") == "occurred"
    }
    for key in sorted(occurred):
        before = source_gold[key]
        after = gold.get(key) or {}
        for field in (
            "event_status_after_session",
            "linked_event_instance_id",
            "cue_annotations",
        ):
            if after.get(field) != before.get(field):
                errors.append(f"{key}: occurred Gold changed in {field}")

    trajectories = sorted({key[0] for key in gold})
    if len(trajectories) != int(expected["trajectories"]):
        errors.append(
            f"expected {expected['trajectories']} trajectories, "
            f"got {len(trajectories)}"
        )
    for trajectory_id in trajectories:
        ids = sorted(
            int(key[1][1:]) for key in gold if key[0] == trajectory_id
        )
        wanted = list(range(1, int(expected["sessions_per_trajectory"]) + 1))
        if ids != wanted:
            errors.append(f"{trajectory_id}: non-contiguous sessions")

    report = {
        "schema_version": "stage2_2_raw_validation-v1",
        "decision": "PASS" if not errors else "FAIL",
        "session_count": len(gold),
        "trajectory_count": len(trajectories),
        "substitution_count": len(substitutions),
        "occurred_session_count": len(occurred),
        "errors": errors,
        "raw_tree_hash": manifest["tree_hash"],
    }
    write_json(root / "stage2_2_validation.json", report)
    if errors:
        raise ValueError("Stage 2.2 raw validation failed:\n- " + "\n- ".join(errors[:30]))
    return report


def _observable_property(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {field: copy.deepcopy(value.get(field)) for field in _PROPERTY_FIELDS}


def project_state(raw_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    properties = (
        (raw_state.get("housing.properties") or {}).get("value") or []
    )
    primary_property_id = str(
        (raw_state.get("housing.primary_residence_property_id") or {}).get(
            "value"
        )
    )
    property_by_id = {
        str(item.get("property_id")): item
        for item in properties
        if isinstance(item, dict) and item.get("property_id")
    }
    projected: dict[str, dict[str, Any]] = {}
    for path in VALUE_KINDS:
        raw = raw_state.get(path) or {}
        value = copy.deepcopy(raw.get("value"))
        if path == "housing.properties":
            value = []
            for raw_property in properties:
                item = _observable_property(raw_property)
                if item is None:
                    continue
                property_id = str(raw_property.get("property_id"))
                if property_id == primary_property_id:
                    item["role"] = "primary_residence"
                elif (
                    item.get("ownership_status") == "owned"
                    and item.get("role") == "primary_residence"
                ):
                    item["role"] = "secondary_property"
                value.append(item)
            value.sort(
                key=lambda item: (
                    str(item.get("address")),
                    str(item.get("role")),
                    str(item.get("ownership_status")),
                )
            )
        elif path == "housing.primary_residence_property_id":
            value = _observable_property(property_by_id.get(str(value)))
            if value is not None:
                value["role"] = "primary_residence"
        projected[path] = {
            "value": value,
            "status": str(raw.get("status") or "unknown"),
        }
    return projected


def _latest_evidence_by_path(
    updates: list[dict[str, Any]],
) -> dict[str, list[str]]:
    latest: dict[str, list[str]] = {}
    for update in updates:
        path = str(update.get("path") or "")
        if path not in VALUE_KINDS:
            continue
        latest[path] = sorted(
            {
                str(turn).split(":", 1)[0].replace("S", "D", 1)
                for turn in (update.get("evidence_turns") or [])
            }
        )
    return latest


def _with_gold_evidence(
    state: dict[str, dict[str, Any]],
    initial: dict[str, dict[str, Any]],
    updates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest = _latest_evidence_by_path(updates)
    result = copy.deepcopy(state)
    for path, cell in result.items():
        changed = cell != initial[path]
        cell["evidence_session_ids"] = latest.get(path, []) if changed else []
    return result


def _closed_value_errors(path: str, value: Any) -> list[str]:
    errors: list[str] = []
    if path in SCALAR_CLOSED_VALUES:
        if value not in SCALAR_CLOSED_VALUES[path]:
            errors.append(f"invalid_closed_value:{value!r}")
        return errors
    if path in LIST_ELEMENT_CLOSED_VALUES:
        if not isinstance(value, list):
            return ["closed_collection_not_list"]
        allowed = LIST_ELEMENT_CLOSED_VALUES[path]
        for element in value:
            if element not in allowed:
                errors.append(f"invalid_closed_element:{element!r}")
        return errors
    if path not in OBJECT_FIELD_CLOSED_VALUES or value is None:
        return errors
    records = value if path == "housing.properties" else [value]
    if not isinstance(records, list):
        return ["structured_value_wrong_shape"]
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"structured_record_not_object:{index}")
            continue
        for field, allowed in OBJECT_FIELD_CLOSED_VALUES[path].items():
            if record.get(field) not in allowed:
                errors.append(
                    f"invalid_object_field:{index}:{field}:{record.get(field)!r}"
                )
    return errors


def _property_consistency_errors(
    state: dict[str, dict[str, Any]],
) -> list[str]:
    properties = state["housing.properties"]["value"]
    primary = state["housing.primary_residence_property_id"]["value"]
    if not isinstance(properties, list):
        return ["housing.properties:not_list"]
    owned_primary = [
        item
        for item in properties
        if isinstance(item, dict)
        and item.get("ownership_status") == "owned"
        and item.get("role") == "primary_residence"
    ]
    errors: list[str] = []
    if len(owned_primary) > 1:
        errors.append("housing.properties:multiple_owned_primary_residences")
    if primary is None:
        if owned_primary:
            errors.append("housing.primary_residence:missing_pointer")
    elif owned_primary != [primary]:
        errors.append("housing.primary_residence:pointer_collection_mismatch")
    return errors


def prepare_stage2_2_data(paths: ExperimentPaths) -> Path:
    validate_stage2_2_raw_data(paths)
    cfg = load_experiment_config(paths)
    stage_cfg = cfg[STAGE2_2]
    raw_manifest = active_stage2_2_raw_manifest(paths)
    raw_root = Path(raw_manifest["root"])
    output = (
        paths.stage2_2_prepared
        / f"{raw_manifest['tree_hash']}--{PROJECTOR_VERSION}"
    )
    if (output / "manifest.json").exists():
        write_json(
            paths.stage2_2_prepared / "active_manifest.json",
            json.loads((output / "manifest.json").read_text(encoding="utf-8")),
        )
        return output

    dialogues = _load_by_key(raw_root / str(stage_cfg["dialogues_dir"]))
    gold = _load_by_key(raw_root / str(stage_cfg["gold_dir"]))
    fixture_dir = paths.repo_root / "tests" / "fixtures" / "trajectories"
    prefix_rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []

    for fixture_path in sorted(fixture_dir.glob("traj_*.json")):
        fixed, _ = _fix_trajectory(json.loads(fixture_path.read_text(encoding="utf-8")))
        trajectory = Trajectory.model_validate(fixed)
        trajectory_id = trajectory.trajectory_id
        (output / "trajectories_fixed").mkdir(parents=True, exist_ok=True)
        (output / "trajectories_fixed" / fixture_path.name).write_text(
            json.dumps(fixed, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        keys = sorted(
            (key for key in dialogues if key[0] == trajectory_id),
            key=lambda key: int(key[1][1:]),
        )
        joined = [{**dialogues[key], **gold[key]} for key in keys]
        answer_free = [
            {
                field: copy.deepcopy(dialogues[key][field])
                for field in ANSWER_FREE_FIELDS
                if field in dialogues[key]
            }
            for key in keys
        ]
        write_jsonl(
            output / "sessions_joined" / f"sessions_{trajectory_id}.jsonl",
            joined,
        )
        write_jsonl(
            output / "sessions_answer_free" / f"{trajectory_id}.jsonl",
            answer_free,
        )

        raw_initial = serialize_memory_state(
            trajectory.initial_financial_memory_state
        )
        initial = project_state(raw_initial)
        s000 = {
            "schema_version": SCHEMA_VERSION,
            "trajectory_id": trajectory_id,
            "persona_id": answer_free[0].get("persona_id"),
            "session_id": "S000",
            "session_date": (
                date.fromisoformat(answer_free[0]["session_date"])
                - timedelta(days=1)
            ).isoformat(),
            "record_type": "initial_observable_financial_memory",
            "state": initial,
        }
        write_json(
            output / "initial_state_s000" / f"{trajectory_id}.json",
            s000,
        )

        prefixes = export_prefix_gold(
            trajectory, joined, checkpoint_stride=15
        )
        prefix_rows.extend(prefix.model_dump(mode="json") for prefix in prefixes)

    prefix_path = output / "prefix_gold" / "prefix_gold_checkpoints_15.jsonl"
    write_jsonl(prefix_path, prefix_rows)
    for prefix in read_prefix_gold(prefix_path):
        initial_path = (
            output
            / "initial_state_s000"
            / f"{prefix['trajectory_id']}.json"
        )
        initial = json.loads(initial_path.read_text(encoding="utf-8"))["state"]
        state = project_state(prefix["gold_full_memory_state"])
        gold_state = _with_gold_evidence(
            state, initial, prefix.get("gold_memory_updates") or []
        )
        checkpoint = int(prefix["checkpoint_session_count"])
        item_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "item_id": (
                    f"{prefix['trajectory_id']}_pfx{checkpoint:03d}"
                    "_s2_2_reconstruct"
                ),
                "stage": STAGE2_2,
                "trajectory_id": prefix["trajectory_id"],
                "prefix_id": prefix["prefix_id"],
                "visible_sessions": list(prefix["visible_sessions"]),
                "question": (
                    "초기 상태와 현재까지의 상담을 바탕으로 checkpoint 시점의 "
                    "전체 금융 memory state를 복원하세요."
                ),
                "gold": {
                    "initial_state": initial,
                    "state": gold_state,
                },
                "metadata": {
                    "query_checkpoint": checkpoint,
                    "checkpoint_session_count": checkpoint,
                    "projector_version": PROJECTOR_VERSION,
                    "required_paths": list(VALUE_KINDS),
                    "allowed_statuses": list(ALLOWED_STATUSES),
                    "max_output_tokens": int(
                        stage_cfg["smoke"]["max_output_tokens"]
                    ),
                },
            }
        )

    item_path = (
        output / "canonical_items" / "stage2_2_reconstruct.jsonl"
    )
    write_jsonl(item_path, item_rows)
    expected = stage_cfg["expected"]
    if len(item_rows) != int(expected["checkpoints"]):
        raise ValueError(
            f"expected {expected['checkpoints']} items, got {len(item_rows)}"
        )
    manifest = {
        "schema_version": "stage2_2_prepared_manifest-v1",
        "root": str(output),
        "raw_manifest": raw_manifest,
        "projector_version": PROJECTOR_VERSION,
        "value_kinds": VALUE_KINDS,
        "scalar_closed_values": SCALAR_CLOSED_VALUES,
        "list_element_closed_values": LIST_ELEMENT_CLOSED_VALUES,
        "object_field_closed_values": OBJECT_FIELD_CLOSED_VALUES,
        "allowed_statuses": list(ALLOWED_STATUSES),
        "item_count": len(item_rows),
        "item_sha256": sha256_file(item_path),
    }
    manifest["manifest_hash"] = sha256_json(manifest)
    write_json(output / "manifest.json", manifest)
    write_json(paths.stage2_2_prepared / "active_manifest.json", manifest)
    return output


def validate_stage2_2_prepared(paths: ExperimentPaths) -> dict[str, Any]:
    manifest = active_stage2_2_prepared_manifest(paths)
    root = Path(manifest["root"])
    items = list(
        read_jsonl(root / "canonical_items" / "stage2_2_reconstruct.jsonl")
    )
    errors: list[str] = []
    counts = Counter(str(item["trajectory_id"]) for item in items)
    for item in items:
        required = set(VALUE_KINDS)
        initial = set((item.get("gold") or {}).get("initial_state") or {})
        state = set((item.get("gold") or {}).get("state") or {})
        if initial != required or state != required:
            errors.append(f"{item['item_id']}: path contract mismatch")
        checkpoint = int((item.get("metadata") or {})["query_checkpoint"])
        for label in ("initial_state", "state"):
            gold_state = item["gold"][label]
            for path, cell in gold_state.items():
                for issue in _closed_value_errors(path, cell.get("value")):
                    errors.append(
                        f"{item['item_id']}:{label}:{path}:{issue}"
                    )
            for issue in _property_consistency_errors(gold_state):
                errors.append(f"{item['item_id']}:{label}:{issue}")
        for cell in (item["gold"]["state"] or {}).values():
            for public_id in cell.get("evidence_session_ids") or []:
                if not public_id.startswith("D") or int(public_id[1:]) > checkpoint:
                    errors.append(
                        f"{item['item_id']}: invalid/future evidence {public_id}"
                    )
    expected_per_trajectory = 20
    if set(counts.values()) != {expected_per_trajectory}:
        errors.append(f"checkpoint counts differ: {dict(counts)}")
    report = {
        "schema_version": "stage2_2_prepared_validation-v1",
        "decision": "PASS" if not errors else "FAIL",
        "item_count": len(items),
        "trajectory_count": len(counts),
        "changed_path_counts": {
            item["item_id"]: sum(
                item["gold"]["state"][path]["value"]
                != item["gold"]["initial_state"][path]["value"]
                or item["gold"]["state"][path]["status"]
                != item["gold"]["initial_state"][path]["status"]
                for path in VALUE_KINDS
            )
            for item in items
        },
        "errors": errors,
    }
    write_json(root / "stage2_2_prepared_validation.json", report)
    if errors:
        raise ValueError(
            "Stage 2.2 prepared validation failed:\n- "
            + "\n- ".join(errors[:30])
        )
    return report


def stage2_2_item_path(paths: ExperimentPaths) -> Path:
    root = Path(active_stage2_2_prepared_manifest(paths)["root"])
    return root / "canonical_items" / "stage2_2_reconstruct.jsonl"


def _extract_json(raw: str) -> Any | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = "\n".join(
            line
            for line in text.splitlines()
            if not line.strip().startswith("```")
        ).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def parse_stage2_2_prediction(
    raw: str, *, checkpoint: int
) -> dict[str, Any]:
    payload = _extract_json(raw)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "state": {},
        "parse_error": None,
        "validation_errors": [],
    }
    if not isinstance(payload, dict) or not isinstance(payload.get("state"), dict):
        result["parse_error"] = "invalid_json_or_missing_state"
        return result
    if payload.get("schema_version") != SCHEMA_VERSION:
        result["parse_error"] = "schema_version_mismatch"
        return result
    raw_state = payload["state"]
    extras = sorted(set(raw_state) - set(VALUE_KINDS))
    result["validation_errors"].extend(f"unknown_path:{path}" for path in extras)
    for path in VALUE_KINDS:
        raw_cell = raw_state.get(path)
        if not isinstance(raw_cell, dict):
            result["validation_errors"].append(f"{path}:missing_or_invalid_cell")
            continue
        status = raw_cell.get("status")
        if status not in ALLOWED_STATUSES:
            result["validation_errors"].append(f"{path}:invalid_status:{status!r}")
            continue
        evidence = raw_cell.get("evidence_session_ids", [])
        if not isinstance(evidence, list):
            result["validation_errors"].append(f"{path}:evidence_not_list")
            continue
        valid_evidence: list[str] = []
        evidence_error = False
        for value in evidence:
            if (
                not isinstance(value, str)
                or re.fullmatch(r"D\d{3}", value) is None
                or int(value[1:]) > checkpoint
            ):
                result["validation_errors"].append(
                    f"{path}:invalid_or_future_evidence:{value!r}"
                )
                evidence_error = True
            elif value not in valid_evidence:
                valid_evidence.append(value)
        if evidence_error:
            continue
        value_errors = _closed_value_errors(path, raw_cell.get("value"))
        if value_errors:
            result["validation_errors"].extend(
                f"{path}:{error}" for error in value_errors
            )
            continue
        result["state"][path] = {
            "value": copy.deepcopy(raw_cell.get("value")),
            "status": status,
            "evidence_session_ids": valid_evidence,
        }
    return result


def _normalize_string(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _normalize_value(path: str, value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, list):
        normalized = [_normalize_value(path, item) for item in value]
        if path in {
            "financial_products.checking_accounts",
            "financial_products.savings_accounts",
            "financial_products.loans",
        }:
            return sorted(
                {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in normalized}
            )
        if path == "household.children":
            return sorted(normalized, key=lambda item: (str(type(item)), str(item)))
        if path == "housing.properties":
            return sorted(
                normalized,
                key=lambda item: json.dumps(
                    item, ensure_ascii=False, sort_keys=True
                ),
            )
        return normalized
    if isinstance(value, dict):
        return {
            str(key): _normalize_value(path, item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _cell_equal(
    path: str, left: dict[str, Any], right: dict[str, Any]
) -> bool:
    return (
        _normalize_value(path, left.get("value"))
        == _normalize_value(path, right.get("value"))
        and left.get("status") == right.get("status")
    )


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    return (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )


def score_stage2_2(
    *,
    prediction: dict[str, Any],
    initial_state: dict[str, dict[str, Any]],
    gold_state: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    predicted_state = prediction.get("state") or {}
    counts = {
        "tn": 0,
        "fp": 0,
        "fn": 0,
        "tp_correct": 0,
        "tp_wrong_value": 0,
    }
    correct_cells = 0
    value_correct = 0
    status_correct = 0
    changed_total = 0
    unchanged_total = 0
    changed_correct = 0
    unchanged_correct = 0
    status_confusion: dict[str, dict[str, int]] = {
        status: {
            predicted: 0
            for predicted in (*ALLOWED_STATUSES, "invalid/missing")
        }
        for status in ALLOWED_STATUSES
    }
    evidence_hits = 0
    evidence_paths = 0
    valid_citations = 0
    total_citations = 0

    for path in VALUE_KINDS:
        initial = initial_state[path]
        gold = gold_state[path]
        predicted = predicted_state.get(path)
        gold_changed = not _cell_equal(path, initial, gold)
        if gold_changed:
            changed_total += 1
        else:
            unchanged_total += 1
        if predicted is None:
            predicted_changed = False
            cell_correct = False
            predicted_status = "invalid/missing"
        else:
            predicted_changed = not _cell_equal(path, initial, predicted)
            cell_correct = _cell_equal(path, predicted, gold)
            predicted_status = str(predicted.get("status"))
            if (
                _normalize_value(path, predicted.get("value"))
                == _normalize_value(path, gold.get("value"))
            ):
                value_correct += 1
            if predicted.get("status") == gold.get("status"):
                status_correct += 1
        status_confusion[str(gold["status"])][predicted_status] += 1

        if cell_correct:
            correct_cells += 1
        if gold_changed and predicted_changed:
            if cell_correct:
                counts["tp_correct"] += 1
                changed_correct += 1
            else:
                counts["tp_wrong_value"] += 1
        elif gold_changed:
            counts["fn"] += 1
        elif predicted_changed:
            counts["fp"] += 1
        else:
            counts["tn"] += 1
            if cell_correct:
                unchanged_correct += 1

        gold_evidence = set(gold.get("evidence_session_ids") or [])
        if gold_changed:
            evidence_paths += 1
            predicted_evidence = set(
                (predicted or {}).get("evidence_session_ids") or []
            )
            if gold_evidence & predicted_evidence:
                evidence_hits += 1
            total_citations += len(predicted_evidence)
            valid_citations += len(gold_evidence & predicted_evidence)

    detected = counts["tp_correct"] + counts["tp_wrong_value"]
    detection_precision = _safe_ratio(detected, detected + counts["fp"])
    detection_recall = _safe_ratio(detected, detected + counts["fn"])
    correct_change_precision = _safe_ratio(
        counts["tp_correct"],
        counts["tp_correct"] + counts["tp_wrong_value"] + counts["fp"],
    )
    correct_change_recall = _safe_ratio(
        counts["tp_correct"],
        counts["tp_correct"] + counts["tp_wrong_value"] + counts["fn"],
    )
    total = len(VALUE_KINDS)
    return {
        "final_state_accuracy": correct_cells / total,
        "value_accuracy": value_correct / total,
        "status_accuracy": status_correct / total,
        "changed_state_accuracy": _safe_ratio(changed_correct, changed_total),
        "unchanged_state_accuracy": _safe_ratio(
            unchanged_correct, unchanged_total
        ),
        "exact_state_match": correct_cells == total,
        "change_detection_precision": detection_precision,
        "change_detection_recall": detection_recall,
        "change_detection_f1": _f1(
            detection_precision, detection_recall
        ),
        "correct_change_precision": correct_change_precision,
        "correct_change_recall": correct_change_recall,
        "correct_change_f1": _f1(
            correct_change_precision, correct_change_recall
        ),
        "evidence_hit_rate": _safe_ratio(evidence_hits, evidence_paths),
        "evidence_citation_precision": _safe_ratio(
            valid_citations, total_citations
        ),
        "changed_path_count": changed_total,
        "unchanged_path_count": unchanged_total,
        "correct_cell_count": correct_cells,
        "change_confusion": counts,
        "status_confusion": status_confusion,
    }


def initial_copy_score(item: dict[str, Any]) -> dict[str, Any]:
    initial = copy.deepcopy(item["gold"]["initial_state"])
    prediction = {
        "state": {
            path: {**cell, "evidence_session_ids": []}
            for path, cell in initial.items()
        }
    }
    return score_stage2_2(
        prediction=prediction,
        initial_state=initial,
        gold_state=item["gold"]["state"],
    )


def write_stage2_2_initial_copy_report(
    paths: ExperimentPaths,
) -> dict[str, Any]:
    from .metrics import summarize_stage2_2_rows

    items = list(read_jsonl(stage2_2_item_path(paths)))
    rows = [
        {
            "trajectory_id": item["trajectory_id"],
            "item_id": item["item_id"],
            "metrics": initial_copy_score(item),
            "parse_error": None,
            "validation_errors": [],
        }
        for item in items
    ]
    report = {
        "schema_version": "stage2_2_initial_copy_report-v1",
        "baseline": "initial_copy",
        **summarize_stage2_2_rows(rows),
    }
    root = Path(active_stage2_2_prepared_manifest(paths)["root"])
    write_json(root / "baselines" / "initial_copy.json", report)
    return report
