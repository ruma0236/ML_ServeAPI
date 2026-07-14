from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.api.control_panel import list_resources
from evm.control_panel.kubernetes_observer import (
    DEFAULT_NAMESPACES,
    WindowsGpuEngineSample,
    aggregate_windows_gpu_engine_samples,
    collect_host_compute_telemetry,
    collect_kubernetes_snapshot,
    collect_nvidia_telemetry,
    load_kubernetes_resource_snapshot,
    replace_with_retry,
    write_snapshot,
)
from evm.control_panel.schemas import ComputeTelemetry, CycleRun


def test_default_observer_scope_includes_production_namespace() -> None:
    assert "evm-production" in DEFAULT_NAMESPACES


def test_local_observer_launcher_keeps_production_namespace() -> None:
    launcher = Path("scripts/dev/start_kubernetes_observer.ps1").read_text(encoding="utf-8")
    assert "evm-training,evm-staging,evm-production" in launcher


def test_host_telemetry_parses_cpu_memory_and_nvidia_metrics() -> None:
    telemetry = collect_host_compute_telemetry(
        now=datetime(2026, 7, 14, 5, 30, tzinfo=timezone.utc),
        cpu_sampler=lambda: 42.25,
        memory_reader=lambda: (32 * 1024**3, 64 * 1024**3),
        gpu_runner=lambda _: (
            "0, NVIDIA GeForce RTX 4080 SUPER, GPU-test, 71, 4096, 16376, 56, 188.5, 320\n"
        ),
        gpu_engine_sampler=lambda: [
            WindowsGpuEngineSample(
                adapter_luid="luid_test",
                utilization_percent=12.5,
                busiest_engine="3D",
                dedicated_memory_mib=4090,
            )
        ],
    )

    assert telemetry.status == "live"
    assert telemetry.cpu_utilization_percent == 42.25
    assert telemetry.memory_utilization_percent == 50.0
    assert telemetry.memory_used_bytes == 32 * 1024**3
    assert len(telemetry.accelerators) == 1
    gpu = telemetry.accelerators[0]
    assert gpu.name == "NVIDIA GeForce RTX 4080 SUPER"
    assert gpu.utilization_percent == 71
    assert gpu.engine_utilization_percent == 12.5
    assert gpu.engine_utilization_source == "windows_pdh"
    assert gpu.busiest_engine == "3D"
    assert gpu.memory_used_mib == 4096
    assert gpu.temperature_c == 56
    assert gpu.power_draw_w == 188.5


def test_windows_gpu_engine_aggregation_matches_task_manager_busiest_engine() -> None:
    samples = aggregate_windows_gpu_engine_samples(
        [
            ("pid_10_luid_0x0_0x1_phys_0_eng_0_engtype_3D", 7.25),
            ("pid_20_luid_0x0_0x1_phys_0_eng_0_engtype_3D", 3.5),
            ("pid_10_luid_0x0_0x1_phys_0_eng_2_engtype_Copy", 2.0),
            ("pid_30_luid_0x0_0x2_phys_0_eng_0_engtype_3D", 1.0),
        ],
        [
            ("luid_0x0_0x1_phys_0", 4096 * 1024**2),
            ("luid_0x0_0x2_phys_0", 0),
        ],
    )

    assert samples[0].adapter_luid == "luid_0x0_0x1_phys_0"
    assert samples[0].utilization_percent == 10.75
    assert samples[0].busiest_engine == "3D"
    assert samples[0].dedicated_memory_mib == 4096


def test_nvidia_parser_accepts_unavailable_optional_measurements() -> None:
    accelerators = collect_nvidia_telemetry(
        gpu_runner=lambda _: "0, NVIDIA GPU, GPU-test, 0, 128, 1024, N/A, [Not Supported], N/A\n"
    )

    assert accelerators[0].utilization_percent == 0
    assert accelerators[0].temperature_c is None
    assert accelerators[0].power_draw_w is None


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
        compute_telemetry=ComputeTelemetry(
            status="live",
            observed_at="2026-07-10T11:00:00Z",
            cpu_utilization_percent=25,
            memory_utilization_percent=50,
        ),
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
    assert loaded.compute_telemetry is not None
    assert loaded.compute_telemetry.status == "stale"


def test_resource_route_overlays_live_kubernetes_snapshot(tmp_path: Path, monkeypatch) -> None:
    observed_at = datetime.now(timezone.utc)
    snapshot = collect_kubernetes_snapshot(
        namespaces=("evm-training", "evm-staging"),
        runner=fixture_runner,
        now=observed_at,
        compute_telemetry=ComputeTelemetry(
            status="live",
            observed_at=observed_at.isoformat().replace("+00:00", "Z"),
            cpu_utilization_percent=33,
            memory_utilization_percent=44,
        ),
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
    assert payload.compute_telemetry is not None
    assert payload.compute_telemetry.cpu_utilization_percent == 33
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
