"""Étape 8 -- configuration OTel partagée, réutilisée par la passerelle ET
l'agent (processus séparés, chacun initialise son propre `TracerProvider`).
Même schéma que le smoke test factice de l'Étape 1 (`scripts/otel_smoke_test.py`),
mais appliqué aux VRAIS composants cette fois -- c'était la dette restée
ouverte depuis l'Étape 1 (span MCP factice jamais rebranché sur la vraie
passerelle)."""

import base64
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

_tracer = None


def init_tracing(service_name: str):
    """Retourne un tracer configuré pour exporter vers Langfuse, ou `None` si
    les clés Langfuse ne sont pas dans l'environnement -- le traçage est un
    bonus d'observabilité, son absence ne doit jamais faire échouer la
    passerelle ou l'agent."""
    global _tracer
    if _tracer is not None:
        return _tracer

    public_key = os.environ.get("LANGFUSE_INIT_PROJECT_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_INIT_PROJECT_SECRET_KEY")
    if not public_key or not secret_key:
        return None

    langfuse_url = os.environ.get("LANGFUSE_URL", "http://localhost:3000")
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{langfuse_url}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"},
    )
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)
    return _tracer


def flush() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
