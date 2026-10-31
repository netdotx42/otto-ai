from __future__ import annotations

from fastapi import FastAPI

from app.core.config import get_settings


def init_telemetry(app: FastAPI) -> None:
    s = get_settings()
    if s.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=s.sentry_dsn, environment=s.env, traces_sample_rate=0.1, send_default_pii=False)
    if s.otel_exporter_otlp_endpoint:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "agent98-api", "deployment.environment": s.env}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{s.otel_exporter_otlp_endpoint}/v1/traces")))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
