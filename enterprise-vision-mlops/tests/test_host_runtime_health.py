from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evm.control_panel.host_runtime import read_host_runtime_supervisor


NOW = datetime(2026, 8, 2, 7, 0, tzinfo=timezone.utc)


def payload(*, observed_at: datetime = NOW, child_status: str = "live") -> dict:
    return {
        "schema_version": "evm.host_runtime_supervisor.v1",
        "status": "healthy",
        "supervisor_pid": 100,
        "supervisor_started_at": (NOW - timedelta(minutes=5)).isoformat(),
        "source_commit": "a" * 40,
        "source_branch": "codex/mac-mini-worker",
        "lease_id": "lease-12345678",
        "fencing_token": 4,
        "last_seen_at": observed_at.isoformat(),
        "check_interval_seconds": 5,
        "heartbeat_stale_seconds": 20,
        "children": [
            {
                "name": name,
                "status": child_status,
                "reason": "healthy" if child_status == "live" else "heartbeat_stale",
                "pid": 101 + index,
                "process_count": 1,
                "exact_identity": True,
                "heartbeat_age_seconds": 1,
                "revision_matches": True,
                "lease_matches": True,
                "fencing_matches": True,
                "source_commit": "a" * 40,
                "process_instance_id": f"instance-{index}-12345678",
                "incident_fingerprint": "f" * 64,
            }
            for index, name in enumerate(("kubernetes_observer", "lifecycle_worker"))
        ],
        "restart_counts": {"kubernetes_observer": 1, "lifecycle_worker": 2},
        "errors": [],
    }


def test_missing_supervisor_snapshot_is_unavailable(tmp_path: Path) -> None:
    observed = read_host_runtime_supervisor(tmp_path / "missing.json", now=NOW)
    assert observed.status == "unavailable"


def test_exact_live_children_are_healthy(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    observed = read_host_runtime_supervisor(path, now=NOW + timedelta(seconds=2))
    assert observed.status == "healthy"
    assert observed.heartbeat_age_seconds == 2
    assert [item.status for item in observed.children] == ["live", "live"]
    assert observed.restart_counts["lifecycle_worker"] == 2


def test_stale_supervisor_never_reports_healthy(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    observed = read_host_runtime_supervisor(
        path,
        now=NOW + timedelta(seconds=16),
        stale_after_seconds=15,
    )
    assert observed.status == "stale"


def test_non_live_child_forces_degraded_status(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps(payload(child_status="blocked")), encoding="utf-8")
    observed = read_host_runtime_supervisor(path, now=NOW)
    assert observed.status == "degraded"
    assert all(item.reason == "heartbeat_stale" for item in observed.children)


def test_future_or_malformed_snapshot_fails_closed(tmp_path: Path) -> None:
    future = tmp_path / "future.json"
    future.write_text(json.dumps(payload(observed_at=NOW + timedelta(seconds=10))), encoding="utf-8")
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    assert read_host_runtime_supervisor(future, now=NOW).status == "unavailable"
    assert read_host_runtime_supervisor(invalid, now=NOW).status == "unavailable"
