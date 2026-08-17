from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from apps.api.control_panel_workloads import router
from evm.control_panel.scenario_workloads import CapacityProbeRequest, WorkloadModelFamily
from evm.model_runtime import capacity_executor
from evm.model_runtime.capacity_probe import clear_capacity_probe_cache
from evm.model_runtime.capacity_executor import shutdown_capacity_probe_executor


FAMILIES = (
    "logistic",
    "probabilistic",
    "online-linear",
    "branch-heavy",
    "incremental",
)
MODEL_TYPES = {
    "logistic": "linear_logit",
    "probabilistic": "gaussian_nb",
    "online-linear": "linear_logit",
    "branch-heavy": "decision_tree",
    "incremental": "linear_logit",
}


@pytest.fixture(autouse=True)
def _reset_capacity_executor():
    shutdown_capacity_probe_executor()
    yield
    shutdown_capacity_probe_executor()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _model_identity(
    family: str,
    dataset_identity: str,
    artifact_sha256: str,
    algorithm: str,
) -> str:
    return hashlib.sha256(
        _json_bytes(
            {
                "schema_version": "evm.s3_capacity_model_identity.v1",
                "probe_family": family,
                "dataset_identity_sha256": dataset_identity,
                "artifact_sha256": artifact_sha256,
                "algorithm": algorithm,
            }
        )
    ).hexdigest()


def _artifact(family: str, dataset_identity: str) -> dict[str, object]:
    model_type = MODEL_TYPES[family]
    if model_type == "linear_logit":
        model: dict[str, object] = {
            "weights": [0.1] * 28,
            "intercept": -0.1,
        }
    elif model_type == "gaussian_nb":
        model = {
            "theta": [[0.0] * 28, [1.0] * 28],
            "variance": [[1.0] * 28, [1.0] * 28],
            "class_log_prior": [-0.6931471805599453, -0.6931471805599453],
        }
    else:
        model = {
            "nodes": [
                {"feature": 0, "threshold": 0.0, "left": 1, "right": 2},
                {"positive_probability": 0.2},
                {"positive_probability": 0.8},
            ]
        }
    return {
        "schema_version": "evm.s3_capacity_probe_artifact.v1",
        "probe_family": family,
        "model_type": model_type,
        "feature_count": 28,
        "dataset_identity_sha256": dataset_identity,
        "transform": {"kind": "identity"},
        "model": model,
    }


def _install_registry(root: Path) -> tuple[Path, str]:
    dataset_identity = "a" * 64
    probes: dict[str, object] = {}
    for family in FAMILIES:
        artifact_path = root / f"{family}.json"
        artifact_bytes = _json_bytes(_artifact(family, dataset_identity))
        artifact_path.write_bytes(artifact_bytes)
        artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
        probes[family] = {
            "algorithm": MODEL_TYPES[family],
            "artifact_uri": artifact_path.name,
            "artifact_sha256": artifact_sha256,
            "model_identity_sha256": _model_identity(
                family,
                dataset_identity,
                artifact_sha256,
                MODEL_TYPES[family],
            ),
        }
    registry_path = root / "capacity-registry.json"
    registry_path.write_bytes(
        _json_bytes(
            {
                "schema_version": "evm.s3_capacity_registry.v1",
                "dataset_id": "uci-higgs",
                "dataset_version": "controlled-test-v1",
                "dataset_identity_sha256": dataset_identity,
                "split_manifest_sha256": "b" * 64,
                "source_uri": "https://archive.ics.uci.edu/static/public/280/higgs.zip",
                "source_doi": "10.24432/C5V312",
                "license": "CC BY 4.0",
                "feature_count": 28,
                "probes": probes,
            }
        )
    )
    return registry_path, dataset_identity


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, str]:
    registry_path, dataset_identity = _install_registry(tmp_path)
    monkeypatch.setenv("EVM_S3_CAPACITY_REGISTRY_PATH", str(registry_path))
    clear_capacity_probe_cache()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), dataset_identity


def test_capacity_catalog_and_all_probe_families_are_backward_compatible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, dataset_identity = _client(tmp_path, monkeypatch)

    catalog_response = client.get("/control-panel/v1/scenario-workloads/capacity-probes")

    assert catalog_response.status_code == 200
    catalog = catalog_response.json()
    assert catalog["schema_version"] == "evm.s3_capacity_probe_catalog.v1"
    assert catalog["dataset_identity_sha256"] == dataset_identity
    assert catalog["source_doi"] == "10.24432/C5V312"
    assert catalog["license"] == "CC BY 4.0"
    assert [item["probe_family"] for item in catalog["probes"]] == list(FAMILIES)

    for family in FAMILIES:
        response = client.post(
            "/control-panel/v1/scenario-workloads/capacity-probes/predict",
            json={
                "schema_version": "evm.s3_capacity_probe_request.v1",
                "probe_family": family,
                "dataset_identity_sha256": dataset_identity,
                "features": [0.0] * 28,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["schema_version"] == "evm.s3_capacity_probe_response.v1"
        assert payload["probe_family"] == family
        assert payload["dataset_identity_sha256"] == dataset_identity
        assert len(payload["model_identity_sha256"]) == 64
        assert payload["prediction"] in {0, 1}
        assert 0 <= payload["positive_probability"] <= 1
        assert payload["timings"]["total_ms"] >= payload["timings"]["prediction_ms"]
        assert payload["timings"]["compute_ms"] >= payload["timings"]["prediction_ms"]
        assert payload["timings"]["queue_wait_ms"] >= 0
        assert payload["runtime"] == {
            "api_replica_id": "replica-0",
            "cpu_worker_count": 1,
            "worker_slot": 0,
            "canonical_request_bytes": payload["runtime"]["canonical_request_bytes"],
        }
        assert payload["runtime"]["canonical_request_bytes"] > 0

    try:
        TypeAdapter(WorkloadModelFamily).validate_python("tabular")
    except ValidationError:
        pass
    else:
        raise AssertionError("the existing VLM/LLM lifecycle contract must not widen")


def test_capacity_probe_rejects_wrong_identity_and_invalid_feature_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, dataset_identity = _client(tmp_path, monkeypatch)
    payload = {
        "schema_version": "evm.s3_capacity_probe_request.v1",
        "probe_family": "logistic",
        "dataset_identity_sha256": dataset_identity,
        "features": [0.0] * 28,
    }

    mismatch = client.post(
        "/control-panel/v1/scenario-workloads/capacity-probes/predict",
        json={**payload, "dataset_identity_sha256": "c" * 64},
    )
    wrong_shape = client.post(
        "/control-panel/v1/scenario-workloads/capacity-probes/predict",
        json={**payload, "features": [0.0] * 27},
    )

    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["error"] == "capacity_dataset_identity_mismatch"
    assert wrong_shape.status_code == 422


def test_capacity_probe_fails_closed_when_artifact_bytes_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, dataset_identity = _client(tmp_path, monkeypatch)
    (tmp_path / "logistic.json").write_text("{}", encoding="utf-8")
    clear_capacity_probe_cache()

    response = client.post(
        "/control-panel/v1/scenario-workloads/capacity-probes/predict",
        json={
            "probe_family": "logistic",
            "dataset_identity_sha256": dataset_identity,
            "features": [0.0] * 28,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "capacity_probe_artifact_digest_mismatch"


def test_capacity_executor_returns_bounded_429_with_retry_after(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EVM_S3_CAPACITY_CPU_WORKERS", "1")
    monkeypatch.setenv("EVM_S3_CAPACITY_MAX_OUTSTANDING", "1")
    monkeypatch.setenv("EVM_S3_CAPACITY_ADMISSION_WAIT_SECONDS", "0.01")
    client, dataset_identity = _client(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()
    original = capacity_executor.run_capacity_probe

    def slow_probe(request):
        started.set()
        assert release.wait(timeout=5)
        return original(request)

    monkeypatch.setattr(capacity_executor, "run_capacity_probe", slow_probe)
    payload = {
        "probe_family": "logistic",
        "dataset_identity_sha256": dataset_identity,
        "features": [0.0] * 28,
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        accepted = pool.submit(
            client.post,
            "/control-panel/v1/scenario-workloads/capacity-probes/predict",
            json=payload,
        )
        assert started.wait(timeout=5)
        pressure = client.post(
            "/control-panel/v1/scenario-workloads/capacity-probes/predict",
            json=payload,
        )
        release.set()
        completed = accepted.result(timeout=5)

    assert pressure.status_code == 429
    assert pressure.headers["Retry-After"] == "1"
    assert pressure.json()["detail"]["error"] == "capacity_executor_saturated"
    assert completed.status_code == 200


def test_async_admission_wait_does_not_block_the_event_loop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry_path, dataset_identity = _install_registry(tmp_path)
    monkeypatch.setenv("EVM_S3_CAPACITY_REGISTRY_PATH", str(registry_path))
    monkeypatch.setenv("EVM_S3_CAPACITY_CPU_WORKERS", "1")
    monkeypatch.setenv("EVM_S3_CAPACITY_MAX_OUTSTANDING", "1")
    monkeypatch.setenv("EVM_S3_CAPACITY_ADMISSION_WAIT_SECONDS", "0.05")
    clear_capacity_probe_cache()
    started = threading.Event()
    release = threading.Event()
    original = capacity_executor.run_capacity_probe

    def slow_probe(request):
        started.set()
        assert release.wait(timeout=5)
        return original(request)

    monkeypatch.setattr(capacity_executor, "run_capacity_probe", slow_probe)
    request = CapacityProbeRequest(
        probe_family="logistic",
        dataset_identity_sha256=dataset_identity,
        features=[0.0] * 28,
    )

    async def exercise() -> float:
        accepted = asyncio.create_task(
            capacity_executor.execute_capacity_probe_async(request)
        )
        while not started.is_set():
            await asyncio.sleep(0.001)
        pressure = asyncio.create_task(
            capacity_executor.execute_capacity_probe_async(request)
        )
        ticker_started = time.perf_counter()
        await asyncio.sleep(0.01)
        ticker_elapsed = time.perf_counter() - ticker_started
        with pytest.raises(capacity_executor.CapacityProbeError) as failure:
            await pressure
        assert failure.value.status_code == 429
        release.set()
        await accepted
        return ticker_elapsed

    ticker_elapsed = asyncio.run(exercise())

    assert ticker_elapsed < 0.04


def test_capacity_executor_rejects_oversized_canonical_item_before_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EVM_S3_CAPACITY_MAX_REQUEST_BYTES", "256")
    client, dataset_identity = _client(tmp_path, monkeypatch)

    response = client.post(
        "/control-panel/v1/scenario-workloads/capacity-probes/predict",
        json={
            "probe_family": "logistic",
            "dataset_identity_sha256": dataset_identity,
            "features": [0.0] * 28,
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "capacity_request_too_large"


def test_capacity_executor_timeout_releases_capacity_after_worker_finishes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EVM_S3_CAPACITY_CPU_WORKERS", "1")
    monkeypatch.setenv("EVM_S3_CAPACITY_MAX_OUTSTANDING", "1")
    monkeypatch.setenv("EVM_S3_CAPACITY_REQUEST_TIMEOUT_SECONDS", "0.1")
    client, dataset_identity = _client(tmp_path, monkeypatch)
    original = capacity_executor.run_capacity_probe
    call_count = 0

    def timeout_once(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            time.sleep(0.2)
        return original(request)

    monkeypatch.setattr(capacity_executor, "run_capacity_probe", timeout_once)
    payload = {
        "probe_family": "logistic",
        "dataset_identity_sha256": dataset_identity,
        "features": [0.0] * 28,
    }

    timed_out = client.post(
        "/control-panel/v1/scenario-workloads/capacity-probes/predict",
        json=payload,
    )
    time.sleep(0.15)
    healthy = client.post(
        "/control-panel/v1/scenario-workloads/capacity-probes/predict",
        json=payload,
    )

    assert timed_out.status_code == 504
    assert timed_out.json()["detail"]["error"] == "capacity_execution_timeout"
    assert healthy.status_code == 200
