from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.control_panel import lifecycle_runs
from evm.control_panel.lifecycle_runs import (
    LifecycleActionRequest,
    LifecycleApprovalRequest,
    LifecycleRunError,
    LifecycleRunRequest,
    approve_lifecycle_run,
    create_lifecycle_run,
    get_lifecycle_run,
    queue_lifecycle_run,
    read_runs,
    transition_stage,
)
from evm.control_panel.pipeline_profiles import default_profile, save_profile


def configure_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv(
        "EVM_PIPELINE_PROFILE_RUNTIME_ROOT",
        "/mnt/evm-data/test-profiles",
    )
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(tmp_path / "lifecycle-runs"))
    monkeypatch.setenv(
        "EVM_LIFECYCLE_RUNTIME_ROOT",
        "/mnt/evm-data/test-lifecycle-runs",
    )
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(tmp_path / "data-root"))
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(Path(__file__).resolve().parents[1]))


def saved_full_lifecycle_profile(tmp_path: Path):
    data_root = tmp_path / "data-root"
    source_manifest = data_root / "manifest.jsonl"
    split_manifest = data_root / "shard_index.json"
    source_manifest.parent.mkdir(parents=True, exist_ok=True)
    source_manifest.write_text('{"sample_id":"sample-1"}\n', encoding="utf-8")
    split_identity = "a" * 64
    split_manifest.write_text(
        json.dumps({"schema_version": "evm.dataset_shards.v1", "identity_sha256": split_identity}),
        encoding="utf-8",
    )
    profile = default_profile()
    profile = profile.model_copy(
        update={
            "data": profile.data.model_copy(
                update={
                    "source_manifest_uri": str(source_manifest),
                    "split_manifest_uri": str(split_manifest),
                    "split_manifest_sha256": split_identity,
                }
            )
        }
    )
    return save_profile(profile)


def executable_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    original = lifecycle_runs.validate_profile

    def validate(profile):
        result = original(profile)
        return result.model_copy(
            update={
                "status": "ready",
                "executable": True,
                "blockers": [],
            }
        )

    monkeypatch.setattr(lifecycle_runs, "validate_profile", validate)


def new_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    configure_roots(tmp_path, monkeypatch)
    record = saved_full_lifecycle_profile(tmp_path)
    return create_lifecycle_run(
        LifecycleRunRequest(
            profile_id=record.profile_id,
            profile_version=record.version,
            actor="requester@example.com",
            reason="Validate immutable lifecycle orchestration",
            dry_run=True,
        )
    )


def queue_run(run, monkeypatch: pytest.MonkeyPatch):
    executable_validation(monkeypatch)
    return queue_lifecycle_run(
        run.run_id,
        LifecycleActionRequest(
            actor="requester@example.com",
            reason="Queue validated lifecycle execution",
            expected_version=run.version,
        ),
    )


def test_dry_run_creates_immutable_runtime_snapshot(tmp_path: Path, monkeypatch) -> None:
    run = new_run(tmp_path, monkeypatch)

    assert run.state == "dry_run"
    assert run.progress == 0.1
    assert run.stages[0].state == "completed"
    assert all(stage.state == "not_started" for stage in run.stages[1:])
    assert get_lifecycle_run(run.run_id) == run
    assert read_runs().runs == [run]

    profile_snapshot = json.loads(Path(run.profile_snapshot_uri).read_text(encoding="utf-8"))
    airflow_snapshot = json.loads(Path(run.airflow_config_uri).read_text(encoding="utf-8"))
    model_snapshot = json.loads(Path(run.model_config_uri).read_text(encoding="utf-8"))
    assert profile_snapshot["execution_scope"] == "full_lifecycle"
    assert airflow_snapshot["control_plane"]["lifecycle_run_id"] == run.run_id
    assert airflow_snapshot["control_plane"]["pipeline_stage_scope"] == "data"
    assert airflow_snapshot["pipelines"]["data_validation"]["output_manifest"].startswith(
        run.airflow_runtime_uri.rsplit("/", 1)[0]
    )
    assert airflow_snapshot["pipelines"]["image_quality"]["input_manifest"] == (
        airflow_snapshot["pipelines"]["data_validation"]["output_manifest"]
    )
    assert model_snapshot["model_matrix"]["matrix_id"] == run.run_id
    assert model_snapshot["inputs"]["base_config"] == run.airflow_runtime_uri
    assert model_snapshot["inputs"]["shard_index"] == (
        f"{run.airflow_runtime_uri.rsplit('/', 1)[0]}/data/shards/shard_index.json"
    )
    assert model_snapshot["resources"]["artifact_root"].lower().startswith(
        str(tmp_path / "data-root").replace("\\", "/").lower()
    )


def test_dry_run_cannot_execute_and_queue_is_version_guarded(tmp_path: Path, monkeypatch) -> None:
    run = new_run(tmp_path, monkeypatch)

    with pytest.raises(LifecycleRunError, match="Dry-run LifecycleRun stages cannot execute"):
        transition_stage(run.run_id, "data_pipeline", "running", actor="worker")

    executable_validation(monkeypatch)
    with pytest.raises(LifecycleRunError, match="Expected version 2"):
        queue_lifecycle_run(
            run.run_id,
            LifecycleActionRequest(
                actor="requester@example.com",
                reason="Queue with stale optimistic version",
                expected_version=2,
            ),
        )


def test_stage_dependencies_and_attempts_are_enforced(tmp_path: Path, monkeypatch) -> None:
    run = queue_run(new_run(tmp_path, monkeypatch), monkeypatch)

    with pytest.raises(LifecycleRunError, match="Incomplete dependencies: data_pipeline"):
        transition_stage(run.run_id, "model_training", "running", actor="worker")

    running = transition_stage(
        run.run_id,
        "data_pipeline",
        "running",
        actor="worker",
        runtime_id="airflow-run-1",
    )
    assert running.state == "running"
    assert running.stages[1].attempt == 1
    completed = transition_stage(
        run.run_id,
        "data_pipeline",
        "completed",
        actor="worker",
        evidence_uri="evidence://airflow-run-1",
    )
    assert completed.state == "queued"
    assert completed.current_stage == "model_training"

    with pytest.raises(LifecycleRunError, match="cannot transition from completed"):
        transition_stage(run.run_id, "data_pipeline", "running", actor="worker")


def test_two_person_approval_advances_waiting_run(tmp_path: Path, monkeypatch) -> None:
    run = queue_run(new_run(tmp_path, monkeypatch), monkeypatch)
    for stage_id in (
        "data_pipeline",
        "model_training",
        "model_evaluation",
        "artifact_readiness",
        "ci_ct_gate",
    ):
        transition_stage(run.run_id, stage_id, "running", actor="worker")
        run = transition_stage(run.run_id, stage_id, "completed", actor="worker")
    run = transition_stage(
        run.run_id,
        "approval",
        "waiting_approval",
        actor="worker",
        detail="All automated gates passed",
    )

    assert run.state == "waiting_approval"
    with pytest.raises(LifecycleRunError, match="requester cannot approve"):
        approve_lifecycle_run(
            run.run_id,
            LifecycleApprovalRequest(
                actor="requester@example.com",
                approver="requester@example.com",
                reason="Self approval must be rejected",
                expected_version=run.version,
            ),
        )

    approved = approve_lifecycle_run(
        run.run_id,
        LifecycleApprovalRequest(
            actor="release-approver@example.com",
            approver="release-approver@example.com",
            reason="Independent release approval granted",
            expected_version=run.version,
        ),
    )
    assert approved.state == "queued"
    assert approved.current_stage == "deployment"
    assert approved.approver == "release-approver@example.com"
    assert approved.stages[6].state == "completed"


def test_invalid_stage_transition_is_rejected(tmp_path: Path, monkeypatch) -> None:
    run = queue_run(new_run(tmp_path, monkeypatch), monkeypatch)
    transition_stage(run.run_id, "data_pipeline", "running", actor="worker")

    with pytest.raises(LifecycleRunError, match="cannot transition from running to queued"):
        transition_stage(run.run_id, "data_pipeline", "queued", actor="worker")
