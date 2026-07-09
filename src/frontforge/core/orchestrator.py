"""The DAG executor. This is the only place that knows about stage order,
retries and dirty propagation — it has no idea what "codegen" or
"requirement" actually produce.
"""

from __future__ import annotations

import asyncio
from typing import Any

from frontforge.config.models import model_for_stage
from frontforge.config.stages import StageRegistry
from frontforge.config.verification import build_stage_verifiers
from frontforge.core.lock import RunLock
from frontforge.core.logger import get_logger
from frontforge.core.session import RunSession
from frontforge.core.state_store import StateStore
from frontforge.core.verification.engine import VerificationEngine
from frontforge.providers.base import Provider
from frontforge.shared.constants import DEFAULT_MAX_RETRIES
from frontforge.shared.types import CodegenResult, StageState, StageStatus
from frontforge.shared.utils import content_hash, read_json
from frontforge.tools.filesystem_tool import FilesystemTool


class Orchestrator:
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
    ):
        self.session = session
        self.provider = provider
        self.registry = registry or StageRegistry()
        self.state = state_store or StateStore(session)
        self.verification = verification_engine or VerificationEngine(build_stage_verifiers())
        self.filesystem = filesystem_tool or FilesystemTool(session.generated_dir)
        self.max_retries = max_retries
        self.logger = get_logger()

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

        self.state.update(stage_id, status=StageStatus.RUNNING, input_hash=input_hash)
        verification_errors: list[str] = []
        last_error: str | None = None

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
                continue

            result = await self.verification.run(stage_id, agent_result.output, self.session)
            if result.passed:
                mapped = agent.map_output(agent_result.output)
                if stage_id == "codegen" and isinstance(mapped, CodegenResult):
                    self.filesystem.write_files(mapped.files)
                self.state.save_output(stage_id, mapped.model_dump(mode="json"))
                return self.state.update(
                    stage_id, status=StageStatus.DONE, input_hash=input_hash, bump_attempts=True
                )

            verification_errors = [
                f"[{issue.verifier}] {issue.message}" for issue in result.issues if issue.severity == "error"
            ]
            last_error = "; ".join(verification_errors)
            self.logger.warning(
                "stage %s attempt %d failed verification: %s", stage_id, attempt + 1, last_error
            )

        return self.state.update(
            stage_id,
            status=StageStatus.FAILED,
            input_hash=input_hash,
            error=last_error,
            bump_attempts=True,
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

            return self.state.all()
        finally:
            lock.release()

    def mark_dirty(self, stage_id: str) -> list[str]:
        return self.state.mark_dirty_cascade(stage_id, self.registry.dependents_of)
