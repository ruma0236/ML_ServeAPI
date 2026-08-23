from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi import HTTPException

from apps.api import control_panel_runtime
from evm.control_panel.api_rollout import ApiDrainController, ApiDrainingError


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
    assert elapsed >= 0.01


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
