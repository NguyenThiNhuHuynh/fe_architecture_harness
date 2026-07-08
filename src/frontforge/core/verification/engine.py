"""Verification is the sole decider of pass/fail — agents never grade their
own output. Each stage maps to a list of verifiers; a stage passes only if
none of its verifiers report an `error`-severity issue."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from frontforge.core.session import RunSession
from frontforge.shared.types import VerificationIssue, VerificationResult


@runtime_checkable
class Verifier(Protocol):
    name: str

    async def verify(
        self, *, stage_id: str, output: dict[str, Any], session: RunSession
    ) -> list[VerificationIssue]: ...


class VerificationEngine:
    def __init__(self, verifiers_by_stage: dict[str, list[Verifier]]):
        self.verifiers_by_stage = verifiers_by_stage

    async def run(
        self, stage_id: str, output: dict[str, Any], session: RunSession
    ) -> VerificationResult:
        issues: list[VerificationIssue] = []
        for verifier in self.verifiers_by_stage.get(stage_id, []):
            issues.extend(await verifier.verify(stage_id=stage_id, output=output, session=session))
        passed = not any(issue.severity == "error" for issue in issues)
        return VerificationResult(passed=passed, issues=issues)
