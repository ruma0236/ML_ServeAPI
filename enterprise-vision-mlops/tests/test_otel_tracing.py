from __future__ import annotations

import json

from evm.core import http
from evm.observability import otel
from evm.observability.trace_context import (
    W3CTraceContext,
    bind_trace_context,
    current_trace_context,
    reset_trace_context,
)


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps({"status": "ok"}).encode("utf-8")


def test_disabled_tracing_preserves_w3c_parent_and_resets_context(monkeypatch) -> None:
    monkeypatch.setattr(otel, "_ENABLED", False)
    parent = W3CTraceContext.new_root()

    with otel.trace_span("unit.child", parent=parent) as active:
        assert active.context.trace_id == parent.trace_id
        assert active.context.parent_span_id == parent.span_id
        assert current_trace_context() == active.context

    assert current_trace_context() is None


def test_disabled_configuration_does_not_latch_process_state(monkeypatch) -> None:
    monkeypatch.delenv("EVM_OTEL_ENABLED", raising=False)
    monkeypatch.setattr(otel, "_CONFIGURED", False)
    monkeypatch.setattr(otel, "_ENABLED", False)

    assert otel.configure_tracing("unit-service") is False
    assert otel._CONFIGURED is False


def test_outbound_http_injects_child_trace_context(monkeypatch) -> None:
    monkeypatch.setattr(otel, "_ENABLED", False)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    parent = W3CTraceContext.new_root()
    token = bind_trace_context(parent)
    try:
        status, payload = http.request_json("GET", "http://service.local/health")
    finally:
        reset_trace_context(token)

    outbound = W3CTraceContext.parse(captured["request"].get_header("Traceparent"))
    assert status == 200
    assert payload == {"status": "ok"}
    assert captured["timeout"] == 5
    assert outbound.trace_id == parent.trace_id
    assert outbound.span_id != parent.span_id
