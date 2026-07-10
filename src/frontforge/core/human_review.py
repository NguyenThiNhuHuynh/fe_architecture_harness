"""Human-in-the-loop checkpoints — the 2 pause points from the harness
diagram: after every stage except `quality_review` ("keep going, or fix
this one?"), and after `quality_review` itself ("auto-fix codegen from
these issues?"). Orchestrator only knows it can call `review_stage()` /
`review_quality()`; it has no idea whether that means a terminal prompt,
a future web UI, or nothing at all (the default hook always says "proceed"
so unattended runs are unaffected unless a hook is explicitly wired in).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class StageDecision:
    proceed: bool
    stop_for_manual_edit: bool = False
    feedback: str | None = None


class HumanReviewHook:
    """No-op default: always proceed, never auto-fix. Fully unattended."""

    async def review_stage(self, stage_id: str, output: dict[str, Any]) -> StageDecision:
        return StageDecision(proceed=True)

    async def review_quality(self, issues: list[dict[str, Any]]) -> bool:
        return False


class CliHumanReviewHook(HumanReviewHook):
    """Pauses the terminal at both checkpoints, per the harness diagram."""

    async def review_stage(self, stage_id: str, output: dict[str, Any]) -> StageDecision:
        import typer

        typer.echo(f"\n--- HUMAN-IN-THE-LOOP: stage '{stage_id}' hoàn tất ---")
        preview = json.dumps(output, indent=2, ensure_ascii=False)
        typer.echo(preview[:2000] + ("\n... (đã cắt bớt)" if len(preview) > 2000 else ""))

        if typer.confirm("Tiếp tục sang stage tiếp theo?", default=True):
            return StageDecision(proceed=True)
        if typer.confirm("Dừng lại để bạn tự sửa output (thoát pipeline)?", default=False):
            return StageDecision(proceed=False, stop_for_manual_edit=True)
        feedback = typer.prompt("Nhập yêu cầu chỉnh sửa cho stage này (feedback tự nhiên)")
        return StageDecision(proceed=False, feedback=feedback)

    async def review_quality(self, issues: list[dict[str, Any]]) -> bool:
        import typer

        if not issues:
            return False
        typer.echo("\n--- HUMAN-IN-THE-LOOP: quality_review issues ---")
        for issue in issues:
            typer.echo(f"[{issue.get('severity', '?')}] {issue.get('description', '')}")
        return typer.confirm("Tự động chạy sửa lỗi codegen với các issue trên?", default=False)
