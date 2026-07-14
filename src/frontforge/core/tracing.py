"""OpenTelemetry wiring — traces and metrics, alongside (not instead of)
core.logger.EventLogger: EventLogger's JSONL remains the source `frontforge
stats` reads for cross-run aggregation; these are for any tool that speaks
the OTel wire format (Jaeger, Tempo, Prometheus, a local collector, or just
this run's own console/log output while no collector exists yet).

Exporting to console (this process's own log file, not stdout, so it doesn't
interleave with the CLI's Rich tables) is the only exporter for now — no
collector infrastructure exists, and OTel's `set_tracer_provider`/
`set_meter_provider` can each only be called once per process, so there's
nothing to select between at runtime.
"""

from __future__ import annotations

from pathlib import Path

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

_tracing_configured = False
_metrics_configured = False


def _resource(run_id: str) -> Resource:
    return Resource.create({"service.name": "frontforge", "service.instance.id": run_id})


def configure_tracing(logs_dir: Path, run_id: str) -> None:
    """Idempotent — only the first call in a process wins (matches
    `trace.set_tracer_provider`'s own one-shot semantics), so it's safe to
    call this unconditionally at CLI startup."""
    global _tracing_configured
    if _tracing_configured:
        return
    _tracing_configured = True

    provider = TracerProvider(resource=_resource(run_id))

    logs_dir.mkdir(parents=True, exist_ok=True)
    out = (logs_dir / f"otel-{run_id}.log").open("a", encoding="utf-8")
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=out)))

    trace.set_tracer_provider(provider)


def get_tracer(name: str = "frontforge") -> trace.Tracer:
    """Deliberately not cached at import time in callers — `trace.get_tracer`
    returns a proxy that binds to whichever TracerProvider is current, so
    modules imported before `configure_tracing()` runs still trace correctly
    once it does."""
    return trace.get_tracer(name)


def configure_metrics(logs_dir: Path, run_id: str, export_interval_millis: int = 5000) -> None:
    """Same idempotent, console-only shape as configure_tracing(). Uses a
    short export interval (default 5s, vs. the SDK's 60s default) because a
    `frontforge run` is a short-lived process — the default interval risks
    the process exiting before a single batch is ever exported. shutdown()
    (called at CLI exit) does one final flush regardless."""
    global _metrics_configured
    if _metrics_configured:
        return
    _metrics_configured = True

    logs_dir.mkdir(parents=True, exist_ok=True)
    out = (logs_dir / f"otel-metrics-{run_id}.log").open("a", encoding="utf-8")
    reader = PeriodicExportingMetricReader(
        ConsoleMetricExporter(out=out), export_interval_millis=export_interval_millis
    )
    provider = MeterProvider(resource=_resource(run_id), metric_readers=[reader])

    metrics.set_meter_provider(provider)


def get_meter(name: str = "frontforge") -> metrics.Meter:
    """Same lazy-binding rationale as get_tracer() above."""
    return metrics.get_meter(name)


def shutdown_metrics() -> None:
    """Flushes any metrics accumulated since the last periodic export —
    without this, a fast pipeline run could exit before
    PeriodicExportingMetricReader's interval ever fires, silently dropping
    every metric. Safe to call even if configure_metrics() was never
    invoked (e.g. in tests): shutdown() on the default no-op provider is a
    no-op itself."""
    provider = metrics.get_meter_provider()
    shutdown = getattr(provider, "shutdown", None)
    if shutdown is not None:
        shutdown()
