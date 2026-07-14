from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from evm.control_panel import lifecycle_runs
from evm.control_panel.lifecycle_runs import (
    LifecycleActionRequest,
    LifecycleApprovalRequest,
    LifecycleRunError,
    LifecycleRunRequest,
    approve_lifecycle_run,
    continue_lifecycle_run,
    create_lifecycle_run,
    get_lifecycle_run,
    lifecycle_deployment_name,
    queue_lifecycle_run,
    read_runs,
    transition_stage,
    update_run_evidence,
)
from evm.control_panel.pipeline_profiles import default_profile, save_profile
from evm.control_panel.stage_handoffs import build_stage_handoff_catalog, handoff_bucket


def configure_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv(
        "EVM_PIPELINE_PROFILE_RUNTIME_ROOT",
        "/mnt/evm-data/test-profiles",
    )
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(tmp_path / "lifecycle-runs"))
    monkeypatch.setenv("EVM_EXPERIMENT_RUN_ROOT", str(tmp_path / "experiments"))
    monkeypatch.setenv(
        "EVM_LIFECYCLE_RUNTIME_ROOT",
        "/mnt/evm-data/test-lifecycle-runs",
    )
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(tmp_path / "data-root"))
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("EVM_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("EVM_GIT_BRANCH", "test/lifecycle-runs")


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
    assert model_snapshot["product"]["target_deployment"] == "evm-b0-staging"
    assert model_snapshot["resources"]["artifact_root"].lower().startswith(
        str(tmp_path / "data-root").replace("\\", "/").lower()
    )
    run_root = Path(run.profile_snapshot_uri).parent
    for shared_name in ("data", "model", "kubernetes", "serving", "monitoring"):
        shared_directory = run_root / shared_name
        assert shared_directory.is_dir()
        if os.name != "nt":
            assert shared_directory.stat().st_mode & 0o777 == 0o777


def test_run_ledger_hydrates_cycle_context_from_persisted_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run = new_run(tmp_path, monkeypatch)
    snapshot = Path(run.profile_snapshot_uri).parent / "cycle.snapshot.json"
    snapshot.write_text(json.dumps({"cycle_id": "cycle-from-lifecycle"}), encoding="utf-8")
    update_run_evidence(
        run.run_id,
        actor="worker",
        cycle_snapshot_uri=str(snapshot),
    )

    hydrated = get_lifecycle_run(run.run_id)

    assert hydrated is not None
    assert hydrated.cycle_id == "cycle-from-lifecycle"


@pytest.mark.parametrize(
    ("architecture", "environment", "expected"),
    [
        ("efficientnet-b0", "staging", "evm-b0-staging"),
        ("efficientnet-b7", "production", "evm-b7-production"),
        ("efficientnet-b0", "pre-production", "evm-b0-preprod"),
    ],
)
def test_lifecycle_deployment_name_matches_allowlist(
    architecture: str,
    environment: str,
    expected: str,
) -> None:
    assert lifecycle_deployment_name(architecture, environment) == expected


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


def test_queue_requires_blueprint_revision_after_quality_regression(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run = new_run(tmp_path, monkeypatch)
    executable_validation(monkeypatch)
    monkeypatch.setattr(
        lifecycle_runs,
        "unresolved_quality_review",
        lambda _digest: SimpleNamespace(
            event_id="model-quality-1234",
            failed_gates=["f1<0.75"],
            recommendations=["unfreeze_backbone", "expand_learning_rate_search"],
        ),
    )

    with pytest.raises(LifecycleRunError) as exc_info:
        queue_lifecycle_run(
            run.run_id,
            LifecycleActionRequest(
                actor="requester@example.com",
                reason="Retry unchanged quality-regressed Blueprint",
                expected_version=run.version,
            ),
        )

    assert exc_info.value.code == "model_quality_review_unresolved"
    assert "Revise and save a new Blueprint" in str(exc_info.value)
    assert "f1<0.75" in str(exc_info.value)


def test_retry_guard_uses_run_quality_review_when_legacy_digest_differs(
    monkeypatch,
) -> None:
    review = SimpleNamespace(
        state="review_required",
        event_id="model-quality-legacy-digest",
        failed_gates=["f1<0.75"],
        recommendations=["revise_blueprint_before_retry"],
    )
    monkeypatch.setattr(
        lifecycle_runs,
        "read_experiment",
        lambda _run_id: SimpleNamespace(quality_review=review),
    )
    monkeypatch.setattr(
        lifecycle_runs,
        "unresolved_quality_review",
        lambda _profile_digest: None,
    )

    with pytest.raises(LifecycleRunError) as exc_info:
        lifecycle_runs.reject_run_quality_review("legacy-run", "different-profile-digest")

    assert exc_info.value.code == "model_quality_review_unresolved"
    assert "model-quality-legacy-digest" in str(exc_info.value)


def test_missing_source_revision_is_visible_and_blocks_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configure_roots(tmp_path, monkeypatch)
    record = saved_full_lifecycle_profile(tmp_path)
    monkeypatch.delenv("EVM_GIT_COMMIT")
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    run = create_lifecycle_run(
        LifecycleRunRequest(
            profile_id=record.profile_id,
            profile_version=record.version,
            actor="requester@example.com",
            reason="Validate source revision fail closed behavior",
            dry_run=True,
        )
    )

    assert run.source_commit is None
    assert run.blockers == ["source_revision_missing"]
    executable_validation(monkeypatch)
    with pytest.raises(LifecycleRunError, match="immutable source commit"):
        queue_lifecycle_run(
            run.run_id,
            LifecycleActionRequest(
                actor="requester@example.com",
                reason="Attempt queue without source revision",
                expected_version=run.version,
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


def test_stepwise_run_pauses_and_exposes_ready_handoff(tmp_path: Path, monkeypatch) -> None:
    configure_roots(tmp_path, monkeypatch)
    record = saved_full_lifecycle_profile(tmp_path)
    dry_run = create_lifecycle_run(
        LifecycleRunRequest(
            profile_id=record.profile_id,
            profile_version=record.version,
            actor="requester@example.com",
            reason="Execute lifecycle one governed stage at a time",
            dry_run=True,
            execution_mode="stepwise",
        )
    )
    run = queue_run(dry_run, monkeypatch)
    transition_stage(run.run_id, "data_pipeline", "running", actor="worker")
    paused = transition_stage(
        run.run_id,
        "data_pipeline",
        "completed",
        actor="worker",
        evidence_uri="evidence://data-pipeline",
    )

    assert paused.execution_mode == "stepwise"
    assert paused.state == "paused"
    assert paused.current_stage == "model_training"
    catalog = build_stage_handoff_catalog(run_id=paused.run_id)
    ready = next(item for item in catalog.handoffs if item.stage_id == "model_training")
    assert ready.bucket == "ready"
    assert ready.eligible_actions == ["continue", "inspect"]
    assert ready.input_refs["previous_stage_evidence"] == "evidence://data-pipeline"

    continued = continue_lifecycle_run(
        paused.run_id,
        LifecycleActionRequest(
            actor="requester@example.com",
            reason="Continue with the ready model training stage",
            expected_version=paused.version,
        ),
    )
    assert continued.state == "queued"
    assert continued.current_stage == "model_training"


def test_automatic_run_still_advances_without_pause(tmp_path: Path, monkeypatch) -> None:
    run = queue_run(new_run(tmp_path, monkeypatch), monkeypatch)
    transition_stage(run.run_id, "data_pipeline", "running", actor="worker")
    completed = transition_stage(run.run_id, "data_pipeline", "completed", actor="worker")

    assert completed.execution_mode == "automatic"
    assert completed.state == "queued"
    assert completed.current_stage == "model_training"


def test_cancelled_stage_is_archived_instead_of_counted_as_blocked() -> None:
    run = SimpleNamespace(state="cancelled", current_stage=None)

    assert handoff_bucket(run, "deployment", "cancelled", None) == "cancelled"


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
    assert approved.stages[6].runtime_state == "approved"
    assert approved.progress == pytest.approx(0.7)


def test_invalid_stage_transition_is_rejected(tmp_path: Path, monkeypatch) -> None:
    run = queue_run(new_run(tmp_path, monkeypatch), monkeypatch)
    transition_stage(run.run_id, "data_pipeline", "running", actor="worker")

    with pytest.raises(LifecycleRunError, match="cannot transition from running to queued"):
        transition_stage(run.run_id, "data_pipeline", "queued", actor="worker")


def test_atomic_json_write_retries_transient_windows_permission_error(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "worker.json"
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(path: Path, destination: Path):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("transient sharing violation")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    lifecycle_runs.atomic_write_json(target, {"status": "online"})

    assert attempts == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "online"}
    assert not list(tmp_path.glob("*.tmp"))
