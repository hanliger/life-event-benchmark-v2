"""The single frozen corpus both reported stages evaluate on.

Stage 1 (`stage1_event_identification`) and Stage 2
(`stage2_2_reconstruct`) both read `dialogues_no_prospective` +
`gold_no_prospective` from the dataset. There is no second corpus wiring: one
`prepare` step materializes the sessions, initial states, prefix gold, and items
that both stages use, so a run cannot silently mix corpora.

The on-disk location is `data/stage2_2_reconstruct/prepared` and the config key
is `stage2_2_reconstruct`. Those names predate Stage 1 moving onto the same
corpus; they are kept so existing prepared trees and run manifests stay valid.
Read them as "the experiment corpus", not "Stage 2.2 only".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .paths import ExperimentPaths
from .stage2_2 import active_stage2_2_prepared_manifest


def corpus_manifest(paths: ExperimentPaths) -> dict[str, Any]:
    return active_stage2_2_prepared_manifest(paths)


def corpus_root(paths: ExperimentPaths) -> Path:
    return Path(corpus_manifest(paths)["root"])


def corpus_manifest_path(paths: ExperimentPaths) -> Path:
    return paths.stage2_2_prepared / "active_manifest.json"
