from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .paths import ExperimentPaths


def load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected YAML mapping: {path}")
    return payload


def load_experiment_config(paths: ExperimentPaths | None = None) -> dict[str, Any]:
    paths = paths or ExperimentPaths.discover()
    return load_yaml(paths.configs / "experiment.yaml")


def load_method_config(paths: ExperimentPaths | None = None) -> dict[str, Any]:
    paths = paths or ExperimentPaths.discover()
    return load_yaml(paths.configs / "methods.yaml")


def load_paid_safety_config(paths: ExperimentPaths | None = None) -> dict[str, Any]:
    paths = paths or ExperimentPaths.discover()
    return load_yaml(paths.configs / "paid_safety.yaml")


def load_paid_cost_ledger(paths: ExperimentPaths | None = None) -> dict[str, Any]:
    paths = paths or ExperimentPaths.discover()
    payload = json.loads(
        (paths.configs / "paid_cost_ledger.json").read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict):
        raise ValueError("expected paid cost ledger JSON mapping")
    return payload
