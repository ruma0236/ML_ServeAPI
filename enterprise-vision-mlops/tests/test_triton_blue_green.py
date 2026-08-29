from __future__ import annotations

import asyncio
import hashlib
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
from evm.observability.trace_context import (
    W3CTraceContext,
    bind_trace_context,
    reset_trace_context,
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


def test_generation_pinned_request_rejects_before_in_flight_accounting(
    manager: TritonBlueGreenManager,
) -> None:
    request = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-test",
        lease_id="lease-test",
        fencing_token="fence-test",
        attempt_id="s6bm-success-generation-test",
        request_id="request-blue-generation-0001",
        request_nonce="nonce-blue-generation-0001",
        traceparent="00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        expected_model_role="blue",
        expected_model_name="s6bm_blue",
        expected_model_version="1",
        expected_artifact_sha256="c" * 64,
        expected_route_generation=manager.snapshot().generation + 1,
    )

    with pytest.raises(TritonBlueGreenError) as mismatch:
        asyncio.run(manager.predict(request))

    assert mismatch.value.code == "causal_route_generation_mismatch"
    assert manager.snapshot().in_flight == {"blue": 0, "green": 0}


def test_route_generation_remains_at_switch_epoch_during_blue_drain(
    manager: TritonBlueGreenManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"outputs": [{"name": "OUTPUT__0", "data": [4, 6, 8, 10]}]}

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    manager.control(control_request(manager, "green_loaded"))
    manager.control(control_request(manager, "canary_started"))
    switched = manager.control(control_request(manager, "green_switched"))
    switch_generation = switched.generation
    manager.control(control_request(manager, "blue_drain_started"))
    assert manager.snapshot().generation == switch_generation + 1

    request = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-test",
        lease_id="lease-test",
        fencing_token="fence-test",
        attempt_id="s6bm-success-route-epoch",
        request_id="request-green-route-epoch",
        request_nonce="nonce-green-route-epoch",
        traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        expected_model_role="green",
        expected_model_name="s6bm_green",
        expected_model_version="1",
        expected_artifact_sha256="e" * 64,
        expected_route_generation=switch_generation,
    )
    result = asyncio.run(manager.predict(request))

    assert result.route_generation == switch_generation
    assert result.route_phase == "blue_draining"


def test_route_revision_is_last_route_changing_control_revision(
    manager: TritonBlueGreenManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"outputs": [{"name": "OUTPUT__0", "data": [4, 6, 8, 10]}]}

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    assert (manager.snapshot().generation, manager.snapshot().route_generation) == (1, 1)
    manager.control(control_request(manager, "green_loaded"))
    assert (manager.snapshot().generation, manager.snapshot().route_generation) == (2, 1)
    manager.control(control_request(manager, "canary_started"))
    assert (manager.snapshot().generation, manager.snapshot().route_generation) == (3, 3)
    manager.control(control_request(manager, "green_switched"))
    assert (manager.snapshot().generation, manager.snapshot().route_generation) == (4, 4)
    manager.control(control_request(manager, "blue_drain_started"))
    assert (manager.snapshot().generation, manager.snapshot().route_generation) == (5, 4)

    base = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-test",
        lease_id="lease-test",
        fencing_token="fence-test",
        attempt_id="s6bm-route-revision-drain",
        request_id="request-green-route-revision",
        request_nonce="nonce-green-route-revision",
        traceparent="00-" + "8" * 32 + "-" + "9" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        expected_model_role="green",
        expected_model_name="s6bm_green",
        expected_model_version="1",
        expected_artifact_sha256="e" * 64,
        expected_route_generation=4,
    )
    assert asyncio.run(manager.predict(base)).route_generation == 4
    for forged in (3, 5):
        changed = base.model_copy(
            update={
                "request_id": f"request-green-route-forged-{forged}",
                "request_nonce": f"nonce-green-route-forged-{forged}",
                "expected_route_generation": forged,
            }
        )
        with pytest.raises(TritonBlueGreenError) as rejected:
            asyncio.run(manager.predict(changed))
        assert rejected.value.code == "causal_route_generation_mismatch"

    stale = control_request(manager, "blue_unloaded").model_copy(update={"expected_generation": 4})
    stale = stale.model_copy(update={"action_digest": action_digest(stale)})
    with pytest.raises(TritonBlueGreenError) as stale_control:
        manager.control(stale)
    assert stale_control.value.code == "route_generation_conflict"


def test_route_revision_is_monotonic_across_rollback_abort_and_second_rollout(
    manager: TritonBlueGreenManager,
) -> None:
    observed = [manager.snapshot().route_generation]
    for action in (
        "green_loaded",
        "canary_started",
        "green_switched",
        "blue_drain_started",
        "blue_unloaded",
        "blue_loaded",
        "blue_switched",
        "green_drain_started",
        "green_unloaded",
        "green_loaded",
        "canary_started",
        "green_aborted",
        "green_loaded",
        "canary_started",
        "green_switched",
    ):
        manager.control(control_request(manager, action))
        observed.append(manager.snapshot().route_generation)

    assert observed == [1, 1, 3, 4, 4, 4, 4, 8, 8, 8, 8, 12, 13, 13, 15, 16]
    changed = [value for left, value in zip(observed, observed[1:]) if value != left]
    assert changed == [3, 4, 8, 12, 13, 15, 16]
    assert len(changed) == len(set(changed))
    assert observed[3] == observed[2] + 1
    assert observed[-1] == observed[-2] + 1

    warmup_abort = TritonBlueGreenManager()
    warmup_abort._assert_lease = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    warmup_abort.initialize(initialize_request())
    warmup_abort.control(control_request(warmup_abort, "green_loaded"))
    warmup_abort.control(control_request(warmup_abort, "green_aborted"))
    assert (
        warmup_abort.snapshot().generation,
        warmup_abort.snapshot().route_generation,
    ) == (3, 1)


def test_active_model_identity_changes_route_signature_without_weight_change() -> None:
    original = initialize_request()
    changed_blue = original.model_copy(
        update={
            "blue": original.blue.model_copy(
                update={"model_version": "2", "artifact_sha256": "1" * 64}
            )
        }
    )
    changed_standby = original.model_copy(
        update={
            "green": original.green.model_copy(
                update={"model_version": "2", "artifact_sha256": "2" * 64}
            )
        }
    )
    blue_only = {"blue": 100, "green": 0}
    assert module.active_route_identity_sha256(original, blue_only) != (
        module.active_route_identity_sha256(changed_blue, blue_only)
    )
    assert module.active_route_identity_sha256(original, blue_only) == (
        module.active_route_identity_sha256(changed_standby, blue_only)
    )


def test_predict_uses_gpu_lease_fence_independently_of_route_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVM_S6BM_ENABLED", "1")
    owner = {"lease_id": "lease-test", "fencing_token": "fence-test"}

    def assert_owner(*, lease_id: str, fencing_token: str, **_kwargs: object) -> None:
        if (lease_id, fencing_token) != (owner["lease_id"], owner["fencing_token"]):
            raise RuntimeError("stale GPU lease")

    monkeypatch.setattr(module, "assert_scale_validation_gpu_lease_owner", assert_owner)
    value = TritonBlueGreenManager()
    value.initialize(initialize_request())
    request = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-test",
        lease_id="lease-test",
        fencing_token="fence-test",
        attempt_id="s6bm-lease-fence-test",
        request_id="request-blue-lease-fence",
        request_nonce="nonce-blue-lease-fence",
        traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        expected_model_role="blue",
        expected_model_name="s6bm_blue",
        expected_model_version="1",
        expected_artifact_sha256="c" * 64,
        expected_route_generation=1,
    )
    owner.update(lease_id="lease-next", fencing_token="fence-next")
    with pytest.raises(RuntimeError, match="stale GPU lease"):
        asyncio.run(value.predict(request))
    rebound = request.model_copy(update={"lease_id": "lease-next", "fencing_token": "fence-next"})
    with pytest.raises(TritonBlueGreenError) as stale_state:
        asyncio.run(value.predict(rebound))
    assert stale_state.value.code == "route_revision_lease_binding_mismatch"
    assert value.snapshot().route_generation == 1


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
        lease_id="lease-test",
        fencing_token="fence-test",
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

    observed_headers: list[dict[str, str]] = []

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **kwargs: object) -> Response:
            observed_headers.append(dict(kwargs.get("headers", {})))
            return Response()

    monkeypatch.setenv("EVM_S6BM_REQUIRE_CAUSAL_FENCE", "1")
    monkeypatch.setenv("EVM_S6BM_REQUIRE_DURABLE_EFFECT", "1")
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    manager.control(control_request(manager, "green_loaded"))
    manager.control(control_request(manager, "canary_started"))
    request = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-test",
        lease_id="lease-test",
        fencing_token="fence-test",
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
        route_switch_deadline_owner=True,
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
            "schema_version": "evm.s6bm.durable_effect_receipt.v4",
            "entity_kind": "s6bm_terminal_effect",
            "entity_id": response.effect_id,
            "request_sha256": "3" * 64,
            "stored_payload_sha256": "4" * 64,
            "database_recorded_at": "2026-08-25T00:00:00Z",
            "entity_created_at": "2026-08-25T00:00:00Z",
            "idempotency_created_at": "2026-08-25T00:00:00Z",
            "readback_at": "2026-08-25T00:00:00.001Z",
            "transaction_id": "101",
            "write_backend_pid": 101,
            "synchronous_commit": "on",
            "commit_ack_monotonic_ns": 1,
            "commit_timestamp": "2026-08-25T00:00:00.0001Z",
            "commit_timestamp_observed_at": "2026-08-25T00:00:00.0002Z",
            "commit_timestamp_backend_pid": 102,
            "commit_timestamp_tracking": "on",
            "commit_timestamp_visible": True,
            "separate_connection_readback": True,
            "commit_timestamp_started_monotonic_ns": 1,
            "commit_timestamp_finished_monotonic_ns": 2,
            "database_clock_anchor": {"schema_version": "evm.s6bm.database_clock_anchor.v2"},
            "database_clock_anchor_candidates": [
                {"schema_version": "evm.s6bm.database_clock_anchor.v2"}
            ],
            "database_clock_anchor_selection": {
                "strategy": "minimum_width_then_sequence",
                "candidate_count": 8,
                "selected_sequence": 1,
            },
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
        now = module.time.perf_counter_ns()
        return {
            "schema_version": "evm.s6bm.route_switch_receipt.v2",
            "readback_visible": True,
            "attempt_id": identity.attempt_id,
            "run_id": identity.run_id,
            "request_id": identity.request_id,
            "transition_id": "1" * 64,
            "fence_id": "2" * 64,
            "cell_id": identity.attempt_id,
            "replica_id": "test-replica",
            "source_revision": "a" * 40,
            "source_payload_sha256": "3" * 64,
            "old_route_generation": control.expected_generation,
            "new_route_generation": control.expected_generation + 1,
            "continuity_receipt_request_ids": [],
            "continuity_receipt_request_count": 0,
            "continuity_crossover_request_ids": [],
            "pending_crossover_request_ids": [identity.request_id],
            "pending_crossover_count": 1,
            "fence_sequence": 4,
            "fence_transaction_id": "100",
            "fence_payload_sha256": "4" * 64,
            "actor_identity": "api-control-plane-route-switch",
            "actor_process_id": module.os.getpid(),
            "actor_thread_id": module.threading.get_ident(),
            "commit_ack_monotonic_ns": now,
            "readback_started_monotonic_ns": now + 1,
            "readback_finished_monotonic_ns": now + 2,
        }

    async def scenario() -> None:
        token = bind_trace_context(W3CTraceContext.parse(request.traceparent))
        try:
            task = asyncio.create_task(
                manager.predict(
                    request,
                    terminal_effect_committer=terminal_committer,
                    start_receipt_committer=start_committer,
                )
            )
        finally:
            reset_trace_context(token)
        await asyncio.sleep(0.01)
        switched = manager.control(
            control_request(
                manager,
                "green_switched",
                causal_crossover=identity.model_dump(mode="json"),
                pending_crossover_request_ids=[identity.request_id],
            ),
            transition_fence_committer=fence_committer,
        )
        assert switched.transition_receipt is not None
        assert switched.transition_receipt["transition_id"] == "1" * 64
        assert switched.transition_receipt["old_route_generation"] == 3
        assert switched.transition_receipt["new_route_generation"] == 4
        assert switched.transition_receipt["state_readback"] == {
            "generation": 4,
            "route_generation": 4,
            "phase": "green_active",
            "route_weights": {"blue": 0, "green": 100},
            "loaded_roles": ["blue", "green"],
        }
        manager.control(control_request(manager, "blue_drain_started"))
        result = await task
        assert result.effect_id == identity.effect_id
        assert len(observed_headers) == 1
        propagated = observed_headers[0]["traceparent"]
        assert propagated.split("-")[1] == request.traceparent.split("-")[1]
        assert propagated.split("-")[2] != request.traceparent.split("-")[2]
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


def test_s6bm_crossover_waits_for_switch_and_times_out_fail_closed(
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
    monkeypatch.setenv("EVM_S6BM_CAUSAL_SWITCH_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    manager.control(control_request(manager, "green_loaded"))
    manager.control(control_request(manager, "canary_started"))
    request = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-test",
        lease_id="lease-test",
        fencing_token="fence-test",
        attempt_id="s6bm-success-causal-timeout",
        request_id="request-blue-causal-timeout",
        request_nonce="nonce-blue-causal-timeout",
        traceparent="00-" + "6" * 32 + "-" + "7" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        hold_ms=1,
        expected_model_role="blue",
        expected_model_name="s6bm_blue",
        expected_model_version="1",
        expected_artifact_sha256="c" * 64,
        expected_route_generation=manager.snapshot().generation,
        causal_crossover=True,
        route_switch_deadline_owner=True,
    )

    async def start_committer(
        _stage: str,
        _request: TritonBlueGreenPredictRequest,
        _payload: dict[str, object],
    ) -> dict[str, object]:
        return {"readback_visible": True}

    async def terminal_committer(
        _request: TritonBlueGreenPredictRequest,
        _response: module.TritonBlueGreenPredictResponse,
    ) -> dict[str, object]:
        raise AssertionError("terminal effect must not run before the switch fence")

    with pytest.raises(TritonBlueGreenError) as timeout:
        asyncio.run(
            manager.predict(
                request,
                terminal_effect_committer=terminal_committer,
                start_receipt_committer=start_committer,
            )
        )

    assert timeout.value.code == "causal_switch_wait_timeout"
    assert manager.snapshot().in_flight == {"blue": 0, "green": 0}
    assert manager.snapshot().pending_crossover_count == 0


def test_s6bm_bridge_start_receipt_does_not_wait_for_route_switch(
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
    observed: list[tuple[str, int]] = []
    generation = manager.snapshot().generation
    request = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-test",
        lease_id="lease-test",
        fencing_token="fence-test",
        attempt_id="s6bm-success-bridge-receipt",
        request_id="request-blue-bridge-receipt",
        request_nonce="nonce-blue-bridge-receipt",
        traceparent="00-" + "8" * 32 + "-" + "9" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        hold_ms=1,
        expected_model_role="blue",
        expected_model_name="s6bm_blue",
        expected_model_version="1",
        expected_artifact_sha256="c" * 64,
        expected_route_generation=generation,
        start_receipt_required=True,
    )

    async def start_committer(
        stage: str,
        _request: TritonBlueGreenPredictRequest,
        payload: dict[str, object],
    ) -> dict[str, object]:
        observed.append((stage, int(payload["route_generation"])))
        return {"readback_visible": True}

    result = asyncio.run(manager.predict(request, start_receipt_committer=start_committer))

    assert result.model_role == "blue"
    assert result.route_generation == generation
    assert observed == [("controller_entry", generation)]
    assert manager.snapshot().in_flight == {"blue": 0, "green": 0}


def test_s6bm_green_switch_releases_exact_hold_and_designated_bridge_set(
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
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    manager.control(control_request(manager, "green_loaded"))
    manager.control(control_request(manager, "canary_started"))
    generation = manager.snapshot().generation

    def predict_request(
        request_id: str, *, receipt_required: bool
    ) -> TritonBlueGreenPredictRequest:
        return TritonBlueGreenPredictRequest(
            run_id="s8-v4-s6bm-test",
            lease_id="lease-test",
            fencing_token="fence-test",
            attempt_id="s6bm-success-exact-pending",
            request_id=request_id,
            request_nonce=f"nonce-{request_id}",
            traceparent=(
                "00-"
                + ("a" if receipt_required else "b") * 32
                + "-"
                + ("c" if receipt_required else "d") * 16
                + "-01"
            ),
            input_values=[1, 2, 3, 4],
            hold_ms=0 if receipt_required else 20,
            expected_model_role="blue",
            expected_model_name="s6bm_blue",
            expected_model_version="1",
            expected_artifact_sha256="c" * 64,
            expected_route_generation=generation,
            start_receipt_required=receipt_required,
            causal_crossover=True,
            route_switch_deadline_owner=receipt_required,
        )

    def blue_request_id(prefix: str) -> str:
        for suffix in range(100):
            candidate = f"{prefix}-{suffix:02d}"
            if module._role_for_request(candidate, {"blue": 90, "green": 10}) == "blue":
                return candidate
        raise AssertionError("failed to generate a Blue-routed request ID")

    hold_request = predict_request(
        blue_request_id("request-blue-causal-hold"), receipt_required=False
    )
    bridge_request = predict_request(
        blue_request_id("request-blue-bridge-crossover"), receipt_required=True
    )
    hold_identity = expected_causal_identity_for_request(hold_request)
    bridge_identity = expected_causal_identity_for_request(bridge_request)
    receipt_ids = sorted(
        [bridge_request.request_id, "receipt-bridge-1", "receipt-bridge-2", "receipt-bridge-3"]
    )
    pending_ids = sorted([hold_request.request_id, bridge_request.request_id])
    terminal_ids = [f"terminal-bridge-{index:02d}" for index in range(39)]
    terminal_set_sha = hashlib.sha256(module.canonical(terminal_ids).encode("ascii")).hexdigest()
    terminal_records_sha = "f" * 64

    async def start_committer(
        _stage: str,
        _request: TritonBlueGreenPredictRequest,
        _payload: dict[str, object],
    ) -> dict[str, object]:
        return {"readback_visible": True}

    def fence_committer(
        control: TritonBlueGreenControlRequest,
        _context: dict[str, object],
    ) -> dict[str, object]:
        now = module.time.perf_counter_ns()
        return {
            "schema_version": "evm.s6bm.route_switch_receipt.v2",
            "readback_visible": True,
            "attempt_id": hold_identity.attempt_id,
            "run_id": hold_identity.run_id,
            "request_id": hold_identity.request_id,
            "transition_id": "1" * 64,
            "fence_id": "2" * 64,
            "cell_id": hold_identity.attempt_id,
            "replica_id": "test-replica",
            "source_revision": "a" * 40,
            "source_payload_sha256": "3" * 64,
            "old_route_generation": control.expected_generation,
            "new_route_generation": control.expected_generation + 1,
            "continuity_receipt_request_ids": receipt_ids,
            "continuity_receipt_request_count": 4,
            "continuity_crossover_request_ids": [bridge_identity.request_id],
            "pending_crossover_request_ids": pending_ids,
            "pending_crossover_count": 2,
            "continuity_terminal_request_ids": terminal_ids,
            "continuity_terminal_request_count": 39,
            "continuity_terminal_request_set_sha256": terminal_set_sha,
            "continuity_terminal_records_sha256": terminal_records_sha,
            "fence_sequence": 13,
            "fence_transaction_id": "100",
            "fence_payload_sha256": "4" * 64,
            "actor_identity": "api-control-plane-route-switch",
            "actor_process_id": module.os.getpid(),
            "actor_thread_id": module.threading.get_ident(),
            "commit_ack_monotonic_ns": now,
            "readback_started_monotonic_ns": now + 1,
            "readback_finished_monotonic_ns": now + 2,
        }

    async def scenario() -> None:
        hold_task = asyncio.create_task(
            manager.predict(hold_request, start_receipt_committer=start_committer)
        )
        bridge_task = asyncio.create_task(
            manager.predict(bridge_request, start_receipt_committer=start_committer)
        )
        await asyncio.sleep(0.01)
        pre_switch = manager.snapshot()
        assert pre_switch.pending_crossover_request_ids == pending_ids
        assert pre_switch.route_switch_deadline_owner_request_id == bridge_request.request_id
        assert pre_switch.route_switch_deadline_started_monotonic_ns is not None
        assert pre_switch.route_switch_deadline_monotonic_ns is not None
        assert (
            pre_switch.route_switch_deadline_monotonic_ns
            - pre_switch.route_switch_deadline_started_monotonic_ns
            == 15_000_000_000
        )
        switched = manager.control(
            control_request(
                manager,
                "green_switched",
                causal_crossover=hold_identity.model_dump(mode="json"),
                continuity_receipt_request_ids=receipt_ids,
                continuity_crossover_request_ids=[bridge_identity.request_id],
                pending_crossover_request_ids=pending_ids,
                continuity_terminal_request_ids=terminal_ids,
                continuity_terminal_request_set_sha256=terminal_set_sha,
                continuity_terminal_records_sha256=terminal_records_sha,
            ),
            transition_fence_committer=fence_committer,
        )
        assert switched.transition_receipt is not None
        assert switched.transition_receipt["pending_crossover_request_ids"] == pending_ids
        assert switched.transition_receipt["released_crossover_request_ids"] == pending_ids
        assert (
            switched.transition_receipt["route_switch_deadline_owner_request_id"]
            == bridge_request.request_id
        )
        await asyncio.gather(hold_task, bridge_task)
        final = manager.snapshot()
        assert final.pending_crossover_count == 0
        assert final.route_switch_deadline_owner_request_id is None
        assert final.route_switch_deadline_monotonic_ns is None

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
