"""Structured logging: console (rich) + a file per run under .harness/logs/."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opentelemetry import trace

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
    """One JSON object per line in .harness/logs/logs-<run_id>.jsonl — for
    machine reading (dashboards, `jq`, scripts) alongside the human-readable
    text log. Every stage completion (success or failure) is recorded here
    with duration_ms/cost_usd so cost/perf can be reconstructed after the
    fact without re-parsing free-text log lines.

    Every record also carries trace_id/span_id of whatever OTel span is
    active at the time `log()` is called (core.orchestrator always calls it
    from inside a "frontforge.stage"/"frontforge.hitl_review"/etc span) — the
    automatic correlation OTel is meant to provide, so a JSONL line can be
    joined back to its exact span without any manual context threading.
    """

    def __init__(self, logs_dir: Path, run_id: str):
        self.run_id = run_id
        self.path = logs_dir / f"logs-{run_id}.jsonl"
        self.payloads_dir = logs_dir / "payloads" / run_id
        logs_dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            record["trace_id"] = format(span_context.trace_id, "032x")
            record["span_id"] = format(span_context.span_id, "016x")
        record.update(fields)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def write_payload(
        self, stage_id: str, attempt: int, *, system_prompt: str, user_prompt: str, response: str
    ) -> str:
        """Persists the FULL, untruncated system_prompt/user_prompt/response
        for one LLM call to its own file, and returns its path (relative to
        `logs_dir`) so the caller can stash it as a `payload_path` field
        alongside the truncated preview `log()` already writes into
        events-<run_id>.jsonl. Fixes the truncated preview's real failure
        mode: every prompt template puts the actual instruction and any
        retry `verification_errors` at the END, which a flat character cap
        cuts off first — the full copy here is never truncated, so nothing
        is ever unrecoverable after the process exits.
        """
        self.payloads_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{stage_id}-attempt{attempt}.json"
        (self.payloads_dir / file_name).write_text(
            json.dumps(
                {"system_prompt": system_prompt, "user_prompt": user_prompt, "response": response},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return f"payloads/{self.run_id}/{file_name}"
