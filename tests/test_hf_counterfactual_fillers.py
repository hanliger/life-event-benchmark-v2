from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from fin_life_benchmark.io.hf_data import (
    fetch_counterfactual_fillers,
)


def test_fetch_counterfactual_fillers_restores_hf_layout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot = tmp_path / "snapshot"
    remote = snapshot / "counterfactual_fillers" / "v1"
    sessions = remote / "sessions"
    plans = remote / "plans"
    audit = remote / "audit"
    sessions.mkdir(parents=True)
    plans.mkdir()
    audit.mkdir()

    rows = [
        {
            "filler_id": f"CF{index:03d}",
            "source_kind": "synthetic_reserve",
        }
        for index in range(1, 21)
    ]
    (sessions / "fillers_traj_001.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    (plans / "plans_traj_001.jsonl").write_text(
        json.dumps({"filler_id": "CF001"}) + "\n",
        encoding="utf-8",
    )
    (audit / "filler_decision.json").write_text(
        json.dumps({"decision": "PASS"}),
        encoding="utf-8",
    )
    filler_path = sessions / "fillers_traj_001.jsonl"
    manifest = {
        "artifact_file_sha256": {
            "counterfactual_fillers/v1/sessions/fillers_traj_001.jsonl": (
                hashlib.sha256(filler_path.read_bytes()).hexdigest()
            )
        }
    }
    (remote / "artifact_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda **_: str(snapshot),
    )

    output = fetch_counterfactual_fillers(
        tmp_path / "local",
        repo_id="example/repo",
        trajectory_ids=["traj_001"],
    )

    assert len(
        (output / "sessions" / "fillers_traj_001.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ) == 20
    assert (output / "plans" / "plans_traj_001.jsonl").exists()
    assert (output / "audit" / "filler_decision.json").exists()
    assert (output / "artifact_manifest.json").exists()


def test_fetch_counterfactual_fillers_rejects_checksum_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot = tmp_path / "snapshot"
    remote = snapshot / "counterfactual_fillers" / "v1"
    sessions = remote / "sessions"
    sessions.mkdir(parents=True)
    filler_path = sessions / "fillers_traj_001.jsonl"
    filler_path.write_text(
        "".join(
            json.dumps(
                {
                    "filler_id": f"CF{index:03d}",
                    "source_kind": "synthetic_reserve",
                }
            )
            + "\n"
            for index in range(1, 21)
        ),
        encoding="utf-8",
    )
    (remote / "artifact_manifest.json").write_text(
        json.dumps(
            {
                "artifact_file_sha256": {
                    "counterfactual_fillers/v1/sessions/fillers_traj_001.jsonl": (
                        "0" * 64
                    )
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda **_: str(snapshot),
    )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        fetch_counterfactual_fillers(
            tmp_path / "local",
            repo_id="example/repo",
            trajectory_ids=["traj_001"],
        )
