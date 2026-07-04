"""Canonical repo paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class RepoPaths:
    root: Path

    @classmethod
    def default(cls) -> "RepoPaths":
        return cls(root=repo_root())

    @property
    def configs(self) -> Path:
        return self.root / "configs"

    @property
    def locales(self) -> Path:
        return self.configs / "locales"

    @property
    def registries(self) -> Path:
        return self.configs / "registries"

    @property
    def generation(self) -> Path:
        return self.configs / "generation"

    @property
    def prompts(self) -> Path:
        return self.root / "prompts"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def personas_normalized(self) -> Path:
        return self.data / "personas" / "normalized"

    @property
    def trajectories(self) -> Path:
        return self.data / "generated" / "trajectories"

    @property
    def sessions(self) -> Path:
        return self.data / "generated" / "sessions"

    @property
    def gold(self) -> Path:
        return self.data / "generated" / "gold"

    @property
    def benchmark_items(self) -> Path:
        return self.data / "generated" / "benchmark_items"

    @property
    def quality_reports(self) -> Path:
        return self.data / "generated" / "quality_reports"

    @property
    def raw_model_outputs(self) -> Path:
        return self.data / "raw_model_outputs"
