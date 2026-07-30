from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from financial_memory_experiment.cli import _write_environment_snapshot
from financial_memory_experiment.paths import ExperimentPaths
from financial_memory_experiment.safety import (
    APPROVAL_PHRASE,
    PaidExecutionBlocked,
    assert_provider_construction_allowed,
    build_smoke_plan,
    load_verified_smoke_plan,
)


def _paths(tmp_path) -> ExperimentPaths:
    root = tmp_path / "experiment"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "paid_safety.yaml").write_text(
        """
schema_version: paid-safety-v1
smoke:
  usd_cap: 3
  concurrency: 3
  checkpoint_concurrency: 5
  automatic_retries: 0
  stop_on_first_error: true
""",
        encoding="utf-8",
    )
    (root / "configs" / "paid_cost_ledger.json").write_text(
        json.dumps(
            {
                "schema_version": "paid-smoke-ledger-v1",
                "standing_limit_usd": 5.0,
                "conservative_spent_usd": 1.98,
                "entries": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "configs" / "experiment.yaml").write_text(
        """
models:
  reasoning_policy: deployment_realistic_medium
  generation_profiles:
    deployment_realistic_low: {}
""",
        encoding="utf-8",
    )
    return ExperimentPaths(root=root, repo_root=tmp_path)


def test_provider_construction_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FIN_MEMORY_DISABLE_PAID_APIS", raising=False)
    with pytest.raises(PaidExecutionBlocked):
        assert_provider_construction_allowed()


def test_paid_plan_requires_exact_hash_and_approval(tmp_path):
    paths = _paths(tmp_path)
    plan = build_smoke_plan(
        paths,
        method_ids=["fc_gemini_3_1_pro"],
        item_ids=["q1"],
        estimated_usd=0.5,
        input_items_sha256="items",
    )
    with pytest.raises(PaidExecutionBlocked):
        load_verified_smoke_plan(
            paths,
            plan_sha=plan["plan_sha256"],
            approval="yes",
            execute_paid=True,
        )
    verified = load_verified_smoke_plan(
        paths,
        plan_sha=plan["plan_sha256"],
        approval=APPROVAL_PHRASE,
        execute_paid=True,
    )
    assert verified["plan_sha256"] == plan["plan_sha256"]
    assert verified["concurrency"] == 3
    assert verified["checkpoint_concurrency"] == 5
    assert verified["reasoning_policy"] == "deployment_realistic_medium"
    assert len(verified["execution_provenance"]["execution_tree_sha256"]) == 64

    path = paths.runs / "paid_plans" / f"{plan['plan_sha256']}.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["estimated_usd"] = 9.0
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(PaidExecutionBlocked):
        load_verified_smoke_plan(
            paths,
            plan_sha=plan["plan_sha256"],
            approval=APPROVAL_PHRASE,
            execute_paid=True,
        )


def test_paid_plan_freezes_low_reasoning_policy(tmp_path):
    paths = _paths(tmp_path)
    plan = build_smoke_plan(
        paths,
        method_ids=["fc_gemini_3_1_pro"],
        item_ids=["q1"],
        estimated_usd=0.5,
        input_items_sha256="items",
        reasoning_policy="deployment_realistic_low",
    )
    assert plan["reasoning_policy"] == "deployment_realistic_low"


def test_cumulative_smoke_limit_is_strictly_enforced(tmp_path):
    paths = _paths(tmp_path)
    ledger_path = paths.configs / "paid_cost_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["conservative_spent_usd"] = 2.0
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(PaidExecutionBlocked, match="reach or exceed"):
        build_smoke_plan(
            paths,
            method_ids=["fc_gemini_3_1_pro"],
            item_ids=["q1"],
            estimated_usd=3.0,
            input_items_sha256="items",
        )


def test_reservation_is_atomic_conservative_and_single_use(tmp_path):
    from financial_memory_experiment.safety import reserve_smoke_budget

    paths = _paths(tmp_path)
    plan = build_smoke_plan(
        paths,
            method_ids=["fc_gemini_3_1_pro"],
        item_ids=["q1"],
        estimated_usd=0.5,
        input_items_sha256="items",
    )
    verified = load_verified_smoke_plan(
        paths,
        plan_sha=plan["plan_sha256"],
        approval=APPROVAL_PHRASE,
        execute_paid=True,
    )
    ledger = reserve_smoke_budget(paths, verified)
    assert ledger["conservative_spent_usd"] == 2.48
    assert ledger["entries"][-1]["plan_sha256"] == plan["plan_sha256"]
    with pytest.raises(PaidExecutionBlocked, match="changed after planning"):
        reserve_smoke_budget(paths, verified)


def test_changed_ledger_invalidates_an_existing_plan(tmp_path):
    paths = _paths(tmp_path)
    plan = build_smoke_plan(
        paths,
            method_ids=["fc_gemini_3_1_pro"],
        item_ids=["q1"],
        estimated_usd=0.5,
        input_items_sha256="items",
    )
    ledger_path = paths.configs / "paid_cost_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["conservative_spent_usd"] = 2.0
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(PaidExecutionBlocked, match="ledger changed"):
        load_verified_smoke_plan(
            paths,
            plan_sha=plan["plan_sha256"],
            approval=APPROVAL_PHRASE,
            execute_paid=True,
        )


def test_environment_snapshot_records_patched_and_base_letta_images(
    tmp_path, monkeypatch
):
    paths = _paths(tmp_path)
    (paths.configs / "experiment.yaml").write_text(
        "models:\n  gemini_reader: test-model\n",
        encoding="utf-8",
    )

    def fake_run(command, **_kwargs):
        if command[0] == "docker":
            image_name = command[3]
            return SimpleNamespace(
                stdout=json.dumps({"Id": f"sha256:{image_name}"})
            )
        return SimpleNamespace(stdout="package==1.0\n")

    monkeypatch.setattr("financial_memory_experiment.cli.subprocess.run", fake_run)
    output = tmp_path / "run"
    _write_environment_snapshot(paths, output)
    snapshot = json.loads((output / "environment.json").read_text(encoding="utf-8"))

    assert set(snapshot["docker_images"]) == {
        "financial-memory-letta:0.16.8-googlecompat1",
        "letta/letta:0.16.8",
    }
    assert snapshot["docker_images"][
        "financial-memory-letta:0.16.8-googlecompat1"
    ]["Id"].startswith("sha256:")
