"""Subprocess wrapper used by verifiers to run npm/npx commands against a
generated project's output directory."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class TerminalTool:
    async def run(
        self, argv: list[str], *, cwd: Path, timeout: int = 300
    ) -> CommandResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return CommandResult(returncode=127, stdout="", stderr=str(exc))

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult(returncode=124, stdout="", stderr=f"command timed out after {timeout}s")

        return CommandResult(
            returncode=proc.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )
