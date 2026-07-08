"""Provider abstraction — agents never know which AI backend answers them."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from frontforge.shared.types import ProviderResult


class Provider(ABC):
    @abstractmethod
    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> ProviderResult:
        """Run one prompt turn and return the (parsed, if schema given) result."""
        raise NotImplementedError
