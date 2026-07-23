"""Fetch dialogue session data from HuggingFace when it is not present locally.

This repo is code-only: the dialogue corpus lives in a private HuggingFace
dataset (see ``data/samples/README.md``). Any consumer that reads
``sessions_*.jsonl`` can call :func:`ensure_dialogue_sessions` so a missing
sessions directory is transparently populated from HF instead of failing.

Configuration comes from the environment (all optional except a token for a
private dataset):

======================  ==================================================
``HF_DIALOGUE_REPO``    dataset repo id (default ``DEFAULT_DIALOGUE_REPO``)
``HF_DIALOGUE_REVISION``  git revision/branch/tag to pull (default: repo default)
``HF_DIALOGUE_SUBDIR``  path prefix inside the dataset holding the session files
``HF_TOKEN`` /          access token for a private dataset
``HUGGINGFACE_TOKEN``
======================  ==================================================
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

DEFAULT_DIALOGUE_REPO = "hangyeul-lee/life-event-benchmark-v2-dialogues"
SESSION_GLOB = "sessions_*.jsonl"


def _has_sessions(sessions_dir: Path) -> bool:
    return sessions_dir.is_dir() and any(sessions_dir.glob(SESSION_GLOB))


def _token(explicit: str | None) -> str | None:
    return explicit or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or None


def ensure_dialogue_sessions(
    sessions_dir: Path | str,
    *,
    repo_id: str | None = None,
    revision: str | None = None,
    subdir: str | None = None,
    token: str | None = None,
    force: bool = False,
) -> Path:
    """Return ``sessions_dir``, downloading ``sessions_*.jsonl`` from HF if absent.

    No-op (and no network access) when the directory already holds session
    files, unless ``force=True``. Raises ``RuntimeError`` with actionable
    guidance when a fetch is required but cannot be completed.
    """
    sessions_dir = Path(sessions_dir)
    if not force and _has_sessions(sessions_dir):
        return sessions_dir
    return fetch_dialogue_sessions(
        sessions_dir, repo_id=repo_id, revision=revision, subdir=subdir, token=token
    )


def fetch_dialogue_sessions(
    sessions_dir: Path | str,
    *,
    repo_id: str | None = None,
    revision: str | None = None,
    subdir: str | None = None,
    token: str | None = None,
) -> Path:
    """Download ``sessions_*.jsonl`` from the HF dataset into ``sessions_dir``."""
    sessions_dir = Path(sessions_dir)
    repo_id = repo_id or os.getenv("HF_DIALOGUE_REPO") or DEFAULT_DIALOGUE_REPO
    revision = revision or os.getenv("HF_DIALOGUE_REVISION") or None
    subdir = (subdir if subdir is not None else os.getenv("HF_DIALOGUE_SUBDIR") or "").strip("/")
    token = _token(token)

    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import HfHubHTTPError
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "huggingface_hub is required to fetch dialogue data from HF. "
            "Install it (`pip install huggingface_hub`, already in requirements.txt)."
        ) from exc

    prefix = f"{subdir}/" if subdir else ""
    allow_patterns = [f"{prefix}{SESSION_GLOB}", f"{prefix}**/{SESSION_GLOB}"]
    try:
        local_root = snapshot_download(
            repo_id=repo_id,
            repo_type="dataset",
            revision=revision,
            token=token,
            allow_patterns=allow_patterns,
        )
    except HfHubHTTPError as exc:  # pragma: no cover - network/auth guard
        raise RuntimeError(
            f"failed to download dialogue sessions from HF dataset '{repo_id}'"
            f"{f' (revision {revision})' if revision else ''}: {exc}. "
            "For a private dataset set HF_TOKEN (or HUGGINGFACE_TOKEN); override the "
            "dataset with HF_DIALOGUE_REPO."
        ) from exc

    search_root = Path(local_root) / subdir if subdir else Path(local_root)
    files = sorted(search_root.rglob(SESSION_GLOB))
    if not files:
        raise RuntimeError(
            f"no {SESSION_GLOB} found in HF dataset '{repo_id}'"
            f"{f' under subdir {subdir!r}' if subdir else ''}. "
            "Set HF_DIALOGUE_SUBDIR to the folder that holds the session files."
        )

    sessions_dir.mkdir(parents=True, exist_ok=True)
    for src in files:
        dest = sessions_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
    return sessions_dir
