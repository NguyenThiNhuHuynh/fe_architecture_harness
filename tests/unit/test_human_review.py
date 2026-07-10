import pytest

from frontforge.core.human_review import HumanReviewHook, StageDecision
from frontforge.core.orchestrator import Orchestrator
from frontforge.shared.types import StageStatus
from frontforge.shared.utils import write_json


class RecordingHook(HumanReviewHook):
    """Test double: scripted answers per stage, consumed once (subsequent
    asks for the same stage default to "proceed") — a persistent
    quality_answer, however, is NOT consumed, to exercise the safety cap."""

    def __init__(self, stage_answers: dict | None = None, quality_answer: bool = False):
        self.stage_answers = dict(stage_answers or {})
        self.quality_answer = quality_answer
        self.stage_calls: list[str] = []
        self.quality_calls: list[list[dict]] = []

    async def review_stage(self, stage_id, output):
        self.stage_calls.append(stage_id)
        return self.stage_answers.pop(stage_id, StageDecision(proceed=True))

    async def review_quality(self, issues):
        self.quality_calls.append(issues)
        return self.quality_answer


@pytest.mark.asyncio
async def test_no_hook_means_fully_unattended(session, scripted_provider):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    orchestrator = Orchestrator(session, scripted_provider)  # human_review=None (default)

    states = await orchestrator.run_all()

    for stage_id, state in states.items():
        assert state.status == StageStatus.DONE, f"{stage_id}: {state.error}"


@pytest.mark.asyncio
async def test_hook_is_asked_for_every_stage_except_quality_review(session, scripted_provider):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    hook = RecordingHook()
    orchestrator = Orchestrator(session, scripted_provider, human_review=hook)

    states = await orchestrator.run_all()

    assert all(s.status == StageStatus.DONE for s in states.values())
    assert "quality_review" not in hook.stage_calls
    assert set(hook.stage_calls) == set(states.keys()) - {"quality_review"}
    assert len(hook.quality_calls) == 1  # quality_review routed to review_quality instead


@pytest.mark.asyncio
async def test_feedback_reruns_the_stage_with_feedback_in_the_prompt(session, scripted_provider):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    hook = RecordingHook(
        stage_answers={
            "clarification": StageDecision(proceed=False, feedback="Add an Admin role too."),
        }
    )
    orchestrator = Orchestrator(session, scripted_provider, human_review=hook)

    states = await orchestrator.run_all()

    assert states["clarification"].status == StageStatus.DONE
    assert states["clarification"].attempts == 2  # ran once, feedback -> ran again (then proceed)

    clarification_calls = [
        c for c in scripted_provider.calls if c["json_schema"]["title"] == "ProjectBrief"
    ]
    assert len(clarification_calls) == 2
    assert "Add an Admin role too." in clarification_calls[1]["user_prompt"]


@pytest.mark.asyncio
async def test_stop_for_manual_edit_ends_the_run_early(session, scripted_provider):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    hook = RecordingHook(
        stage_answers={"clarification": StageDecision(proceed=False, stop_for_manual_edit=True)}
    )
    orchestrator = Orchestrator(session, scripted_provider, human_review=hook)

    states = await orchestrator.run_all()

    assert states["clarification"].status == StageStatus.DONE  # not touched further, just paused
    assert "requirement" not in states  # pipeline stopped before reaching it
    assert not session.run_lock_file.exists()  # lock still released cleanly on early exit


@pytest.mark.asyncio
async def test_quality_review_autofix_is_capped_to_avoid_an_infinite_loop(session, scripted_provider):
    """A hook that always answers "yes, auto-fix" must not loop forever —
    exactly the real-world case where quality_review keeps flagging
    something the model can never actually resolve."""
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    hook = RecordingHook(quality_answer=True)
    orchestrator = Orchestrator(session, scripted_provider, human_review=hook)

    states = await orchestrator.run_all()  # must terminate, not hang

    assert orchestrator._autofix_rounds["codegen"] == Orchestrator.MAX_HUMAN_REVISIONS
    assert states["codegen"].status == StageStatus.DONE
    assert states["codegen"].attempts == Orchestrator.MAX_HUMAN_REVISIONS + 1  # 1 initial + N autofix reruns
    # the cap is checked *before* asking, so the (N+1)-th completion is never
    # even asked about — quality_calls stops growing once the cap is hit.
    assert len(hook.quality_calls) == Orchestrator.MAX_HUMAN_REVISIONS
