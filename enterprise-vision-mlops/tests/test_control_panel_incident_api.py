from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from prometheus_client import generate_latest

from apps.api.control_panel_incidents import (
    get_guard_incident,
    list_guard_incidents,
    list_recovery_actions,
    list_recovery_owners,
    router,
)
from evm.operations.recovery_coordination import (
    IncidentPlaneRecord,
    IncidentPlaneSnapshot,
    IncidentTiming,
    write_incident_plane_snapshot,
)


NOW = datetime.now(UTC)
REVISION = "c0bf42277ec4e227b9a38e0326e638eada736026"


def snapshot() -> IncidentPlaneSnapshot:
    return IncidentPlaneSnapshot(
        status="live",
        generated_at_utc=NOW,
        source_revision=REVISION,
        policy_version="recovery-coordination-v1",
        incidents=[
            IncidentPlaneRecord(
                incident_id="inc-api-current-0001",
                correlation_id="correlation-api-current-0001",
                state="recovery_owned",
                root_fingerprint="f" * 64,
                event_count=2,
                causal_edge_count=1,
                target_class="production-b0",
                target_identity_digest="a" * 64,
                owner_id="scenario-a-controller",
                fencing_token=3,
                lease_expires_at_utc=NOW + timedelta(seconds=20),
                authorized_recommendation_count=1,
                timing=IncidentTiming(
                    collection_delay_ms=5000,
                    correlation_overhead_ms=31.2,
                    recovery_seconds=10.1,
                ),
                child_evidence_uris=["F:/evidence/scenario-a.json"],
                created_at_utc=NOW - timedelta(seconds=6),
                updated_at_utc=NOW,
            )
        ],
        leases=[],
        actions=[],
        blocked_decision_count=2,
        active_blockers=["approval_expired"],
        evidence_root="F:/evidence/recovery-proof",
    )


def test_incident_routes_expose_read_only_snapshot(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "incident-plane.json"
    write_incident_plane_snapshot(source, snapshot())
    monkeypatch.setenv("EVM_INCIDENT_PLANE_SNAPSHOT_PATH", str(source))
    monkeypatch.setenv("EVM_INCIDENT_PLANE_MAX_AGE_SECONDS", "300")

    catalog = list_guard_incidents()
    incident = get_guard_incident("inc-api-current-0001")
    owners = list_recovery_owners()
    actions = list_recovery_actions()

    assert catalog.status == "live"
    assert catalog.mutation_endpoint_available is False
    assert incident.timing.correlation_overhead_ms == 31.2
    assert incident.child_evidence_uris == ["F:/evidence/scenario-a.json"]
    assert owners.source_revision == REVISION
    assert actions.actions == []
    assert all(route.methods <= {"GET", "HEAD"} for route in router.routes)


def test_incident_route_marks_old_snapshot_stale(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "incident-plane.json"
    old = snapshot().model_copy(update={"generated_at_utc": NOW - timedelta(minutes=10)})
    write_incident_plane_snapshot(source, old)
    monkeypatch.setenv("EVM_INCIDENT_PLANE_SNAPSHOT_PATH", str(source))
    monkeypatch.setenv("EVM_INCIDENT_PLANE_MAX_AGE_SECONDS", "60")

    assert list_guard_incidents().status == "stale"


def test_missing_snapshot_is_explicitly_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_INCIDENT_PLANE_SNAPSHOT_PATH", str(tmp_path / "missing.json"))

    payload = list_guard_incidents()
    assert payload.status == "unavailable"
    assert payload.active_blockers == ["snapshot_missing"]
    assert payload.mutation_endpoint_available is False


def test_unknown_incident_returns_404(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "incident-plane.json"
    write_incident_plane_snapshot(source, snapshot())
    monkeypatch.setenv("EVM_INCIDENT_PLANE_SNAPSHOT_PATH", str(source))

    with pytest.raises(HTTPException) as missing:
        get_guard_incident("inc-does-not-exist")
    assert missing.value.status_code == 404


def test_incident_metrics_are_low_cardinality_and_read_only(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "incident-plane.json"
    write_incident_plane_snapshot(source, snapshot())
    monkeypatch.setenv("EVM_INCIDENT_PLANE_SNAPSHOT_PATH", str(source))
    list_guard_incidents()

    metrics = generate_latest().decode("utf-8")
    assert 'evm_guard_incident_state{state="recovery_owned"} 1.0' in metrics
    assert 'evm_guard_recovery_blocked_decision_count{code="approval_expired"} 1.0' in metrics
    assert "evm_guard_incident_mutation_endpoint_available 0.0" in metrics
    assert "inc-api-current-0001" not in metrics
