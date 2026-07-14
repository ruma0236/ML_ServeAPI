from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.api.control_panel import list_resources
from evm.control_panel.kubernetes_observer import (
    DEFAULT_NAMESPACES,
    collect_kubernetes_snapshot,
    load_kubernetes_resource_snapshot,
    replace_with_retry,
    write_snapshot,
)
from evm.control_panel.schemas import CycleRun


def test_default_observer_scope_includes_production_namespace() -> None:
    assert "evm-production" in DEFAULT_NAMESPACES


def test_local_observer_launcher_keeps_production_namespace() -> None:
    launcher = Path("scripts/dev/start_kubernetes_observer.ps1").read_text(encoding="utf-8")
    assert "evm-training,evm-staging,evm-production" in launcher


def fixture_runner(arguments: list[str]) -> dict:
    if "nodes" in arguments:
        return {
            "items": [
                {
                    "kind": "Node",
                    "metadata": {"name": "docker-desktop", "creationTimestamp": "2026-07-10T06:00:00Z"},
                    "status": {
                        "capacity": {"cpu": "24"},
                        "conditions": [
                            {
                                "type": "Ready",
                                "status": "True",
                                "lastTransitionTime": "2026-07-10T06:01:00Z",
                            }
                        ],
                    },
                }
            ]
        }
    namespace = arguments[arguments.index("-n") + 1]
    if namespace == "evm-training":
        return {
            "items": [
                {
                    "kind": "Job",
                    "metadata": {
                        "name": "evm-b7-training",
                        "namespace": namespace,
                        "creationTimestamp": "2026-07-10T07:00:00Z",
                    },
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "resources": {
                                            "requests": {
                                                "cpu": "6",
                                                "memory": "12Gi",
                                                "nvidia.com/gpu": "1",
                                            }
                                        }
                                    }
                                ],
                                "volumes": [
                                    {"persistentVolumeClaim": {"claimName": "evm-large-data"}}
                                ],
                            }
                        }
                    },
                    "status": {
                        "failed": 1,
                        "conditions": [
                            {
                                "type": "Failed",
                                "status": "True",
                                "reason": "DeadlineExceeded",
                                "message": "Job was active longer than specified deadline",
                                "lastTransitionTime": "2026-07-10T09:00:00Z",
                            }
                        ],
                    },
                }
            ]
        }
    return {
        "items": [
            {
                "kind": "Deployment",
                "metadata": {
                    "name": "evm-b7-serving",
                    "namespace": namespace,
                    "creationTimestamp": "2026-07-10T07:00:00Z",
                },
                "spec": {"replicas": 0, "template": {"spec": {"containers": []}}},
                "status": {"readyReplicas": 0},
            }
        ]
    }


def test_observer_collects_failed_job_and_missing_gpu_without_credentials() -> None:
    observed_at = datetime(2026, 7, 10, 11, 0, tzinfo=timezone.utc)
    snapshot = collect_kubernetes_snapshot(
        namespaces=("evm-training", "evm-staging"),
        runner=fixture_runner,
        now=observed_at,
    )

    resources = {resource.resource_id: resource for resource in snapshot.resources}
    node = resources["_cluster:Node:docker-desktop"]
    training = resources["evm-training:Job:evm-b7-training"]
    serving = resources["evm-staging:Deployment:evm-b7-serving"]

    assert snapshot.collection_status == "pass"
    assert snapshot.resource_status == "fail"
    assert node.status == "warn"
    assert node.reason == "GpuNotAdvertised"
    assert training.status == "fail"
    assert training.reason == "DeadlineExceeded"
    assert training.gpu_request == "1 x GPU"
    assert training.storage_claim == "evm-large-data"
    assert serving.status == "queued"
    assert serving.desired_replicas == 0


def test_snapshot_loader_marks_old_observations_stale(tmp_path: Path) -> None:
    observed_at = datetime(2026, 7, 10, 11, 0, tzinfo=timezone.utc)
    snapshot = collect_kubernetes_snapshot(
        namespaces=("evm-training", "evm-staging"),
        runner=fixture_runner,
        now=observed_at,
    )
    path = tmp_path / "latest.json"
    write_snapshot(snapshot, path)

    loaded = load_kubernetes_resource_snapshot(
        path,
        now=observed_at + timedelta(seconds=16),
        stale_after_seconds=15,
    )

    assert loaded.observation_status == "stale"
    assert loaded.snapshot_age_seconds == 16
    assert all(resource.observation_status == "stale" for resource in loaded.resources)


def test_resource_route_overlays_live_kubernetes_snapshot(tmp_path: Path, monkeypatch) -> None:
    observed_at = datetime.now(timezone.utc)
    snapshot = collect_kubernetes_snapshot(
        namespaces=("evm-training", "evm-staging"),
        runner=fixture_runner,
        now=observed_at,
    )
    path = tmp_path / "latest.json"
    write_snapshot(snapshot, path)
    cycle = CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr("apps.api.control_panel.cycle_snapshot", lambda: cycle)
    monkeypatch.setenv("EVM_KUBERNETES_RESOURCE_SNAPSHOT_PATH", str(path))

    payload = list_resources()
    resources = {resource.resource_id: resource for resource in payload.resources}

    assert payload.observation_status == "live"
    assert payload.cluster_context == "docker-desktop"
    assert resources["evm-training:Job:evm-b7-training"].status == "fail"
    assert resources["evm-training:Job:evm-b7-training"].observation_source == "kubernetes_snapshot"
    assert resources["evm-platform:Deployment:evm-api"].observation_status == "projected"


def test_malformed_snapshot_fails_closed_without_breaking_projection(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "latest.json"
    path.write_text(json.dumps({"schema_version": "invalid"}), encoding="utf-8")
    cycle = CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr("apps.api.control_panel.cycle_snapshot", lambda: cycle)
    monkeypatch.setenv("EVM_KUBERNETES_RESOURCE_SNAPSHOT_PATH", str(path))

    payload = list_resources()

    assert payload.observation_status == "unavailable"
    assert payload.observation_message
    assert any(resource.name == "evm-api" for resource in payload.resources)
    assert all(resource.observation_status == "projected" for resource in payload.resources)


def test_snapshot_replace_retries_transient_windows_file_lock(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "latest.json.tmp"
    target = tmp_path / "latest.json"
    source.write_text("fresh", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    real_replace = os.replace
    calls = 0

    def flaky_replace(source_path, target_path):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError("simulated bind-mount read lock")
        real_replace(source_path, target_path)

    monkeypatch.setattr("evm.control_panel.kubernetes_observer.os.replace", flaky_replace)
    monkeypatch.setattr("evm.control_panel.kubernetes_observer.time.sleep", lambda _: None)

    replace_with_retry(source, target, attempts=3)

    assert calls == 3
    assert target.read_text(encoding="utf-8") == "fresh"


def test_history_records_state_changes_not_heartbeat_timestamps(tmp_path: Path) -> None:
    first_time = datetime(2026, 7, 10, 11, 0, tzinfo=timezone.utc)
    first = collect_kubernetes_snapshot(
        namespaces=("evm-training", "evm-staging"),
        runner=fixture_runner,
        now=first_time,
    )
    second = collect_kubernetes_snapshot(
        namespaces=("evm-training", "evm-staging"),
        runner=fixture_runner,
        now=first_time + timedelta(seconds=5),
    )
    output = tmp_path / "latest.json"
    history = tmp_path / "history"

    write_snapshot(first, output, history_root=history)
    write_snapshot(second, output, history_root=history)

    assert len(list(history.glob("*.json"))) == 1
    assert "2026-07-10T11:00:05Z" in output.read_text(encoding="utf-8")
