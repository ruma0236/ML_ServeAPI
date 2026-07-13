from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from evm.control_panel.lifecycle_kubernetes import (
    LifecycleKubernetesError,
    build_training_evidence,
    materialize_training_bundle,
)
from evm.control_panel.lifecycle_runs import LifecycleRunRequest, create_lifecycle_run
from evm.control_panel.operations import create_task_assignment, update_task_runtime
from evm.control_panel.pipeline_profiles import default_profile, save_profile
from evm.control_panel.schemas import TaskAssignmentRequest


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def lifecycle_run(tmp_path: Path, monkeypatch):
    project = Path(__file__).resolve().parents[1]
    data_root = tmp_path / "evm-data"
    source = data_root / "data" / "quality.jsonl"
    shard = data_root / "data" / "shard_index.json"
    train_shard = data_root / "data" / "train.jsonl"
    image = data_root / "data" / "raw" / "sample-1.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"real-image-bytes")
    source.write_text('{"sample_id":"sample-1"}\n', encoding="utf-8")
    train_shard.write_text(
        json.dumps(
            {
                "sample_id": "sample-1",
                "image_uri": "/mnt/evm-data/data/raw/sample-1.png",
                "image_path": "/mnt/evm-data/data/raw/sample-1.png",
                "label_type": "normal",
                "split": "train",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    identity = "a" * 64
    write_json(
        shard,
        {
            "schema_version": "evm.dataset_shards.v1",
            "identity_sha256": identity,
            "shards": [
                {
                    "shard_id": "train-0000",
                    "split": "train",
                    "path": str(train_shard),
                    "record_count": 1,
                }
            ],
        },
    )
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(project))
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(data_root))
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_ROOT", str(data_root / "artifacts" / "profiles"))
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_RUNTIME_ROOT", "/mnt/evm-data/artifacts/profiles")
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(data_root / "artifacts" / "lifecycle"))
    monkeypatch.setenv("EVM_LIFECYCLE_RUNTIME_ROOT", "/mnt/evm-data/artifacts/lifecycle")
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(data_root / "artifacts" / "operations"))
    profile = default_profile()
    profile = profile.model_copy(
        update={
            "profile_name": "p",
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
    return create_lifecycle_run(
        LifecycleRunRequest(
            profile_id=record.profile_id,
            profile_version=record.version,
            actor="ml-platform",
            reason="Validate generated Kubernetes lifecycle resources",
            dry_run=True,
        )
    )


def test_training_bundle_renders_profile_resources_and_pinned_image(tmp_path, monkeypatch) -> None:
    run = lifecycle_run(tmp_path, monkeypatch)
    bundle = materialize_training_bundle(run)

    job = json.loads((bundle.manifest_dir / "training-job.json").read_text(encoding="utf-8"))
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert bundle.namespace == "evm-training"
    assert container["image"].endswith(
        "@sha256:9f77d41bfcbecb82a9dfaa42aec621e43621ed1fd1c3110315b8524c808dd5d5"
    )
    assert container["resources"]["requests"] == {
        "cpu": "6",
        "memory": "16Gi",
        "nvidia.com/gpu": "1",
    }
    assert run.model_runtime_uri in container["command"][2]
    assert (bundle.manifest_dir / "kustomization.yaml").is_file()
    pv = json.loads((bundle.manifest_dir / "storage-pv.json").read_text(encoding="utf-8"))
    pvc = json.loads((bundle.manifest_dir / "storage-pvc.json").read_text(encoding="utf-8"))
    storage_suffix = bundle.job_name.removeprefix("evm-lifecycle-train-")
    expected_pv = f"evm-training-{storage_suffix}-pv"
    expected_pvc = f"evm-training-{storage_suffix}-pvc"
    assert pv["metadata"]["name"] == expected_pv
    assert pv["spec"]["claimRef"]["name"] == expected_pvc
    assert pv["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
    assert pv["spec"]["accessModes"] == ["ReadOnlyMany"]
    assert pvc["metadata"]["name"] == expected_pvc
    assert pvc["spec"]["volumeName"] == expected_pv
    assert pvc["spec"]["accessModes"] == ["ReadOnlyMany"]
    assert "training_views" in pv["spec"]["hostPath"]["path"]
    data_volume = next(
        item for item in job["spec"]["template"]["spec"]["volumes"] if item["name"] == "large-data"
    )
    assert data_volume["persistentVolumeClaim"]["claimName"] == expected_pvc
    assert any(
        item == {
            "name": "EVM_TRAINING_DATA_SCOPE",
            "value": "development-only",
        }
        for item in container["env"]
    )
    data_mount = next(
        item for item in container["volumeMounts"] if item["name"] == "large-data"
    )
    assert data_mount["readOnly"] is True


def test_training_bundle_uses_blueprint_component_image_and_rejects_unpinned_image(
    tmp_path,
    monkeypatch,
) -> None:
    run = lifecycle_run(tmp_path, monkeypatch)
    model_path = Path(run.model_config_uri)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    selected = model["candidates"][0]
    selected["training_image"] = "registry.example/evm-trainer@sha256:" + "d" * 64
    write_json(model_path, model)

    bundle = materialize_training_bundle(run)

    assert bundle.image == selected["training_image"]
    selected["training_image"] = "registry.example/evm-trainer:latest"
    write_json(model_path, model)
    with pytest.raises(LifecycleKubernetesError, match="container_image_not_pinned"):
        materialize_training_bundle(run)


def test_training_bundle_routes_enabled_search_to_experiment_pipeline(
    tmp_path,
    monkeypatch,
) -> None:
    run = lifecycle_run(tmp_path, monkeypatch)
    model_path = Path(run.model_config_uri)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model["experiment_search"]["enabled"] = True
    write_json(model_path, model)

    bundle = materialize_training_bundle(run)
    job = json.loads((bundle.manifest_dir / "training-job.json").read_text(encoding="utf-8"))
    container = job["spec"]["template"]["spec"]["containers"][0]

    assert "evm.pipelines.experiment_search.run" in container["command"][2]
    assert "--require-pass" in container["command"][2]
    assert any(
        item == {
            "name": "EVM_EXPERIMENT_RUN_ROOT",
            "value": "/mnt/evm-data/artifacts/w8/experiment_runs",
        }
        for item in container["env"]
    )


def test_training_image_installs_experiment_runtime_dependencies() -> None:
    dockerfile = Path("infra/docker/efficientnet-training/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "optuna==4.9.0" in dockerfile
    assert "pydantic==2.10.3" in dockerfile
    assert (
        "COPY configs/w7_efficientnet_kubernetes.toml "
        "/app/configs/w7_efficientnet_kubernetes.toml"
    ) in dockerfile
    assert "COPY configs /app/configs" not in dockerfile
    build_script = Path("scripts/dev/build_efficientnet_training_image.ps1").read_text(
        encoding="utf-8"
    )
    assert "docker build --provenance=false" in build_script
    assert "catalog_repo_digest" in build_script


def test_training_evidence_binds_job_gpu_mlflow_and_model_digest(tmp_path, monkeypatch) -> None:
    run = lifecycle_run(tmp_path, monkeypatch)
    bundle = materialize_training_bundle(run)
    model_config = json.loads(Path(run.model_config_uri).read_text(encoding="utf-8"))
    artifact_root = Path(model_config["resources"]["artifact_root"])
    candidate_id = model_config["model_matrix"]["selected_candidate_id"]
    candidate_dir = artifact_root / run.run_id / candidate_id
    model_artifact = candidate_dir / "model.pt"
    model_artifact.parent.mkdir(parents=True, exist_ok=True)
    model_artifact.write_bytes(b"real-torch-checkpoint")
    digest = hashlib.sha256(model_artifact.read_bytes()).hexdigest()
    write_json(
        candidate_dir / "candidate_summary.json",
        {
            "candidate_id": candidate_id,
            "dataset_version": "visa-open-data-e35d93d5561f",
            "status": "pass",
            "model_artifact": str(model_artifact),
            "model_sha256": digest,
            "mlflow_run_id": "mlflow-run-1",
        },
    )
    write_json(
        artifact_root / "latest_model_matrix.json",
        {
            "status": "pass",
            "candidates": [{"candidate_id": candidate_id, "artifact_uri": str(candidate_dir)}],
        },
    )
    task = create_task_assignment(
        TaskAssignmentRequest(
            cycle_id=run.run_id,
            task_type="kubernetes_job",
            owner="ml-platform",
            priority="high",
            resource_profile="docker-desktop-gpu",
            approval_policy="auto",
            config_payload={
                "adapter": "host-kubectl-bridge",
                "manifest_dir": str(bundle.manifest_dir),
                "namespace": bundle.namespace,
                "job_name": bundle.job_name,
            },
            dry_run=False,
        )
    )
    task = update_task_runtime(
        task.task_id,
        actor="test",
        event="completed",
        status="done",
        runtime_system="kubernetes",
        runtime_id=f"{bundle.namespace}/job/{bundle.job_name}",
        runtime_state="complete",
        runtime_evidence_uri=str(tmp_path / "job-evidence"),
    )
    assert task is not None

    def runner(command, **_kwargs):
        if command[2] == "job":
            payload = {"metadata": {"uid": "job-uid-1"}, "status": {"succeeded": 1}}
        else:
            payload = {
                "items": [
                    {"status": {"allocatable": {"nvidia.com/gpu": "1"}}}
                ]
            }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    evidence, evidence_path = build_training_evidence(run, task, bundle, runner=runner)

    assert evidence["status"] == "pass"
    assert evidence["completion_claim_allowed"] is True
    assert evidence["job_uid"] == "job-uid-1"
    assert evidence["gpu_allocatable"] == "1"
    assert evidence["mlflow_run_id"] == "mlflow-run-1"
    assert evidence["trained_model_sha256"] == digest
    assert evidence_path.is_file()
