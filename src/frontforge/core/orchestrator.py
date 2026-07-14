"""The DAG executor. This is the only place that knows about stage order,
retries and dirty propagation — it has no idea what "codegen" or
"requirement" actually produce.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.trace import Status, StatusCode

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

# Resolved lazily against whatever TracerProvider/MeterProvider
# core.tracing.configure_tracing()/configure_metrics() install later
# (get_tracer()/get_meter() at import time return proxies for exactly this
# reason) — traces/metrics are a standardized layer alongside EventLogger's
# JSONL, not a replacement for it.
_tracer = otel_trace.get_tracer("frontforge.orchestrator")
_meter = otel_metrics.get_meter("frontforge.orchestrator")

_stage_duration_histogram = _meter.create_histogram(
    "frontforge.stage.duration_ms",
    unit="ms",
    description="Stage execution duration, recorded once per stage completion (done or failed).",
)
_stage_count_counter = _meter.create_counter(
    "frontforge.stage.count",
    description="Stage completions, labeled by outcome (done/failed).",
)
_cost_counter = _meter.create_counter(
    "frontforge.cost.total_usd",
    unit="usd",
    description="Cumulative LLM call cost, recorded per attempt (including failed attempts).",
)

# The only Gen-AI backend this harness talks to — a fixed constant, not a
# per-call attribute, since every provider here is the `claude` CLI.
_GEN_AI_SYSTEM = "anthropic"

# Cap on prompt/response text captured per event-log line — enough to debug
# a run from the JSONL alone without turning the log into a second copy of
# every LLM payload ever sent.
_EVENT_TEXT_TRUNCATE = 2000


def _truncate(text: str, limit: int = _EVENT_TEXT_TRUNCATE) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated, {len(text)} chars total]"


class PipelineBudgetExceededError(RuntimeError):
    """Raised by a stage's on_batch_cost callback the moment a mid-stage cost
    report pushes cumulative pipeline spend past max_total_cost_usd. Lets a
    multi-call agent (e.g. codegen's batched path) be stopped between its own
    internal calls, instead of the cap only being checked between whole
    stages — where a single batched stage could already have spent well past
    the configured cap before anyone looked."""


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
        max_total_cost_usd: float | None = None,
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
        # Pipeline-wide spending cap — distinct from the provider's own
        # --max-budget-usd (which only bounds a single call). Checked between
        # DAG loop iterations in run_all(), using the same cost_usd already
        # tracked per stage — so knowing what was spent and stopping before
        # overspending share one source of truth.
        self.max_total_cost_usd = max_total_cost_usd
        self._total_cost_usd = 0.0
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
        # Sequential id for each HITL checkpoint reached this run_all() call —
        # gives every "frontforge.hitl_review" span/event a unique
        # frontforge.hitl.checkpoint_id, since a stage can be revisited
        # multiple times (feedback -> rerun -> reviewed again).
        self._hitl_checkpoint_seq = 0

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
        with _tracer.start_as_current_span(
            "frontforge.stage", attributes={"frontforge.stage.name": stage_id}
        ) as stage_span:
            return await self._run_stage(stage_id, stage_span)

    async def _run_stage(self, stage_id: str, stage_span: otel_trace.Span) -> StageState:
        agent = self.registry.create_agent(stage_id)
        seed = self._seed()
        ancestors = self._ancestor_outputs(stage_id)
        input_hash = content_hash({"seed": seed, "ancestors": ancestors})
        model = model_for_stage(stage_id)
        stage_span.set_attribute("gen_ai.system", _GEN_AI_SYSTEM)
        stage_span.set_attribute("gen_ai.request.model", model or "")

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
            # Cost a batched agent (e.g. codegen) reports as each of its
            # internal calls completes — added to self._total_cost_usd in
            # real time below, so a mid-attempt budget check can fire and a
            # later batch failing can't erase the spend of the batches that
            # already succeeded this attempt. Stays 0 for single-call agents.
            attempt_batch_cost = 0.0

            def _on_batch_cost(cost: float) -> None:
                nonlocal attempt_batch_cost
                attempt_batch_cost += cost
                self._total_cost_usd += cost
                if self.max_total_cost_usd is not None and self._total_cost_usd >= self.max_total_cost_usd:
                    raise PipelineBudgetExceededError(
                        f"pipeline cost cap ${self.max_total_cost_usd:.4f} reached mid-stage "
                        f"{stage_id!r} (spent ${self._total_cost_usd:.4f})"
                    )

            try:
                agent_result = await agent.run(
                    self.provider,
                    seed=seed,
                    ancestors=ancestors,
                    model=model,
                    verification_errors=verification_errors or None,
                    on_batch_cost=_on_batch_cost,
                )
            except PipelineBudgetExceededError as exc:
                total_cost_usd += attempt_batch_cost
                duration_ms = int((time.monotonic() - stage_start) * 1000)
                self.logger.warning("stage %s stopped: %s", stage_id, exc)
                self.events.log(
                    "stage_budget_exceeded_mid_stage",
                    stage_id=stage_id,
                    attempt=attempt + 1,
                    cost_usd=round(total_cost_usd, 6),
                    cap_usd=self.max_total_cost_usd,
                )
                stage_span.set_attribute("frontforge.stage.duration_ms", duration_ms)
                stage_span.set_attribute("frontforge.stage.cost_usd", total_cost_usd)
                stage_span.set_status(Status(StatusCode.ERROR, description=str(exc)))
                return self.state.update(
                    stage_id,
                    status=StageStatus.FAILED,
                    input_hash=input_hash,
                    error=str(exc),
                    bump_attempts=True,
                    duration_ms=duration_ms,
                    cost_usd=total_cost_usd,
                )
            except Exception as exc:  # provider/agent failure — retry with the error as feedback
                total_cost_usd += attempt_batch_cost
                last_error = str(exc)
                # An agent/tool can mark an exception `.retryable = False` (e.g.
                # FigmaTool: a missing token or an invalid file URL fails
                # identically every time) to say retrying is pointless — fail
                # the stage now instead of burning through max_retries for a
                # failure no attempt could ever fix.
                if getattr(exc, "retryable", True) is False:
                    duration_ms = int((time.monotonic() - stage_start) * 1000)
                    self.logger.warning("stage %s failed non-retryably: %s", stage_id, exc)
                    self.events.log(
                        "stage_failed_non_retryable",
                        stage_id=stage_id,
                        attempt=attempt + 1,
                        error=last_error,
                        cost_usd=round(total_cost_usd, 6),
                    )
                    stage_span.set_attribute("frontforge.stage.duration_ms", duration_ms)
                    stage_span.set_attribute("frontforge.stage.cost_usd", total_cost_usd)
                    stage_span.set_status(Status(StatusCode.ERROR, description=last_error))
                    return self.state.update(
                        stage_id,
                        status=StageStatus.FAILED,
                        input_hash=input_hash,
                        error=last_error,
                        bump_attempts=True,
                        duration_ms=duration_ms,
                        cost_usd=total_cost_usd,
                    )
                verification_errors = [f"agent/provider error: {exc}"]
                self.logger.warning("stage %s attempt %d raised: %s", stage_id, attempt + 1, exc)
                self.events.log(
                    "stage_attempt_error",
                    stage_id=stage_id,
                    attempt=attempt + 1,
                    error=str(exc),
                    cost_usd=round(attempt_batch_cost, 6),
                )
                stage_span.add_event(
                    "attempt_error",
                    attributes={"frontforge.stage.attempt": attempt + 1, "frontforge.error": str(exc)[:500]},
                )
                continue

            attempt_cost = agent_result.provider_result.cost_usd or 0.0
            total_cost_usd += attempt_cost
            # attempt_batch_cost already flowed into self._total_cost_usd via
            # on_batch_cost as each internal call completed (0 for
            # single-call agents) — only add whatever wasn't reported that way.
            self._total_cost_usd += attempt_cost - attempt_batch_cost
            # Captures what was actually sent/received for this attempt —
            # tracing needs this even when verification later fails, so it's
            # logged unconditionally rather than only on the final outcome.
            self.events.log(
                "stage_attempt_llm_call",
                stage_id=stage_id,
                attempt=attempt + 1,
                model=agent_result.provider_result.model,
                duration_ms=agent_result.provider_result.duration_ms,
                cost_usd=round(attempt_cost, 6),
                system_prompt=_truncate(agent_result.system_prompt),
                user_prompt=_truncate(agent_result.user_prompt),
                response=_truncate(agent_result.provider_result.raw_text),
            )
            llm_call_attributes: dict[str, Any] = {
                "gen_ai.system": _GEN_AI_SYSTEM,
                "gen_ai.request.model": agent_result.provider_result.model,
                "gen_ai.operation.name": "chat",
                "frontforge.stage.attempt": attempt + 1,
                "frontforge.cost_usd": attempt_cost,
                "frontforge.duration_ms": agent_result.provider_result.duration_ms,
            }
            if agent_result.provider_result.input_tokens is not None:
                llm_call_attributes["gen_ai.usage.input_tokens"] = agent_result.provider_result.input_tokens
            if agent_result.provider_result.output_tokens is not None:
                llm_call_attributes["gen_ai.usage.output_tokens"] = agent_result.provider_result.output_tokens
            stage_span.add_event("llm_call", attributes=llm_call_attributes)
            _cost_counter.add(attempt_cost, attributes={"frontforge.stage.name": stage_id})

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
                stage_span.set_attribute("frontforge.stage.duration_ms", duration_ms)
                stage_span.set_attribute("frontforge.stage.cost_usd", total_cost_usd)
                stage_span.set_attribute("frontforge.stage.attempts", attempt + 1)
                stage_span.set_status(Status(StatusCode.OK))
                _stage_duration_histogram.record(
                    duration_ms,
                    attributes={"frontforge.stage.name": stage_id, "frontforge.stage.status": "done"},
                )
                _stage_count_counter.add(
                    1, attributes={"frontforge.stage.name": stage_id, "frontforge.stage.status": "done"}
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
                # Structured form of the same failure — keeps verifier/severity
                # queryable instead of only living inside a flattened string,
                # so cross-run aggregation (e.g. "which verifier fails most")
                # doesn't need to re-parse `errors`.
                issues=[issue.model_dump() for issue in result.issues],
            )
            stage_span.add_event(
                "verification_failed",
                attributes={
                    "frontforge.stage.attempt": attempt + 1,
                    "frontforge.verification.issue_count": len(result.issues),
                    "frontforge.verification.verifiers": ",".join(
                        sorted({issue.verifier for issue in result.issues})
                    ),
                },
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
        stage_span.set_attribute("frontforge.stage.duration_ms", duration_ms)
        stage_span.set_attribute("frontforge.stage.cost_usd", total_cost_usd)
        stage_span.set_attribute("frontforge.stage.attempts", self.max_retries + 1)
        stage_span.set_status(Status(StatusCode.ERROR, description=last_error or ""))
        _stage_duration_histogram.record(
            duration_ms,
            attributes={"frontforge.stage.name": stage_id, "frontforge.stage.status": "failed"},
        )
        _stage_count_counter.add(
            1, attributes={"frontforge.stage.name": stage_id, "frontforge.stage.status": "failed"}
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
        with _tracer.start_as_current_span(
            "frontforge.pipeline_run",
            attributes={
                "frontforge.dag.run_id": self.run_id,
                "frontforge.dag.only": only or "",
                "frontforge.dag.to": to or "",
            },
        ) as run_span:
            try:
                states = await self._run_all(only=only, to=to)
            except Exception as exc:
                run_span.set_status(Status(StatusCode.ERROR, description=str(exc)))
                raise
            failed = [s for s in states.values() if s.status == StageStatus.FAILED]
            run_span.set_attribute("frontforge.dag.stages_done", len(states) - len(failed))
            run_span.set_attribute("frontforge.dag.stages_failed", len(failed))
            run_span.set_attribute("frontforge.dag.total_cost_usd", self._total_cost_usd)
            run_span.set_status(Status(StatusCode.ERROR if failed else StatusCode.OK))
            return states

    async def _run_all(
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
        self._total_cost_usd = 0.0
        self._hitl_checkpoint_seq = 0
        try:
            target_ids = self._resolve_targets(only=only, to=to)

            while True:
                if self.max_total_cost_usd is not None and self._total_cost_usd >= self.max_total_cost_usd:
                    self.logger.warning(
                        "pipeline cost $%.4f has reached the $%.4f cap — stopping before starting more stages",
                        self._total_cost_usd,
                        self.max_total_cost_usd,
                    )
                    self.events.log(
                        "pipeline_budget_exceeded",
                        total_cost_usd=round(self._total_cost_usd, 6),
                        cap_usd=self.max_total_cost_usd,
                    )
                    break

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

        self._hitl_checkpoint_seq += 1
        checkpoint_id = f"{self.run_id}:{self._hitl_checkpoint_seq}"
        with _tracer.start_as_current_span(
            "frontforge.hitl_review",
            attributes={
                "frontforge.stage.name": stage_id,
                "frontforge.hitl.checkpoint_id": checkpoint_id,
            },
        ) as review_span:
            await self._run_human_review_traced(stage_id, checkpoint_id, review_span)

    async def _run_human_review_traced(
        self, stage_id: str, checkpoint_id: str, review_span: otel_trace.Span
    ) -> None:
        if stage_id == "quality_review":
            if self._autofix_rounds.get("codegen", 0) >= self.MAX_HUMAN_REVISIONS:
                self.logger.warning(
                    "codegen already auto-fixed %d time(s) this run; not asking again",
                    self.MAX_HUMAN_REVISIONS,
                )
                self.events.log(
                    "hitl_autofix_skipped_cap_reached",
                    stage_id=stage_id,
                    checkpoint_id=checkpoint_id,
                    rounds=self._autofix_rounds.get("codegen", 0),
                )
                review_span.add_event("autofix_skipped_cap_reached")
                return
            output = self.state.load_output(stage_id) or {}
            issues = output.get("issues", [])
            approved = await self.human_review.review_quality(issues)
            self.events.log(
                "hitl_autofix_decision",
                stage_id=stage_id,
                checkpoint_id=checkpoint_id,
                approved=approved,
                issues_count=len(issues),
            )
            review_span.set_attribute("frontforge.hitl.approved", approved)
            review_span.set_attribute("frontforge.hitl.issues_count", len(issues))
            if approved:
                self._pending_feedback["codegen"] = [
                    f"[quality_review:{issue.get('severity', '?')}] {issue.get('description', '')}"
                    for issue in issues
                ]
                self._autofix_rounds["codegen"] = self._autofix_rounds.get("codegen", 0) + 1
                self.mark_dirty("codegen", reason="quality_review_autofix")
            return

        if self._autofix_rounds.get(stage_id, 0) >= self.MAX_HUMAN_REVISIONS:
            self.logger.warning(
                "stage %s already revised %d time(s) this run; not asking again",
                stage_id,
                self.MAX_HUMAN_REVISIONS,
            )
            self.events.log(
                "hitl_review_skipped_cap_reached",
                stage_id=stage_id,
                checkpoint_id=checkpoint_id,
                rounds=self._autofix_rounds.get(stage_id, 0),
            )
            review_span.add_event("review_skipped_cap_reached")
            return
        output = self.state.load_output(stage_id) or {}
        decision = await self.human_review.review_stage(stage_id, output)
        self.events.log(
            "hitl_decision",
            stage_id=stage_id,
            checkpoint_id=checkpoint_id,
            proceed=decision.proceed,
            stop_for_manual_edit=decision.stop_for_manual_edit,
            has_feedback=bool(decision.feedback),
        )
        review_span.set_attribute("frontforge.hitl.proceed", decision.proceed)
        review_span.set_attribute("frontforge.hitl.stop_for_manual_edit", decision.stop_for_manual_edit)
        review_span.set_attribute("frontforge.hitl.has_feedback", bool(decision.feedback))
        if decision.stop_for_manual_edit:
            self._paused = True
        elif decision.feedback:
            self._pending_feedback[stage_id] = [decision.feedback]
            self._autofix_rounds[stage_id] = self._autofix_rounds.get(stage_id, 0) + 1
            self.mark_dirty(stage_id, reason="hitl_feedback")

    def mark_dirty(self, stage_id: str, reason: str = "manual") -> list[str]:
        affected = self.state.mark_dirty_cascade(stage_id, self.registry.dependents_of)
        self.events.log(
            "mark_dirty",
            stage_id=stage_id,
            affected=affected,
            affected_count=len(affected),
            reason=reason,
        )
        otel_trace.get_current_span().add_event(
            "mark_dirty",
            attributes={
                "frontforge.mark_dirty.stage_id": stage_id,
                "frontforge.mark_dirty.affected_count": len(affected),
                "frontforge.mark_dirty.reason": reason,
            },
        )
        return affected
