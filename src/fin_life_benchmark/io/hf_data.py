"""Reconstruct dialogue session data from the HuggingFace dataset when absent.

This repo is code-only; the frozen dialogue corpus lives in a HuggingFace
dataset (see ``data/samples/README.md``). The dataset stores two answer-split
configs, one file per trajectory:

- ``dialogues/traj_XXX.jsonl`` — turns + neutral positional context (answer-free)
- ``gold/traj_XXX.jsonl``      — labels: ``plan``, ``cue_annotations``,
  ``action_resolution``, ``session_type``, ...

The local pipeline reads a single joined ``sessions_traj_XXX.jsonl`` per
trajectory (dialogue fields ∪ gold fields, keyed on ``session_id``), so
:func:`ensure_dialogue_sessions` downloads both configs and rebuilds those
files. Nothing is regenerated: the turns and labels are the frozen dataset
content, only reassembled into the layout the pipeline expects.

Configuration (env; all optional except a token for a gated dataset):

============================  ================================================
``HF_DIALOGUE_REPO``          dataset repo id (default ``DEFAULT_DIALOGUE_REPO``)
``HF_DIALOGUE_REVISION``      git revision/branch/tag (default: repo default)
``HF_DIALOGUES_SUBDIR``       config dir with the answer-free files (default ``dialogues``)
``HF_GOLD_SUBDIR``            config dir with the label files (default ``gold``)
``HF_TOKEN`` / ``HUGGINGFACE_TOKEN``  access token for a gated dataset
============================  ================================================
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

DEFAULT_DIALOGUE_REPO = "hangyeul-lee/life-event-benchmark-v2-dialogues"
SESSION_GLOB = "sessions_*.jsonl"
DEFAULT_DIALOGUES_SUBDIR = "dialogues"
DEFAULT_GOLD_SUBDIR = "gold"


def _has_sessions(sessions_dir: Path) -> bool:
    return sessions_dir.is_dir() and any(sessions_dir.glob(SESSION_GLOB))


def _token(explicit: str | None) -> str | None:
    return explicit or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or None


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ensure_dialogue_sessions(
    sessions_dir: Path | str,
    *,
    repo_id: str | None = None,
    revision: str | None = None,
    token: str | None = None,
    include_gold: bool = True,
    trajectory_ids: Iterable[str] | None = None,
    force: bool = False,
) -> Path:
    """Return ``sessions_dir``, rebuilding ``sessions_*.jsonl`` from HF if absent.

    No-op (no network) when the directory already holds session files, unless
    ``force=True``. Raises ``RuntimeError`` with actionable guidance when a
    fetch is required but cannot be completed.
    """
    sessions_dir = Path(sessions_dir)
    if not force and _has_sessions(sessions_dir):
        return sessions_dir
    return fetch_dialogue_sessions(
        sessions_dir,
        repo_id=repo_id,
        revision=revision,
        token=token,
        include_gold=include_gold,
        trajectory_ids=trajectory_ids,
    )


def fetch_dialogue_sessions(
    sessions_dir: Path | str,
    *,
    repo_id: str | None = None,
    revision: str | None = None,
    token: str | None = None,
    include_gold: bool = True,
    trajectory_ids: Iterable[str] | None = None,
) -> Path:
    """Download the HF dataset and rebuild joined ``sessions_*.jsonl`` files.

    ``include_gold=False`` writes the answer-free dialogue rows only (for pure
    evaluation prompts). The default joins the ``gold`` labels back in, which
    the gold/benchmark-item builders require.
    """
    sessions_dir = Path(sessions_dir)
    repo_id = repo_id or os.getenv("HF_DIALOGUE_REPO") or DEFAULT_DIALOGUE_REPO
    revision = revision or os.getenv("HF_DIALOGUE_REVISION") or None
    token = _token(token)
    dialogues_dir = (os.getenv("HF_DIALOGUES_SUBDIR") or DEFAULT_DIALOGUES_SUBDIR).strip("/")
    gold_dir = (os.getenv("HF_GOLD_SUBDIR") or DEFAULT_GOLD_SUBDIR).strip("/")
    wanted = set(trajectory_ids) if trajectory_ids is not None else None

    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import HfHubHTTPError
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "huggingface_hub is required to fetch dialogue data from HF. "
            "Install it (`pip install huggingface_hub`, already in requirements.txt)."
        ) from exc

    if wanted:
        allow = [f"{dialogues_dir}/{t}.jsonl" for t in sorted(wanted)]
        if include_gold:
            allow += [f"{gold_dir}/{t}.jsonl" for t in sorted(wanted)]
    else:
        allow = [f"{dialogues_dir}/*.jsonl"]
        if include_gold:
            allow.append(f"{gold_dir}/*.jsonl")
    try:
        local_root = Path(
            snapshot_download(
                repo_id=repo_id,
                repo_type="dataset",
                revision=revision,
                token=token,
                allow_patterns=allow,
            )
        )
    except HfHubHTTPError as exc:  # pragma: no cover - network/auth guard
        raise RuntimeError(
            f"failed to download dialogue data from HF dataset '{repo_id}'"
            f"{f' (revision {revision})' if revision else ''}: {exc}. "
            "For a gated dataset set HF_TOKEN (or HUGGINGFACE_TOKEN); override the "
            "dataset with HF_DIALOGUE_REPO."
        ) from exc

    dialogue_files = sorted((local_root / dialogues_dir).glob("*.jsonl"))
    if not dialogue_files:
        raise RuntimeError(
            f"no {dialogues_dir}/*.jsonl found in HF dataset '{repo_id}'. "
            "Set HF_DIALOGUES_SUBDIR to the config dir that holds the dialogue files."
        )

    sessions_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for dfile in dialogue_files:
        traj = dfile.stem  # e.g. "traj_001"
        if wanted is not None and traj not in wanted:
            continue
        rows = _read_jsonl(dfile)
        if include_gold:
            gfile = local_root / gold_dir / f"{traj}.jsonl"
            if not gfile.exists():
                raise RuntimeError(
                    f"gold file missing for {traj} in HF dataset '{repo_id}' "
                    f"({gold_dir}/{traj}.jsonl). Use include_gold=False for "
                    "answer-free dialogues only, or set HF_GOLD_SUBDIR."
                )
            gold_by_sid = {g["session_id"]: g for g in _read_jsonl(gfile)}
            rows = [{**row, **gold_by_sid.get(row["session_id"], {})} for row in rows]
        dest = sessions_dir / f"sessions_{traj}.jsonl"
        with dest.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        written += 1

    if written == 0:
        raise RuntimeError(
            f"no matching trajectories fetched from '{repo_id}' "
            f"(requested: {sorted(wanted) if wanted else 'all'})."
        )
    return sessions_dir
