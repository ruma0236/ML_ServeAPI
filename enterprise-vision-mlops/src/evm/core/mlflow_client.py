from __future__ import annotations

from typing import Any

from evm.core.http import request_json


class MlflowRestClient:
    def __init__(self, tracking_uri: str) -> None:
        self.tracking_uri = tracking_uri.rstrip("/")

    def health(self) -> bool:
        status, _ = request_json("GET", f"{self.tracking_uri}/health")
        return status == 200

    def get_or_create_experiment(self, name: str) -> str | None:
        status, payload = request_json(
            "GET",
            f"{self.tracking_uri}/api/2.0/mlflow/experiments/get-by-name?experiment_name={name}",
        )
        if status == 200 and isinstance(payload, dict):
            experiment = payload.get("experiment", {})
            return experiment.get("experiment_id")

        status, payload = request_json(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/experiments/create",
            {"name": name},
        )
        if status == 200 and isinstance(payload, dict):
            return payload.get("experiment_id")
        return None

    def create_run(self, experiment_id: str, run_name: str) -> str | None:
        status, payload = request_json(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/runs/create",
            {"experiment_id": experiment_id, "run_name": run_name},
        )
        if status == 200 and isinstance(payload, dict):
            return payload.get("run", {}).get("info", {}).get("run_id")
        return None

    def log_param(self, run_id: str, key: str, value: Any) -> None:
        request_json(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/runs/log-parameter",
            {"run_id": run_id, "key": key, "value": str(value)},
        )

    def log_metric(self, run_id: str, key: str, value: float, step: int = 0) -> None:
        request_json(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/runs/log-metric",
            {"run_id": run_id, "key": key, "value": float(value), "step": step},
        )

    def terminate_run(self, run_id: str, status: str = "FINISHED") -> None:
        request_json(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/runs/update",
            {"run_id": run_id, "status": status},
        )
