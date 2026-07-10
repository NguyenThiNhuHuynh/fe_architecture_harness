import json

from frontforge.core.logger import EventLogger


def test_event_logger_writes_one_json_line_per_call(tmp_path):
    events = EventLogger(tmp_path, "abc123")

    events.log("stage_started", stage_id="clarification", model="sonnet")
    events.log("stage_done", stage_id="clarification", duration_ms=120, cost_usd=0.01)

    path = tmp_path / "events-abc123.jsonl"
    assert path.exists()

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["event"] == "stage_started"
    assert first["stage_id"] == "clarification"
    assert "ts" in first

    second = json.loads(lines[1])
    assert second["event"] == "stage_done"
    assert second["duration_ms"] == 120
    assert second["cost_usd"] == 0.01


def test_event_logger_creates_logs_dir_if_missing(tmp_path):
    logs_dir = tmp_path / "nested" / "logs"
    assert not logs_dir.exists()

    EventLogger(logs_dir, "run1")
    assert logs_dir.exists()
