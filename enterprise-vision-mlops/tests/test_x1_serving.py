from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from evm.model_runtime.x1_serving import X1InferenceRequest, X1ServingError, X1ServingManager
from evm.scale_validation.x1_contract import MODEL_IDS


def request(model_id: str = MODEL_IDS[0]) -> X1InferenceRequest:
    count = 39 if model_id == "criteo_dlrm_lite" else 28
    return X1InferenceRequest(
        schema_version="evm.s8_v4.x1_inference_request.v1",
        suite_id="x1-unit-suite-0001",
        attempt_id="x1-unit-attempt-0001",
        request_id="x1-unit-request-0001",
        traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        model_id=model_id,
        model_version="1",
        artifact_sha256="c" * 64,
        config_sha256="d" * 64,
        features=[0.0] * count,
        deadline_unix_ns=time.time_ns() + 5_000_000_000,
        lease_id="lease-unit-0001",
        fencing_token="fencing-token-unit-0001",
    )


def runtime_manifest(tmp_path: Path) -> Path:
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}\n", encoding="ascii")
    payload = {
        "schema_version": "evm.s8_v4.x1_runtime_manifest.v1",
        "artifact_manifest_path": str(artifact),
        "manifest_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "active_profile": "disabled",
        "profiles": {
            "disabled": {"models": {model_id: {"max_batch_size": 0} for model_id in MODEL_IDS}}
        },
        "models": {
            model_id: {
                "model_version": "1",
                "artifact_sha256": "c" * 64,
                "config_sha256": "d" * 64,
                "feature_count": 39 if model_id == "criteo_dlrm_lite" else 28,
                "max_batch_size": 0,
                "preferred_batch_size": [],
                "max_queue_delay_microseconds": 0,
                "instance_group_count": 1,
                "gpu_device_index": 0,
            }
            for model_id in MODEL_IDS
        },
    }
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def configure_environment(monkeypatch: pytest.MonkeyPatch, manifest: Path) -> None:
    monkeypatch.setenv("EVM_X1_RUNTIME_MANIFEST", str(manifest))
    monkeypatch.setenv("EVM_X1_LEASE_ID", "lease-unit-0001")
    monkeypatch.setenv("EVM_X1_FENCING_TOKEN", "fencing-token-unit-0001")
    monkeypatch.setenv("EVM_POD_UID", "pod-uid-unit")
    monkeypatch.setenv("EVM_POD_NAME", "pod-name-unit")
    monkeypatch.setenv("OTEL_SERVICE_INSTANCE_ID", "pod-uid-unit")
    monkeypatch.setenv("EVM_X1_API_REPLICAS", "1")
    monkeypatch.setenv("EVM_X1_CPU_WORKERS", "1")


def test_x1_serving_binds_topology_model_trace_and_durable_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_environment(monkeypatch, runtime_manifest(tmp_path))

    def handler(call: httpx.Request) -> httpx.Response:
        if call.url.path.endswith("/config"):
            return httpx.Response(200, json=triton_config(MODEL_IDS[0]))
        assert call.url.path.endswith("/versions/1/infer")
        return httpx.Response(
            200,
            json={
                "outputs": [{"name": "OUTPUT__0", "datatype": "FP32", "shape": [1], "data": [0.5]}]
            },
        )

    manager = X1ServingManager()
    manager._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://triton"
    )

    async def commit(offered: object, response: Any) -> dict[str, object]:
        return {
            "effect_id": response.effect_id,
            "replayed": False,
            "committed": True,
            "readback_visible": True,
        }

    result = asyncio.run(manager.predict(request(), terminal_committer=commit))
    assert result.terminal_outcome == "completed"
    assert result.trace_id == "a" * 32
    assert result.topology.pod_uid == "pod-uid-unit"
    assert result.topology.worker_slot.startswith("pod-uid-unit:")
    assert result.durable_effect is not None
    assert result.runtime_device == "cuda"
    asyncio.run(manager.close())


def test_x1_serving_rejects_unconfirmed_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_environment(monkeypatch, runtime_manifest(tmp_path))
    manager = X1ServingManager()
    manager._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda call: httpx.Response(200, json=triton_config(MODEL_IDS[0]))
            if call.url.path.endswith("/config")
            else httpx.Response(
                200,
                json={
                    "outputs": [
                        {
                            "name": "OUTPUT__0",
                            "datatype": "FP32",
                            "shape": [1],
                            "data": [0.5],
                        }
                    ]
                },
            )
        ),
        base_url="http://triton",
    )

    async def commit(offered: object, response: Any) -> dict[str, object]:
        return {"effect_id": response.effect_id, "committed": True, "readback_visible": False}

    with pytest.raises(X1ServingError) as error:
        asyncio.run(manager.predict(request(), terminal_committer=commit))
    assert error.value.code == "x1_durable_effect_unconfirmed"
    asyncio.run(manager.close())


def test_x1_serving_distinguishes_durable_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_environment(monkeypatch, runtime_manifest(tmp_path))
    manager = X1ServingManager()
    manager._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda call: httpx.Response(200, json=triton_config(MODEL_IDS[0]))
            if call.url.path.endswith("/config")
            else httpx.Response(
                200,
                json={
                    "outputs": [
                        {
                            "name": "OUTPUT__0",
                            "datatype": "FP32",
                            "shape": [1],
                            "data": [0.5],
                        }
                    ]
                },
            )
        ),
        base_url="http://triton",
    )

    async def commit(offered: object, response: Any) -> dict[str, object]:
        del offered, response
        raise RuntimeError("database receipt failed")

    with pytest.raises(X1ServingError) as error:
        asyncio.run(manager.predict(request(), terminal_committer=commit))
    assert error.value.code == "x1_durable_effect_commit_failed"
    assert str(error.value) == "database receipt failed"
    asyncio.run(manager.close())


def test_x1_serving_rejects_wrong_feature_count() -> None:
    payload = request().model_dump(mode="json")
    payload["features"] = [0.0] * 27
    with pytest.raises(ValueError, match="requires 28 features"):
        X1InferenceRequest.model_validate(payload)


def test_x1_readiness_recovers_after_initial_triton_connect_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_environment(monkeypatch, runtime_manifest(tmp_path))
    manager = X1ServingManager()
    transports = iter(
        (
            httpx.MockTransport(
                lambda call: (_ for _ in ()).throw(httpx.ConnectError("not ready", request=call))
            ),
            httpx.MockTransport(readiness_handler),
        )
    )
    monkeypatch.setattr(
        manager,
        "_new_http_client",
        lambda: httpx.AsyncClient(transport=next(transports), base_url="http://triton"),
    )

    with pytest.raises(X1ServingError) as error:
        asyncio.run(manager.readiness())
    assert error.value.code == "x1_triton_connection_unavailable"
    assert manager._validated_models == set()

    result = asyncio.run(manager.readiness())
    assert result["status"] == "ok"
    assert manager._validated_models == set(MODEL_IDS)


def test_x1_readiness_persistent_triton_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_environment(monkeypatch, runtime_manifest(tmp_path))
    manager = X1ServingManager()
    monkeypatch.setattr(
        manager,
        "_new_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda call: (_ for _ in ()).throw(httpx.ConnectError("not ready", request=call))
            ),
            base_url="http://triton",
        ),
    )

    for _ in range(2):
        with pytest.raises(X1ServingError) as error:
            asyncio.run(manager.readiness())
        assert error.value.code == "x1_triton_connection_unavailable"
        assert manager._validated_models == set()


def test_x1_readiness_rejects_nonempty_repository_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_environment(monkeypatch, runtime_manifest(tmp_path))
    manager = X1ServingManager()

    def handler(call: httpx.Request) -> httpx.Response:
        response = readiness_handler(call)
        if call.url.path == "/v2/repository/index":
            payload = response.json()
            payload[0]["reason"] = "loading"
            return httpx.Response(200, json=payload)
        return response

    monkeypatch.setattr(
        manager,
        "_new_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://triton"),
    )
    with pytest.raises(X1ServingError) as error:
        asyncio.run(manager.readiness())
    assert error.value.code == "x1_repository_index_reason"
    assert manager._validated_models == set()


def triton_config(model_id: str) -> dict[str, object]:
    feature_count = 39 if model_id == "criteo_dlrm_lite" else 28
    return {
        "name": model_id,
        "backend": "pytorch",
        "max_batch_size": 0,
        "version_policy": {"specific": {"versions": [1]}},
        "instance_group": [{"name": f"{model_id}_0", "kind": "KIND_GPU", "count": 1, "gpus": [0]}],
        "input": [
            {
                "name": "INPUT__0",
                "data_type": "TYPE_FP32",
                "dims": [feature_count],
                "is_shape_tensor": False,
                "allow_ragged_batch": False,
                "optional": False,
            }
        ],
        "output": [
            {
                "name": "OUTPUT__0",
                "data_type": "TYPE_FP32",
                "dims": [1],
                "label_filename": "",
                "is_shape_tensor": False,
            }
        ],
    }


def readiness_handler(call: httpx.Request) -> httpx.Response:
    if call.url.path.endswith("/config"):
        model_id = call.url.path.split("/")[3]
        return httpx.Response(200, json=triton_config(model_id))
    assert call.url.path == "/v2/repository/index"
    return httpx.Response(
        200,
        json=[{"name": model_id, "version": "1", "state": "READY"} for model_id in MODEL_IDS],
    )
