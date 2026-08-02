from __future__ import annotations

import json
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from pydantic import ValidationError

from evm.operations.metrics import OperationalMetricProjection, OperationalMetrics


ROOT = Path(__file__).parents[1]


def _projection() -> OperationalMetricProjection:
    return OperationalMetricProjection(
        schema_version="evm.operational_metrics.v1",
        scenario="A",
        target="production-b0",
        state="baseline_validated",
        signals={
            "gpu_allocatable": True,
            "device_plugin": True,
            "deployment_ready": True,
            "pod_ready": True,
            "readiness": True,
            "prometheus": True,
            "identity": True,
        },
        validation_result="not_run",
        blockers=[],
    )


def test_metrics_use_only_low_cardinality_labels() -> None:
    registry = CollectorRegistry()
    metrics = OperationalMetrics(registry)
    metrics.update(_projection())
    rendered = generate_latest(registry).decode("utf-8")

    assert 'target="production-b0"' in rendered
    assert 'signal="identity"' in rendered
    for forbidden in ("run_id", "pod_name", "digest", "artifact_uri"):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target", "pod-uid-123"),
        ("state", "random-state"),
        ("signals", {"pod_uid_123": True}),
        ("blockers", ["free-form-error-123"]),
    ],
)
def test_projection_rejects_unbounded_vocabulary(field: str, value: object) -> None:
    payload = _projection().model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        OperationalMetricProjection.model_validate(payload)


def test_prometheus_rule_and_dashboard_contracts_are_versioned() -> None:
    prometheus = (ROOT / "monitoring" / "prometheus" / "prometheus.yml").read_text()
    rules = (
        ROOT / "monitoring" / "prometheus" / "rules" / "operational-reliability.yml"
    ).read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    dashboard = json.loads(
        (
            ROOT
            / "monitoring"
            / "grafana"
            / "dashboards"
            / "operational-reliability.json"
        ).read_text()
    )

    assert "/etc/prometheus/rules/*.yml" in prometheus
    assert "./monitoring/prometheus/rules:/etc/prometheus/rules:ro" in compose
    for alert in (
        "EVMOperationalGPUAllocatableLost",
        "EVMOperationalDevicePluginUnavailable",
        "EVMOperationalServingTargetDown",
        "EVMOperationalIdentityMismatch",
        "EVMOperationalRecoveryBudgetExceeded",
        "EVMHostRuntimeSupervisorUnhealthy",
        "EVMHostRuntimeChildNotLive",
        "EVMHostRuntimeHeartbeatStale",
        "EVMHostRuntimeRevisionMismatch",
        "EVMHostRuntimeProcessCountInvalid",
        "EVMDataArtifactIntegrityBlocked",
    ):
        assert alert in rules
    assert dashboard["uid"] == "evm-operational-reliability"
    assert {panel["title"] for panel in dashboard["panels"]} == {
        "Exact Target Health",
        "Scenario State",
        "Detection And Recovery Seconds",
        "Active Blockers",
        "Validation Result",
        "Runtime Supervisor",
        "Worker And Observer State",
        "Heartbeat Age",
        "Process And Restart Counts",
    }
