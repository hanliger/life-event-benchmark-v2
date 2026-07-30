from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fin_life_benchmark.gold.prefix_gold_exporter import serialize_memory_state
from fin_life_benchmark.trajectory.models import Trajectory

from .config import load_experiment_config
from .paths import ExperimentPaths
from .util import read_jsonl, session_number, sha256_file, sha256_json, write_json, write_jsonl


ANSWER_FREE_FIELDS = (
    "persona_id",
    "trajectory_id",
    "session_id",
    "session_date",
    "month_index",
    "age",
    "transition_order",
    "window_index",
    "position_in_window",
    "turns",
    "model",
    "provider",
)
GOLD_ONLY_FIELD_NAMES = {
    "gold",
    "plan",
    "cue_annotations",
    "linked_event_instance_id",
    "window_event_instance_id",
    "event_status_after_session",
    "structured_context",
    "generation_metadata",
}
EDUCATION_STAGES = ("pre_school", "primary", "middle", "high")


def _slug(repo_id: str) -> str:
    return repo_id.replace("/", "--")


def _hash_tree(
    root: Path, *, exclude_relative_paths: set[str] | None = None
) -> dict[str, str]:
    excluded = exclude_relative_paths or set()
    # huggingface_hub writes `.cache/huggingface/` inside local_dir, holding
    # per-file .lock/.metadata and a trees json. Those are machine- and
    # time-local, so hashing them makes the tree hash unreproducible on any
    # other machine -- an integrity pin computed over them can never validate.
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and ".git" not in path.parts
        and ".cache" not in path.parts
        and str(path.relative_to(root)) not in excluded
    }


def active_raw_manifest(paths: ExperimentPaths) -> dict[str, Any]:
    manifest_path = paths.raw / "active_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("raw data is not ready; run download-data first")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def active_prepared_manifest(paths: ExperimentPaths) -> dict[str, Any]:
    manifest_path = paths.prepared / "active_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("prepared data is not ready; run prepare-data first")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def download_data(
    paths: ExperimentPaths,
    *,
    source_dir: Path | None = None,
    revision: str | None = None,
) -> Path:
    """Materialize one immutable HF snapshot without loading any paid API keys."""

    cfg = load_experiment_config(paths)
    repo_id = str(cfg["dataset"]["repo_id"])
    requested_revision = revision or cfg["dataset"].get("revision")
    if source_dir is not None:
        source_dir = source_dir.resolve()
        if not (source_dir / "dialogues").is_dir() or not (source_dir / "gold").is_dir():
            raise ValueError(f"source directory lacks dialogues/ and gold/: {source_dir}")
        try:
            resolved_revision = subprocess.check_output(
                ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
                text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            resolved_revision = f"local-{sha256_json(_hash_tree(source_dir))[:16]}"
        destination = paths.raw / "hf" / _slug(repo_id) / resolved_revision
        if not destination.exists():
            destination.mkdir(parents=True)
            for relative in (
                "dialogues",
                "gold",
                "counterfactual_fillers",
                "counterfactual_filler_plans",
            ):
                candidate = source_dir / relative
                if candidate.exists():
                    shutil.copytree(candidate, destination / relative)
    else:
        try:
            from huggingface_hub import HfApi, snapshot_download
        except ModuleNotFoundError as exc:
            raise RuntimeError("huggingface-hub is required for download-data") from exc
        info = HfApi().dataset_info(repo_id, revision=requested_revision)
        resolved_revision = str(info.sha)
        destination = paths.raw / "hf" / _slug(repo_id) / resolved_revision
        if not destination.exists():
            destination.mkdir(parents=True)
            snapshot_download(
                repo_id=repo_id,
                repo_type="dataset",
                revision=resolved_revision,
                local_dir=destination,
                allow_patterns=[
                    "dialogues/*.jsonl",
                    "gold/*.jsonl",
                    "counterfactual_fillers/**",
                    "counterfactual_filler_plans/**",
                ],
            )

    file_hashes = _hash_tree(
        destination, exclude_relative_paths={"manifest.json"}
    )
    manifest = {
        "schema_version": "raw-data-manifest-v1",
        "repo_id": repo_id,
        "requested_revision": requested_revision,
        "resolved_revision": resolved_revision,
        "root": str(destination),
        "files": file_hashes,
        "tree_hash": sha256_json(file_hashes),
    }
    write_json(destination / "manifest.json", manifest)
    write_json(paths.raw / "active_manifest.json", manifest)
    return destination


def _load_by_key(directory: Path) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(directory.glob("traj_*.jsonl")):
        for row in read_jsonl(path):
            key = (str(row.get("trajectory_id")), str(row.get("session_id")))
            if key in records:
                raise ValueError(f"duplicate record key {key} under {directory}")
            records[key] = row
    return records


def _counterfactual_sessions_dir(raw_root: Path) -> Path | None:
    candidates = (
        raw_root / "counterfactual_fillers" / "v1" / "sessions",
        raw_root / "counterfactual_fillers" / "sessions",
        raw_root / "counterfactual_fillers",
    )
    return next(
        (candidate for candidate in candidates if list(candidate.glob("*.jsonl"))),
        None,
    )


def validate_raw_data(paths: ExperimentPaths) -> dict[str, Any]:
    cfg = load_experiment_config(paths)
    expected = cfg["dataset"]["expected"]
    manifest = active_raw_manifest(paths)
    root = Path(manifest["root"])
    dialogues = _load_by_key(root / "dialogues")
    gold = _load_by_key(root / "gold")
    errors: list[str] = []
    pinned_revision = cfg["dataset"].get("revision")
    pinned_tree_hash = cfg["dataset"].get("tree_hash")
    revision_matches = str(manifest.get("resolved_revision")) == str(
        pinned_revision
    )
    tree_matches = str(manifest.get("tree_hash")) == str(pinned_tree_hash)
    if pinned_revision and pinned_tree_hash and not (
        revision_matches or tree_matches
    ):
        errors.append(
            "active raw snapshot does not match the pinned dataset revision "
            "or content tree: "
            f"expected_revision={pinned_revision}, "
            f"actual_revision={manifest.get('resolved_revision')}, "
            f"expected_tree={pinned_tree_hash}, "
            f"actual_tree={manifest.get('tree_hash')}"
        )
    if set(dialogues) != set(gold):
        errors.append(
            f"dialogue/gold key mismatch: dialogue_only={len(set(dialogues)-set(gold))}, "
            f"gold_only={len(set(gold)-set(dialogues))}"
        )
    trajectories = sorted({key[0] for key in dialogues})
    if len(trajectories) != int(expected["trajectories"]):
        errors.append(f"expected {expected['trajectories']} trajectories, got {len(trajectories)}")
    if len(dialogues) != int(expected["sessions"]):
        errors.append(f"expected {expected['sessions']} sessions, got {len(dialogues)}")

    for trajectory_id in trajectories:
        rows = [dialogues[key] for key in dialogues if key[0] == trajectory_id]
        rows.sort(key=lambda row: session_number(str(row["session_id"])))
        if len(rows) != int(expected["sessions_per_trajectory"]):
            errors.append(f"{trajectory_id}: expected 300 sessions, got {len(rows)}")
        expected_ids = [f"S{index:03d}" for index in range(1, len(rows) + 1)]
        actual_ids = [str(row["session_id"]) for row in rows]
        if actual_ids != expected_ids:
            errors.append(f"{trajectory_id}: non-contiguous session IDs")
        dates = [str(row.get("session_date") or "") for row in rows]
        if not all(dates):
            errors.append(f"{trajectory_id}: missing session_date")
        elif dates != sorted(dates):
            errors.append(f"{trajectory_id}: session_date is not monotonic")
        for row in rows:
            turns = row.get("turns") or []
            if not turns:
                errors.append(f"{trajectory_id}/{row['session_id']}: missing turns")
                continue
            speakers = [turn.get("speaker") for turn in turns]
            if speakers != ["user" if index % 2 == 0 else "assistant" for index in range(len(turns))]:
                errors.append(f"{trajectory_id}/{row['session_id']}: invalid speaker alternation")

    fillers_dir = _counterfactual_sessions_dir(root)
    filler_count = 0
    if fillers_dir is not None:
        filler_count = sum(1 for path in fillers_dir.glob("*.jsonl") for _ in read_jsonl(path))
    if filler_count != int(expected["fillers"]):
        errors.append(f"expected {expected['fillers']} fillers, got {filler_count}")

    report = {
        "schema_version": "raw-validation-v1",
        "decision": "PASS" if not errors else "FAIL",
        "trajectory_count": len(trajectories),
        "session_count": len(dialogues),
        "filler_count": filler_count,
        "errors": errors,
        "raw_tree_hash": manifest["tree_hash"],
    }
    write_json(root / "validation_report.json", report)
    if errors:
        raise ValueError("raw-data validation failed:\n- " + "\n- ".join(errors[:30]))
    return report


def _education_predecessor(stage: Any) -> Any:
    normalized = "pre_school" if stage == "preschool" else stage
    if normalized not in EDUCATION_STAGES:
        return normalized
    index = EDUCATION_STAGES.index(normalized)
    return EDUCATION_STAGES[max(0, index - 1)]


def _fix_trajectory(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    result = copy.deepcopy(payload)
    changed = 0
    for instance in result.get("life_event_instances") or []:
        if instance.get("event_id") != "education_child_stage_entry":
            continue
        params = instance.get("params") or {}
        new_stage = "pre_school" if params.get("new_stage") == "preschool" else params.get("new_stage")
        previous = _education_predecessor(new_stage)
        if params.get("previous_stage") != previous:
            params["previous_stage"] = previous
            changed += 1
    for step in result.get("timeline_steps") or []:
        for update in step.get("memory_updates") or []:
            if "child_education_stage" not in str(update.get("path") or ""):
                continue
            new_stage = "pre_school" if update.get("new_value") == "preschool" else update.get("new_value")
            if new_stage not in EDUCATION_STAGES:
                continue
            previous = _education_predecessor(new_stage)
            if update.get("old_value") != previous:
                update["old_value"] = previous
                changed += 1
    return result, changed


def _initial_date(first_session_date: str) -> str:
    return (date.fromisoformat(first_session_date) - timedelta(days=1)).isoformat()


def prepare_data(paths: ExperimentPaths) -> Path:
    validate_raw_data(paths)
    raw_manifest = active_raw_manifest(paths)
    raw_root = Path(raw_manifest["root"])
    data_hash = raw_manifest["tree_hash"]
    output = paths.prepared / data_hash
    if output.exists() and (output / "manifest.json").exists():
        write_json(paths.prepared / "active_manifest.json", json.loads((output / "manifest.json").read_text()))
        return output

    dialogues = _load_by_key(raw_root / "dialogues")
    gold = _load_by_key(raw_root / "gold")
    joined_dir = output / "sessions_joined"
    answer_free_dir = output / "sessions_answer_free"
    s000_dir = output / "initial_state_s000"
    trajectories_dir = output / "trajectories_fixed"
    changes: dict[str, int] = {}

    fixture_dir = paths.repo_root / "tests" / "fixtures" / "trajectories"
    fixture_paths = sorted(fixture_dir.glob("traj_*.json"))
    if len(fixture_paths) != 20:
        raise ValueError(f"expected 20 trajectory fixtures, got {len(fixture_paths)}")

    for fixture_path in fixture_paths:
        fixed, count = _fix_trajectory(json.loads(fixture_path.read_text(encoding="utf-8")))
        trajectory = Trajectory.model_validate(fixed)
        trajectory_id = trajectory.trajectory_id
        changes[trajectory_id] = count
        trajectories_dir.mkdir(parents=True, exist_ok=True)
        (trajectories_dir / fixture_path.name).write_text(
            json.dumps(fixed, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        keys = sorted(
            (key for key in dialogues if key[0] == trajectory_id),
            key=lambda key: session_number(key[1]),
        )
        joined_rows: list[dict[str, Any]] = []
        answer_free_rows: list[dict[str, Any]] = []
        for key in keys:
            dialogue = dialogues[key]
            gold_row = gold[key]
            collisions = {
                field
                for field in set(dialogue) & set(gold_row)
                if dialogue[field] != gold_row[field]
                and field in {"trajectory_id", "persona_id", "session_id", "session_date"}
            }
            if collisions:
                raise ValueError(f"identity-field collision at {key}: {sorted(collisions)}")
            joined_rows.append({**dialogue, **gold_row})
            answer_free_rows.append(
                {field: copy.deepcopy(dialogue[field]) for field in ANSWER_FREE_FIELDS if field in dialogue}
            )
        write_jsonl(joined_dir / f"sessions_{trajectory_id}.jsonl", joined_rows)
        write_jsonl(answer_free_dir / f"traj_{trajectory_id.removeprefix('traj_')}.jsonl", answer_free_rows)

        if not answer_free_rows:
            raise ValueError(f"no answer-free sessions for {trajectory_id}")
        state = serialize_memory_state(trajectory.initial_financial_memory_state)
        s000 = {
            "schema_version": "initial-memory-s000-v1",
            "trajectory_id": trajectory_id,
            "persona_id": answer_free_rows[0].get("persona_id"),
            "session_id": "S000",
            "session_date": _initial_date(str(answer_free_rows[0]["session_date"])),
            "record_type": "initial_financial_memory",
            "state": state,
        }
        write_json(s000_dir / f"{trajectory_id}.json", s000)

    manifest = {
        "schema_version": "prepared-data-manifest-v1",
        "data_hash": data_hash,
        "raw_manifest": raw_manifest,
        "root_repo_commit": subprocess.check_output(
            ["git", "-C", str(paths.repo_root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "trajectory_source_hashes": {
            path.name: sha256_file(path) for path in fixture_paths
        },
        "education_transition_corrections": changes,
        "answer_free_fields": list(ANSWER_FREE_FIELDS),
        "gold_only_field_names": sorted(GOLD_ONLY_FIELD_NAMES),
        "root": str(output),
    }
    manifest["manifest_hash"] = sha256_json(manifest)
    write_json(output / "manifest.json", manifest)
    write_json(paths.prepared / "active_manifest.json", manifest)
    return output


def assert_answer_free_record(record: dict[str, Any]) -> None:
    leaked = GOLD_ONLY_FIELD_NAMES & set(record)
    if leaked:
        raise ValueError(f"answer-free record contains gold-only fields: {sorted(leaked)}")
