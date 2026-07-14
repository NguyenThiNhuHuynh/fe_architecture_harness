import pytest
from opentelemetry.trace import StatusCode

from frontforge.core.human_review import HumanReviewHook, StageDecision
from frontforge.core.orchestrator import Orchestrator
from frontforge.shared.utils import write_json


def _span_names(spans):
    return [s.name for s in spans]


@pytest.mark.asyncio
async def test_pipeline_run_and_stage_spans_are_recorded(session, scripted_provider, otel_spans):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    orchestrator = Orchestrator(session, scripted_provider)

    states = await orchestrator.run_all()

    spans = otel_spans.get_finished_spans()
    names = _span_names(spans)
    assert names.count("frontforge.pipeline_run") == 1
    assert names.count("frontforge.stage") == len(states)

    pipeline_span = next(s for s in spans if s.name == "frontforge.pipeline_run")
    assert pipeline_span.attributes["frontforge.dag.run_id"] == orchestrator.run_id
    assert pipeline_span.attributes["frontforge.dag.stages_failed"] == 0
    assert pipeline_span.status.status_code == StatusCode.OK

    stage_spans = [s for s in spans if s.name == "frontforge.stage"]
    for stage_span in stage_spans:
        assert stage_span.attributes["frontforge.stage.name"] in states
        assert stage_span.attributes["gen_ai.system"] == "anthropic"
        assert stage_span.attributes["gen_ai.request.model"]
        assert stage_span.status.status_code == StatusCode.OK
        assert stage_span.attributes["frontforge.stage.cost_usd"] == pytest.approx(0.02)
        events_by_name = {e.name: e for e in stage_span.events}
        assert "llm_call" in events_by_name
        llm_call = events_by_name["llm_call"]
        assert llm_call.attributes["gen_ai.system"] == "anthropic"
        assert llm_call.attributes["gen_ai.request.model"]
        assert llm_call.attributes["gen_ai.operation.name"] == "chat"

    # Every stage span is a child of the pipeline_run span.
    pipeline_span_id = pipeline_span.context.span_id
    for stage_span in stage_spans:
        assert stage_span.parent.span_id == pipeline_span_id


@pytest.mark.asyncio
async def test_failed_stage_span_has_error_status(session, otel_spans):
    from fixtures_data import ScriptedProvider

    provider = ScriptedProvider({"ProjectBrief": {"project_name": "Demo"}})  # missing required fields
    write_json(session.seed_file, {"raw_requirement": "Build something."})
    orchestrator = Orchestrator(session, provider, max_retries=0)

    await orchestrator.run_all(only="clarification")

    spans = otel_spans.get_finished_spans()
    stage_span = next(s for s in spans if s.name == "frontforge.stage")
    assert stage_span.status.status_code == StatusCode.ERROR
    event_names = {e.name for e in stage_span.events}
    assert "verification_failed" in event_names

    pipeline_span = next(s for s in spans if s.name == "frontforge.pipeline_run")
    assert pipeline_span.status.status_code == StatusCode.ERROR
    assert pipeline_span.attributes["frontforge.dag.stages_failed"] == 1


class RecordingHook(HumanReviewHook):
    def __init__(self, stage_answers=None, quality_answer=False):
        self.stage_answers = dict(stage_answers or {})
        self.quality_answer = quality_answer

    async def review_stage(self, stage_id, output):
        return self.stage_answers.pop(stage_id, StageDecision(proceed=True))

    async def review_quality(self, issues):
        return self.quality_answer


@pytest.mark.asyncio
async def test_hitl_review_span_and_mark_dirty_event_are_recorded(
    session, scripted_provider, otel_spans
):
    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    hook = RecordingHook(
        stage_answers={
            "clarification": StageDecision(proceed=False, feedback="Add an Admin role too."),
        }
    )
    orchestrator = Orchestrator(session, scripted_provider, human_review=hook)

    await orchestrator.run_all()

    spans = otel_spans.get_finished_spans()
    review_spans = [s for s in spans if s.name == "frontforge.hitl_review"]
    assert len(review_spans) >= 1

    clarification_review = next(
        s for s in review_spans if s.attributes["frontforge.stage.name"] == "clarification"
    )
    assert clarification_review.attributes["frontforge.hitl.has_feedback"] is True
    # Every checkpoint gets a unique id, joinable against the same field
    # EventLogger writes on hitl_decision/hitl_autofix_decision JSONL lines.
    assert clarification_review.attributes["frontforge.hitl.checkpoint_id"] == f"{orchestrator.run_id}:1"

    # mark_dirty() is called from inside the active hitl_review span, so its
    # event lands there rather than on the pipeline_run root.
    dirty_events = [e for e in clarification_review.events if e.name == "mark_dirty"]
    assert len(dirty_events) == 1
    assert dirty_events[0].attributes["frontforge.mark_dirty.stage_id"] == "clarification"
    assert dirty_events[0].attributes["frontforge.mark_dirty.reason"] == "hitl_feedback"
