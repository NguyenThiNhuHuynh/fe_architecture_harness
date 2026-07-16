"""Workspace layout for a single project: where state, outputs, logs and
generated code live on disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from frontforge.shared.constants import (
    FIGMA_ASSETS_DIR_NAME,
    GENERATED_DIR_NAME,
    HARNESS_DIR_NAME,
    LOGS_DIR_NAME,
    OUTPUTS_DIR_NAME,
    SEED_FILE_NAME,
    STATE_FILE_NAME,
)
from frontforge.shared.utils import ensure_dir


@dataclass(frozen=True)
class RunSession:
    project_root: Path

    @property
    def harness_dir(self) -> Path:
        return self.project_root / HARNESS_DIR_NAME

    @property
    def state_file(self) -> Path:
        return self.harness_dir / STATE_FILE_NAME

    @property
    def outputs_dir(self) -> Path:
        return self.harness_dir / OUTPUTS_DIR_NAME

    @property
    def logs_dir(self) -> Path:
        return self.harness_dir / LOGS_DIR_NAME

    @property
    def seed_file(self) -> Path:
        return self.harness_dir / SEED_FILE_NAME

    @property
    def run_lock_file(self) -> Path:
        return self.harness_dir / "run.lock"

    @property
    def generated_dir(self) -> Path:
        return self.project_root / GENERATED_DIR_NAME

    @property
    def figma_assets_dir(self) -> Path:
        return self.harness_dir / FIGMA_ASSETS_DIR_NAME

    def scaffold(self) -> None:
        ensure_dir(self.harness_dir)
        ensure_dir(self.outputs_dir)
        ensure_dir(self.logs_dir)
        ensure_dir(self.generated_dir)
        ensure_dir(self.figma_assets_dir)

    @classmethod
    def at(cls, project_root: str | Path) -> "RunSession":
        return cls(project_root=Path(project_root))
