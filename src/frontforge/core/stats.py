"""Cross-run aggregation over events-*.jsonl.

Every run already writes duration_ms/cost_usd/pass-fail per stage attempt to
its own events-<run_id>.jsonl (see core/logger.EventLogger). This module is
the only place that folds *all* of a project's run logs together so
questions like "what's this stage's p95 duration across every run" can be
answered without re-running anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StageStats:
    stage_id: str
    done_count: int = 0
    failed_count: int = 0
    durations_ms: list[int] = field(default_factory=list)
    total_cost_usd: float = 0.0
    verification_failures: int = 0
    mark_dirty_count: int = 0
    hitl_decisions: int = 0
    hitl_autofix_approvals: int = 0
    hitl_autofix_rejections: int = 0

    @property
    def total_runs(self) -> int:
        return self.done_count + self.failed_count

    @property
    def success_rate(self) -> float | None:
        if self.total_runs == 0:
            return None
        return self.done_count / self.total_runs

    def percentile(self, pct: float) -> int | None:
        if not self.durations_ms:
            return None
        data = sorted(self.durations_ms)
        idx = min(len(data) - 1, int(round(pct / 100 * (len(data) - 1))))
        return data[idx]


def read_all_events(logs_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for events_file in sorted(logs_dir.glob("events-*.jsonl")):
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def compute_stage_stats(events: list[dict[str, Any]]) -> dict[str, StageStats]:
    stats: dict[str, StageStats] = {}

    def get(stage_id: str) -> StageStats:
        return stats.setdefault(stage_id, StageStats(stage_id=stage_id))

    for event in events:
        stage_id = event.get("stage_id")
        if stage_id is None:
            continue
        name = event.get("event")
        s = get(stage_id)

        if name in ("stage_done", "stage_failed"):
            if name == "stage_done":
                s.done_count += 1
            else:
                s.failed_count += 1
            if event.get("duration_ms") is not None:
                s.durations_ms.append(event["duration_ms"])
            s.total_cost_usd += event.get("cost_usd") or 0.0
        elif name == "stage_attempt_verification_failed":
            s.verification_failures += 1
        elif name == "mark_dirty":
            s.mark_dirty_count += 1
        elif name == "hitl_decision":
            s.hitl_decisions += 1
        elif name == "hitl_autofix_decision":
            s.hitl_decisions += 1
            if event.get("approved"):
                s.hitl_autofix_approvals += 1
            else:
                s.hitl_autofix_rejections += 1

    return stats
