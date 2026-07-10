"""The DAG executor. This is the only place that knows about stage order,
retries and dirty propagation — it has no idea what "codegen" or
"requirement" actually produce.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from frontforge.config.models import model_for_stage
from frontforge.config.stages import StageRegistry
from frontforge.config.verification import build_stage_verifiers
from frontforge.core.human_review import HumanReviewHook
from frontforge.core.lock import RunLock
from frontforge.core.logger import EventLogger, get_logger
from frontforge.core.session import RunSession
from frontforge.core.state_store import StateStore
from frontforge.core.verification.engine import VerificationEngine
from frontforge.providers.base import Provider
from frontforge.shared.constants import DEFAULT_MAX_RETRIES
from frontforge.shared.types import CodegenResult, StageState, StageStatus
from frontforge.shared.utils import content_hash, read_json
from frontforge.tools.filesystem_tool import FilesystemTool


class Orchestrator:
    MAX_HUMAN_REVISIONS = 3

    def __init__(
        self,
        session: RunSession,
        provider: Provider,
        *,
        registry: StageRegistry | None = None,
        state_store: StateStore | None = None,
        verification_engine: VerificationEngine | None = None,
        filesystem_tool: FilesystemTool | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        run_id: str | None = None,
        human_review: HumanReviewHook | None = None,
    ):
        self.session = session
        self.provider = provider
        self.registry = registry or StageRegistry()
        self.state = state_store or StateStore(session)
        self.verification = verification_engine or VerificationEngine(build_stage_verifiers())
        self.filesystem = filesystem_tool or FilesystemTool(session.generated_dir)
        self.max_retries = max_retries
        self.logger = get_logger()
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.events = EventLogger(session.logs_dir, self.run_id)
        # None = fully unattended (no pauses at all) — distinct from
        # HumanReviewHook() the no-op default, which still *asks* but always
        # gets "proceed"/"no auto-fix". Orchestrator only calls these hooks
        # when one is actually supplied, so existing unattended callers
        # (tests, `--only`/`--to` scripting) are unaffected.
        self.human_review = human_review
        # Feedback queued for a stage's *next* attempt — either from a human
        # rejecting a stage's output (feedback text) or from quality_review's
        # issues after an auto-fix approval. Seeded into that stage's first
        # verification_errors so it flows through the exact same prompt
        # channel as a normal retry, no new plumbing needed.
        self._pending_feedback: dict[str, list[str]] = {}
        self._paused = False
        # Safety cap on how many times a stage can be re-triggered via HITL
        # feedback/auto-fix *within one run_all() call*. Without this, a
        # human (or a script) that keeps answering "yes, fix it" to an issue
        # the model can never actually resolve — exactly the false-positive
        # quality_review case seen in testing — would loop forever.
        self._autofix_rounds: dict[str, int] = {}

    # -- context assembly -------------------------------------------------

    def _seed(self) -> dict[str, Any]:
        if self.session.seed_file.exists():
            return read_json(self.session.seed_file)
        return {}

    def _ancestor_outputs(self, stage_id: str) -> dict[str, dict[str, Any]]:
        return self.state.outputs_for(self.registry.ancestors_of(stage_id))

    def _current_input_hash(self, stage_id: str) -> str:
        return content_hash({"seed": self._seed(), "ancestors": self._ancestor_outputs(stage_id)})

    def _is_effectively_done(self, stage_id: str) -> bool:
        state = self.state.get(stage_id)
        if state.status != StageStatus.DONE:
            return False
        return state.input_hash == self._current_input_hash(stage_id)

    # -- single stage execution --------------------------------------------

    async def run_stage(self, stage_id: str) -> StageState:
        agent = self.registry.create_agent(stage_id)
        seed = self._seed()
        ancestors = self._ancestor_outputs(stage_id)
        input_hash = content_hash({"seed": seed, "ancestors": ancestors})
        model = model_for_stage(stage_id)

        stage_start = time.monotonic()
        self.state.update(stage_id, status=StageStatus.RUNNING, input_hash=input_hash)
        self.events.log("stage_started", stage_id=stage_id, model=model)
        # Seed with any feedback queued for this stage (human rejected its
        # previous output, or quality_review issues approved for auto-fix) —
        # rides the same "errors from last time" prompt channel as a retry.
        verification_errors: list[str] = self._pending_feedback.pop(stage_id, [])
        last_error: str | None = None
        total_cost_usd = 0.0

        for attempt in range(self.max_retries + 1):
            try:
                agent_result = await agent.run(
                    self.provider,
                    seed=seed,
                    ancestors=ancestors,
                    model=model,
                    verification_errors=verification_errors or None,
                )
            except Exception as exc:  # provider/agent failure — retry with the error as feedback
                last_error = str(exc)
                verification_errors = [f"agent/provider error: {exc}"]
                self.logger.warning("stage %s attempt %d raised: %s", stage_id, attempt + 1, exc)
                self.events.log(
                    "stage_attempt_error", stage_id=stage_id, attempt=attempt + 1, error=str(exc)
                )
                continue

            total_cost_usd += agent_result.provider_result.cost_usd or 0.0

            result = await self.verification.run(stage_id, agent_result.output, self.session)
            if result.passed:
                mapped = agent.map_output(agent_result.output)
                if stage_id == "codegen" and isinstance(mapped, CodegenResult):
                    self.filesystem.write_files(mapped.files)
                self.state.save_output(stage_id, mapped.model_dump(mode="json"))
                duration_ms = int((time.monotonic() - stage_start) * 1000)
                self.logger.info(
                    "stage %s DONE in %dms (cost=$%.4f, model=%s, attempts=%d)",
                    stage_id,
                    duration_ms,
                    total_cost_usd,
                    model,
                    attempt + 1,
                )
                self.events.log(
                    "stage_done",
                    stage_id=stage_id,
                    duration_ms=duration_ms,
                    cost_usd=round(total_cost_usd, 6),
                    model=model,
                    attempts=attempt + 1,
                )
                return self.state.update(
                    stage_id,
                    status=StageStatus.DONE,
                    input_hash=input_hash,
                    bump_attempts=True,
                    duration_ms=duration_ms,
                    cost_usd=total_cost_usd,
                )

            verification_errors = [
                f"[{issue.verifier}] {issue.message}" for issue in result.issues if issue.severity == "error"
            ]
            last_error = "; ".join(verification_errors)
            self.logger.warning(
                "stage %s attempt %d failed verification: %s", stage_id, attempt + 1, last_error
            )
            self.events.log(
                "stage_attempt_verification_failed",
                stage_id=stage_id,
                attempt=attempt + 1,
                errors=verification_errors,
            )

        duration_ms = int((time.monotonic() - stage_start) * 1000)
        self.logger.warning(
            "stage %s FAILED after %d attempt(s) in %dms (cost=$%.4f)",
            stage_id,
            self.max_retries + 1,
            duration_ms,
            total_cost_usd,
        )
        self.events.log(
            "stage_failed",
            stage_id=stage_id,
            duration_ms=duration_ms,
            cost_usd=round(total_cost_usd, 6),
            error=last_error,
        )
        return self.state.update(
            stage_id,
            status=StageStatus.FAILED,
            input_hash=input_hash,
            error=last_error,
            bump_attempts=True,
            duration_ms=duration_ms,
            cost_usd=total_cost_usd,
        )

    # -- DAG execution -----------------------------------------------------

    def _resolve_targets(self, *, only: str | None, to: str | None) -> set[str]:
        if only:
            return {only}
        if to:
            return self.registry.ancestors_of(to) | {to}
        return set(self.registry.all_ids())

    async def run_all(
        self, *, only: str | None = None, to: str | None = None
    ) -> dict[str, StageState]:
        # Guards against a second `frontforge run` racing this one on the
        # same project — both would read/write .harness/state.json with no
        # other coordination. Raises RunLockError if another live process
        # already holds it; self-heals if the previous holder is dead.
        lock = RunLock(self.session.run_lock_file)
        lock.acquire()
        # Tracks stages that failed *within this run_all() call* so we don't
        # re-select the same failed stage forever in the loop below. This is
        # deliberately NOT based on persisted status: a stage FAILED from a
        # previous run (e.g. hit a usage/rate limit) must still be eligible
        # for a fresh attempt the next time `frontforge run` is invoked.
        failed_this_call: set[str] = set()
        self._paused = False
        self._autofix_rounds = {}
        try:
            target_ids = self._resolve_targets(only=only, to=to)

            while True:
                done_ids = {
                    sid for sid in self.registry.all_ids() if self._is_effectively_done(sid)
                }
                remaining = [sid for sid in target_ids if sid not in done_ids]
                if not remaining:
                    break
                ready = [
                    sid
                    for sid in self.registry.ready_stages(done_ids)
                    if sid in remaining and sid not in failed_this_call
                ]
                if not ready:
                    break  # remaining stages are blocked by a failed/incomplete dependency
                results = await asyncio.gather(*(self.run_stage(sid) for sid in ready))
                for stage_id, result in zip(ready, results):
                    if result.status == StageStatus.FAILED:
                        failed_this_call.add(stage_id)
                    elif result.status == StageStatus.DONE:
                        await self._run_human_review(stage_id)

                if self._paused:
                    break  # human chose to stop for manual editing

            return self.state.all()
        finally:
            lock.release()

    async def _run_human_review(self, stage_id: str) -> None:
        """The 2 HITL checkpoints: every DONE stage except quality_review
        asks "keep going?"; quality_review asks "auto-fix from these
        issues?". No-op entirely when no hook was supplied."""
        if self.human_review is None:
            return

        if stage_id == "quality_review":
            if self._autofix_rounds.get("codegen", 0) >= self.MAX_HUMAN_REVISIONS:
                self.logger.warning(
                    "codegen already auto-fixed %d time(s) this run; not asking again",
                    self.MAX_HUMAN_REVISIONS,
                )
                return
            output = self.state.load_output(stage_id) or {}
            issues = output.get("issues", [])
            if await self.human_review.review_quality(issues):
                self._pending_feedback["codegen"] = [
                    f"[quality_review:{issue.get('severity', '?')}] {issue.get('description', '')}"
                    for issue in issues
                ]
                self._autofix_rounds["codegen"] = self._autofix_rounds.get("codegen", 0) + 1
                self.mark_dirty("codegen")
            return

        if self._autofix_rounds.get(stage_id, 0) >= self.MAX_HUMAN_REVISIONS:
            self.logger.warning(
                "stage %s already revised %d time(s) this run; not asking again",
                stage_id,
                self.MAX_HUMAN_REVISIONS,
            )
            return
        output = self.state.load_output(stage_id) or {}
        decision = await self.human_review.review_stage(stage_id, output)
        if decision.stop_for_manual_edit:
            self._paused = True
        elif decision.feedback:
            self._pending_feedback[stage_id] = [decision.feedback]
            self._autofix_rounds[stage_id] = self._autofix_rounds.get(stage_id, 0) + 1
            self.mark_dirty(stage_id)

    def mark_dirty(self, stage_id: str) -> list[str]:
        return self.state.mark_dirty_cascade(stage_id, self.registry.dependents_of)
