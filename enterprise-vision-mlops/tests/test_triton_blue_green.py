from __future__ import annotations

import asyncio
from typing import Any

import pytest

from evm.model_runtime import triton_blue_green as module
from evm.model_runtime.triton_blue_green import (
    TritonBlueGreenControlRequest,
    TritonBlueGreenError,
    TritonBlueGreenInitializeRequest,
    TritonBlueGreenManager,
    TritonBlueGreenPredictRequest,
    TritonModelIdentity,
    action_digest,
)


def initialize_request() -> TritonBlueGreenInitializeRequest:
    return TritonBlueGreenInitializeRequest(
        run_id="s8-v4-s6bm-test",
        source_revision="a" * 40,
        triton_http_url="http://127.0.0.1:18100",
        image_digest="sha256:" + "b" * 64,
        gpu_uuid="GPU-test",
        lease_id="lease-test",
        fencing_token="fence-test",
        blue=TritonModelIdentity(
            role="blue",
            model_name="s6bm_blue",
            model_version="1",
            artifact_sha256="c" * 64,
            config_sha256="d" * 64,
            expected_output=[3, 5, 7, 9],
        ),
        green=TritonModelIdentity(
            role="green",
            model_name="s6bm_green",
            model_version="1",
            artifact_sha256="e" * 64,
            config_sha256="f" * 64,
            expected_output=[4, 6, 8, 10],
        ),
    )


def control_request(
    manager: TritonBlueGreenManager,
    action: str,
    *,
    approval_id: str | None = None,
    green_digest: str = "e" * 64,
    **signals: Any,
) -> TritonBlueGreenControlRequest:
    request = TritonBlueGreenControlRequest(
        run_id="s8-v4-s6bm-test",
        action=action,
        expected_generation=manager.snapshot().generation,
        lease_id="lease-test",
        fencing_token="fence-test",
        blue_artifact_sha256="c" * 64,
        green_artifact_sha256=green_digest,
        approval_id=approval_id or f"approval-{action}-{manager.snapshot().generation}",
        action_digest="0" * 64,
        **signals,
    )
    return request.model_copy(update={"action_digest": action_digest(request)})


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> TritonBlueGreenManager:
    monkeypatch.setenv("EVM_S6BM_ENABLED", "1")
    value = TritonBlueGreenManager()
    monkeypatch.setattr(value, "_assert_lease", lambda *_args, **_kwargs: None)
    value.initialize(initialize_request())
    return value


def test_s6bm_transition_contract_and_fail_closed_guards(
    manager: TritonBlueGreenManager,
) -> None:
    initial = manager.snapshot()
    assert initial.phase == "blue_only"
    assert initial.route_weights == {"blue": 100, "green": 0}

    with pytest.raises(TritonBlueGreenError, match="s8-v4-s6bm-test") as mismatch:
        manager.control(control_request(manager, "green_loaded", green_digest="1" * 64))
    assert mismatch.value.code == "green_digest_mismatch"
    assert manager.snapshot() == initial

    with pytest.raises(TritonBlueGreenError) as vram:
        manager.control(
            control_request(manager, "green_loaded", preflight_vram_passed=False)
        )
    assert vram.value.code == "vram_preflight_rejected"
    assert manager.snapshot() == initial

    manager.control(control_request(manager, "green_loaded"))
    manager.control(control_request(manager, "canary_started"))
    assert manager.snapshot().route_weights == {"blue": 90, "green": 10}
    manager.control(control_request(manager, "green_switched"))
    manager.control(control_request(manager, "blue_drain_started"))
    manager.control(control_request(manager, "blue_unloaded"))
    manager.control(control_request(manager, "blue_loaded"))
    manager.control(control_request(manager, "blue_switched"))
    manager.control(control_request(manager, "green_drain_started"))
    manager.control(control_request(manager, "green_unloaded"))
    assert manager.snapshot().phase == "rolled_back"
    assert manager.snapshot().loaded_roles == ["blue"]


def test_s6bm_approval_is_bound_and_single_use(manager: TritonBlueGreenManager) -> None:
    first = control_request(manager, "green_loaded", approval_id="approval-reuse")
    manager.control(first)
    second = control_request(manager, "canary_started", approval_id="approval-reuse")
    with pytest.raises(TritonBlueGreenError) as reused:
        manager.control(second)
    assert reused.value.code == "approval_reused"
    assert manager.snapshot().phase == "green_warmup"


def test_s6bm_external_effect_is_idempotent_and_drain_blocks_in_flight(
    manager: TritonBlueGreenManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"outputs": [{"name": "OUTPUT__0", "data": [3, 5, 7, 9]}]}

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    request = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-test",
        request_id="request-blue-0001",
        traceparent="00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        hold_ms=100,
    )
    async def scenario() -> None:
        task = asyncio.create_task(manager.predict(request))
        await asyncio.sleep(0.02)
        assert manager.snapshot().in_flight["blue"] == 1

        manager.control(control_request(manager, "green_loaded"))
        manager.control(control_request(manager, "canary_started"))
        manager.control(control_request(manager, "green_switched"))
        manager.control(control_request(manager, "blue_drain_started"))
        with pytest.raises(TritonBlueGreenError) as drain:
            manager.control(control_request(manager, "blue_unloaded"))
        assert drain.value.code == "blue_drain_incomplete"

        result = await task
        manager.control(control_request(manager, "blue_unloaded"))
        replay = await manager.predict(request)
        assert result.replayed is False
        assert replay.replayed is True
        assert manager.snapshot().accepted_unique == 1
        assert manager.snapshot().terminal_unique == 1
        assert manager.snapshot().duplicate_replays == 1

    asyncio.run(scenario())


def test_s6bm_rejects_readiness_and_canary_without_route_switch(
    manager: TritonBlueGreenManager,
) -> None:
    with pytest.raises(TritonBlueGreenError) as readiness:
        manager.control(control_request(manager, "green_loaded", readiness_passed=False))
    assert readiness.value.code == "green_readiness_rejected"
    assert manager.snapshot().route_weights == {"blue": 100, "green": 0}

    manager.control(control_request(manager, "green_loaded"))
    with pytest.raises(TritonBlueGreenError) as canary:
        manager.control(control_request(manager, "canary_started", canary_passed=False))
    assert canary.value.code == "green_canary_rejected"
    assert manager.snapshot().phase == "green_warmup"
