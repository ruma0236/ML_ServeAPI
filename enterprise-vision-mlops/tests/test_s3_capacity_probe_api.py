from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from apps.api.control_panel_workloads import router
from evm.control_panel.scenario_workloads import WorkloadModelFamily
from evm.model_runtime.capacity_probe import clear_capacity_probe_cache


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
