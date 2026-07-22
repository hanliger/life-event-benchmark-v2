"""Safety controls shared by dialogue generation, canary, and bake-off CLIs."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..io import RepoPaths, load_yaml
from .models import RawDialogueResponse

PLANNER_INPUT_SCHEMA_VERSION = "dialogue-plan-v6-semantic-contracts"
FROZEN_MANIFEST_FIELDS = (
    "provider",
    "model",
    "reasoning_effort",
    "max_output_tokens",
    "generation_prompt_hash",
    "repair_prompt_hash",
    "dialogue_config_hash",
    "dialogue_contract_registry_hashes",
    "dialogue_pipeline_source_hashes",
    "planner_input_schema_version",
)


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_model_profile(
    profile_name: str | None,
    provider_override: str | None = None,
    model_override: str | None = None,
    paths: RepoPaths | None = None,
) -> dict[str, Any]:
    paths = paths or RepoPaths.default()
    profiles = load_yaml(paths.generation / "dialogue_models.yaml").get("profiles", {})
    if profile_name:
        if profile_name not in profiles:
            raise ValueError(f"unknown dialogue model profile: {profile_name}")
        effective = dict(profiles[profile_name])
    else:
        effective = {
            "provider": provider_override or "mock",
            "model": model_override or "mock",
            "reasoning_effort": None,
            "response_format": "prompt_json",
            "max_tokens": 8192,
        }
    if provider_override:
        effective["provider"] = provider_override
    if model_override:
        effective["model"] = model_override
    effective["profile"] = profile_name
    effective["overrides"] = {
        "provider": provider_override,
        "model": model_override,
    }
    return effective


def select_trajectory_files(
    trajectories_dir: Path | str,
    trajectory_id: str | None = None,
    exclude_trajectory_ids: Iterable[str] = (),
    trajectory_ids_file: Path | str | None = None,
    max_trajectories: int | None = None,
) -> list[Path]:
    available = {
        path.stem: path for path in sorted(Path(trajectories_dir).glob("traj_*.json"))
    }
    if not available:
        raise ValueError(f"no traj_*.json under {trajectories_dir}")
    requested: list[str] | None = None
    if trajectory_id:
        requested = [trajectory_id]
    if trajectory_ids_file:
        file_ids = [
            line.strip()
            for line in Path(trajectory_ids_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        requested = list(dict.fromkeys((requested or []) + file_ids))
    if requested is not None:
        missing = sorted(set(requested) - set(available))
        if missing:
            raise ValueError(f"requested trajectory ID(s) do not exist: {', '.join(missing)}")
        selected = [available[item] for item in requested]
    else:
        selected = list(available.values())
    excluded = set(exclude_trajectory_ids)
    unknown_excluded = excluded - set(available)
    if unknown_excluded:
        raise ValueError(f"excluded trajectory ID(s) do not exist: {', '.join(sorted(unknown_excluded))}")
    selected = [path for path in selected if path.stem not in excluded]
    if max_trajectories is not None:
        selected = selected[:max_trajectories]
    return selected


def raw_dialogue_json_schema() -> dict[str, Any]:
    schema = RawDialogueResponse.model_json_schema()
    schema["additionalProperties"] = False
    return schema


def _git_value(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def package_versions() -> dict[str, str]:
    versions = {}
    for package in ("pydantic", "PyYAML", "openai", "anthropic", "tenacity"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def build_generation_manifest(
    *,
    run_id: str,
    trajectory_files: list[Path],
    plans_dir: Path,
    effective_model: dict[str, Any],
    mode: str,
    seed: int,
    overwrite_policy: dict[str, Any],
    paths: RepoPaths | None = None,
) -> dict[str, Any]:
    paths = paths or RepoPaths.default()
    trajectory_ids = [path.stem for path in trajectory_files]
    plan_files = [plans_dir / f"plans_{trajectory_id}.jsonl" for trajectory_id in trajectory_ids]
    plan_files = [path for path in plan_files if path.exists()]
    contract_registry_files = [
        paths.registries / filename
        for filename in (
            "dialogue_evidence_realization.yaml",
            "dialogue_lifecycle_surface.yaml",
            "dialogue_event_disclosure_patterns.yaml",
            "high_risk_action_contracts.yaml",
            "bank_policy_profile.yaml",
            "dialogue_hard_negative_templates.yaml",
        )
    ]
    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": _git_value(paths.root, "rev-parse", "HEAD"),
        "git_branch": _git_value(paths.root, "branch", "--show-current"),
        "trajectory_ids": trajectory_ids,
        "provider": effective_model["provider"],
        "model": effective_model["model"],
        "model_profile": effective_model.get("profile"),
        "profile_overrides": effective_model.get("overrides", {}),
        "reasoning_effort": effective_model.get("reasoning_effort"),
        "response_format": effective_model.get("response_format", "prompt_json"),
        "max_output_tokens": int(effective_model.get("max_tokens", 8192)),
        "dialogue_config_hash": sha256_file(paths.generation / "dialogue.yaml"),
        "dialogue_contract_registry_hashes": {
            path.name: sha256_file(path) for path in contract_registry_files
        },
        "dialogue_pipeline_source_hashes": {
            path.name: sha256_file(path)
            for path in (
                paths.root / "src/fin_life_benchmark/dialogue/generator.py",
                paths.root / "src/fin_life_benchmark/dialogue/models.py",
                paths.root
                / "src/fin_life_benchmark/validation/dialogue_validator.py",
            )
        },
        "generation_prompt_hash": sha256_file(paths.prompts / "dialogue/generate_banking_session_ko.md"),
        "repair_prompt_hash": sha256_file(paths.prompts / "dialogue/repair_banking_session_ko.md"),
        "plans_directory": str(plans_dir.resolve()),
        "plan_file_hashes": {path.name: sha256_file(path) for path in plan_files},
        "trajectory_file_hashes": {path.name: sha256_file(path) for path in trajectory_files},
        "planner_input_schema_version": PLANNER_INPUT_SCHEMA_VERSION,
        "seed": seed,
        "mode": mode,
        "overwrite_policy": overwrite_policy,
        "package_versions": package_versions(),
        "python_version": platform.python_version(),
    }


def write_immutable_manifest(path: Path | str, manifest: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        # These fields describe an invocation, not the frozen generation
        # contract.  A continuation must be allowed to replace --overwrite
        # with --resume/--retry-errors (and to change worker parallelism)
        # without making the original dataset manifest appear incompatible.
        operational_fields = {"generated_at", "overwrite_policy"}
        comparable = {
            key: value for key, value in manifest.items() if key not in operational_fields
        }
        old_comparable = {
            key: value for key, value in previous.items() if key not in operational_fields
        }
        if comparable != old_comparable:
            raise ValueError(f"immutable generation manifest mismatch: {path}")
        return
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_canary_manifest(
    current: dict[str, Any],
    canary_manifest_path: Path | str,
    allow_mismatch: bool = False,
) -> list[str]:
    canary = json.loads(Path(canary_manifest_path).read_text(encoding="utf-8"))
    mismatches = [
        key for key in FROZEN_MANIFEST_FIELDS if current.get(key) != canary.get(key)
    ]
    if mismatches and not allow_mismatch:
        raise ValueError(f"canary configuration mismatch: {', '.join(mismatches)}")
    current["canary_manifest"] = str(Path(canary_manifest_path).resolve())
    current["canary_config_mismatch_fields"] = mismatches
    current["canary_config_mismatch_override"] = bool(mismatches and allow_mismatch)
    return mismatches


def require_canary_pass(path: Path | str) -> None:
    decision = json.loads(Path(path).read_text(encoding="utf-8"))
    if decision.get("decision") != "PASS":
        raise ValueError(f"production requires PASS canary, got {decision.get('decision')!r}")


def require_human_review_pass(path: Path | str) -> None:
    decision = json.loads(Path(path).read_text(encoding="utf-8"))
    if decision.get("decision") != "PASS":
        raise ValueError(
            "production requires PASS human review, got "
            f"{decision.get('decision')!r}"
        )


def require_regression_pass(path: Path | str) -> None:
    decision = json.loads(Path(path).read_text(encoding="utf-8"))
    if decision.get("decision") != "PASS":
        raise ValueError(
            "full canary v2 requires PASS regression canary, got "
            f"{decision.get('decision')!r}"
        )
