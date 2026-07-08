"""Helpers for extracting/validating JSON out of raw provider text, used as
a fallback when a provider result's `data` field couldn't be parsed."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def extract_json(raw_text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = _FENCED_JSON_RE.search(raw_text)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None
