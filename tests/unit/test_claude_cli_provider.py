import json

from frontforge.providers.claude_cli import ClaudeCliProvider, _build_stdin_payload, build_argv
from frontforge.shared.types import ImageAttachment


def test_build_argv_omits_max_budget_by_default():
    argv = build_argv(
        system_prompt_file="/tmp/x.md", json_schema=None, model="sonnet", claude_bin="claude"
    )
    assert "--max-budget-usd" not in argv


def test_build_argv_includes_max_budget_when_set():
    argv = build_argv(
        system_prompt_file="/tmp/x.md",
        json_schema=None,
        model="sonnet",
        claude_bin="claude",
        max_budget_usd=0.5,
    )
    assert "--max-budget-usd" in argv
    assert argv[argv.index("--max-budget-usd") + 1] == "0.5"


def test_parse_output_extracts_token_usage_when_present():
    envelope = {
        "result": json.dumps({"ok": True}),
        "total_cost_usd": 0.03,
        "duration_ms": 42,
        "usage": {"input_tokens": 123, "output_tokens": 45},
    }
    result = ClaudeCliProvider._parse_output(json.dumps(envelope), model="sonnet", elapsed_ms=10)

    assert result.input_tokens == 123
    assert result.output_tokens == 45
    assert result.cost_usd == 0.03


def test_parse_output_leaves_token_usage_none_when_envelope_has_no_usage():
    envelope = {"result": json.dumps({"ok": True}), "total_cost_usd": 0.01}
    result = ClaudeCliProvider._parse_output(json.dumps(envelope), model="sonnet", elapsed_ms=10)

    assert result.input_tokens is None
    assert result.output_tokens is None


def test_build_argv_uses_plain_json_output_without_images():
    argv = build_argv(system_prompt_file="/tmp/x.md", json_schema=None, model="sonnet")
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--input-format" not in argv


def test_build_argv_switches_to_stream_json_with_images():
    argv = build_argv(system_prompt_file="/tmp/x.md", json_schema=None, model="sonnet", with_images=True)
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv


def test_build_stdin_payload_is_plain_text_without_images():
    payload = _build_stdin_payload("hello", [])
    assert payload == b"hello"


def test_build_stdin_payload_wraps_images_in_ndjson_message():
    images = [ImageAttachment(label="Login", media_type="image/png", base64_data="Zm9v")]
    payload = _build_stdin_payload("describe this", images)
    message = json.loads(payload.decode("utf-8"))

    assert message["type"] == "user"
    content = message["message"]["content"]
    assert content[0] == {"type": "text", "text": "Screenshot: Login"}
    assert content[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "Zm9v"},
    }
    assert content[-1] == {"type": "text", "text": "describe this"}


def test_parse_output_reads_ndjson_result_event_from_stream_json_output():
    """--output-format stream-json (used whenever images are attached) emits
    one JSON object per line; only the final `type: "result"` line carries
    the fields _parse_output needs."""
    lines = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}},
        {
            "type": "result",
            "result": json.dumps({"ok": True}),
            "total_cost_usd": 0.02,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    ]
    stdout = "\n".join(json.dumps(line) for line in lines)

    result = ClaudeCliProvider._parse_output(stdout, model="sonnet", elapsed_ms=10)

    assert result.data == {"ok": True}
    assert result.cost_usd == 0.02
    assert result.input_tokens == 10
    assert result.output_tokens == 5
