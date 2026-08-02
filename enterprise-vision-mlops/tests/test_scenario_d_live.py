from __future__ import annotations

from datetime import datetime, timezone

from evm.operations.scenario_d_live import (
    action_digest,
    approval_binding_errors,
    heartbeat_cadence_summary,
    percentile_95,
    same_production,
)


def test_action_digest_binds_run_target_revision_and_expiry() -> None:
    identity = {
        "pid": 101,
        "process_started_at": "2026-08-02T00:00:00+00:00",
        "process_instance_id": "instance-12345678",
    }
    expires = datetime(2026, 8, 2, 1, tzinfo=timezone.utc)
    first = action_digest(
        run_id="run-1",
        child="lifecycle_worker",
        identity=identity,
        source_commit="a" * 40,
        expires_at=expires,
    )
    replay = action_digest(
        run_id="run-1",
        child="lifecycle_worker",
        identity=identity,
        source_commit="a" * 40,
        expires_at=expires,
    )
    changed = action_digest(
        run_id="run-2",
        child="lifecycle_worker",
        identity=identity,
        source_commit="a" * 40,
        expires_at=expires,
    )
    assert first == replay
    assert first != changed


def test_approval_binding_is_exact_unexpired_and_single_use() -> None:
    identity = {
        "pid": 101,
        "process_started_at": "2026-08-02T00:00:00+00:00",
        "process_instance_id": "instance-12345678",
    }
    expires = datetime(2026, 8, 2, 1, tzinfo=timezone.utc)
    approval = {
        "approval_id": "scenario-d-maintenance-123",
        "decision": "approved",
        "run_id": "run-1",
        "target_uid": identity["process_instance_id"],
        "target_pid": identity["pid"],
        "action_digest": action_digest(
            run_id="run-1",
            child="lifecycle_worker",
            identity=identity,
            source_commit="a" * 40,
            expires_at=expires,
        ),
        "source_revision": "a" * 40,
        "expires_at": expires.isoformat(),
        "single_use": True,
    }
    assert approval_binding_errors(
        approval,
        run_id="run-1",
        child="lifecycle_worker",
        identity=identity,
        source_commit="a" * 40,
        now=datetime(2026, 8, 2, 0, 30, tzinfo=timezone.utc),
    ) == []
    approval["target_pid"] = 999
    approval["consumed_at"] = "2026-08-02T00:20:00+00:00"
    errors = approval_binding_errors(
        approval,
        run_id="run-1",
        child="lifecycle_worker",
        identity=identity,
        source_commit="a" * 40,
        now=datetime(2026, 8, 2, 2, tzinfo=timezone.utc),
    )
    assert errors == [
        "approval_already_consumed",
        "approval_expired",
        "approval_target_pid_mismatch",
    ]


def test_percentile_95_is_deterministic() -> None:
    assert percentile_95([]) is None
    assert percentile_95([5.0, 4.0, 6.0, 5.5]) == 6.0


def test_heartbeat_cadence_summary_requires_real_timestamp_deltas() -> None:
    timestamps = [
        datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 2, 0, 0, 5, tzinfo=timezone.utc),
        datetime(2026, 8, 2, 0, 0, 11, tzinfo=timezone.utc),
    ]
    assert heartbeat_cadence_summary(timestamps) == {
        "heartbeat_timestamps": [value.isoformat() for value in timestamps],
        "heartbeat_deltas_seconds": [5.0, 6.0],
        "heartbeat_delta_count": 2,
        "heartbeat_p95_seconds": 6.0,
    }


def test_same_production_requires_exact_uid_model_gpu_plugin_and_prometheus() -> None:
    before = {
        "production_ready": {"model_sha256": "a" * 64},
        "kubernetes": {"deployment_uid": "uid-1"},
    }
    after = {
        "production_ready": {
            "model_sha256": "a" * 64,
            "status": "ok",
            "cuda_available": True,
        },
        "production_inference": {
            "candidate_id": "candidate-1",
            "model_sha256": "a" * 64,
            "dataset_version": "dataset-1",
            "device": "cuda",
            "prediction": "normal",
        },
        "kubernetes": {
            "deployment_uid": "uid-1",
            "ready_replicas": 1,
            "available_replicas": 1,
            "gpu_allocatable": "1",
            "plugin_ready": 1,
        },
        "prometheus": {"health": "up"},
    }
    before["production_inference"] = {
        "candidate_id": "candidate-1",
        "model_sha256": "a" * 64,
        "dataset_version": "dataset-1",
    }
    assert same_production(before, after) is True
    assert same_production(before, {**after, "prometheus": {"health": "down"}}) is False
