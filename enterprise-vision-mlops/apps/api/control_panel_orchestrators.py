from __future__ import annotations

import os
from datetime import UTC, datetime

import requests
from fastapi import APIRouter

from evm.control_panel.kubernetes_observer import load_kubernetes_resource_snapshot
from evm.control_panel.schemas import (
    OrchestratorConnection,
    OrchestratorConnectionList,
    State,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-orchestrators"])


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def http_connection(
    *,
    orchestrator: str,
    mode: str,
    control_mode: str,
    base_url: str,
    health_path: str,
    supported_actions: list[str],
    auth: tuple[str, str] | None = None,
) -> OrchestratorConnection:
    checked_at = utc_now()
    blockers: list[str] = []
    notes: str | None = None
    status: State = "blocked"
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}{health_path}",
            auth=auth,
            timeout=3,
        )
        if response.ok:
            status = "pass"
            notes = f"HTTP {response.status_code}"
            if orchestrator == "airflow":
                payload = response.json()
                unhealthy = [
                    key
                    for key in ("metadatabase", "scheduler")
                    if payload.get(key, {}).get("status") != "healthy"
                ]
                if unhealthy:
                    status = "warn"
                    blockers = [f"airflow_{key}_unhealthy" for key in unhealthy]
                    notes = ", ".join(blockers)
        else:
            blockers = [f"{orchestrator}_http_{response.status_code}"]
            notes = response.text[:160] or blockers[0]
    except (requests.RequestException, ValueError) as exc:
        blockers = [f"{orchestrator}_connection_failed"]
        notes = f"{type(exc).__name__}: {exc}"
    return OrchestratorConnection(
        orchestrator=orchestrator,
        mode=mode,
        control_mode=control_mode,
        status=status,
        base_url=base_url,
        supported_actions=supported_actions,
        notes=notes,
        checked_at=checked_at,
        blockers=blockers,
    )


@router.get("/orchestrators", response_model=OrchestratorConnectionList)
def list_orchestrators() -> OrchestratorConnectionList:
    checked_at = utc_now()
    airflow_url = os.getenv("EVM_AIRFLOW_API_URL", "http://airflow-webserver:8080/api/v1")
    mlflow_url = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
    airflow = http_connection(
        orchestrator="airflow",
        mode="external-compose",
        control_mode="rest-api",
        base_url=airflow_url,
        health_path="/health",
        auth=(
            os.getenv("EVM_AIRFLOW_API_USERNAME", "admin"),
            os.getenv("EVM_AIRFLOW_API_PASSWORD", "admin"),
        ),
        supported_actions=["list_dags", "trigger_dag", "observe_dag_run"],
    )
    mlflow = http_connection(
        orchestrator="mlflow",
        mode="compose-tracking-server",
        control_mode="read-only",
        base_url=mlflow_url,
        health_path="/health",
        supported_actions=["list_runs", "inspect_artifacts", "inspect_registry"],
    )
    snapshot = load_kubernetes_resource_snapshot()
    kubernetes_status: State = {
        "live": "pass",
        "stale": "warn",
        "projected": "warn",
        "unavailable": "blocked",
    }[snapshot.observation_status]
    kubernetes = OrchestratorConnection(
        orchestrator="kubernetes",
        mode="docker-desktop",
        control_mode="cli-bridge",
        status=kubernetes_status,
        namespace="_cluster",
        supported_actions=["inspect_resources", "submit_guarded_job_intent"],
        notes=snapshot.observation_message,
        checked_at=checked_at,
        blockers=[] if kubernetes_status == "pass" else [f"kubernetes_{snapshot.observation_status}"],
    )
    orchestrators = [airflow, mlflow, kubernetes]
    overall: State = "blocked" if any(item.status == "blocked" for item in orchestrators) else (
        "warn" if any(item.status == "warn" for item in orchestrators) else "pass"
    )
    return OrchestratorConnectionList(
        orchestrators=orchestrators,
        checked_at=checked_at,
        status=overall,
    )
