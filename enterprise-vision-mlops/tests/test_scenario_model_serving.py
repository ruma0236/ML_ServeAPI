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
