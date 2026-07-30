"""End-to-end Stage 1 grid on a synthetic prepared dataset with mock readers.

The real prepared corpus is not committed, so this fixture builds the minimum
`prepared` tree the Stage 1 runner reads and drives the three frozen models
through `run_method` exactly as the paid runner does, then exercises the Stage 1
reporting path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from financial_memory_experiment import stage1_runner
from financial_memory_experiment.evaluator import run_method
from financial_memory_experiment.metrics import (
    summarize_predictions,
    write_tables,
)
from financial_memory_experiment.paths import ExperimentPaths
from financial_memory_experiment.stage1 import (
    STAGE1,
    STAGE1_METHODS,
    STAGE1_MAX_OUTPUT_TOKENS,
    audit_rendered_prompt,
)
from financial_memory_experiment.util import read_jsonl, write_json


TRAJECTORIES = ("traj_001", "traj_002")
CHECKPOINTS = (2, 4)
CANDIDATE_EVENTS = [
    {"event_id": "E001", "label_ko": "이직"},
    {"event_id": "E002", "label_ko": "결혼"},
    {"event_id": "E003", "label_ko": "이사"},
]
_TEXT = {
    1: "한빛테크로 이직을 완료했습니다.",
    2: "혼인신고를 마쳤습니다.",
    3: "전세 계약을 갱신했습니다.",
    4: "서울로 이사를 완료했습니다.",
}


@pytest.fixture()
def stage1_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    real = ExperimentPaths.discover()
    root = tmp_path / "experiment"
    (root / "prompts").mkdir(parents=True)
    shutil.copytree(real.configs, root / "configs")
    shutil.copyfile(
        real.prompts / "system_ko.txt", root / "prompts" / "system_ko.txt"
    )
    (root / "docs").mkdir()
    shutil.copyfile(
        real.root / "docs" / "stage1_prompt_leakage_audit.md",
        root / "docs" / "stage1_prompt_leakage_audit.md",
    )
    prepared = root / "data" / "stage2_2_reconstruct" / "prepared" / "synthetic"
    for trajectory in TRAJECTORIES:
        write_json(
            prepared / "initial_state_s000" / f"{trajectory}.json",
            {
                "trajectory_id": trajectory,
                "session_id": "S000",
                "session_date": "2025-12-31",
                "state": {
                    "employment.employer": {
                        "status": "current",
                        "value": "이전직장",
                    }
                },
            },
        )
        sessions = [
            {
                "trajectory_id": trajectory,
                "session_id": f"S{number:03d}",
                "session_date": f"2026-01-{number:02d}",
                "turns": [
                    {"speaker": "user", "text": _TEXT[number]},
                    {"speaker": "assistant", "text": "반영하겠습니다."},
                ],
            }
            for number in sorted(_TEXT)
        ]
        session_path = (
            prepared / "sessions_answer_free" / f"{trajectory}.jsonl"
        )
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in sessions
            ),
            encoding="utf-8",
        )
    items = []
    for trajectory in TRAJECTORIES:
        for index, checkpoint in enumerate(CHECKPOINTS, start=1):
            start = checkpoint - 1
            items.append(
                {
                    "item_id": f"{trajectory}_cp{checkpoint:03d}_stage1",
                    "stage": STAGE1,
                    "trajectory_id": trajectory,
                    "prefix_id": f"{trajectory}_w{index:02d}",
                    "question": (
                        "지금까지 실제로 일어난 모든 Life Event와 각 발생을 "
                        "처음 확정하는 상담 세션의 pair를 찾으시오."
                    ),
                    "checkpoint_session_count": checkpoint,
                    "visible_sessions": [
                        f"S{number:03d}" for number in range(1, checkpoint + 1)
                    ],
                    "gold": {
                        "occurred_event_evidence_pairs": [
                            {
                                "event_id": "E001",
                                "evidence_session_id": "D001",
                            },
                            {
                                "event_id": "E002",
                                "evidence_session_id": "D002",
                            },
                            *(
                                [
                                    {
                                        "event_id": "E003",
                                        "evidence_session_id": "D004",
                                    }
                                ]
                                if checkpoint == 4
                                else []
                            ),
                        ],
                    },
                    "metadata": {
                        "query_checkpoint": checkpoint,
                        "candidate_events": CANDIDATE_EVENTS,
                        "n_visible_sessions": checkpoint,
                    },
                }
            )
    item_path = prepared / "canonical_items" / f"{STAGE1}.jsonl"
    item_path.parent.mkdir(parents=True, exist_ok=True)
    item_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in items),
        encoding="utf-8",
    )
    write_json(
        root / "data" / "stage2_2_reconstruct" / "prepared" / "active_manifest.json",
        {"root": str(prepared), "schema_version": "synthetic-prepared-v1"},
    )
    paths = ExperimentPaths(root=root, repo_root=real.repo_root)
    monkeypatch.setattr(ExperimentPaths, "discover", classmethod(lambda cls: paths))
    return paths


def test_stage1_grid_runs_all_three_models_and_reports(stage1_paths, tmp_path):
    items = stage1_runner._all_items(stage1_paths)
    assert len(items) == len(TRAJECTORIES) * len(CHECKPOINTS)
    outputs = []
    for method_id in STAGE1_METHODS:
        output = stage1_paths.runs / "grid" / f"{method_id}.jsonl"
        outputs.append(output)
        run_method(
            stage1_paths,
            method_id=method_id,
            items=items,
            output=output,
            mock=True,
            top_k=10,
            query_concurrency=2,
            parse_retries=1,
            prompt_artifact_root=stage1_paths.runs / "grid" / "prompts",
        )
        rows = list(read_jsonl(output))
        manifest = json.loads(
            output.with_suffix(".manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["status"] == "COMPLETE"
        assert manifest["top_k"] == 10
        assert len(rows) == len(items)
        assert all(row["stage"] == STAGE1 for row in rows)
        # The rendered prompt is externalized, not inlined into the artifact.
        assert all(
            "rendered_user_prompt" not in row["response_metadata"]
            and row["response_metadata"].get("prompt_sha256")
            for row in rows
        )
        assert all(
            (
                stage1_paths.runs
                / "grid"
                / "prompts"
                / method_id
                / row["trajectory_id"]
                / f"cp_{int(row['query_checkpoint']):03d}.txt.gz"
            ).exists()
            for row in rows
        )
        # No session past the checkpoint may appear in the used evidence.
        assert all(
            all(
                not session_id.startswith("S")
                or session_id == "S000"
                or int(session_id[1:]) <= row["query_checkpoint"]
                for session_id in row["evidence_session_ids"]
            )
            for row in rows
        )
        if method_id.startswith("fc_"):
            assert all(
                row["response_metadata"].get("max_output_tokens") is None
                or row["response_metadata"]["max_output_tokens"]
                == STAGE1_MAX_OUTPUT_TOKENS
                for row in rows
            )

    report = summarize_predictions(stage1_paths, outputs, allow_partial=True)
    assert sorted(report["methods"]) == sorted(STAGE1_METHODS)
    for stages in report["methods"].values():
        assert (
            stages[STAGE1]["aggregation"]
            == "checkpoint_macro_then_equal_checkpoint_average"
        )
        assert stages[STAGE1]["items"] == len(items)

    run_dir = stage1_paths.runs / "reported"
    write_json(run_dir / "provider_lock.json", {"methods": {}})
    write_tables(report, run_dir / "metrics")
    checkpoint_rows = stage1_runner._write_auxiliary_metrics(run_dir, outputs)
    stage1_runner._materialize_answer_pairs(stage1_paths, run_dir, outputs)

    assert len(checkpoint_rows) == len(STAGE1_METHODS) * len(items)
    for name in (
        "checkpoint_metrics.csv",
        "trajectory_metrics.csv",
        "parse_reliability.csv",
        "cost_latency.csv",
        "retrieval_recall.csv",
        "main_results.csv",
        "paired_method_deltas.csv",
    ):
        assert (run_dir / "metrics" / name).read_text(encoding="utf-8")
    trajectory_csv = (
        run_dir / "metrics" / "trajectory_metrics.csv"
    ).read_text(encoding="utf-8").splitlines()
    assert len(trajectory_csv) - 1 == len(STAGE1_METHODS) * len(TRAJECTORIES)
    for figure in (
        "checkpoint_strict_pair_f1.svg",
        "method_trajectory_strict_pair_f1_heatmap.svg",
    ):
        svg = (run_dir / "report" / "figures" / figure).read_text(
            encoding="utf-8"
        )
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    pair = json.loads(
        (
            run_dir
            / "answer_pairs"
            / "fc_claude_opus_4_8"
            / "traj_001"
            / "cp_004.json"
        ).read_text(encoding="utf-8")
    )
    assert pair["gold_pairs"][-1] == {
        "event_id": "E003",
        "evidence_session_id": "D004",
    }
    assert pair["candidate_event_count"] == len(CANDIDATE_EVENTS)
    assert pair["retrieval_evidence"]["visible_prefix_recall"] == 1.0


def test_stage1_offline_prompt_render_passes_audit_for_all_methods(
    stage1_paths,
):
    for method_id in STAGE1_METHODS:
        for checkpoint in CHECKPOINTS:
            rendered = stage1_runner._render_prompt_offline(
                stage1_paths,
                method_id=method_id,
                trajectory_id="traj_001",
                checkpoint=checkpoint,
            )
            check = audit_rendered_prompt(rendered)
            assert check["passed"], (method_id, checkpoint, check)
            assert check["max_visible_session_id"] <= checkpoint
            if method_id == stage1_runner.LETTA_METHOD:
                assert "archival search는 최대 1회" in rendered["prompt"]


def test_stage1_plan_audit_report_commands(stage1_paths, capsys):
    import argparse

    # Local-only methods keep this offline: no OpenRouter provider lock lookup.
    methods = "fc_claude_opus_4_8,fc_gpt_5_6_sol"
    stage1_runner.command_plan(
        argparse.Namespace(
            methods=methods,
            trajectories=",".join(TRAJECTORIES),
            checkpoint_start=min(CHECKPOINTS),
            checkpoint_end=max(CHECKPOINTS),
            checkpoint_stride=max(CHECKPOINTS) - min(CHECKPOINTS),
            model_workers=2,
            trajectory_workers=2,
            checkpoint_workers=2,
            max_in_flight=8,
            anthropic_max_in_flight=4,
            openrouter_max_in_flight=4,
            request_timeout_seconds=600,
            provider_retries=0,
            parse_retries=0,
            budget_cap_usd=10.0,
            estimated_usd=1.0,
            provider_lock_file=None,
        )
    )
    plan = json.loads(capsys.readouterr().out)
    run_dir = Path(plan["run_dir"])
    assert plan["prediction_count"] == 2 * len(TRAJECTORIES) * len(CHECKPOINTS)
    assert plan["retrieval"] == {
        "strategy": "single_question_query",
        "top_k": 10,
    }
    assert plan["max_output_tokens"] == STAGE1_MAX_OUTPUT_TOKENS
    assert plan["provider_lock_status"] == "NOT_APPLICABLE"
    assert plan["plan_sha256"]
    manifest = json.loads(
        (run_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "PLANNED"

    stage1_runner.command_audit_prompt(
        argparse.Namespace(run_dir=str(run_dir))
    )
    capsys.readouterr()
    audit = json.loads(
        (run_dir / "prompt_audit.json").read_text(encoding="utf-8")
    )
    assert audit["checks"] and all(
        check["passed"] for check in audit["checks"]
    )
    assert (run_dir / "prompt_leakage_audit.md").exists()
    assert list(
        (run_dir / "prompts" / "audit_examples").glob("*.txt.gz")
    )

    items_by_trajectory: dict[str, list[dict[str, object]]] = {}
    for item in stage1_runner._all_items(stage1_paths):
        items_by_trajectory.setdefault(
            str(item["trajectory_id"]), []
        ).append(item)
    for method_id in plan["methods"]:
        for trajectory, subset in items_by_trajectory.items():
            run_method(
                stage1_paths,
                method_id=method_id,
                items=subset,
                output=(
                    run_dir / "raw" / method_id / trajectory / "attempt_01.jsonl"
                ),
                mock=True,
                top_k=int(plan["retrieval"]["top_k"]),
                query_concurrency=2,
                parse_retries=1,
                prompt_artifact_root=run_dir / "prompts",
            )
    stage1_runner._validate_complete_grid(run_dir, plan)

    stage1_runner.command_report(argparse.Namespace(run_dir=str(run_dir)))
    capsys.readouterr()
    assert (run_dir / "report" / "report.md").read_text(encoding="utf-8")
    metrics = json.loads(
        (run_dir / "metrics" / "metrics.json").read_text(encoding="utf-8")
    )
    assert sorted(metrics["methods"]) == sorted(plan["methods"])
    # Stable per-trajectory copies are published next to the attempt files.
    assert (run_dir / "raw" / "fc_claude_opus_4_8" / "traj_001.jsonl").exists()
