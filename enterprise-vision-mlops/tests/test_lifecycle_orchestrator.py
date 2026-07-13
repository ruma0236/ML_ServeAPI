from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from evm.control_panel import lifecycle_orchestrator, lifecycle_runs, operations
from evm.control_panel.lifecycle_kubernetes import ServingBundle
from evm.control_panel.lifecycle_orchestrator import (
    clear_prometheus_target,
    process_artifact_readiness,
    process_lifecycle_run,
    rollback_lifecycle,
    write_prometheus_target,
)
from evm.control_panel.lifecycle_runs import (
    LifecycleActionRequest,
    LifecycleRunRequest,
    create_lifecycle_run,
    queue_lifecycle_run,
    transition_stage,
)
from evm.control_panel.pipeline_profiles import default_profile, save_profile
from evm.control_panel.schemas import ArtifactReadinessEvaluation, ReadinessEvidenceCheck


class FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def queued_run(tmp_path: Path, monkeypatch):
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
    assert result.stages[1].evidence_uri.endswith("provenance-validation.json")


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
