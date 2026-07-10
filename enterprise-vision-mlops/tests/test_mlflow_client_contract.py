from __future__ import annotations

from evm.core.mlflow_client import MlflowRestClient


def test_log_metric_sends_mlflow_timestamp_and_returns_success(monkeypatch):
    captured = {}

    def fake_request(method, url, payload=None, timeout=5):
        captured.update({"method": method, "url": url, "payload": payload, "timeout": timeout})
        return 200, {}

    monkeypatch.setattr("evm.core.mlflow_client.request_json", fake_request)
    client = MlflowRestClient("http://mlflow:5000/")

    assert client.log_metric("run-1", "f1", 0.81, step=3, timestamp_ms=123456789) is True
    assert captured["url"] == "http://mlflow:5000/api/2.0/mlflow/runs/log-metric"
    assert captured["payload"] == {
        "run_id": "run-1",
        "key": "f1",
        "value": 0.81,
        "timestamp": 123456789,
        "step": 3,
    }


def test_mlflow_write_methods_surface_http_failure(monkeypatch):
    monkeypatch.setattr(
        "evm.core.mlflow_client.request_json",
        lambda *_args, **_kwargs: (500, {"error": "failed"}),
    )
    client = MlflowRestClient("http://mlflow:5000")

    assert client.log_param("run-1", "dataset_version", "v1") is False
    assert client.log_metric("run-1", "f1", 0.5) is False
    assert client.terminate_run("run-1") is False
