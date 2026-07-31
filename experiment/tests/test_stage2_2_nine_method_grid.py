from __future__ import annotations

import json
import os

from financial_memory_experiment.evaluator import run_method
from financial_memory_experiment.paths import ExperimentPaths
from financial_memory_experiment.metrics import (
    summarize_predictions,
    write_tables,
)
from financial_memory_experiment.stage2_2 import stage2_2_item_path
from financial_memory_experiment.stage2_2_runner import (
    DEFAULT_METHODS,
    DIRECT_API_METHODS,
    _load_approved_environment,
    _materialize_state_pairs,
    _selected_methods,
    _write_auxiliary_metrics,
)
from financial_memory_experiment.util import read_jsonl, write_json


def test_stage2_2_paid_environment_loads_repo_root_fallback(
    tmp_path, monkeypatch
):
    experiment_root = tmp_path / "experiment"
    experiment_root.mkdir()
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=repo-root-key\n"
        "GEMINI_API_KEY=gemini-key\n",
        encoding="utf-8",
    )
    paths = ExperimentPaths(root=experiment_root, repo_root=tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    _load_approved_environment(paths)

    assert os.environ["ANTHROPIC_API_KEY"] == "repo-root-key"
    assert os.environ["GOOGLE_API_KEY"] == "gemini-key"


def test_direct_api_methods_are_explicit_without_changing_method9_all():
    assert _selected_methods("all") == list(DEFAULT_METHODS)
    assert _selected_methods(
        "fc_gpt_5_6_sol,fc_claude_opus_4_8"
    ) == list(DIRECT_API_METHODS)


def test_nine_methods_share_stage2_2_contract_on_mock_grid(tmp_path):
    paths = ExperimentPaths.discover()
    items = [
        item
        for item in read_jsonl(stage2_2_item_path(paths))
        if item["trajectory_id"] in {"traj_001", "traj_002"}
        and int(item["metadata"]["query_checkpoint"]) in {15, 30, 45}
    ]
    assert len(items) == 6
    outputs = []

    for method_id in DEFAULT_METHODS:
        output = tmp_path / f"{method_id}.jsonl"
        outputs.append(output)
        run_method(
            paths,
            method_id=method_id,
            items=items,
            output=output,
            mock=True,
            query_concurrency=3,
            parse_retries=1,
            prompt_artifact_root=tmp_path / "prompts",
        )
        rows = list(read_jsonl(output))
        manifest = json.loads(
            output.with_suffix(".manifest.json").read_text(encoding="utf-8")
        )
        assert len(rows) == 6
        assert manifest["status"] == "COMPLETE"
        assert all(row["stage"] == "stage2_2_reconstruct" for row in rows)
        assert all(
            "rendered_user_prompt" not in row["response_metadata"]
            and row["response_metadata"].get("prompt_sha256")
            for row in rows
        )
        assert all(
            (
                tmp_path
                / "prompts"
                / method_id
                / row["trajectory_id"]
                / f"cp_{int(row['query_checkpoint']):03d}.txt.gz"
            ).exists()
            for row in rows
        )
        assert all(row["response_metadata"]["max_output_tokens"] == 20_000
                   if "max_output_tokens" in row["response_metadata"]
                   else True for row in rows)
        assert all(
            all(
                not session_id.startswith("S")
                or session_id == "S000"
                or int(session_id[1:]) <= row["query_checkpoint"]
                for session_id in row["evidence_session_ids"]
            )
            for row in rows
        )
        if method_id.startswith(
            ("bm25_", "dense_", "mem0_", "letta_")
        ):
            assert all(
                row["evidence_session_ids"][0] == "S000" for row in rows
            )

    report = summarize_predictions(paths, outputs, allow_partial=True)
    run_dir = tmp_path / "reported"
    write_json(run_dir / "provider_lock.json", {"methods": {}})
    write_tables(report, run_dir / "metrics")
    _write_auxiliary_metrics(run_dir, outputs, report)
    _materialize_state_pairs(paths, run_dir, outputs)
    assert (
        len(
            (
                run_dir / "metrics/path_trajectory_metrics.csv"
            ).read_text(encoding="utf-8").splitlines()
        )
        - 1
        == 9 * 2 * 34
    )
    assert (
        len(
            (
                run_dir / "metrics/path_trajectory_macro.csv"
            ).read_text(encoding="utf-8").splitlines()
        )
        - 1
        == 9 * 34
    )
    assert len(list((run_dir / "state_pairs").glob("*/*/cp_*.json"))) == 54
    assert len(list((run_dir / "report/figures").glob("*.svg"))) == 3
