from __future__ import annotations

import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from prometheus_client import Gauge
from pydantic import Field

from evm.operations.correlation import REVISION_PATTERN, StrictModel
from evm.operations.recovery_coordination import (
    IncidentPlaneRecord,
    IncidentPlaneSnapshot,
    RecoveryActionRecord,
    RecoveryLease,
)


router = APIRouter(prefix="/control-panel/v1", tags=["guard-incidents"])
_METRIC_LOCK = Lock()

INCIDENT_STATE = Gauge(
    "evm_guard_incident_state",
    "Current guard incident count grouped by bounded state.",
    ["state"],
)
RECOVERY_OWNER = Gauge(
    "evm_guard_recovery_owner_active",
    "Active recovery owner count grouped by bounded target class.",
    ["target_class"],
)
RECOVERY_RECOMMENDATION = Gauge(
    "evm_guard_recovery_recommendation_count",
    "Authorized non-mutating recovery recommendation count grouped by action.",
    ["action"],
)
RECOVERY_BLOCKER = Gauge(
    "evm_guard_recovery_blocked_decision_count",
    "Latest fail-closed recovery blocker presence grouped by bounded code.",
    ["code"],
)
INCIDENT_SNAPSHOT_AGE = Gauge(
    "evm_guard_incident_snapshot_age_seconds",
    "Age of the latest read-only guard incident snapshot.",
)
INCIDENT_MUTATION_ENDPOINT = Gauge(
    "evm_guard_incident_mutation_endpoint_available",
    "Whether the guard incident API exposes a mutation endpoint; fixed to zero.",
)


class RecoveryLeaseList(StrictModel):
    schema_version: Literal["evm.recovery_lease_list.v1"] = "evm.recovery_lease_list.v1"
    status: Literal["live", "stale", "unavailable"]
    generated_at_utc: datetime
    source_revision: str = Field(pattern=REVISION_PATTERN)
    leases: list[RecoveryLease]


class RecoveryActionList(StrictModel):
    schema_version: Literal["evm.recovery_action_list.v1"] = "evm.recovery_action_list.v1"
    status: Literal["live", "stale", "unavailable"]
    generated_at_utc: datetime
    source_revision: str = Field(pattern=REVISION_PATTERN)
    actions: list[RecoveryActionRecord]


def incident_plane_snapshot_path() -> Path:
    configured = os.getenv("EVM_INCIDENT_PLANE_SNAPSHOT_PATH", "").strip()
    if configured:
        return Path(configured)
    data_root = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    return (
        Path(data_root)
        / "artifacts"
        / "operations"
        / "lifecycle_guard_validation"
        / "_latest"
        / "incident-plane.json"
    )


def incident_plane_max_age_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("EVM_INCIDENT_PLANE_MAX_AGE_SECONDS", "60")))
    except ValueError:
        return 60.0


def empty_incident_plane() -> IncidentPlaneSnapshot:
    return IncidentPlaneSnapshot(
        status="unavailable",
        generated_at_utc=datetime.now(UTC),
        source_revision="0000000",
        policy_version="unavailable",
        incidents=[],
        leases=[],
        actions=[],
        active_blockers=["snapshot_missing"],
    )


def load_incident_plane_snapshot(path: Path | None = None) -> IncidentPlaneSnapshot:
    source = path or incident_plane_snapshot_path()
    if not source.is_file():
        return empty_incident_plane()
    snapshot = IncidentPlaneSnapshot.model_validate_json(source.read_text(encoding="utf-8"))
    age_seconds = max(0.0, (datetime.now(UTC) - snapshot.generated_at_utc).total_seconds())
    if age_seconds > incident_plane_max_age_seconds() and snapshot.status == "live":
        snapshot = snapshot.model_copy(update={"status": "stale"})
    return snapshot


def refresh_incident_plane_metrics(snapshot: IncidentPlaneSnapshot | None = None) -> None:
    current = snapshot or load_incident_plane_snapshot()
    now = datetime.now(UTC)
    with _METRIC_LOCK:
        INCIDENT_STATE.clear()
        RECOVERY_OWNER.clear()
        RECOVERY_RECOMMENDATION.clear()
        RECOVERY_BLOCKER.clear()
        incident_counts = Counter(item.state for item in current.incidents)
        owner_counts = Counter(
            item.target.target_class
            for item in current.leases
            if item.state == "active" and item.expires_at_utc > now
        )
        action_counts = Counter(item.action for item in current.actions)
        for state, count in incident_counts.items():
            INCIDENT_STATE.labels(state=state).set(count)
        for target_class, count in owner_counts.items():
            RECOVERY_OWNER.labels(target_class=target_class).set(count)
        for action, count in action_counts.items():
            RECOVERY_RECOMMENDATION.labels(action=action).set(count)
        for blocker in current.active_blockers:
            RECOVERY_BLOCKER.labels(code=blocker).set(1)
        INCIDENT_SNAPSHOT_AGE.set(
            max(0.0, (now - current.generated_at_utc).total_seconds())
        )
        INCIDENT_MUTATION_ENDPOINT.set(0)


@router.get("/guard-incidents", response_model=IncidentPlaneSnapshot)
def list_guard_incidents(
    state: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> IncidentPlaneSnapshot:
    try:
        snapshot = load_incident_plane_snapshot()
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "incident_snapshot_invalid", "message": str(exc)},
        ) from exc
    incidents = snapshot.incidents
    if state:
        incidents = [item for item in incidents if item.state == state]
    result = snapshot.model_copy(update={"incidents": incidents[:limit]})
    refresh_incident_plane_metrics(result)
    return result


@router.get("/guard-incidents/{incident_id}", response_model=IncidentPlaneRecord)
def get_guard_incident(incident_id: str) -> IncidentPlaneRecord:
    snapshot = list_guard_incidents(limit=500)
    for incident in snapshot.incidents:
        if incident.incident_id == incident_id:
            return incident
    raise HTTPException(
        status_code=404,
        detail={"error": "guard_incident_not_found", "incident_id": incident_id},
    )


@router.get("/recovery-owners", response_model=RecoveryLeaseList)
def list_recovery_owners() -> RecoveryLeaseList:
    snapshot = list_guard_incidents(limit=500)
    return RecoveryLeaseList(
        status=snapshot.status,
        generated_at_utc=snapshot.generated_at_utc,
        source_revision=snapshot.source_revision,
        leases=snapshot.leases,
    )


@router.get("/recovery-actions", response_model=RecoveryActionList)
def list_recovery_actions() -> RecoveryActionList:
    snapshot = list_guard_incidents(limit=500)
    return RecoveryActionList(
        status=snapshot.status,
        generated_at_utc=snapshot.generated_at_utc,
        source_revision=snapshot.source_revision,
        actions=snapshot.actions,
    )
