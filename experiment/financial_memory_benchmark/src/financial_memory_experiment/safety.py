from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_paid_safety_config
from .paths import ExperimentPaths
from .util import sha256_file, sha256_json, write_json


class PaidExecutionBlocked(RuntimeError):
    """Raised before any paid client is constructed."""


APPROVAL_PHRASE = "I_APPROVE_PAID_SMOKE"
FULL_APPROVAL_PHRASE = "I_APPROVE_PAID_FULL"


def paid_apis_disabled() -> bool:
    return os.environ.get("FIN_MEMORY_DISABLE_PAID_APIS", "1") != "0"


def assert_provider_construction_allowed() -> None:
    if paid_apis_disabled():
        raise PaidExecutionBlocked(
            "paid APIs are disabled; use the paid smoke entrypoint only after explicit approval"
        )


def _execution_provenance(paths: ExperimentPaths) -> dict[str, Any]:
    roots = (
        paths.root / "src",
        paths.root / "configs",
        paths.root / "prompts",
        paths.root / "scripts",
        paths.root / "infra",
    )
    files = [
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    files.extend(
        path
        for path in (
            paths.root / "pyproject.toml",
            paths.root / "requirements.lock",
        )
        if path.exists()
    )
    hashes = {
        str(path.relative_to(paths.root)): sha256_file(path)
        for path in sorted(files)
    }
    prepared_manifest = paths.prepared / "active_manifest.json"
    return {
        "execution_tree_sha256": sha256_json(hashes),
        "prepared_manifest_sha256": (
            sha256_file(prepared_manifest)
            if prepared_manifest.exists()
            else None
        ),
    }


def build_smoke_plan(
    paths: ExperimentPaths,
    *,
    method_ids: list[str],
    item_ids: list[str],
    estimated_usd: float,
    operation_limits: dict[str, int] | None = None,
    input_items_sha256: str,
) -> dict[str, Any]:
    config = load_paid_safety_config(paths)
    cap = float(config["smoke"]["usd_cap"])
    if estimated_usd <= 0 or estimated_usd > cap:
        raise ValueError(f"estimated_usd must be in (0, {cap}]")
    body = {
        "schema_version": "paid-smoke-plan-v1",
        "kind": "smoke",
        "method_ids": sorted(set(method_ids)),
        "item_ids": sorted(set(item_ids)),
        "estimated_usd": round(float(estimated_usd), 6),
        "usd_cap": cap,
        "concurrency": 1,
        "automatic_retries": 0,
        "stop_on_first_error": True,
        "timeout_policy": "unknown_billing_state_stop_no_auto_resume",
        "operation_limits": operation_limits or {},
        "input_items_sha256": input_items_sha256,
        "execution_provenance": _execution_provenance(paths),
        "config": config,
    }
    plan = {**body, "plan_sha256": sha256_json(body)}
    write_json(paths.runs / "paid_plans" / f"{plan['plan_sha256']}.json", plan)
    return plan


def load_verified_smoke_plan(
    paths: ExperimentPaths,
    *,
    plan_sha: str,
    approval: str,
    execute_paid: bool,
) -> dict[str, Any]:
    if not execute_paid:
        raise PaidExecutionBlocked("--execute-paid is required")
    if approval != APPROVAL_PHRASE:
        raise PaidExecutionBlocked(f"--approval must exactly equal {APPROVAL_PHRASE}")
    plan_path = paths.runs / "paid_plans" / f"{plan_sha}.json"
    if not plan_path.exists():
        raise PaidExecutionBlocked(f"immutable plan not found: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    claimed = str(plan.pop("plan_sha256", ""))
    actual = sha256_json(plan)
    if claimed != plan_sha or actual != plan_sha:
        raise PaidExecutionBlocked("plan SHA mismatch; refusing paid execution")
    plan["plan_sha256"] = claimed
    if float(plan["estimated_usd"]) > float(plan["usd_cap"]):
        raise PaidExecutionBlocked("planned estimate exceeds the smoke cap")
    if plan.get("execution_provenance") != _execution_provenance(paths):
        raise PaidExecutionBlocked(
            "code/config/data provenance changed after planning; create a new plan"
        )
    return plan


def build_full_plan(
    paths: ExperimentPaths,
    *,
    method_ids: list[str],
    item_ids: list[str],
    estimated_usd: float,
    operation_limits: dict[str, int],
    input_items_sha256: str,
) -> dict[str, Any]:
    if estimated_usd <= 0:
        raise ValueError("estimated_usd must be positive")
    body = {
        "schema_version": "paid-full-plan-v1",
        "kind": "full",
        "method_ids": sorted(set(method_ids)),
        "item_ids": sorted(set(item_ids)),
        "estimated_usd": round(float(estimated_usd), 6),
        "usd_cap": None,
        "concurrency": 1,
        "automatic_retries": 0,
        "stop_on_first_error": True,
        "timeout_policy": "unknown_billing_state_stop_no_auto_resume",
        "operation_limits": operation_limits,
        "input_items_sha256": input_items_sha256,
        "execution_provenance": _execution_provenance(paths),
        "config": load_paid_safety_config(paths),
    }
    plan = {**body, "plan_sha256": sha256_json(body)}
    write_json(paths.runs / "paid_plans" / f"{plan['plan_sha256']}.json", plan)
    return plan


def load_verified_full_plan(
    paths: ExperimentPaths,
    *,
    plan_sha: str,
    approval: str,
    execute_paid: bool,
) -> dict[str, Any]:
    if not execute_paid:
        raise PaidExecutionBlocked("--execute-paid is required")
    if approval != FULL_APPROVAL_PHRASE:
        raise PaidExecutionBlocked(f"--approval must exactly equal {FULL_APPROVAL_PHRASE}")
    path = paths.runs / "paid_plans" / f"{plan_sha}.json"
    if not path.exists():
        raise PaidExecutionBlocked(f"immutable plan not found: {path}")
    plan = json.loads(path.read_text(encoding="utf-8"))
    claimed = str(plan.pop("plan_sha256", ""))
    if claimed != plan_sha or sha256_json(plan) != plan_sha:
        raise PaidExecutionBlocked("plan SHA mismatch; refusing paid execution")
    if plan.get("kind") != "full" or plan.get("usd_cap") is not None:
        raise PaidExecutionBlocked("not an uncapped full-run plan")
    if plan.get("execution_provenance") != _execution_provenance(paths):
        raise PaidExecutionBlocked(
            "code/config/data provenance changed after planning; create a new plan"
        )
    plan["plan_sha256"] = claimed
    return plan


@dataclass
class CostGuard:
    cap_usd: float
    reserved_usd: float = 0.0
    observed_usd: float = 0.0
    unknown_billing_state: bool = False

    def reserve(self, maximum_usd: float) -> None:
        if self.unknown_billing_state:
            raise PaidExecutionBlocked("billing state is unknown; automatic resume is forbidden")
        if maximum_usd < 0:
            raise ValueError("maximum_usd must be non-negative")
        if self.reserved_usd + maximum_usd > self.cap_usd:
            raise PaidExecutionBlocked("next request could exceed the smoke USD cap")
        self.reserved_usd += maximum_usd

    def settle(self, *, reserved_usd: float, observed_usd: float | None) -> None:
        self.reserved_usd -= reserved_usd
        if observed_usd is None:
            self.unknown_billing_state = True
            raise PaidExecutionBlocked(
                "provider did not return attributable cost; billing state is unknown"
            )
        self.observed_usd += observed_usd
        if self.observed_usd > self.cap_usd:
            raise PaidExecutionBlocked("observed spend exceeds the smoke cap")

    def mark_timeout(self) -> None:
        self.unknown_billing_state = True
