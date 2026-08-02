from __future__ import annotations

from pathlib import Path
from typing import Literal

from prometheus_client import CollectorRegistry, Gauge, REGISTRY
from pydantic import BaseModel, ConfigDict, Field, field_validator


SCENARIO_STATES = (
    "planned",
    "baseline_validated",
    "non_disruptive_validated",
    "pending_approval",
    "approved",
    "injecting",
    "detected",
    "contained",
    "recovering",
    "verifying",
    "passed",
    "blocked",
    "failed",
)
ALLOWED_TARGETS = {"production-b0", "staging-b7", "lifecycle-control", "data-artifact"}
ALLOWED_SIGNALS = {
    "gpu_allocatable",
    "device_plugin",
    "deployment_ready",
    "pod_ready",
    "readiness",
    "inference",
    "prometheus",
    "identity",
    "worker_heartbeat",
    "observer_heartbeat",
    "artifact_integrity",
}
ALLOWED_BLOCKERS = {
    "approval_missing",
    "approval_expired",
    "dirty_worktree",
    "identity_mismatch",
    "target_ambiguous",
    "target_missing",
    "target_unhealthy",
    "revision_mismatch",
    "rollback_missing",
    "cooldown_active",
    "live_proof_not_run",
    "integrity_admission_missing",
    "integrity_admission_malformed",
    "integrity_admission_stale",
    "integrity_admission_signature_invalid",
    "integrity_admission_identity_mismatch",
    "integrity_admission_evidence_mismatch",
}


class OperationalMetricProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.operational_metrics.v1"]
    scenario: Literal["A", "B", "C", "D", "E", "CROSS"]
    target: str
    state: str
    signals: dict[str, bool]
    detection_seconds: float | None = Field(default=None, ge=0)
    containment_seconds: float | None = Field(default=None, ge=0)
    recovery_seconds: float | None = Field(default=None, ge=0)
    validation_result: Literal["not_run", "passed", "failed", "blocked"]
    blockers: list[str] = Field(default_factory=list)

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        if value not in ALLOWED_TARGETS:
            raise ValueError(f"unsupported low-cardinality target: {value}")
        return value

    @field_validator("state")
    @classmethod
    def validate_state(cls, value: str) -> str:
        if value not in SCENARIO_STATES:
            raise ValueError(f"unsupported scenario state: {value}")
        return value

    @field_validator("signals")
    @classmethod
    def validate_signals(cls, value: dict[str, bool]) -> dict[str, bool]:
        invalid = sorted(set(value) - ALLOWED_SIGNALS)
        if invalid:
            raise ValueError(f"unsupported health signals: {invalid}")
        return value

    @field_validator("blockers")
    @classmethod
    def validate_blockers(cls, value: list[str]) -> list[str]:
        invalid = sorted(set(value) - ALLOWED_BLOCKERS)
        if invalid:
            raise ValueError(f"unsupported blocker codes: {invalid}")
        return value


class OperationalMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self.state = Gauge(
            "evm_operational_scenario_state",
            "Current operational scenario state as a one-hot gauge.",
            ["scenario", "target", "state"],
            registry=registry,
        )
        self.target_health = Gauge(
            "evm_operational_target_health",
            "Current target-scoped operational health signal.",
            ["scenario", "target", "signal"],
            registry=registry,
        )
        self.detection = Gauge(
            "evm_operational_detection_seconds",
            "Measured injection-to-detection duration.",
            ["scenario", "target"],
            registry=registry,
        )
        self.containment = Gauge(
            "evm_operational_containment_seconds",
            "Measured injection-to-containment duration.",
            ["scenario", "target"],
            registry=registry,
        )
        self.recovery = Gauge(
            "evm_operational_recovery_seconds",
            "Measured injection-to-recovery duration.",
            ["scenario", "target"],
            registry=registry,
        )
        self.validation = Gauge(
            "evm_operational_validation_total",
            "Latest operational validation result as one-hot gauges.",
            ["scenario", "result"],
            registry=registry,
        )
        self.blockers = Gauge(
            "evm_operational_active_blockers",
            "Current operational blocker presence.",
            ["scenario", "code"],
            registry=registry,
        )

    def update(self, projection: OperationalMetricProjection) -> None:
        self.state.clear()
        self.target_health.clear()
        self.detection.clear()
        self.containment.clear()
        self.recovery.clear()
        self.validation.clear()
        self.blockers.clear()

        for state in SCENARIO_STATES:
            self.state.labels(
                scenario=projection.scenario,
                target=projection.target,
                state=state,
            ).set(1 if state == projection.state else 0)
        for signal, healthy in projection.signals.items():
            self.target_health.labels(
                scenario=projection.scenario,
                target=projection.target,
                signal=signal,
            ).set(1 if healthy else 0)
        for metric, value in (
            (self.detection, projection.detection_seconds),
            (self.containment, projection.containment_seconds),
            (self.recovery, projection.recovery_seconds),
        ):
            if value is not None:
                metric.labels(scenario=projection.scenario, target=projection.target).set(value)
        for result in ("not_run", "passed", "failed", "blocked"):
            self.validation.labels(scenario=projection.scenario, result=result).set(
                1 if result == projection.validation_result else 0
            )
        for blocker in projection.blockers:
            self.blockers.labels(scenario=projection.scenario, code=blocker).set(1)


def load_metric_projection(path: Path) -> OperationalMetricProjection:
    return OperationalMetricProjection.model_validate_json(path.read_text(encoding="utf-8"))
