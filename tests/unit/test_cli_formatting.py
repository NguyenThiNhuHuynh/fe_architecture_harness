from frontforge.cli import _format_cost, _format_duration


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
