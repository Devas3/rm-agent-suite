"""Shared Phoenix/OpenTelemetry setup and Kafka context propagation."""

from __future__ import annotations

from typing import Any

from opentelemetry import context, propagate, trace
from rm_agent.settings import PhoenixSettings


def configure_tracing(service_name: str) -> trace.Tracer:
    settings = PhoenixSettings()
    if not settings.phoenix_enabled:
        return trace.get_tracer(service_name)

    from phoenix.otel import register

    headers = {"api_key": settings.phoenix_api_key} if settings.phoenix_api_key else {}
    register(
        project_name=settings.phoenix_project_name,
        endpoint=settings.phoenix_endpoint,
        api_key=settings.phoenix_api_key or None,
        headers=headers or None,
    )
    return trace.get_tracer(service_name)


def inject_trace_headers() -> list[tuple[str, bytes]]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return [(key, value.encode("utf-8")) for key, value in carrier.items()]


def extract_trace_context(headers: list[tuple[str, bytes]] | None) -> context.Context:
    carrier: dict[str, str] = {}
    for key, value in headers or []:
        if value is not None:
            carrier[key] = value.decode("utf-8")
    return propagate.extract(carrier)


def set_span_attributes(span: trace.Span, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if value is not None and isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)