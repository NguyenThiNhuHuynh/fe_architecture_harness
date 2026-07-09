import pytest

import frontforge.core.lock as lock_module
from frontforge.core.orchestrator import Orchestrator
from frontforge.shared.types import StageStatus
from frontforge.shared.utils import write_json


@pytest.mark.asyncio
async def test_full_pipeline_runs_to_done_with_valid_outputs(session, scripted_provider):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    orchestrator = Orchestrator(session, scripted_provider)

    states = await orchestrator.run_all()

    for stage_id, state in states.items():
        assert state.status == StageStatus.DONE, f"{stage_id} did not complete: {state.error}"

    # codegen's single file should have been written to disk by FilesystemTool
    assert (session.generated_dir / "package.json").exists()

    # run_all() must always release the lock, success or failure
    assert not session.run_lock_file.exists()


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

    states = await orchestrator.run_all()
    for stage_id in affected:
        assert states[stage_id].status == StageStatus.DONE
        assert states[stage_id].attempts == 2  # ran once, marked dirty, ran again
