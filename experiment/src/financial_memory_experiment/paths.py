from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExperimentPaths:
    root: Path
    repo_root: Path

    @classmethod
    def discover(cls) -> "ExperimentPaths":
        root = Path(__file__).resolve().parents[2]
        repo_root = root.parent
        return cls(root=root, repo_root=repo_root)

    @property
    def configs(self) -> Path:
        return self.root / "configs"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def prepared(self) -> Path:
        return self.data / "prepared"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def prompts(self) -> Path:
        return self.root / "prompts"
