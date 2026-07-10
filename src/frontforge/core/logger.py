"""Structured logging: console (rich) + a file per run under .harness/logs/."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CONFIGURED_LOGGERS: set[str] = set()


def configure_logging(logs_dir: Path, run_id: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("frontforge")
    logger.setLevel(level)

    if "frontforge" not in _CONFIGURED_LOGGERS:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(console_handler)
        _CONFIGURED_LOGGERS.add("frontforge")

    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(logs_dir / f"run-{run_id}.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)
    return logger


def get_logger(name: str = "frontforge") -> logging.Logger:
    return logging.getLogger(name)


class EventLogger:
    """One JSON object per line in .harness/logs/events-<run_id>.jsonl — for
    machine reading (dashboards, `jq`, scripts) alongside the human-readable
    text log. Every stage completion (success or failure) is recorded here
    with duration_ms/cost_usd so cost/perf can be reconstructed after the
    fact without re-parsing free-text log lines."""

    def __init__(self, logs_dir: Path, run_id: str):
        self.path = logs_dir / f"events-{run_id}.jsonl"
        logs_dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
