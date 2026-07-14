"""Provider backed by the Claude Code CLI (`claude -p ...`) instead of a
raw Anthropic API key — reuses whatever auth the CLI already has configured.

No agent is ever given file/shell tool access through this provider: every
stage (including codegen) returns structured JSON validated by
`--json-schema`, and `--tools ""` disables tool use entirely. Writing files
is FilesystemTool's job, invoked by the orchestrator only after verification
passes.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from typing import Any

from frontforge.providers.base import Provider
from frontforge.shared.constants import DEFAULT_MODEL, DEFAULT_TIMEOUT_SECONDS
from frontforge.shared.types import ProviderResult


class ClaudeCliError(RuntimeError):
    def __init__(self, message: str, *, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


def build_argv(
    *,
    system_prompt_file: str,
    json_schema: dict[str, Any] | None,
    model: str,
    claude_bin: str = "claude",
    max_budget_usd: float | None = None,
) -> list[str]:
    # user_prompt is piped via stdin to avoid Windows 32 KB command-line limit
    argv = [
        claude_bin,
        "-p",
        "--system-prompt-file",
        system_prompt_file,
        "--output-format",
        "json",
        "--model",
        model,
        "--no-session-persistence",
        "--tools",
        "",
    ]
    if json_schema is not None:
        argv += ["--json-schema", json.dumps(json_schema)]
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", str(max_budget_usd)]
    return argv


class ClaudeCliProvider(Provider):
    def __init__(self, claude_bin: str = "claude", max_budget_usd: float | None = None):
        self.claude_bin = claude_bin
        # Per-CALL cap enforced by the claude CLI itself (--max-budget-usd) —
        # stops one runaway stage from spending unboundedly. Distinct from
        # Orchestrator's pipeline-wide total cap, which stops the whole run
        # once cumulative spend crosses a threshold.
        self.max_budget_usd = max_budget_usd

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> ProviderResult:
        model = model or DEFAULT_MODEL
        timeout = timeout or DEFAULT_TIMEOUT_SECONDS

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(system_prompt)
            tmp.close()

            argv = build_argv(
                system_prompt_file=tmp.name,
                json_schema=json_schema,
                model=model,
                claude_bin=self.claude_bin,
                max_budget_usd=self.max_budget_usd,
            )

            start = time.monotonic()
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=user_prompt.encode("utf-8")), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ClaudeCliError(f"claude CLI timed out after {timeout}s")
        finally:
            os.unlink(tmp.name)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            raise ClaudeCliError(
                f"claude CLI exited with code {proc.returncode}", stdout=stdout, stderr=stderr
            )

        return self._parse_output(stdout, model=model, elapsed_ms=elapsed_ms)

    @staticmethod
    def _parse_output(stdout: str, *, model: str, elapsed_ms: int) -> ProviderResult:
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeCliError(
                f"could not parse claude CLI stdout as JSON: {exc}", stdout=stdout
            ) from exc

        result_text = envelope.get("result", stdout) if isinstance(envelope, dict) else stdout
        data: dict[str, Any] | None = None
        if isinstance(result_text, str):
            try:
                parsed = json.loads(result_text)
                if isinstance(parsed, dict):
                    data = parsed
            except json.JSONDecodeError:
                data = None
        elif isinstance(result_text, dict):
            data = result_text

        cost_usd = None
        duration_ms = elapsed_ms
        input_tokens = None
        output_tokens = None
        if isinstance(envelope, dict):
            cost_usd = envelope.get("total_cost_usd", envelope.get("cost_usd"))
            duration_ms = envelope.get("duration_ms", elapsed_ms)
            usage = envelope.get("usage")
            if isinstance(usage, dict):
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")

        return ProviderResult(
            raw_text=result_text if isinstance(result_text, str) else json.dumps(result_text),
            data=data,
            model=model,
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
