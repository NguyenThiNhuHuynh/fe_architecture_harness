"""Thin wrapper around git for the generated project's output directory."""

from __future__ import annotations

from pathlib import Path

from frontforge.tools.terminal_tool import CommandResult, TerminalTool


class GitTool:
    def __init__(self, terminal: TerminalTool | None = None):
        self.terminal = terminal or TerminalTool()

    async def init(self, repo_dir: Path) -> CommandResult:
        return await self.terminal.run(["git", "init"], cwd=repo_dir)

    async def commit_all(self, repo_dir: Path, message: str) -> CommandResult:
        add_result = await self.terminal.run(["git", "add", "-A"], cwd=repo_dir)
        if not add_result.ok:
            return add_result
        return await self.terminal.run(["git", "commit", "-m", message], cwd=repo_dir)
