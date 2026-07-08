"""The only place that writes a generated project's files to disk.

Agents return file contents as structured data (see CodegenResult); the
orchestrator calls FilesystemTool only after verification has passed.
"""

from __future__ import annotations

from pathlib import Path

from frontforge.shared.types import GeneratedFile
from frontforge.shared.utils import safe_join


class FilesystemTool:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_files(self, files: list[GeneratedFile]) -> list[Path]:
        written: list[Path] = []
        for file in files:
            destination = safe_join(self.root, file.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(file.content, encoding="utf-8")
            written.append(destination)
        return written
