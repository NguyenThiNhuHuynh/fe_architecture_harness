import pytest

from frontforge.core.orchestrator import Orchestrator
from frontforge.shared.utils import write_json

from conftest import metric_data_points


def _sum_value(metrics_data, metric_name, attributes):
    for attrs, point in metric_data_points(metrics_data, metric_name):
        if attrs == attributes:
            return point.value
    return None


def _histogram_count(metrics_data, metric_name, attributes):
    for attrs, point in metric_data_points(metrics_data, metric_name):
        if attrs == attributes:
            return point.count
    return None


@pytest.mark.asyncio
async def test_stage_done_records_count_and_duration_histogram(
    session, scripted_provider, otel_metrics_reader
):
    before = otel_metrics_reader.get_metrics_data()
    attrs = {"frontforge.stage.name": "clarification", "frontforge.stage.status": "done"}
    before_count = _sum_value(before, "frontforge.stage.count", attrs) or 0
    before_hist_count = _histogram_count(before, "frontforge.stage.duration_ms", attrs) or 0

    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    orchestrator = Orchestrator(session, scripted_provider)
    await orchestrator.run_all(only="clarification")

    after = otel_metrics_reader.get_metrics_data()
    after_count = _sum_value(after, "frontforge.stage.count", attrs)
    after_hist_count = _histogram_count(after, "frontforge.stage.duration_ms", attrs)

    assert after_count == before_count + 1
    assert after_hist_count == before_hist_count + 1


@pytest.mark.asyncio
async def test_stage_failure_records_failed_status_count(session, otel_metrics_reader):
    from fixtures_data import ScriptedProvider

    attrs = {"frontforge.stage.name": "clarification", "frontforge.stage.status": "failed"}
    before = otel_metrics_reader.get_metrics_data()
    before_count = _sum_value(before, "frontforge.stage.count", attrs) or 0

    provider = ScriptedProvider({"ProjectBrief": {"project_name": "Demo"}})  # missing required fields
    write_json(session.seed_file, {"raw_requirement": "Build something."})
    orchestrator = Orchestrator(session, provider, max_retries=0)
    await orchestrator.run_all(only="clarification")

    after = otel_metrics_reader.get_metrics_data()
    after_count = _sum_value(after, "frontforge.stage.count", attrs)

    assert after_count == before_count + 1


@pytest.mark.asyncio
async def test_cost_counter_accumulates_per_attempt(session, scripted_provider, otel_metrics_reader):
    attrs = {"frontforge.stage.name": "clarification"}
    before = otel_metrics_reader.get_metrics_data()
    before_cost = _sum_value(before, "frontforge.cost.total_usd", attrs) or 0.0

    write_json(session.seed_file, {"raw_requirement": "Build a recruitment site."})
    orchestrator = Orchestrator(session, scripted_provider)
    await orchestrator.run_all(only="clarification")

    after = otel_metrics_reader.get_metrics_data()
    after_cost = _sum_value(after, "frontforge.cost.total_usd", attrs)

    assert after_cost == pytest.approx(before_cost + 0.02)
