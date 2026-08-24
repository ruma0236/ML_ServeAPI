from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from evm.model_runtime.serving import (
    ScenarioInferenceRequest,
    ScenarioModelService,
    ScenarioServingConfig,
    create_app,
)


def test_scenario_inference_request_separates_vlm_and_llm_inputs() -> None:
    vlm = ScenarioInferenceRequest(
        model_family="vlm",
        image_uri="file:///F:/image.png",
        image_sha256="a" * 64,
        question="What is shown?",
        choices=["one", "two"],
    )
    llm = ScenarioInferenceRequest(
        model_family="llm",
        instruction="Give a concise answer.",
        context="Bounded local context.",
    )
    assert vlm.choices == ["one", "two"]
    assert llm.max_new_tokens == 32
    assert vlm.deadline_seconds is None


def test_scenario_serving_metrics_keep_exact_identity_out_of_labels(monkeypatch) -> None:
    monkeypatch.setattr(ScenarioModelService, "_load", lambda _self: None)
    monkeypatch.setattr(
        "evm.model_runtime.serving.runtime_inventory",
        lambda: {"torch": "test", "cuda_available": True},
    )
    service = ScenarioModelService(
        ScenarioServingConfig(
            model_family="vlm",
            base_model_dir=Path("base"),
            adapter_dir=Path("adapter"),
            model_repository="generalized/repository",
            model_revision="a" * 40,
            model_artifact_sha256="b" * 64,
            data_identity_sha256="c" * 64,
            source_commit="d" * 40,
            lifecycle_run_id="run-high-cardinality-identity",
            environment="local-staging",
        )
    )

    metrics = generate_latest(service.registry).decode("utf-8")

    assert 'model_family="vlm"' in metrics
    assert 'environment="local-staging"' in metrics
    assert "run-high-cardinality-identity" not in metrics
    assert "b" * 64 not in metrics
    assert 'model_family="vlm"' in metrics


def test_scenario_serving_returns_w3c_trace_header(monkeypatch) -> None:
    monkeypatch.setattr(ScenarioModelService, "_load", lambda _self: None)
    monkeypatch.setattr(
        "evm.model_runtime.serving.runtime_inventory",
        lambda: {"torch": "test", "cuda_available": True},
    )
    service = ScenarioModelService(
        ScenarioServingConfig(
            model_family="llm",
            base_model_dir=Path("base"),
            adapter_dir=Path("adapter"),
            model_repository="generalized/repository",
            model_revision="a" * 40,
            model_artifact_sha256="b" * 64,
            data_identity_sha256="c" * 64,
            source_commit="d" * 40,
            lifecycle_run_id="runtime-control",
        )
    )

    with TestClient(create_app(service)) as client:
        response = client.get(
            "/ready",
            headers={"traceparent": f"00-{'1' * 32}-{'2' * 16}-01"},
        )

    assert response.status_code == 200
    assert response.headers["traceparent"].startswith(f"00-{'1' * 32}-")


def test_scenario_serving_separates_model_and_runtime_source_identity(monkeypatch) -> None:
    monkeypatch.setattr(ScenarioModelService, "_load", lambda _self: None)
    monkeypatch.setattr(
        "evm.model_runtime.serving.runtime_inventory",
        lambda: {"torch": "test", "cuda_available": True},
    )
    service = ScenarioModelService(
        ScenarioServingConfig(
            model_family="vlm",
            base_model_dir=Path("base"),
            adapter_dir=Path("adapter"),
            model_repository="generalized/repository",
            model_revision="a" * 40,
            model_artifact_sha256="b" * 64,
            data_identity_sha256="c" * 64,
            source_commit="d" * 40,
            runtime_source_commit="e" * 40,
            lifecycle_run_id="runtime-control",
        )
    )

    payload = service.ready_payload()

    assert payload["source_commit"] == "d" * 40
    assert payload["model_source_commit"] == "d" * 40
    assert payload["runtime_source_commit"] == "e" * 40
    assert payload["admission"]["policy"]["family"] == "vlm"
    assert payload["admission"]["active_requests"] == 0


def test_scenario_serving_ready_exposes_observed_quantization_runtime(monkeypatch) -> None:
    def load(service: ScenarioModelService) -> None:
        service.quantization_runtime = {
            "requested": "int4_nf4",
            "observed": "int4_nf4",
            "loaded_in_4bit": True,
            "linear_4bit_module_count": 8,
        }

    monkeypatch.setattr(ScenarioModelService, "_load", load)
    monkeypatch.setattr(
        "evm.model_runtime.serving.runtime_inventory",
        lambda: {"torch": "test", "cuda_available": True},
    )
    service = ScenarioModelService(
        ScenarioServingConfig(
            model_family="llm",
            base_model_dir=Path("base"),
            adapter_dir=Path("adapter"),
            model_repository="generalized/repository",
            model_revision="a" * 40,
            model_artifact_sha256="b" * 64,
            data_identity_sha256="c" * 64,
            source_commit="d" * 40,
            lifecycle_run_id="runtime-control",
            quantization="int4_nf4",
        )
    )

    payload = service.ready_payload()

    assert payload["quantization"] == "int4_nf4"
    assert payload["quantization_runtime"] == {
        "requested": "int4_nf4",
        "observed": "int4_nf4",
        "loaded_in_4bit": True,
        "linear_4bit_module_count": 8,
    }
