from __future__ import annotations

from pathlib import Path


SUPERVISOR = Path("scripts/dev/start_host_runtime_supervisor.ps1")
WORKER = Path("scripts/dev/start_lifecycle_worker.ps1")
OBSERVER = Path("scripts/dev/start_kubernetes_observer.ps1")


def test_supervisor_uses_fail_closed_scenario_d_engine_and_exact_restart() -> None:
    script = SUPERVISOR.read_text(encoding="utf-8")

    assert 'schema_version = "evm.host_runtime_supervisor.v1"' in script
    assert 'supervision_contract_version = "evm.scenario_d_supervision.v1"' in script
    assert 'HeartbeatProperty = "observed_at"' in script
    assert 'HeartbeatProperty = "last_seen_at"' in script
    assert 'CommandMarker = "evm.control_panel.kubernetes_observer"' in script
    assert 'CommandMarker = "evm.control_panel.lifecycle_worker"' in script
    assert "source_commit = $commit" in script
    assert "New-SupervisorLease" in script
    assert "EVM_SUPERVISOR_FENCING_TOKEN" in script
    assert "evm.operations.scenario_d_supervision evaluate" in script
    assert "Assert-AndStopExactTarget" in script
    assert 'if ($Decision.action -ne "restart_exact")' in script
    assert "restart_counts" in script


def test_host_launchers_only_terminate_owned_processes_and_wait_for_fresh_state() -> None:
    worker = WORKER.read_text(encoding="utf-8")
    observer = OBSERVER.read_text(encoding="utf-8")

    assert "Get-OwnedWorkerProcess" in worker
    assert "*evm.control_panel.lifecycle_worker*" in worker
    assert "revision-matched heartbeat" in worker
    assert "$payload.source_commit -eq $env:EVM_GIT_COMMIT" in worker
    assert "$payload.process_instance_id -eq $env:EVM_PROCESS_INSTANCE_ID" in worker
    assert "worker.identity.json" in worker
    assert "Get-OwnedObserverProcess" in observer
    assert "*evm.control_panel.kubernetes_observer*" in observer
    assert "fresh snapshot within 15 seconds" in observer
    assert "$payload.process_instance_id -eq $env:EVM_PROCESS_INSTANCE_ID" in observer
    assert "observer.identity.json" in observer
