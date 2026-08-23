from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi import HTTPException, Request

from apps.api import control_panel_runtime
from evm.control_panel.api_rollout import (
    API_DRAIN_CONTROLLER,
    ApiDrainController,
    ApiDrainMiddleware,
    ApiDrainingError,
)


class FakeStore:
    def __init__(self) -> None:
        self.payloads: dict[str, dict[str, object]] = {}
        self.requests: dict[str, dict[str, object]] = {}

    def commit_idempotent_terminal_entity(
        self,
        *,
        idempotency_key: str,
        request_payload: dict[str, object],
        response_payload: dict[str, object],
        **_kwargs,
    ) -> tuple[dict[str, object], bool]:
        existing = self.payloads.get(idempotency_key)
        if existing is not None:
            assert self.requests[idempotency_key] == request_payload
            return dict(existing), True
        self.requests[idempotency_key] = dict(request_payload)
        self.payloads[idempotency_key] = dict(response_payload)
        return dict(response_payload), False

    def get_entity(self, _kind: str, entity_id: str):
        return self.payloads.get(entity_id)


def test_drain_controller_rejects_new_work_and_waits_for_accepted_work() -> None:
    controller = ApiDrainController()
    counted = controller.enter("/control-panel/v1/runtime/rollout-probes")
    assert counted is True
    controller.begin_drain("unit-test")
    with pytest.raises(ApiDrainingError, match="api_replica_draining"):
        controller.enter("/control-panel/v1/runtime/rollout-probes")

    thread = threading.Thread(target=lambda: (time.sleep(0.02), controller.exit(counted)))
    thread.start()
    snapshot, elapsed = controller.wait_until_drained(1.0)
    thread.join(timeout=1)

    assert snapshot.state == "drained"
    assert snapshot.in_flight == 0
    assert snapshot.started_at is not None
    assert elapsed >= 0.01


def test_drain_event_is_persisted_with_exact_runtime_identity(monkeypatch) -> None:
    store = FakeStore()
    monkeypatch.setattr(control_panel_runtime, "get_transactional_store", lambda: store)
    monkeypatch.setattr(control_panel_runtime, "API_DRAIN_CONTROLLER", ApiDrainController())
    monkeypatch.setenv("EVM_API_RELEASE_ID", "release-a")
    monkeypatch.setenv("EVM_POD_UID", "pod-a")
    monkeypatch.setenv("EVM_IMAGE_SOURCE_REVISION", "a" * 40)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/control-panel/v1/runtime/drain",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
            "query_string": b"",
        }
    )

    result = asyncio.run(
        control_panel_runtime.drain_runtime(
            control_panel_runtime.ApiDrainRequest(
                reason="unit-test-drain",
                timeout_seconds=1.0,
            ),
            request,
        )
    )

    assert result["schema_version"] == "evm.api_drain_event.v1"
    assert result["instance_id"] == "pod-a"
    assert result["release_id"] == "release-a"
    assert result["drain_completed"] is True
    assert store.payloads["pod-a:release-a"]["state"] == "drained"


def test_asgi_drain_middleware_holds_in_flight_until_final_body_send() -> None:
    API_DRAIN_CONTROLLER.reset_for_test()
    body_started = asyncio.Event()
    body_release = asyncio.Event()
    sent: list[dict[str, object]] = []

    async def app(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        body_started.set()
        await body_release.wait()
        await send({"type": "http.response.body", "body": b"last", "more_body": False})

    async def exercise() -> None:
        middleware = ApiDrainMiddleware(app)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        task = asyncio.create_task(
            middleware(
                {"type": "http", "path": "/work"},
                receive,
                send,
            )
        )
        await body_started.wait()
        assert API_DRAIN_CONTROLLER.snapshot().in_flight == 1
        API_DRAIN_CONTROLLER.begin_drain("unit-stream")
        body_release.set()
        await task

    asyncio.run(exercise())

    assert API_DRAIN_CONTROLLER.snapshot().in_flight == 0
    assert sent[-1]["more_body"] is False
    API_DRAIN_CONTROLLER.reset_for_test()


def test_rollout_probe_is_idempotent_across_runtime_replay(monkeypatch) -> None:
    store = FakeStore()
    monkeypatch.setattr(control_panel_runtime, "get_transactional_store", lambda: store)
    monkeypatch.setenv("EVM_API_RELEASE_ID", "release-a")
    monkeypatch.setenv("EVM_POD_UID", "pod-a")
    monkeypatch.setenv("EVM_IMAGE_SOURCE_REVISION", "a" * 40)
    request = control_panel_runtime.RolloutProbeRequest(
        logical_request_id="s6-test-request-0001",
        seed=20260823,
        processing_delay_ms=0,
    )

    first = asyncio.run(
        control_panel_runtime.execute_rollout_probe(
            request,
            idempotency_key=request.logical_request_id,
        )
    )
    monkeypatch.setenv("EVM_API_RELEASE_ID", "release-b")
    monkeypatch.setenv("EVM_POD_UID", "pod-b")
    replay = asyncio.run(
        control_panel_runtime.execute_rollout_probe(
            request,
            idempotency_key=request.logical_request_id,
        )
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.effect_id == first.effect_id
    assert replay.release_id == "release-a"
    assert len(store.payloads) == 1


def test_rollout_probe_rejects_header_and_payload_identity_mismatch() -> None:
    request = control_panel_runtime.RolloutProbeRequest(
        logical_request_id="s6-test-request-0002",
        seed=20260823,
        processing_delay_ms=0,
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            control_panel_runtime.execute_rollout_probe(
                request,
                idempotency_key="s6-different-request",
            )
        )
    assert error.value.status_code == 409
