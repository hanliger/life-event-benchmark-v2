from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import Any, Iterator

from .config import load_paid_cost_ledger, load_paid_safety_config
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


def _ledger_path(paths: ExperimentPaths) -> Path:
    return paths.configs / "paid_cost_ledger.json"


def _validate_ledger(ledger: dict[str, Any]) -> tuple[float, float]:
    if ledger.get("schema_version") != "paid-smoke-ledger-v1":
        raise PaidExecutionBlocked("unsupported paid smoke cost ledger")
    spent = float(ledger["conservative_spent_usd"])
    limit = float(ledger["standing_limit_usd"])
    if spent < 0 or limit <= 0 or spent >= limit:
        raise PaidExecutionBlocked("paid smoke cost ledger has no executable allowance")
    return spent, limit


def _assert_cumulative_allowance(
    ledger: dict[str, Any], estimated_usd: float
) -> tuple[float, float, float]:
    spent, limit = _validate_ledger(ledger)
    after = round(spent + estimated_usd, 6)
    if after >= limit:
        raise PaidExecutionBlocked(
            "planned smoke reservation would reach or exceed the cumulative "
            f"${limit:.2f} standing limit (reserved=${spent:.2f}, "
            f"plan=${estimated_usd:.2f})"
        )
    return spent, limit, after


@contextmanager
def _exclusive_ledger_lock(paths: ExperimentPaths) -> Iterator[None]:
    lock_path = paths.runs / "paid_cost_ledger.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as handle:
        flock(handle.fileno(), LOCK_EX)
        try:
            yield
        finally:
            flock(handle.fileno(), LOCK_UN)


def reserve_smoke_budget(
    paths: ExperimentPaths, plan: dict[str, Any]
) -> dict[str, Any]:
    """Atomically reserve the full plan estimate before any paid client is enabled."""

    path = _ledger_path(paths)
    with _exclusive_ledger_lock(paths):
        ledger = load_paid_cost_ledger(paths)
        if sha256_file(path) != plan.get("cost_ledger_sha256"):
            raise PaidExecutionBlocked(
                "paid cost ledger changed after planning; create a new smoke plan"
            )
        plan_sha = str(plan["plan_sha256"])
        if any(
            entry.get("kind") == "plan_reservation"
            and entry.get("plan_sha256") == plan_sha
            for entry in ledger.get("entries") or []
        ):
            raise PaidExecutionBlocked("this smoke plan has already been reserved")
        _spent, _limit, after = _assert_cumulative_allowance(
            ledger, float(plan["estimated_usd"])
        )
        updated = dict(ledger)
        updated["conservative_spent_usd"] = after
        updated["entries"] = list(ledger.get("entries") or []) + [
            {
                "kind": "plan_reservation",
                "plan_sha256": plan_sha,
                "amount_usd": float(plan["estimated_usd"]),
                "status": "reserved",
                "note": (
                    "Conservative reservation made before provider construction; "
                    "manual billing reconciliation is required to reduce it."
                ),
            }
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
    return updated


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
    ledger_path = _ledger_path(paths)
    ledger = load_paid_cost_ledger(paths)
    spent, standing_limit, after = _assert_cumulative_allowance(
        ledger, float(estimated_usd)
    )
    body = {
        "schema_version": "paid-smoke-plan-v2",
        "kind": "smoke",
        "method_ids": sorted(set(method_ids)),
        "item_ids": sorted(set(item_ids)),
        "estimated_usd": round(float(estimated_usd), 6),
        "usd_cap": cap,
        "standing_limit_usd": standing_limit,
        "conservative_spent_before_usd": spent,
        "conservative_spent_after_reservation_usd": after,
        "cost_ledger_sha256": sha256_file(ledger_path),
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
    ledger = load_paid_cost_ledger(paths)
    _assert_cumulative_allowance(ledger, float(plan["estimated_usd"]))
    if sha256_file(_ledger_path(paths)) != plan.get("cost_ledger_sha256"):
        raise PaidExecutionBlocked(
            "paid cost ledger changed after planning; create a new smoke plan"
        )
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
