"""Runs `npm run build` against the generated project (codegen stage only)."""

from __future__ import annotations

from typing import Any

from frontforge.core.session import RunSession
from frontforge.shared.types import VerificationIssue
from frontforge.tools.terminal_tool import TerminalTool


class BuildVerifier:
    name = "build"

    def __init__(self, terminal: TerminalTool | None = None):
        self.terminal = terminal or TerminalTool()

    async def verify(
        self, *, stage_id: str, output: dict[str, Any], session: RunSession
    ) -> list[VerificationIssue]:
        if not (session.generated_dir / "package.json").exists():
            return []
        if not (session.generated_dir / "node_modules").exists():
            return [
                VerificationIssue(
                    verifier=self.name,
                    message="skipped: run `npm install` in the generated project to enable a build check",
                    severity="warning",
                )
            ]
        result = await self.terminal.run(["npm", "run", "build"], cwd=session.generated_dir)
        if result.ok:
            return []
        return [VerificationIssue(verifier=self.name, message=(result.stdout or result.stderr).strip())]
