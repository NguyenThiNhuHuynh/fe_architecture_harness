import json

from frontforge.providers.claude_cli import ClaudeCliProvider, build_argv


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
