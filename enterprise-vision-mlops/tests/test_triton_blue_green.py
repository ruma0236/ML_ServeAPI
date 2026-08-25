from __future__ import annotations

import asyncio
from typing import Any

import httpx
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
    expected_causal_identity_for_request,
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
        manager.control(control_request(manager, "green_loaded", preflight_vram_passed=False))
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
        status_code = 200

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


def test_s6bm_strict_causal_transition_requires_receipts_and_fence(
    manager: TritonBlueGreenManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        status_code = 200

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

    monkeypatch.setenv("EVM_S6BM_REQUIRE_CAUSAL_FENCE", "1")
    monkeypatch.setenv("EVM_S6BM_REQUIRE_DURABLE_EFFECT", "1")
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    manager.control(control_request(manager, "green_loaded"))
    manager.control(control_request(manager, "canary_started"))
    request = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-test",
        attempt_id="s6bm-success-causal-test",
        request_id="request-blue-causal-0001",
        request_nonce="nonce-blue-causal-0001",
        traceparent="00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        hold_ms=50,
        expected_model_role="blue",
        expected_model_name="s6bm_blue",
        expected_model_version="1",
        expected_artifact_sha256="c" * 64,
        expected_route_generation=manager.snapshot().generation,
        causal_crossover=True,
    )
    identity = expected_causal_identity_for_request(request)
    start_stages: list[str] = []
    fence_actions: list[str] = []

    async def start_committer(
        stage: str,
        _request: TritonBlueGreenPredictRequest,
        _payload: dict[str, object],
    ) -> dict[str, object]:
        start_stages.append(stage)
        return {"readback_visible": True}

    async def terminal_committer(
        _request: TritonBlueGreenPredictRequest,
        response: module.TritonBlueGreenPredictResponse,
    ) -> dict[str, object]:
        return {
            "schema_version": "evm.s6bm.durable_effect_receipt.v1",
            "entity_kind": "s6bm_terminal_effect",
            "entity_id": response.effect_id,
            "request_sha256": "3" * 64,
            "stored_payload_sha256": "4" * 64,
            "database_recorded_at": "2026-08-25T00:00:00Z",
            "entity_created_at": "2026-08-25T00:00:00Z",
            "idempotency_created_at": "2026-08-25T00:00:00Z",
            "readback_at": "2026-08-25T00:00:00.001Z",
            "transaction_id": "101",
            "synchronous_commit": "on",
            "commit_ack_monotonic_ns": 1,
            "readback_started_monotonic_ns": 2,
            "readback_finished_monotonic_ns": 3,
            "readback_visible": True,
            "replayed": False,
            "causal_sequence": 5,
            "causal_payload_sha256": "5" * 64,
        }

    def fence_committer(
        control: TritonBlueGreenControlRequest,
        context: dict[str, object],
    ) -> dict[str, object]:
        fence_actions.append(control.action)
        if control.action == "blue_unloaded":
            assert context["pre_switch_blue_effects"] == [
                {"request_id": request.request_id, "effect_id": identity.effect_id}
            ]
        return {"readback_visible": True}

    async def scenario() -> None:
        task = asyncio.create_task(
            manager.predict(
                request,
                terminal_effect_committer=terminal_committer,
                start_receipt_committer=start_committer,
            )
        )
        await asyncio.sleep(0.01)
        manager.control(
            control_request(
                manager,
                "green_switched",
                causal_crossover=identity.model_dump(mode="json"),
            ),
            transition_fence_committer=fence_committer,
        )
        manager.control(control_request(manager, "blue_drain_started"))
        result = await task
        assert result.effect_id == identity.effect_id
        manager.control(
            control_request(
                manager,
                "blue_unloaded",
                causal_crossover=identity.model_dump(mode="json"),
            ),
            transition_fence_committer=fence_committer,
        )

    asyncio.run(scenario())
    assert start_stages == ["controller_entry"]
    assert fence_actions == ["green_switched", "blue_unloaded"]


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


def test_s6bm_triton_model_control_failure_keeps_route_and_phase(
    manager: TritonBlueGreenManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, url: str) -> httpx.Response:
            request = httpx.Request("POST", url)
            return httpx.Response(400, request=request, json={"error": "missing model"})

        def get(self, url: str) -> httpx.Response:
            request = httpx.Request("GET", url)
            return httpx.Response(404, request=request)

    monkeypatch.setenv("EVM_S6BM_APPLY_MODEL_CONTROL", "1")
    monkeypatch.setattr(module.httpx, "Client", Client)
    before = manager.snapshot()
    with pytest.raises(TritonBlueGreenError) as failed:
        manager.control(control_request(manager, "green_loaded"))
    assert failed.value.code == "triton_model_control_failed"
    assert manager.snapshot() == before
