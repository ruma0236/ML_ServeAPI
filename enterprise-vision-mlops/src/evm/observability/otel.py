from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Literal

from evm.observability.trace_context import (
    W3CTraceContext,
    bind_trace_context,
    current_trace_context,
    reset_trace_context,
)


SpanKindName = Literal["internal", "server", "client", "producer", "consumer"]
_CONFIG_LOCK = threading.Lock()
_CONFIGURED = False
_ENABLED = False
_PROVIDER: Any = None


def runtime_service_version(default: str = "0.1.0") -> str:
    return (
        os.getenv("EVM_IMAGE_SOURCE_REVISION")
        or os.getenv("EVM_GIT_COMMIT")
        or os.getenv("GIT_COMMIT")
        or os.getenv("GITHUB_SHA")
        or default
    )


def _enabled_by_environment() -> bool:
    return os.getenv("EVM_OTEL_ENABLED", "false").lower() in {"1", "true", "yes"}


def configure_tracing(service_name: str, *, service_version: str | None = None) -> bool:
    global _CONFIGURED, _ENABLED, _PROVIDER
    with _CONFIG_LOCK:
        if _CONFIGURED:
            return _ENABLED
        if not _enabled_by_environment():
            return False
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
                SimpleSpanProcessor,
            )
        except ImportError:
            if os.getenv("EVM_OTEL_REQUIRED", "false").lower() in {"1", "true", "yes"}:
                raise
            return False

        attributes = {
            "service.name": service_name,
            "service.namespace": os.getenv("OTEL_SERVICE_NAMESPACE", "enterprise-mlops"),
        }
        instance_id = os.getenv("OTEL_SERVICE_INSTANCE_ID")
        if instance_id:
            attributes["service.instance.id"] = instance_id
        if service_version:
            attributes["service.version"] = service_version
        provider = TracerProvider(resource=Resource.create(attributes))
        exporter = OTLPSpanExporter(
            endpoint=os.getenv(
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                "http://127.0.0.1:4318/v1/traces",
            )
        )
        processor_mode = os.getenv("EVM_OTEL_PROCESSOR", "batch").lower()
        processor = (
            SimpleSpanProcessor(exporter)
            if processor_mode == "simple"
            else BatchSpanProcessor(exporter)
        )
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        _PROVIDER = provider
        _CONFIGURED = True
        _ENABLED = True
        return True


def shutdown_tracing() -> None:
    provider = _PROVIDER
    if provider is not None:
        provider.force_flush()
        provider.shutdown()


def _otel_parent_context(parent: W3CTraceContext):
    from opentelemetry import trace
    from opentelemetry.trace import (
        NonRecordingSpan,
        SpanContext,
        TraceFlags,
        TraceState,
        set_span_in_context,
    )

    trace_state = TraceState()
    if parent.tracestate:
        trace_state = TraceState.from_header([parent.tracestate])
    span_context = SpanContext(
        trace_id=int(parent.trace_id, 16),
        span_id=int(parent.span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(int(parent.trace_flags, 16)),
        trace_state=trace_state,
    )
    return set_span_in_context(NonRecordingSpan(span_context)), trace


def _otel_span_kind(name: SpanKindName):
    from opentelemetry.trace import SpanKind

    return {
        "internal": SpanKind.INTERNAL,
        "server": SpanKind.SERVER,
        "client": SpanKind.CLIENT,
        "producer": SpanKind.PRODUCER,
        "consumer": SpanKind.CONSUMER,
    }[name]


@dataclass
class ActiveTraceSpan:
    context: W3CTraceContext
    span: Any = None

    def set_attribute(self, key: str, value: str | int | float | bool) -> None:
        if self.span is not None:
            self.span.set_attribute(key, value)

    def record_exception(self, exc: BaseException) -> None:
        if self.span is not None:
            from opentelemetry.trace import Status, StatusCode

            self.span.record_exception(exc)
            self.span.set_status(Status(StatusCode.ERROR, str(exc)))


@contextmanager
def trace_span(
    name: str,
    *,
    parent: W3CTraceContext | None = None,
    kind: SpanKindName = "internal",
    attributes: dict[str, str | int | float | bool] | None = None,
) -> Iterator[ActiveTraceSpan]:
    effective_parent = parent or current_trace_context()
    fallback = (
        effective_parent.child() if effective_parent is not None else W3CTraceContext.new_root()
    )
    if not _ENABLED:
        token = bind_trace_context(fallback)
        try:
            yield ActiveTraceSpan(context=fallback)
        finally:
            reset_trace_context(token)
        return

    from opentelemetry import trace

    parent_context = None
    if effective_parent is not None:
        parent_context, _ = _otel_parent_context(effective_parent)
    tracer = trace.get_tracer("evm.observability")
    with tracer.start_as_current_span(
        name,
        context=parent_context,
        kind=_otel_span_kind(kind),
        attributes=attributes,
    ) as span:
        span_context = span.get_span_context()
        context = W3CTraceContext(
            trace_id=f"{span_context.trace_id:032x}",
            span_id=f"{span_context.span_id:016x}",
            trace_flags=f"{int(span_context.trace_flags):02x}",
            tracestate=(
                span_context.trace_state.to_header()
                if span_context.trace_state is not None
                else None
            ),
            parent_span_id=(effective_parent.span_id if effective_parent else None),
        )
        token = bind_trace_context(context)
        try:
            yield ActiveTraceSpan(context=context, span=span)
        except Exception as exc:
            ActiveTraceSpan(context=context, span=span).record_exception(exc)
            raise
        finally:
            reset_trace_context(token)
