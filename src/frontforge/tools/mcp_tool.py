"""Stub interface for future MCP server integration — not implemented yet.

Kept as a separate module so agents/tools can depend on this interface
without caring whether it's backed by a real MCP client later.
"""

from __future__ import annotations

from typing import Any


class McpTool:
    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError("MCP integration is not implemented yet.")
