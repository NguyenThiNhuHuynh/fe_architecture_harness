"""Stub — same Provider interface, wired in later without touching any agent."""

from __future__ import annotations

from typing import Any

from frontforge.providers.base import Provider
from frontforge.shared.types import ProviderResult


class GeminiProvider(Provider):
    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> ProviderResult:
        raise NotImplementedError("GeminiProvider is not implemented yet.")
