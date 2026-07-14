from typer.testing import CliRunner

from frontforge.cli import _format_cost, _format_duration, app

runner = CliRunner()


def test_init_rejects_a_malformed_figma_url(tmp_path):
    result = runner.invoke(
        app,
        [
            "init",
            str(tmp_path / "proj"),
            "--requirement",
            "Build something.",
            "--figma-url",
            "https://example.com/not-figma",
        ],
    )

    assert result.exit_code != 0
    assert "could not extract a Figma file key" in result.output


def test_init_warns_when_figma_url_given_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("FIGMA_ACCESS_TOKEN", raising=False)
    result = runner.invoke(
        app,
        [
            "init",
            str(tmp_path / "proj"),
            "--requirement",
            "Build something.",
            "--figma-url",
            "https://www.figma.com/file/ABC123/My-Design",
        ],
    )

    assert result.exit_code == 0
    assert "FIGMA_ACCESS_TOKEN is not set" in result.output


def test_format_duration_none_is_blank():
    assert _format_duration(None) == ""


def test_format_duration_formats_seconds():
    assert _format_duration(1500) == "1.5s"
    assert _format_duration(500) == "0.5s"


def test_format_cost_none_is_blank():
    assert _format_cost(None) == ""


def test_format_cost_formats_four_decimals():
    assert _format_cost(0.02) == "$0.0200"
    assert _format_cost(1.23456) == "$1.2346"
