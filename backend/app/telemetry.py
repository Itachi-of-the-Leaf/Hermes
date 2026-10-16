"""Observability: OpenTelemetry traces + metrics, and JSON logs that carry the trace ID.

Locally, point OTEL_EXPORTER_OTLP_ENDPOINT at the grafana/otel-lgtm container (docker compose).
On AWS, point it at the ADOT collector sidecar, which ships traces to X-Ray and metrics to
CloudWatch. With no endpoint set, every call below is a cheap no-op.
"""
import json
import logging
import os
import sys
import time
from contextlib import contextmanager

from opentelemetry import metrics, trace

from .config import settings

tracer = trace.get_tracer("coordinator")
meter = metrics.get_meter("coordinator")

# ---- business metrics (the numbers judges and on-call both care about)
replies_classified = meter.create_counter(
    "coordinator.replies.classified", description="Replies classified, by engine, intent and gate action")
decision_latency = meter.create_histogram(
    "coordinator.decision.latency", unit="ms", description="Time from reply received to decision")
messages_sent = meter.create_counter(
    "coordinator.messages.sent", description="Outbound messages, by channel and result")
model_cost = meter.create_counter(
    "coordinator.model.cost_usd", unit="USD", description="Reported model cost, by provider")
model_errors = meter.create_counter("coordinator.model.errors", description="Jev / LLM call failures")
meeting_utterances = meter.create_counter(
    "coordinator.meeting.utterances", description="Meeting utterances triaged, by flagged=true/false")
reviews_created = meter.create_counter("coordinator.reviews.created", description="Updates sent to the lead")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        span = trace.get_current_span().get_span_context()
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
            "service": settings.service_name, "role": settings.role,
        }
        if span.is_valid:
            # X-Ray-compatible trace id lets CloudWatch Logs link straight to the trace
            out["trace_id"] = format(span.trace_id, "032x")
            out["span_id"] = format(span.span_id, "016x")
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "telegram.ext", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def setup_tracing(app=None, engine=None) -> None:
    if not settings.otel_endpoint:
        return
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": settings.service_name, "service.namespace": "coordinator",
                                "deployment.environment": settings.env, "coordinator.role": settings.role})
    id_gen = None
    if os.getenv("OTEL_AWS_XRAY", "").lower() == "true":    # X-Ray-compatible trace IDs on AWS
        from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator
        id_gen = AwsXRayIdGenerator()
    tp = TracerProvider(resource=resource, id_generator=id_gen) if id_gen else TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(MeterProvider(
        resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(), 30_000)]))

    HTTPXClientInstrumentor().instrument()            # Jev, LLM, Recall, Slack/Teams HTTP calls
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, excluded_urls="/api/health,/api/stream")
    if engine is not None:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        SQLAlchemyInstrumentor().instrument(engine=engine)
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
    except Exception:
        pass
    logging.getLogger("telemetry").info("OpenTelemetry exporting to %s", settings.otel_endpoint)


@contextmanager
def span(name: str, **attrs):
    with tracer.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v)
        yield sp
