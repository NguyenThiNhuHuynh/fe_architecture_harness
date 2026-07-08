"""The single place that persists pipeline state to disk.

Agents never write files directly (see FilesystemTool for the one exception —
writing the codegen result to the generated/ output dir). Everything about
*pipeline progress* — status, input hashes, outputs — goes through here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone

from frontforge.core.session import RunSession
from frontforge.shared.types import StageState, StageStatus
from frontforge.shared.utils import read_json, write_json


class StateStore:
    def __init__(self, session: RunSession):
        self.session = session
        self._states: dict[str, StageState] = {}
        self._load()

    def _load(self) -> None:
        if self.session.state_file.exists():
            raw = read_json(self.session.state_file)
            self._states = {
                stage_id: StageState.model_validate(data) for stage_id, data in raw.items()
            }

    def _persist(self) -> None:
        write_json(
            self.session.state_file,
            {stage_id: state.model_dump(mode="json") for stage_id, state in self._states.items()},
        )

    def get(self, stage_id: str) -> StageState:
        return self._states.get(stage_id, StageState(stage_id=stage_id))

    def all(self) -> dict[str, StageState]:
        return dict(self._states)

    def update(
        self,
        stage_id: str,
        *,
        status: StageStatus,
        input_hash: str | None = None,
        error: str | None = None,
        bump_attempts: bool = False,
    ) -> StageState:
        current = self.get(stage_id)
        attempts = current.attempts + 1 if bump_attempts else current.attempts
        new_state = StageState(
            stage_id=stage_id,
            status=status,
            input_hash=input_hash if input_hash is not None else current.input_hash,
            updated_at=datetime.now(timezone.utc),
            attempts=attempts,
            error=error,
        )
        self._states[stage_id] = new_state
        self._persist()
        return new_state

    def save_output(self, stage_id: str, output: dict) -> None:
        write_json(self.session.outputs_dir / f"{stage_id}.json", output)

    def load_output(self, stage_id: str) -> dict | None:
        path = self.session.outputs_dir / f"{stage_id}.json"
        if not path.exists():
            return None
        return read_json(path)

    def outputs_for(self, stage_ids: Iterable[str]) -> dict[str, dict]:
        result = {}
        for stage_id in stage_ids:
            output = self.load_output(stage_id)
            if output is not None:
                result[stage_id] = output
        return result

    def mark_dirty_cascade(
        self, stage_id: str, dependents_of: Callable[[str], list[str]]
    ) -> list[str]:
        """Mark `stage_id` and every transitive dependent as dirty.

        `dependents_of(stage_id)` returns the direct dependents of a stage —
        supplied by StageRegistry to avoid a circular import.
        """
        affected: list[str] = []
        queue = [stage_id]
        seen: set[str] = set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            affected.append(current)
            self.update(current, status=StageStatus.DIRTY)
            queue.extend(dependents_of(current))
        return affected
