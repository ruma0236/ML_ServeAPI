from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from evm.control_panel.schemas import ContractModel


RuntimeChildState = Literal[
    "starting",
    "live",
    "suspect",
    "recovering",
    "backoff",
    "blocked",
    "circuit_open",
    "disabled",
]


class HostRuntimeChildHealth(ContractModel):
    name: Literal["lifecycle_worker", "kubernetes_observer"]
    status: RuntimeChildState
    reason: str | None = None
    pid: int | None = None
    process_count: int = Field(default=0, ge=0)
    exact_identity: bool = False
    heartbeat_age_seconds: float | None = Field(default=None, ge=0)
    revision_matches: bool = False
    lease_matches: bool = False
    fencing_matches: bool = False
    source_commit: str | None = None
    process_instance_id: str | None = None
    incident_fingerprint: str | None = None


class HostRuntimeSupervisorHealth(ContractModel):
    schema_version: Literal["evm.host_runtime_health.v1"] = "evm.host_runtime_health.v1"
    status: Literal["healthy", "degraded", "stale", "unavailable"]
    message: str | None = None
    supervisor_pid: int | None = None
    supervisor_started_at: str | None = None
    source_commit: str | None = None
    source_branch: str | None = None
    lease_id: str | None = None
    fencing_token: int | None = None
    last_seen_at: str | None = None
    heartbeat_age_seconds: float | None = Field(default=None, ge=0)
    check_interval_seconds: float | None = Field(default=None, gt=0)
    heartbeat_stale_seconds: float | None = Field(default=None, gt=0)
    children: list[HostRuntimeChildHealth] = Field(default_factory=list)
    restart_counts: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    snapshot_uri: str


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def supervisor_snapshot_path() -> Path:
    return Path(
        os.getenv(
            "EVM_HOST_RUNTIME_SUPERVISOR_PATH",
            "/app/artifacts/w7/host_runtime/supervisor.json",
        )
    )


def read_host_runtime_supervisor(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    stale_after_seconds: float | None = None,
) -> HostRuntimeSupervisorHealth:
    source = path or supervisor_snapshot_path()
    stale_limit = stale_after_seconds
    if stale_limit is None:
        try:
            stale_limit = max(
                1.0,
                float(os.getenv("EVM_HOST_RUNTIME_SUPERVISOR_STALE_SECONDS", "15")),
            )
        except ValueError:
            stale_limit = 15.0
    if not source.is_file():
        return HostRuntimeSupervisorHealth(
            status="unavailable",
            message="host runtime supervisor snapshot is missing",
            snapshot_uri=str(source),
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        observed_at = parse_utc(payload.get("last_seen_at"))
        if observed_at is None:
            raise ValueError("supervisor_last_seen_at_invalid")
        age = ((now or datetime.now(UTC)) - observed_at).total_seconds()
        if age < -5:
            raise ValueError("supervisor_heartbeat_from_future")
        age = max(0.0, age)
        children = [HostRuntimeChildHealth.model_validate(item) for item in payload["children"]]
        status = str(payload.get("status") or "degraded")
        if age > stale_limit:
            status = "stale"
        elif status == "healthy" and any(
            item.status not in {"live", "disabled"} for item in children
        ):
            status = "degraded"
        return HostRuntimeSupervisorHealth(
            status=status,
            message=None,
            supervisor_pid=payload.get("supervisor_pid"),
            supervisor_started_at=payload.get("supervisor_started_at"),
            source_commit=payload.get("source_commit"),
            source_branch=payload.get("source_branch"),
            lease_id=payload.get("lease_id"),
            fencing_token=payload.get("fencing_token"),
            last_seen_at=payload.get("last_seen_at"),
            heartbeat_age_seconds=age,
            check_interval_seconds=payload.get("check_interval_seconds"),
            heartbeat_stale_seconds=payload.get("heartbeat_stale_seconds"),
            children=children,
            restart_counts={
                str(key): max(0, int(value))
                for key, value in (payload.get("restart_counts") or {}).items()
            },
            errors=[str(value) for value in payload.get("errors") or []],
            snapshot_uri=str(source),
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return HostRuntimeSupervisorHealth(
            status="unavailable",
            message=f"host runtime supervisor snapshot is invalid: {exc}",
            snapshot_uri=str(source),
        )
