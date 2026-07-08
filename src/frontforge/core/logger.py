"""Structured logging: console (rich) + a file per run under .harness/logs/."""

from __future__ import annotations

import logging
from pathlib import Path

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
