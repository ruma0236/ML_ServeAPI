from __future__ import annotations

import time
from typing import Any

from evm.core.http import request_json
from evm.observability.otel import trace_span
from evm.observability.trace_context import TraceContextError, W3CTraceContext


class MlflowRestClient:
    def __init__(
        self,
        tracking_uri: str,
        *,
        traceparent: str | None = None,
        tracestate: str | None = None,
    ) -> None:
        self.tracking_uri = tracking_uri.rstrip("/")
        try:
            self.trace_context = (
                W3CTraceContext.parse(traceparent, tracestate=tracestate)
                if traceparent
                else None
            )
        except TraceContextError:
            self.trace_context = None

    def _request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | list[Any] | str]:
        with trace_span(
            "mlflow.rest",
            parent=self.trace_context,
            kind="client",
            attributes={"evm.stage": "mlflow", "http.request.method": method},
        ):
            return request_json(method, url, payload)

    def health(self) -> bool:
        status, _ = self._request("GET", f"{self.tracking_uri}/health")
        return status == 200

    def get_or_create_experiment(self, name: str) -> str | None:
        status, payload = self._request(
            "GET",
            f"{self.tracking_uri}/api/2.0/mlflow/experiments/get-by-name?experiment_name={name}",
        )
        if status == 200 and isinstance(payload, dict):
            experiment = payload.get("experiment", {})
            return experiment.get("experiment_id")

        status, payload = self._request(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/experiments/create",
            {"name": name},
        )
        if status == 200 and isinstance(payload, dict):
            return payload.get("experiment_id")
        return None

    def create_run(
        self,
        experiment_id: str,
        run_name: str,
        *,
        tags: dict[str, str] | None = None,
    ) -> str | None:
        body: dict[str, Any] = {"experiment_id": experiment_id, "run_name": run_name}
        if tags:
            body["tags"] = [
                {"key": key, "value": str(value)}
                for key, value in sorted(tags.items())
            ]
        status, payload = self._request(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/runs/create",
            body,
        )
        if status == 200 and isinstance(payload, dict):
            return payload.get("run", {}).get("info", {}).get("run_id")
        return None

    def log_param(self, run_id: str, key: str, value: Any) -> bool:
        status, _ = self._request(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/runs/log-parameter",
            {"run_id": run_id, "key": key, "value": str(value)},
        )
        return status == 200

    def log_metric(
        self,
        run_id: str,
        key: str,
        value: float,
        step: int = 0,
        timestamp_ms: int | None = None,
    ) -> bool:
        status, _ = self._request(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/runs/log-metric",
            {
                "run_id": run_id,
                "key": key,
                "value": float(value),
                "timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
                "step": step,
            },
        )
        return status == 200

    def terminate_run(self, run_id: str, status: str = "FINISHED") -> bool:
        response_status, _ = self._request(
            "POST",
            f"{self.tracking_uri}/api/2.0/mlflow/runs/update",
            {"run_id": run_id, "status": status},
        )
        return response_status == 200
