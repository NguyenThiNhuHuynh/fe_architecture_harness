"""Small shared helpers: hashing, JSON I/O, path safety."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> str:
    """Stable JSON serialization used for hashing stage inputs."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    """Atomic write: a crash mid-write leaves the old file intact instead of
    a truncated/corrupt one, since os.replace() is an atomic rename on both
    POSIX and Windows (same filesystem)."""
    ensure_dir(path.parent)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp_path, path)


def safe_join(root: Path, relative: str) -> Path:
    """Resolve `relative` under `root`, raising if it would escape root.

    Used by FilesystemTool so a codegen result can never write outside the
    project's generated/ output directory.
    """
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Path {relative!r} escapes output root {root}")
    return candidate
