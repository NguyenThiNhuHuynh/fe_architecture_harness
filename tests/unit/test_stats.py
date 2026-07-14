import pytest

from frontforge.core.stats import compute_stage_stats, read_all_events


def _write_events(logs_dir, run_id, lines):
    import json

    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"events-{run_id}.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def test_read_all_events_merges_every_run_log_in_order(tmp_path):
    _write_events(tmp_path, "run1", [{"event": "stage_started", "stage_id": "clarification"}])
    _write_events(tmp_path, "run2", [{"event": "stage_started", "stage_id": "requirement"}])

    events = read_all_events(tmp_path)

    assert [e["stage_id"] for e in events] == ["clarification", "requirement"]


def test_compute_stage_stats_success_rate_and_percentiles(tmp_path):
    events = [
        {"event": "stage_done", "stage_id": "codegen", "duration_ms": 100, "cost_usd": 0.1},
        {"event": "stage_done", "stage_id": "codegen", "duration_ms": 200, "cost_usd": 0.2},
        {"event": "stage_failed", "stage_id": "codegen", "duration_ms": 300, "cost_usd": 0.05},
    ]

    stats = compute_stage_stats(events)
    codegen = stats["codegen"]

    assert codegen.total_runs == 3
    assert codegen.done_count == 2
    assert codegen.failed_count == 1
    assert codegen.success_rate == 2 / 3
    assert codegen.total_cost_usd == pytest.approx(0.35)
    assert codegen.percentile(50) == 200
    assert codegen.percentile(99) == 300


def test_compute_stage_stats_counts_verification_dirty_and_hitl_events():
    events = [
        {"event": "stage_attempt_verification_failed", "stage_id": "codegen"},
        {"event": "mark_dirty", "stage_id": "requirement"},
        {"event": "hitl_decision", "stage_id": "requirement", "proceed": True},
        {"event": "hitl_autofix_decision", "stage_id": "quality_review", "approved": True},
        {"event": "hitl_autofix_decision", "stage_id": "quality_review", "approved": False},
    ]

    stats = compute_stage_stats(events)

    assert stats["codegen"].verification_failures == 1
    assert stats["requirement"].mark_dirty_count == 1
    assert stats["requirement"].hitl_decisions == 1
    assert stats["quality_review"].hitl_decisions == 2
    assert stats["quality_review"].hitl_autofix_approvals == 1
    assert stats["quality_review"].hitl_autofix_rejections == 1


def test_stage_with_no_runs_has_no_success_rate_or_percentile():
    stats = compute_stage_stats([{"event": "mark_dirty", "stage_id": "requirement"}])
    requirement = stats["requirement"]

    assert requirement.success_rate is None
    assert requirement.percentile(50) is None
