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
from frontforge.shared.types import ImageAttachment, ProviderResult


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
    with_images: bool = False,
) -> list[str]:
    # user_prompt is piped via stdin to avoid Windows 32 KB command-line limit
    argv = [claude_bin, "-p", "--system-prompt-file", system_prompt_file]
    if with_images:
        # Image content blocks require the structured stdin protocol
        # (--input-format stream-json), which the CLI only allows paired
        # with a matching structured output stream — --output-format json
        # is rejected in that mode — and --verbose is required by --print
        # + --output-format stream-json. The closing `type: "result"` line
        # carries the same result/total_cost_usd/usage/duration_ms fields
        # as the plain json envelope (see `_extract_envelope`).
        argv += ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    else:
        argv += ["--output-format", "json"]
    argv += ["--model", model, "--no-session-persistence", "--tools", ""]
    if json_schema is not None:
        argv += ["--json-schema", json.dumps(json_schema)]
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", str(max_budget_usd)]
    return argv


def _build_stdin_payload(user_prompt: str, images: list[ImageAttachment]) -> bytes:
    if not images:
        return user_prompt.encode("utf-8")
    content: list[dict[str, Any]] = []
    for img in images:
        # A text block naming each image immediately before it, since the
        # model otherwise has no way to tell which screenshot is which once
        # there's more than one in the same turn.
        content.append({"type": "text", "text": f"Screenshot: {img.label}"})
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": img.media_type, "data": img.base64_data},
            }
        )
    content.append({"type": "text", "text": user_prompt})
    message = {"type": "user", "message": {"role": "user", "content": content}}
    # A single NDJSON line — one user turn, no multi-turn streaming needed.
    return (json.dumps(message) + "\n").encode("utf-8")


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
        images: list[ImageAttachment] | None = None,
    ) -> ProviderResult:
        model = model or DEFAULT_MODEL
        timeout = timeout or DEFAULT_TIMEOUT_SECONDS
        images = images or []

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
                with_images=bool(images),
            )
            stdin_payload = _build_stdin_payload(user_prompt, images)

            start = time.monotonic()
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=stdin_payload), timeout=timeout
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
    def _extract_envelope(stdout: str) -> dict[str, Any]:
        # Plain mode (--output-format json): the whole stdout is one JSON
        # object. Image mode (--output-format stream-json): stdout is
        # NDJSON — one event per line — and the closing `type: "result"`
        # line carries the same result/total_cost_usd/usage/duration_ms
        # fields the plain envelope does, so the rest of this parser
        # doesn't need to know which mode produced it.
        try:
            envelope = json.loads(stdout)
            if isinstance(envelope, dict):
                return envelope
        except json.JSONDecodeError:
            pass

        result_event: dict[str, Any] | None = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "result":
                result_event = obj
        if result_event is not None:
            return result_event

        raise ClaudeCliError("could not parse claude CLI stdout as JSON", stdout=stdout)

    @staticmethod
    def _parse_output(stdout: str, *, model: str, elapsed_ms: int) -> ProviderResult:
        envelope = ClaudeCliProvider._extract_envelope(stdout)

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
