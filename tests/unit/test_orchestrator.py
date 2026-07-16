import json

import pytest

import frontforge.core.lock as lock_module
from frontforge.agents.base import StageAgent
from frontforge.config.stages import StageDefinition, StageRegistry
from frontforge.core.orchestrator import Orchestrator
from frontforge.shared.types import AgentResult, ProviderResult, StageStatus
from frontforge.shared.utils import write_json

from conftest import read_events


class _EmptyOutput:
    """Stand-in output_model — these tests never verify/map output, they only
    exercise the cost-tracking path around on_batch_cost."""

    @staticmethod
    def model_json_schema() -> dict:
        return {"title": "Empty", "type": "object"}


class _MultiCallFakeAgent(StageAgent):
    """A minimal multi-call agent, mirroring CodegenAgent._run_batched's
    contract: it reports each internal call's cost via on_batch_cost as soon
    as that call completes, before deciding whether to raise."""

    stage_id = "solo"
    output_model = _EmptyOutput

    def __init__(self, call_costs: list[float], fail_at: int | None = None):
        super().__init__()
        self.call_costs = call_costs
        self.fail_at = fail_at

    async def run(
        self, provider, *, seed, ancestors, model=None, verification_errors=None, on_batch_cost=None, session=None
    ):
        for i, cost in enumerate(self.call_costs):
            if on_batch_cost is not None:
                on_batch_cost(cost)
            if self.fail_at is not None and i == self.fail_at:
                raise RuntimeError(f"call {i} failed")
        return AgentResult(
            stage_id=self.stage_id,
            output={},
            provider_result=ProviderResult(
                raw_text="{}", data={}, model="test", duration_ms=1, cost_usd=sum(self.call_costs)
            ),
        )


def _solo_registry(agent: StageAgent) -> StageRegistry:
    return StageRegistry([StageDefinition("solo", lambda: agent, ())])


class _NonRetryableError(RuntimeError):
    retryable = False


class _AlwaysFailsAgent(StageAgent):
    stage_id = "solo"
    output_model = _EmptyOutput

    def __init__(self, exc: Exception):
        super().__init__()
        self.exc = exc
        self.call_count = 0

    async def run(
        self, provider, *, seed, ancestors, model=None, verification_errors=None, on_batch_cost=None, session=None
    ):
        self.call_count += 1
        raise self.exc


@pytest.mark.asyncio
async def test_full_pipeline_runs_to_done_with_valid_outputs(session, scripted_provider, otel_spans):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    orchestrator = Orchestrator(session, scripted_provider)

    states = await orchestrator.run_all()

    for stage_id, state in states.items():
        assert state.status == StageStatus.DONE, f"{stage_id} did not complete: {state.error}"

    # codegen's single file should have been written to disk by FilesystemTool
    assert (session.generated_dir / "package.json").exists()

    # run_all() must always release the lock, success or failure
    assert not session.run_lock_file.exists()

    # Observability: every DONE stage records duration/cost, and the
    # structured event log has a "stage_done" line for each of them.
    for stage_id, state in states.items():
        assert state.duration_ms is not None and state.duration_ms >= 0
        assert state.cost_usd == pytest.approx(0.02)

    events_files = list(session.logs_dir.glob("logs-*.jsonl"))
    assert len(events_files) == 1
    events_text = events_files[0].read_text(encoding="utf-8")
    assert events_text.count('"event": "stage_done"') == len(states)

    # Tracing: one stage_attempt_llm_call per successful attempt, carrying
    # the actual prompt/response text so a run can be debugged from the
    # JSONL alone.
    events = read_events(session)
    llm_calls = [e for e in events if e["event"] == "stage_attempt_llm_call"]
    assert len(llm_calls) == len(states)
    for call in llm_calls:
        assert call["user_prompt"]
        assert call["system_prompt"]
        assert call["response"]
        assert call["cost_usd"] == pytest.approx(0.02)
        # Logged from inside the active "frontforge.stage" span — the
        # automatic log<->trace correlation OTel is meant to provide.
        assert "trace_id" in call
        assert "span_id" in call

    # All llm_call lines for the same stage share one trace_id (the whole
    # pipeline_run is one trace) but each stage gets its own span_id.
    trace_ids = {call["trace_id"] for call in llm_calls}
    assert len(trace_ids) == 1
    span_ids = {call["span_id"] for call in llm_calls}
    assert len(span_ids) == len(states)


@pytest.mark.asyncio
async def test_llm_call_event_references_a_full_untruncated_payload_file(session, scripted_provider):
    """logs-*.jsonl only keeps a truncated preview of system_prompt/
    user_prompt/response — the orchestrator must also persist the full copy
    to its own file and record where, so nothing is unrecoverable once the
    process exits."""
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    orchestrator = Orchestrator(session, scripted_provider)

    await orchestrator.run_all(only="clarification")

    llm_call_events = [e for e in read_events(session) if e["event"] == "stage_attempt_llm_call"]
    assert len(llm_call_events) == 1
    event = llm_call_events[0]

    assert event["payload_path"] == f"payloads/{orchestrator.run_id}/clarification-attempt1.json"
    payload_file = session.logs_dir / event["payload_path"]
    assert payload_file.exists()

    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    # the full copy matches what actually went into the truncated preview,
    # just without the "...[truncated, N chars total]" cutoff
    assert payload["user_prompt"] == event["user_prompt"]
    assert payload["response"] == event["response"]


@pytest.mark.asyncio
async def test_run_all_refuses_to_start_while_another_process_holds_the_lock(
    session, scripted_provider, monkeypatch
):
    from frontforge.core.lock import RunLockError

    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    session.run_lock_file.write_text("99999", encoding="utf-8")
    monkeypatch.setattr(lock_module, "_pid_alive", lambda pid: True)

    orchestrator = Orchestrator(session, scripted_provider)
    with pytest.raises(RunLockError):
        await orchestrator.run_all()

    # the lock file must be left untouched — it belongs to the other process
    assert session.run_lock_file.read_text(encoding="utf-8").strip() == "99999"


@pytest.mark.asyncio
async def test_stage_retries_then_fails_on_persistent_bad_output(session):
    from fixtures_data import ScriptedProvider

    provider = ScriptedProvider({"ProjectBrief": {"project_name": "Demo"}})  # missing required fields
    write_json(session.seed_file, {"raw_requirement": "Build something."})
    orchestrator = Orchestrator(session, provider, max_retries=1)

    states = await orchestrator.run_all(only="clarification")

    assert states["clarification"].status == StageStatus.FAILED
    # max_retries=1 means the agent gets 2 attempts (1 initial + 1 retry)
    # before the stage is finally marked failed for this one run_all() call.
    assert len(provider.calls) == 2
    assert states["clarification"].attempts == 1

    # A FAILED stage still records duration/cost — cost isn't only tracked
    # on the happy path, since the failing calls still cost real money.
    assert states["clarification"].duration_ms is not None
    assert states["clarification"].cost_usd == pytest.approx(0.02 * 2)

    # Verification failures preserve VerificationIssue's structured fields
    # (verifier/message/severity), not just the flattened prompt-feedback
    # strings — needed to answer "which verifier fails most" later.
    events = read_events(session)
    failures = [e for e in events if e["event"] == "stage_attempt_verification_failed"]
    assert len(failures) == 2
    for failure in failures:
        assert failure["issues"]
        for issue in failure["issues"]:
            assert set(issue) == {"verifier", "message", "severity"}


@pytest.mark.asyncio
async def test_downstream_stages_never_run_after_upstream_failure(session):
    from fixtures_data import ScriptedProvider

    provider = ScriptedProvider({"ProjectBrief": {"project_name": "Demo"}})
    write_json(session.seed_file, {"raw_requirement": "Build something."})
    orchestrator = Orchestrator(session, provider, max_retries=0)

    states = await orchestrator.run_all()

    assert states["clarification"].status == StageStatus.FAILED
    assert "requirement" not in states


@pytest.mark.asyncio
async def test_failed_stage_is_retried_on_a_fresh_run_all_call(session):
    """Simulates hitting a usage limit mid-pipeline: a stage FAILs and the
    process exits. A later `frontforge run` (a brand new Orchestrator/run_all
    call, e.g. after the quota resets) must still retry that stage instead of
    treating a persisted FAILED status as permanently stuck."""
    from fixtures_data import ScriptedProvider

    write_json(session.seed_file, {"raw_requirement": "Build something."})

    failing_provider = ScriptedProvider({"ProjectBrief": {"project_name": "Demo"}})  # invalid
    first_run = Orchestrator(session, failing_provider, max_retries=0)
    states = await first_run.run_all(only="clarification")
    assert states["clarification"].status == StageStatus.FAILED

    # Fresh process/orchestrator instance, provider now works again.
    second_run = Orchestrator(session, ScriptedProvider(), max_retries=0)
    states = await second_run.run_all(only="clarification")
    assert states["clarification"].status == StageStatus.DONE


@pytest.mark.asyncio
async def test_mark_dirty_forces_rerun(session, scripted_provider):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    orchestrator = Orchestrator(session, scripted_provider)
    await orchestrator.run_all()

    affected = orchestrator.mark_dirty("requirement")
    assert "requirement" in affected
    assert "business_analysis" in affected  # cascades downstream

    events = read_events(session)
    dirty_events = [e for e in events if e["event"] == "mark_dirty"]
    assert len(dirty_events) == 1
    assert dirty_events[0]["stage_id"] == "requirement"
    assert set(dirty_events[0]["affected"]) == set(affected)
    assert dirty_events[0]["affected_count"] == len(affected)
    assert dirty_events[0]["reason"] == "manual"  # default reason for a direct mark_dirty() call

    states = await orchestrator.run_all()
    for stage_id in affected:
        assert states[stage_id].status == StageStatus.DONE
        assert states[stage_id].attempts == 2  # ran once, marked dirty, ran again


@pytest.mark.asyncio
async def test_pipeline_stops_once_total_cost_cap_is_reached(session, scripted_provider):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    # scripted_provider charges $0.02/call; cap after ~2 calls' worth so the
    # pipeline must stop well before all 12 stages complete.
    orchestrator = Orchestrator(session, scripted_provider, max_total_cost_usd=0.05)

    states = await orchestrator.run_all()

    assert len(states) < len(orchestrator.registry.all_ids())
    assert all(s.status == StageStatus.DONE for s in states.values())  # stopped cleanly, nothing left broken
    assert orchestrator._total_cost_usd >= 0.05
    assert not session.run_lock_file.exists()  # lock still released on early stop


@pytest.mark.asyncio
async def test_no_cap_means_pipeline_runs_to_completion(session, scripted_provider):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    orchestrator = Orchestrator(session, scripted_provider)  # max_total_cost_usd=None (default)

    states = await orchestrator.run_all()

    assert len(states) == len(orchestrator.registry.all_ids())
    assert all(s.status == StageStatus.DONE for s in states.values())


@pytest.mark.asyncio
async def test_budget_cap_stops_a_multi_call_stage_between_its_own_calls(session, scripted_provider):
    """A batched agent (e.g. codegen) can make several internal provider
    calls inside one run(). The pipeline-wide cap must fire as soon as one of
    those calls pushes cumulative spend over the cap, not only after the
    whole (possibly much more expensive) stage finishes."""
    agent = _MultiCallFakeAgent(call_costs=[0.05, 0.05, 0.05, 0.05])  # 4 calls, $0.05 each
    orchestrator = Orchestrator(
        session, scripted_provider, registry=_solo_registry(agent), max_total_cost_usd=0.12
    )

    states = await orchestrator.run_all()

    assert states["solo"].status == StageStatus.FAILED
    assert "pipeline cost cap" in states["solo"].error
    # cap ($0.12) is crossed after the 3rd call (0.05*3=0.15) — the 4th call
    # must never have been made, and the spend from all 3 completed calls
    # must be reflected, not just the cost known once run() returns.
    assert orchestrator._total_cost_usd == pytest.approx(0.15)

    events = read_events(session)
    assert any(e["event"] == "stage_budget_exceeded_mid_stage" for e in events)


@pytest.mark.asyncio
async def test_cost_of_completed_calls_survives_a_later_call_failing(session, scripted_provider):
    """If call N of a multi-call stage fails, the cost already spent on
    calls 1..N-1 (real money, already charged) must not vanish from cost
    tracking just because the stage as a whole ends up failing."""
    agent = _MultiCallFakeAgent(call_costs=[0.05, 0.05, 0.05], fail_at=2)  # 3rd call raises
    orchestrator = Orchestrator(
        session, scripted_provider, registry=_solo_registry(agent), max_retries=0
    )

    states = await orchestrator.run_all()

    assert states["solo"].status == StageStatus.FAILED
    assert states["solo"].cost_usd == pytest.approx(0.15)  # all 3 calls' cost retained
    assert orchestrator._total_cost_usd == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_non_retryable_agent_error_fails_the_stage_immediately(session, scripted_provider):
    """An agent/tool can mark an exception `.retryable = False` (e.g.
    FigmaTool on a missing token or a malformed URL) to say every retry
    would fail identically — the orchestrator must not burn through
    max_retries on it."""
    agent = _AlwaysFailsAgent(_NonRetryableError("FIGMA_ACCESS_TOKEN is not set"))
    orchestrator = Orchestrator(session, scripted_provider, registry=_solo_registry(agent), max_retries=5)

    states = await orchestrator.run_all()

    assert states["solo"].status == StageStatus.FAILED
    assert "FIGMA_ACCESS_TOKEN is not set" in states["solo"].error
    assert agent.call_count == 1  # not retried, despite max_retries=5

    events = read_events(session)
    assert any(e["event"] == "stage_failed_non_retryable" for e in events)


@pytest.mark.asyncio
async def test_retryable_agent_error_still_uses_all_attempts(session, scripted_provider):
    """A plain exception (no `.retryable` attribute, or True) keeps the
    existing retry behavior — only an explicit `retryable = False` opts out."""
    agent = _AlwaysFailsAgent(RuntimeError("could not reach Figma API: connection reset"))
    orchestrator = Orchestrator(session, scripted_provider, registry=_solo_registry(agent), max_retries=2)

    states = await orchestrator.run_all()

    assert states["solo"].status == StageStatus.FAILED
    assert agent.call_count == 3  # 1 initial + 2 retries
