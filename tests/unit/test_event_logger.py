import json

from frontforge.core.logger import EventLogger
from frontforge.core.tracing import get_tracer


def test_event_logger_writes_one_json_line_per_call(tmp_path):
    events = EventLogger(tmp_path, "abc123")

    events.log("stage_started", stage_id="clarification", model="sonnet")
    events.log("stage_done", stage_id="clarification", duration_ms=120, cost_usd=0.01)

    path = tmp_path / "logs-abc123.jsonl"
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


def test_log_called_inside_a_span_carries_that_spans_trace_and_span_id(tmp_path, otel_spans):
    events = EventLogger(tmp_path, "run1")
    tracer = get_tracer("test")

    with tracer.start_as_current_span("test.span") as span:
        events.log("something_happened")
        expected_trace_id = format(span.get_span_context().trace_id, "032x")
        expected_span_id = format(span.get_span_context().span_id, "016x")

    record = json.loads((tmp_path / "logs-run1.jsonl").read_text(encoding="utf-8").strip())
    assert record["trace_id"] == expected_trace_id
    assert record["span_id"] == expected_span_id


def test_log_called_outside_any_span_omits_trace_and_span_id(tmp_path):
    events = EventLogger(tmp_path, "run2")

    events.log("something_happened")

    record = json.loads((tmp_path / "logs-run2.jsonl").read_text(encoding="utf-8").strip())
    assert "trace_id" not in record
    assert "span_id" not in record


def test_write_payload_saves_full_content_and_returns_relative_path(tmp_path):
    events = EventLogger(tmp_path, "run3")
    long_user_prompt = "x" * 5000 + "\n\nActually do this specific thing at the very end."

    path = events.write_payload(
        "codegen",
        1,
        system_prompt="short system prompt",
        user_prompt=long_user_prompt,
        response="short response",
    )

    assert path == "payloads/run3/codegen-attempt1.json"
    saved = json.loads((tmp_path / path).read_text(encoding="utf-8"))
    # nothing truncated, unlike the preview written into logs-*.jsonl
    assert saved["user_prompt"] == long_user_prompt
    assert saved["user_prompt"].endswith("Actually do this specific thing at the very end.")


def test_write_payload_keeps_separate_files_per_attempt(tmp_path):
    events = EventLogger(tmp_path, "run4")

    first = events.write_payload("clarification", 1, system_prompt="s", user_prompt="u1", response="r1")
    second = events.write_payload("clarification", 2, system_prompt="s", user_prompt="u2", response="r2")

    assert first != second
    assert json.loads((tmp_path / first).read_text(encoding="utf-8"))["user_prompt"] == "u1"
    assert json.loads((tmp_path / second).read_text(encoding="utf-8"))["user_prompt"] == "u2"
