from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from evm.control_panel import lifecycle_orchestrator, lifecycle_runs, operations
from evm.control_panel.lifecycle_kubernetes import CTBundle, ServingBundle
from evm.control_panel.lifecycle_integrity import build_lifecycle_release_submission
from evm.control_panel.lifecycle_orchestrator import (
    LifecycleStageBlocked,
    clear_prometheus_target,
    execute_guarded_kubernetes_task,
    lifecycle_guard_directory,
    process_artifact_readiness,
    process_approval,
    process_ci_ct_gate,
    process_deployment,
    process_lifecycle_run,
    reserve_external_action,
    rollback_lifecycle,
    training_failure_blockers,
    write_prometheus_target,
)
from evm.control_panel.lifecycle_runs import (
    LifecycleActionRequest,
    LifecycleRunRequest,
    create_lifecycle_run,
    queue_lifecycle_run,
    transition_stage,
    update_run_evidence,
)
from evm.control_panel.pipeline_profiles import default_profile, save_profile
from evm.control_panel.schemas import (
    ArtifactReadinessEvaluation,
    CDCTGate,
    CTDatasetSnapshot,
    CTEvaluation,
    ReadinessEvidenceCheck,
    TaskAssignmentRequest,
)
from evm.core.dataset import shard_index_identity_digest


class FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_lifecycle_guard_directory_maps_container_uri_for_host_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    host_root = tmp_path / "data"
    expected = host_root / "artifacts" / "w7" / "lifecycle_runs" / "run-1"
    expected.mkdir(parents=True)
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(host_root))
    run = SimpleNamespace(
        identity_envelope_uri=(
            "/app/artifacts/w7/lifecycle_runs/run-1/identity.envelope.json"
        ),
        lifecycle_series_id="series-12345678",
        attempt_id="attempt-12345678",
        correlation_id="correlation-12345678",
    )

    assert lifecycle_guard_directory(run) == expected


def queued_run(
    tmp_path: Path,
    monkeypatch,
    *,
    approval_policy: str = "two_person",
):
    project = Path(__file__).resolve().parents[1]
    data_root = tmp_path / "d"
    source = data_root / "manifest.jsonl"
    shard = data_root / "shard.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"sample_id":"1","image_uri":"file:///sample.jpg"}\n', encoding="utf-8")
    identity = "a" * 64
    shard.write_text(json.dumps({"identity_sha256": identity}), encoding="utf-8")
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(project))
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(data_root))
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_ROOT", str(tmp_path / "p"))
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_RUNTIME_ROOT", "/mnt/evm-data/p")
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(tmp_path / "l"))
    monkeypatch.setenv("EVM_LIFECYCLE_RUNTIME_ROOT", "/mnt/evm-data/l")
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path / "o"))
    monkeypatch.setenv("GIT_COMMIT", "1" * 40)
    profile = default_profile().model_copy(update={"profile_name": "orchestrator-test"})
    profile = profile.model_copy(
        update={
            "gates": profile.gates.model_copy(
                update={"approval_policy": approval_policy}
            )
        }
    )
    profile = profile.model_copy(
        update={
            "data": profile.data.model_copy(
                update={
                    "source_manifest_uri": str(source),
                    "split_manifest_uri": str(shard),
                    "split_manifest_sha256": identity,
                }
            )
        }
    )
    record = save_profile(profile)
    run = create_lifecycle_run(
        LifecycleRunRequest(
            profile_id=record.profile_id,
            profile_version=record.version,
            actor="requester@example.com",
            reason="Exercise dependency aware lifecycle worker",
            dry_run=True,
        )
    )
    original = lifecycle_runs.validate_profile

    def executable(value):
        result = original(value)
        return result.model_copy(update={"status": "ready", "executable": True, "blockers": []})

    monkeypatch.setattr(lifecycle_runs, "validate_profile", executable)
    return queue_lifecycle_run(
        run.run_id,
        LifecycleActionRequest(
            actor="requester@example.com",
            reason="Queue dependency aware lifecycle worker",
            expected_version=run.version,
        ),
    )


def test_airflow_success_advances_lifecycle_to_model_training(tmp_path, monkeypatch) -> None:
    run = queued_run(tmp_path, monkeypatch)
    write_data_provenance(run)
    responses = iter(
        [
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "queued"}),
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "success"}),
        ]
    )
    monkeypatch.setattr(operations, "urlopen", lambda *_args, **_kwargs: next(responses))

    result = process_lifecycle_run(run.run_id)

    assert result.state == "queued"
    assert result.current_stage == "model_training"
    assert result.stages[1].state == "completed"
    tasks = operations.read_tasks().tasks
    assert len(tasks) == 1
    assert tasks[0].cycle_id == run.run_id
    assert tasks[0].status == "done"
    assert tasks[0].config_payload["pipeline_stage_scope"] == "data"
    assert tasks[0].config_payload["source_commit"] == run.source_commit
    assert tasks[0].config_payload["source_branch"] == run.source_branch
    assert result.stages[1].evidence_uri.endswith("provenance-validation.json")
    side_effects = json.loads(
        Path(str(result.side_effect_ledger_uri)).read_text(encoding="utf-8")
    )["entries"]
    assert [item["action"] for item in side_effects] == [
        "create_airflow_task_assignment",
        "dispatch_task_assignment",
    ]
    assert all(item["state"] == "completed" for item in side_effects)
    assert len({item["side_effect_key"] for item in side_effects}) == 2


def test_running_kubernetes_side_effect_is_reconciled_before_worker_resume(
    tmp_path,
    monkeypatch,
) -> None:
    run = queued_run(tmp_path, monkeypatch)
    request = TaskAssignmentRequest(
        cycle_id=run.run_id,
        task_type="kubernetes_job",
        owner=run.actor,
        priority="high",
        resource_profile="docker-desktop-gpu",
        approval_policy="auto",
        config_payload={
            "adapter": "host-kubectl-bridge",
            "manifest_dir": str(tmp_path / "generated"),
            "namespace": "evm-training",
            "job_name": "evm-lifecycle-train-123456789abc",
            "lifecycle_run_id": run.run_id,
        },
        dry_run=False,
    )
    task = operations.create_task_assignment(request)
    task = operations.update_task_runtime(
        task.task_id,
        actor="test",
        event="job_admitted_before_worker_exit",
        status="running",
        runtime_system="kubernetes",
        runtime_id="evm-training/job/evm-lifecycle-train-123456789abc",
        runtime_state="running",
    )
    assert task is not None
    entry, created = reserve_external_action(
        run,
        "model_training",
        "execute_kubernetes_job",
        {"task_id": task.task_id, "config_payload": task.config_payload},
    )
    assert created is True
    calls: list[str] = []

    def observe(task_id, *, runner):
        del runner
        calls.append(f"observe:{task_id}")
        return SimpleNamespace(
            runtime_id=task.runtime_id,
            evidence_uri=str(tmp_path / "reconciliation.json"),
        )

    def execute(task_id, *, runner, progress_callback=None):
        del runner, progress_callback
        calls.append(f"execute:{task_id}")
        return operations.update_task_runtime(
            task_id,
            actor="test",
            event="reconciled_job_completed",
            status="done",
            runtime_state="complete",
        )

    monkeypatch.setattr(lifecycle_orchestrator, "observe_exact_kubernetes_task", observe)
    monkeypatch.setattr(lifecycle_orchestrator, "execute_kubernetes_task", execute)

    result = execute_guarded_kubernetes_task(
        run,
        "model_training",
        task,
        runner=lambda *_args, **_kwargs: None,
    )

    assert result.status == "done"
    assert calls == [f"observe:{task.task_id}", f"execute:{task.task_id}"]
    ledger = json.loads(
        Path(str(run.side_effect_ledger_uri)).read_text(encoding="utf-8")
    )
    reconciled = next(
        item for item in ledger["entries"] if item["side_effect_key"] == entry.side_effect_key
    )
    assert reconciled["state"] == "completed"
    assert reconciled["runtime_id"] == task.runtime_id


def test_airflow_success_blocks_when_source_commit_does_not_match(tmp_path, monkeypatch) -> None:
    run = queued_run(tmp_path, monkeypatch)
    write_data_provenance(run, commit="2" * 40)
    responses = iter(
        [
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "queued"}),
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "success"}),
        ]
    )
    monkeypatch.setattr(operations, "urlopen", lambda *_args, **_kwargs: next(responses))

    result = process_lifecycle_run(run.run_id)

    assert result.state == "blocked"
    assert result.current_stage == "data_pipeline"
    assert result.stages[1].state == "blocked"
    assert any(item.startswith("data_source_commit_mismatch:") for item in result.blockers)
    report = json.loads(
        (Path(run.artifact_root) / "data" / "provenance-validation.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "blocked"


def test_airflow_success_blocks_corrupt_derived_shard_identity(
    tmp_path,
    monkeypatch,
) -> None:
    run = queued_run(tmp_path, monkeypatch)
    write_data_provenance(run)
    index_path = Path(run.artifact_root) / "data" / "shards" / "shard_index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["identity_sha256"] = "0" * 64
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    responses = iter(
        [
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "queued"}),
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "success"}),
        ]
    )
    monkeypatch.setattr(operations, "urlopen", lambda *_args, **_kwargs: next(responses))

    result = process_lifecycle_run(run.run_id)

    assert result.state == "blocked"
    assert result.current_stage == "data_pipeline"
    assert "integrity_shard_index_identity_mismatch" in result.blockers
    assert result.stages[2].state == "not_started"


def write_data_provenance(run, *, commit: str | None = None) -> None:
    trace = {
        "trace_id": "enterprise_vision_mlops_daily__cp__lifecycle",
        "git_commit": commit or run.source_commit,
        "git_branch": run.source_branch,
    }
    for relative in lifecycle_orchestrator.DATA_PIPELINE_PROVENANCE_FILES:
        path = Path(run.artifact_root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"trace": trace}), encoding="utf-8")
    source_path = Path(run.artifact_root) / "data" / "quality" / "quality_manifest.jsonl"
    records = [
        {
            "sample_id": "sample-train",
            "content_sha256": "1" * 64,
            "label": "normal",
            "split": "train",
        },
        {
            "sample_id": "sample-validation",
            "content_sha256": "2" * 64,
            "label": "normal",
            "split": "validation",
        },
        {
            "sample_id": "sample-test",
            "content_sha256": "3" * 64,
            "label": "anomaly",
            "split": "test",
        },
    ]
    source_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    shard_root = Path(run.artifact_root) / "data" / "shards"
    shards = []
    for index, record in enumerate(records):
        split = str(record["split"])
        path = shard_root / f"{split}.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        shards.append(
            {
                "shard_id": f"{split}-{index:04d}",
                "split": split,
                "path": str(path),
                "record_count": 1,
                "first_sample_id": record["sample_id"],
                "last_sample_id": record["sample_id"],
            }
        )
    index_payload = {
        "schema_version": "evm.dataset_shards.v1",
        "input_manifest": str(source_path),
        "records_per_shard": 1,
        "record_count": len(records),
        "shard_count": len(shards),
        "split_counts": {"train": 1, "validation": 1, "test": 1},
        "label_counts": {"normal": 2, "anomaly": 1},
        "label_type_counts": {"binary": 3},
        "shards": shards,
        "trace": trace,
    }
    index_payload["identity_sha256"] = shard_index_identity_digest(index_payload)
    (shard_root / "shard_index.json").write_text(
        json.dumps(index_payload),
        encoding="utf-8",
    )


def test_airflow_running_observation_reaches_lifecycle_stage(tmp_path, monkeypatch) -> None:
    run = queued_run(tmp_path, monkeypatch)
    responses = iter(
        [
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "queued"}),
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "running"}),
            FakeResponse(
                {
                    "task_instances": [
                        {"task_id": "intake", "state": "success"},
                        {"task_id": "quality", "state": "running"},
                        {"task_id": "curation", "state": None},
                    ]
                }
            ),
        ]
    )
    monkeypatch.setattr(operations, "urlopen", lambda *_args, **_kwargs: next(responses))

    result = process_lifecycle_run(run.run_id)

    assert result.state == "running"
    assert result.current_stage == "data_pipeline"
    assert result.stages[1].state == "running"
    assert result.stages[1].runtime_state == "running; 1/3 tasks complete"
    assert result.stages[1].progress == pytest.approx(1 / 3)
    assert "active: quality" in (result.stages[1].detail or "")


def test_airflow_runtime_refreshes_after_lifecycle_stage_is_running(
    tmp_path, monkeypatch
) -> None:
    run = queued_run(tmp_path, monkeypatch)
    responses = iter(
        [
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "queued"}),
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "queued"}),
            FakeResponse({"task_instances": [{"task_id": "intake", "state": None}]}),
            FakeResponse({"dag_run_id": "cp__lifecycle", "state": "running"}),
            FakeResponse(
                {"task_instances": [{"task_id": "intake", "state": "running"}]}
            ),
        ]
    )
    monkeypatch.setattr(operations, "urlopen", lambda *_args, **_kwargs: next(responses))

    first = process_lifecycle_run(run.run_id)
    refreshed = process_lifecycle_run(run.run_id)

    assert first.stages[1].state == "running"
    assert first.stages[1].runtime_state == "queued; 0/1 tasks complete"
    assert refreshed.stages[1].state == "running"
    assert refreshed.stages[1].runtime_state == "running; 0/1 tasks complete"
    assert refreshed.version > first.version
    assert refreshed.audit[-1].event == "lifecycle_stage_runtime_updated"


def test_airflow_dispatch_failure_propagates_reason_to_lifecycle(tmp_path, monkeypatch) -> None:
    run = queued_run(tmp_path, monkeypatch)

    def unavailable(*_args, **_kwargs):
        raise URLError("airflow offline")

    monkeypatch.setattr(operations, "urlopen", unavailable)
    result = process_lifecycle_run(run.run_id)

    assert result.state == "failed"
    assert result.current_stage == "data_pipeline"
    assert result.stages[1].state == "failed"
    assert result.stages[1].attempt == 1
    assert "airflow_api_unavailable" in (result.failure_reason or "")
    assert any("Airflow API is unavailable" in item for item in result.blockers)


def test_training_failure_includes_experiment_and_metric_blockers(
    tmp_path,
    monkeypatch,
) -> None:
    matrix_path = tmp_path / "latest_model_matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {"promotion_blockers": ["f1<0.75"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        lifecycle_orchestrator,
        "read_experiment",
        lambda _run_id: SimpleNamespace(
            blockers=["required_candidate_promotion_blocked:efficientnet-b0"]
        ),
    )
    monkeypatch.setattr(
        lifecycle_orchestrator,
        "model_matrix_path",
        lambda _run: matrix_path,
    )

    blockers = training_failure_blockers(
        SimpleNamespace(run_id="lifecycle-test"),
        SimpleNamespace(status="failed", failure_reason="kubernetes_job_failed"),
    )

    assert blockers == [
        "f1<0.75",
        "kubernetes_job_failed",
        "required_candidate_promotion_blocked:efficientnet-b0",
    ]


def test_approval_stage_stops_worker_until_independent_action(tmp_path, monkeypatch) -> None:
    run = queued_run(tmp_path, monkeypatch)
    for stage_id in (
        "data_pipeline",
        "model_training",
        "model_evaluation",
        "artifact_readiness",
        "ci_ct_gate",
    ):
        transition_stage(run.run_id, stage_id, "running", actor="test")
        run = transition_stage(run.run_id, stage_id, "completed", actor="test")

    waiting = process_lifecycle_run(run.run_id)
    unchanged = process_lifecycle_run(run.run_id)

    assert waiting.state == "waiting_approval"
    assert waiting.current_stage == "approval"
    assert unchanged.version == waiting.version
    assert unchanged.stages[7].state == "not_started"


def test_staging_policy_approval_advances_without_human_action(
    tmp_path,
    monkeypatch,
) -> None:
    run = queued_run(
        tmp_path,
        monkeypatch,
        approval_policy="automated_non_production",
    )
    for stage_id in (
        "data_pipeline",
        "model_training",
        "model_evaluation",
        "artifact_readiness",
        "ci_ct_gate",
    ):
        transition_stage(run.run_id, stage_id, "running", actor="test")
        run = transition_stage(run.run_id, stage_id, "completed", actor="test")
    install_release_submission(run, tmp_path, monkeypatch)

    approved = process_approval(
        run,
        runner=lambda *_args, **_kwargs: None,
        http_client=lambda *_args, **_kwargs: (200, {}),
    )

    approval = next(item for item in approved.stages if item.stage_id == "approval")
    assert approval.state == "completed"
    assert approval.runtime_state == "approved"
    assert approved.approver == "release-policy-bot@local"
    assert approved.current_stage == "deployment"
    assert approved.state == "queued"
    assert approved.audit[-1].event == "lifecycle_run_approved"


def test_artifact_readiness_persists_report_before_stage_completion(
    tmp_path,
    monkeypatch,
) -> None:
    run = queued_run(tmp_path, monkeypatch)
    for stage_id in ("data_pipeline", "model_training", "model_evaluation"):
        transition_stage(run.run_id, stage_id, "running", actor="test")
        run = transition_stage(run.run_id, stage_id, "completed", actor="test")
    model = json.loads(Path(run.model_config_uri).read_text(encoding="utf-8"))
    report_path = Path(model["control_plane"]["runtime_evidence"]["readiness"])
    evaluation = ArtifactReadinessEvaluation(
        evaluation_id="readiness-persisted",
        decision="ready",
        status="pass",
        data_status="pass",
        model_status="pass",
        runtime_status="pass",
        candidate_id="efficientnet-b0-test",
        dataset_version="visa-test",
        evaluated_at="2026-07-12T00:00:00Z",
        input_digest="a" * 64,
        checks=[
            ReadinessEvidenceCheck(
                check_id="model_artifact",
                category="model",
                status="pass",
            )
        ],
        report_uri=str(report_path),
    )
    monkeypatch.setattr(
        lifecycle_orchestrator,
        "rebuild_cycle",
        lambda _run: SimpleNamespace(readiness_evaluation=evaluation),
    )

    completed = process_artifact_readiness(
        run,
        runner=lambda *_args, **_kwargs: None,
        http_client=lambda *_args, **_kwargs: (200, {}),
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert completed.stages[4].state == "completed"
    assert completed.readiness_uri == payload["report_uri"]
    assert completed.stages[4].evidence_uri == payload["report_uri"]
    assert payload["evaluation_id"] == "readiness-persisted"


def ct_ready_run(tmp_path: Path, monkeypatch):
    run = queued_run(tmp_path, monkeypatch)
    for stage_id in (
        "data_pipeline",
        "model_training",
        "model_evaluation",
        "artifact_readiness",
    ):
        transition_stage(run.run_id, stage_id, "running", actor="test")
        run = transition_stage(run.run_id, stage_id, "completed", actor="test")
    return run


def ct_snapshot(run, tmp_path: Path) -> CTDatasetSnapshot:
    root = tmp_path / "ct" / "snapshots" / "snapshot-test"
    root.mkdir(parents=True, exist_ok=True)
    return CTDatasetSnapshot(
        snapshot_id="snapshot-test",
        lifecycle_run_id=run.run_id,
        profile_id=run.profile_id,
        profile_version=run.profile_version,
        profile_digest=run.profile_digest,
        dataset_version="visa-test",
        split="test",
        record_count=2,
        byte_count=100,
        records_sha256="2" * 64,
        source_index_uri="F:/source/shard_index.json",
        source_index_sha256="3" * 64,
        source_identity_sha256="4" * 64,
        manifest_uri=str(root / "manifest.jsonl"),
        manifest_sha256="5" * 64,
        snapshot_uri=str(root / "snapshot.json"),
        snapshot_digest="6" * 64,
        isolation_root=str(root),
        immutable=True,
        training_mount_isolated=True,
        status="pass",
        blockers=[],
        created_at="2026-07-13T00:00:00Z",
    )


def ct_evaluation(run, snapshot: CTDatasetSnapshot, tmp_path: Path) -> CTEvaluation:
    return CTEvaluation(
        evaluation_id="ct-eval-test",
        lifecycle_run_id=run.run_id,
        snapshot_id=snapshot.snapshot_id,
        candidate_id="efficientnet-b0-test",
        dataset_version=snapshot.dataset_version,
        status="pass",
        decision="pass",
        evaluated_at="2026-07-13T00:10:00Z",
        snapshot_digest=snapshot.snapshot_digest,
        expected_manifest_sha256=snapshot.manifest_sha256,
        observed_manifest_sha256=snapshot.manifest_sha256,
        expected_records_sha256=snapshot.records_sha256,
        observed_records_sha256=snapshot.records_sha256,
        ct_record_count=2,
        training_record_count=4,
        overlap_count=0,
        mutated=False,
        training_mount_isolated=True,
        model_artifact_uri=str(tmp_path / "model.pt"),
        model_sha256="7" * 64,
        device="cuda:0",
        metrics={"accuracy": 0.95},
        metric_thresholds={"accuracy": 0.93},
        checks={"record_overlap": "pass", "snapshot_integrity": "pass"},
        blockers=[],
        snapshot_uri=snapshot.snapshot_uri,
        report_uri=str(tmp_path / "ct-evaluation.json"),
    )


def install_ct_runtime_stubs(run, tmp_path: Path, monkeypatch):
    snapshot = ct_snapshot(run, tmp_path)
    evaluation = ct_evaluation(run, snapshot, tmp_path)
    model_path = tmp_path / "model.pt"
    model_path.write_bytes(b"real-model-fixture")
    model_digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    evaluation = evaluation.model_copy(
        update={"model_artifact_uri": str(model_path), "model_sha256": model_digest}
    )
    Path(str(evaluation.report_uri)).write_text(
        evaluation.model_dump_json(indent=2),
        encoding="utf-8",
    )
    readiness_path = tmp_path / "readiness.json"
    matrix_path = tmp_path / "model-matrix.json"
    image_digest = "sha256:" + "8" * 64
    mlflow_run_id = "mlflow-run-test"
    readiness_path.write_text(
        json.dumps(
            {
                "decision": "ready",
                "status": "pass",
                "candidate_id": evaluation.candidate_id,
                "dataset_version": evaluation.dataset_version,
                "checks": [
                    {
                        "check_id": "model_artifact",
                        "evidence_uri": str(model_path),
                        "observed": {"actual_sha256": model_digest},
                    },
                    {
                        "check_id": "kubernetes_runtime",
                        "observed": {"serving_image_digest": image_digest},
                    },
                    {
                        "check_id": "mlflow_run",
                        "observed": {"run_id": mlflow_run_id},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    matrix_path.write_text(
        json.dumps(
            {
                "status": "pass",
                "candidates": [
                    {
                        "candidate_id": evaluation.candidate_id,
                        "status": "pass",
                        "dataset_version": evaluation.dataset_version,
                        "model_sha256": model_digest,
                        "mlflow_run_id": mlflow_run_id,
                        "model_artifact": str(model_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    update_run_evidence(
        run.run_id,
        actor="test",
        readiness_uri=str(readiness_path),
        model_matrix_uri=str(matrix_path),
    )
    bundle = CTBundle(
        manifest_dir=tmp_path / "kubernetes" / "ct",
        namespace="evm-validation",
        job_name="evm-ct-test",
        candidate_id=evaluation.candidate_id,
        candidate_summary_path=tmp_path / "candidate_summary.json",
        model_artifact_path=tmp_path / "model.pt",
        fold_manifest_path=tmp_path / "fold_manifest.json",
        training_job_manifest_path=tmp_path / "training-job.json",
        image="training@sha256:" + "8" * 64,
    )
    bundle.manifest_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(lifecycle_orchestrator, "create_ct_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(lifecycle_orchestrator, "materialize_ct_bundle", lambda *_args, **_kwargs: bundle)
    monkeypatch.setattr(lifecycle_orchestrator, "acquire_training_gpu_handoff", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle_orchestrator, "release_training_gpu_handoff", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lifecycle_orchestrator,
        "execute_kubernetes_task",
        lambda task_id, **_kwargs: lifecycle_orchestrator.task_for(task_id).model_copy(
            update={"status": "done", "runtime_state": "complete"}
        ),
    )
    return snapshot, evaluation


def install_release_submission(run, tmp_path: Path, monkeypatch) -> None:
    _snapshot, evaluation = install_ct_runtime_stubs(run, tmp_path, monkeypatch)
    current = lifecycle_runs.get_lifecycle_run(run.run_id)
    assert current is not None
    submission = build_lifecycle_release_submission(
        artifact_root=Path(current.artifact_root),
        run_id=current.run_id,
        source_commit=str(current.source_commit),
        readiness_uri=str(current.readiness_uri),
        model_matrix_uri=str(current.model_matrix_uri),
        ct_evaluation_uri=str(evaluation.report_uri),
    )
    update_run_evidence(
        current.run_id,
        actor="test",
        ct_evaluation_uri=str(evaluation.report_uri),
        release_submission_uri=str(submission),
    )


def test_ci_ct_gate_requires_ci_and_isolated_ct_before_completion(
    tmp_path,
    monkeypatch,
) -> None:
    run = ct_ready_run(tmp_path, monkeypatch)
    snapshot, evaluation = install_ct_runtime_stubs(run, tmp_path, monkeypatch)
    ci = SimpleNamespace(valid=True, blockers=[], workflow_run_id="gha-123")
    gate = CDCTGate(
        status="pass",
        ci_status="pass",
        cd_status="pass",
        ct_status="pass",
        required_checks=["ci_evidence", "isolated_ct_evaluation"],
        passed_checks=["ci_evidence", "isolated_ct_evaluation"],
        promotion_decision="allow",
        ct_snapshot_id=snapshot.snapshot_id,
        ct_evaluation_id=evaluation.evaluation_id,
    )
    cycles = iter(
        [
            SimpleNamespace(ci_evidence=ci),
            SimpleNamespace(ci_evidence=ci, cdct_gate=gate),
        ]
    )
    monkeypatch.setattr(lifecycle_orchestrator, "rebuild_cycle", lambda _run: next(cycles))
    monkeypatch.setattr(lifecycle_orchestrator, "load_ct_evaluation", lambda: evaluation)

    completed = process_ci_ct_gate(
        run,
        runner=lambda *_args, **_kwargs: None,
        http_client=lambda *_args, **_kwargs: (200, {}),
    )

    stage = next(item for item in completed.stages if item.stage_id == "ci_ct_gate")
    assert stage.state == "completed"
    assert stage.evidence_uri == evaluation.report_uri
    assert completed.ct_snapshot_uri == snapshot.snapshot_uri
    assert completed.ct_evaluation_uri == evaluation.report_uri
    assert completed.release_submission_uri


def test_ci_ct_gate_fails_closed_when_gpu_job_produces_no_evaluation(
    tmp_path,
    monkeypatch,
) -> None:
    run = ct_ready_run(tmp_path, monkeypatch)
    install_ct_runtime_stubs(run, tmp_path, monkeypatch)
    monkeypatch.setattr(
        lifecycle_orchestrator,
        "rebuild_cycle",
        lambda _run: SimpleNamespace(
            ci_evidence=SimpleNamespace(valid=True, blockers=[], workflow_run_id="gha-123")
        ),
    )
    monkeypatch.setattr(lifecycle_orchestrator, "load_ct_evaluation", lambda: None)

    with pytest.raises(LifecycleStageBlocked, match="ct_evaluation_missing"):
        process_ci_ct_gate(
            run,
            runner=lambda *_args, **_kwargs: None,
            http_client=lambda *_args, **_kwargs: (200, {}),
        )


def test_prometheus_target_uses_container_reachable_host(tmp_path, monkeypatch) -> None:
    run = queued_run(tmp_path, monkeypatch)
    target_path = tmp_path / "prometheus" / "target.json"
    monkeypatch.setenv("EVM_PROMETHEUS_FILE_SD_PATH", str(target_path))

    write_prometheus_target(
        run,
        ServingBundle(
            manifest_dir=tmp_path,
            namespace="evm-staging",
            deployment_name="evm-b0-staging",
            endpoint="http://127.0.0.1:30813",
            image="serving@sha256:" + "a" * 64,
        ),
    )

    payload = json.loads(target_path.read_text(encoding="utf-8"))
    assert payload[0]["targets"] == ["host.docker.internal:30813"]
    assert payload[0]["labels"]["lifecycle_run_id"] == run.run_id

    foreign = {
        "targets": ["host.docker.internal:30999"],
        "labels": {"job": "evm-lifecycle-serving", "lifecycle_run_id": "other-run"},
    }
    target_path.write_text(json.dumps([payload[0], foreign]), encoding="utf-8")

    clear_prometheus_target(run)

    assert json.loads(target_path.read_text(encoding="utf-8")) == [foreign]


def test_deployment_revalidates_release_seal_before_any_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    run = queued_run(tmp_path, monkeypatch)
    submission = tmp_path / "tampered-release-submission.json"
    submission.write_text("{}", encoding="utf-8")
    guarded = run.model_copy(
        update={
            "approver": "release-approver@example.com",
            "release_submission_uri": str(submission),
        }
    )
    mutation_called = False

    def unexpected_mutation():
        nonlocal mutation_called
        mutation_called = True

    monkeypatch.setattr(lifecycle_orchestrator, "ensure_generated_manifest_root", unexpected_mutation)

    with pytest.raises(LifecycleStageBlocked, match="lifecycle_release_integrity_blocked"):
        process_deployment(
            guarded,
            runner=lambda *_args, **_kwargs: None,
            http_client=lambda *_args, **_kwargs: (200, {}),
        )

    assert mutation_called is False

def test_rollback_executor_failure_terminates_run_with_explicit_blocker(tmp_path, monkeypatch) -> None:
    run = queued_run(tmp_path, monkeypatch)
    transition_stage(run.run_id, "data_pipeline", "running", actor="test")
    run = transition_stage(
        run.run_id,
        "data_pipeline",
        "failed",
        actor="test",
        detail="deployment prerequisite failed",
    )
    monkeypatch.setattr(
        lifecycle_orchestrator,
        "execute_rollback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rollback command failed")),
    )

    result = rollback_lifecycle(run, "deploy-1", lambda *_args, **_kwargs: None, "test")

    assert result.state == "failed"
    assert "lifecycle_rollback_failed" in result.blockers
    assert "rollback command failed" in (result.failure_reason or "")
