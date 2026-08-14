from __future__ import annotations

import asyncio

import pytest
from fastapi import Request, Response

from apps.api.main import propagate_w3c_trace_context
from evm.core.traceability import TraceContext
from evm.observability.trace_context import (
    TraceContextError,
    W3CTraceContext,
    current_trace_context,
    normalize_legacy_trace_id,
)


def test_w3c_trace_context_creates_child_without_changing_trace_identity() -> None:
    root = W3CTraceContext.new_root()
    child = W3CTraceContext.parse(root.traceparent).child()

    assert child.trace_id == root.trace_id
    assert child.parent_span_id == root.span_id
    assert child.span_id != root.span_id
    assert child.traceparent.startswith(f"00-{root.trace_id}-")
    assert child.headers() == {"traceparent": child.traceparent}


@pytest.mark.parametrize(
    "traceparent",
    [
        "00-00000000000000000000000000000000-1111111111111111-01",
        "00-11111111111111111111111111111111-0000000000000000-01",
        "ff-11111111111111111111111111111111-1111111111111111-01",
        "not-a-traceparent",
    ],
)
def test_w3c_trace_context_rejects_invalid_or_unsafe_parent(traceparent: str) -> None:
    with pytest.raises(TraceContextError):
        W3CTraceContext.parse(traceparent)


def test_legacy_trace_identity_is_deterministic_and_w3c_compatible() -> None:
    first = normalize_legacy_trace_id("airflow-dag__scheduled-run")
    second = normalize_legacy_trace_id("airflow-dag__scheduled-run")

    assert first == second
    assert len(first) == 32
    assert int(first, 16) != 0


def test_pipeline_trace_is_a_child_of_airflow_supplied_parent(monkeypatch) -> None:
    parent = W3CTraceContext.new_root()
    monkeypatch.setenv("EVM_TRACEPARENT", parent.traceparent)
    monkeypatch.setenv("EVM_TRACE_ID", parent.trace_id)

    context = TraceContext.from_environment("data-validation", "run-1")

    assert context.trace_id == parent.trace_id
    assert context.w3c_trace_id == parent.trace_id
    assert context.parent_span_id == parent.span_id
    assert context.span_id != parent.span_id
    assert context.traceparent.startswith(f"00-{parent.trace_id}-")


def test_api_middleware_propagates_trace_and_isolates_request_context() -> None:
    parent = W3CTraceContext.new_root()
    observed: dict[str, W3CTraceContext | None] = {}
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": [(b"traceparent", parent.traceparent.encode("ascii"))],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    request = Request(scope)

    async def call_next(_request: Request) -> Response:
        observed["context"] = current_trace_context()
        return Response(status_code=204)

    response = asyncio.run(propagate_w3c_trace_context(request, call_next))
    context = observed["context"]

    assert context is not None
    assert context.trace_id == parent.trace_id
    assert context.parent_span_id == parent.span_id
    assert response.headers["traceparent"] == context.traceparent
    assert response.headers["x-evm-trace-id"] == parent.trace_id
    assert current_trace_context() is None
