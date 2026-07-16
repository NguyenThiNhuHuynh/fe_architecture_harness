from __future__ import annotations

import json

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from frontforge.core.session import RunSession

from fixtures_data import ScriptedProvider


@pytest.fixture
def session(tmp_path) -> RunSession:
    s = RunSession.at(tmp_path / "demo-project")
    s.scaffold()
    return s


@pytest.fixture
def scripted_provider() -> ScriptedProvider:
    return ScriptedProvider()


# Module-level: `trace.set_tracer_provider()` only ever takes effect once per
# process (a real OTel API constraint), so the whole test session shares one
# TracerProvider/exporter — tests get isolation via `.clear()`, not by
# reconfiguring the provider per test.
_otel_test_exporter = InMemorySpanExporter()


def _ensure_test_tracer_provider() -> None:
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return  # already wired up by an earlier test in this session
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_otel_test_exporter))
    trace.set_tracer_provider(provider)


@pytest.fixture
def otel_spans():
    """Finished spans recorded during this test only — an in-memory exporter
    standing in for the real console/OTLP one so span creation/attributes can
    be asserted without any collector infrastructure."""
    _ensure_test_tracer_provider()
    _otel_test_exporter.clear()
    yield _otel_test_exporter
    _otel_test_exporter.clear()


# Same one-shot-provider constraint as tracing above. Counters/histograms are
# cumulative for the life of the process (module-level singletons in
# orchestrator.py), so unlike spans there's no per-test .clear() — tests must
# compare before/after deltas for a specific attribute set instead of
# asserting absolute totals.
_otel_metrics_reader = InMemoryMetricReader()


def _ensure_test_meter_provider() -> None:
    if isinstance(metrics.get_meter_provider(), MeterProvider):
        return
    provider = MeterProvider(metric_readers=[_otel_metrics_reader])
    metrics.set_meter_provider(provider)


@pytest.fixture
def otel_metrics_reader():
    _ensure_test_meter_provider()
    yield _otel_metrics_reader


def metric_data_points(metrics_data, metric_name: str):
    """Flattened (attributes, data_point) pairs for one metric name, across
    whatever resource/scope it was recorded under — test helper since real
    MetricsData nests 3 levels deep before you reach a usable value.
    `get_metrics_data()` returns None outright when nothing has been
    recorded yet (e.g. the very first call in the whole test session)."""
    if metrics_data is None:
        return []
    points = []
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name != metric_name:
                    continue
                for point in metric.data.data_points:
                    points.append((dict(point.attributes), point))
    return points


def read_events(session: RunSession) -> list[dict]:
    """All JSONL event records logged for this session's run(s), in order —
    a test helper since every observability assertion needs to parse the
    same logs-*.jsonl file(s)."""
    events: list[dict] = []
    for events_file in sorted(session.logs_dir.glob("logs-*.jsonl")):
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events
