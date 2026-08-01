from __future__ import annotations

from pathlib import Path


SUPERVISOR = Path("scripts/dev/start_host_runtime_supervisor.ps1")
WORKER = Path("scripts/dev/start_lifecycle_worker.ps1")
OBSERVER = Path("scripts/dev/start_kubernetes_observer.ps1")


def test_supervisor_restarts_stale_children_and_records_revision() -> None:
    script = SUPERVISOR.read_text(encoding="utf-8")

    assert 'schema_version = "evm.host_runtime_supervisor.v1"' in script
    assert 'HeartbeatProperty "observed_at"' in script
    assert 'HeartbeatProperty "last_seen_at"' in script
    assert 'CommandMarker "evm.control_panel.kubernetes_observer"' in script
    assert 'CommandMarker "evm.control_panel.lifecycle_worker"' in script
    assert "source_commit = $commit" in script
    assert "revision_matches" in script
    assert "Start-ChildRuntime" in script
    assert "restart_counts" in script


def test_host_launchers_only_terminate_owned_processes_and_wait_for_fresh_state() -> None:
    worker = WORKER.read_text(encoding="utf-8")
    observer = OBSERVER.read_text(encoding="utf-8")

    assert "Get-OwnedWorkerProcess" in worker
    assert "*evm.control_panel.lifecycle_worker*" in worker
    assert "revision-matched heartbeat" in worker
    assert "$payload.source_commit -eq $env:EVM_GIT_COMMIT" in worker
    assert "Get-OwnedObserverProcess" in observer
    assert "*evm.control_panel.kubernetes_observer*" in observer
    assert "fresh snapshot within 15 seconds" in observer
