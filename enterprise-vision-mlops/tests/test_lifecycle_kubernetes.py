from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from evm.control_panel.lifecycle_kubernetes import (
    LifecycleKubernetesError,
    build_training_evidence,
    ct_evaluation_command,
    materialize_ct_bundle,
    materialize_training_bundle,
    serving_command,
    training_command,
)
from evm.control_panel.lifecycle_runs import LifecycleRunRequest, create_lifecycle_run
from evm.control_panel.operations import create_task_assignment, update_task_runtime
from evm.control_panel.pipeline_profiles import default_profile, save_profile
from evm.control_panel.schemas import CTDatasetSnapshot, TaskAssignmentRequest


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
    run = create_lifecycle_run(
        LifecycleRunRequest(
            profile_id=record.profile_id,
            profile_version=record.version,
            actor="ml-platform",
            reason="Validate generated Kubernetes lifecycle resources",
            dry_run=True,
        )
    )
    model = json.loads(Path(run.model_config_uri).read_text(encoding="utf-8"))
    lifecycle_index = Path(
        str(model["inputs"]["shard_index"]).replace("/mnt/evm-data", str(data_root))
    )
    lifecycle_index.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_index.write_bytes(shard.read_bytes())
    return run


def test_training_bundle_renders_profile_resources_and_pinned_image(tmp_path, monkeypatch) -> None:
    run = lifecycle_run(tmp_path, monkeypatch)
    bundle = materialize_training_bundle(run)

    job = json.loads((bundle.manifest_dir / "training-job.json").read_text(encoding="utf-8"))
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert bundle.namespace == "evm-training"
    assert container["image"].endswith(
        "@sha256:098fed87af346d67ac0bf5d891a087f08a7aade9bf4cd429f2986a425224d8d4"
    )
    assert container["resources"]["requests"] == {
        "cpu": "6",
        "memory": "16Gi",
        "nvidia.com/gpu": "1",
    }
    assert "/kubernetes/training/training.runtime.json" in container["command"][2]
    training_config = json.loads(
        (bundle.manifest_dir / "training.runtime.json").read_text(encoding="utf-8")
    )
    assert training_config["inputs"]["shard_index"] == "/mnt/evm-data/data/shard_index.json"
    assert (
        training_config["inputs"]["shard_identity_sha256"]
        == training_config["training_view"]["identity_sha256"]
    )
    assert training_config["inputs"]["lifecycle_shard_identity_sha256"] == "a" * 64
    assert training_config["inputs"]["training_data_scope"] == "development-only"
    assert training_config["inputs"]["ct_evidence_exposed"] is False
    assert training_config["training_view"]["excluded_split"] == "test"
    assert bundle.progress_path == Path(run.artifact_root) / "training-progress.json"
    usage_manifest = json.loads(
        (bundle.manifest_dir / "fold_manifest.json").read_text(encoding="utf-8")
    )
    assert usage_manifest["schema_version"] == "evm.training_data_usage_manifest.v1"
    assert usage_manifest["development_records"] == 1
    assert usage_manifest["holdout_used_for_selection"] is False
    assert usage_manifest["ct_evidence_exposed"] is False
    assert len(usage_manifest["assignments"]) == 1
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
    assert pv["spec"]["hostPath"]["path"].endswith("/data")
    data_volume = next(
        item for item in job["spec"]["template"]["spec"]["volumes"] if item["name"] == "large-data"
    )
    assert data_volume["persistentVolumeClaim"]["claimName"] == expected_pvc
    data_mount = next(
        item
        for item in job["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
        if item["name"] == "large-data"
    )
    assert data_mount == {
        "name": "large-data",
        "mountPath": "/mnt/evm-data/data",
        "readOnly": True,
    }
    assert any(
        item == {
            "name": "EVM_TRAINING_DATA_SCOPE",
            "value": "development-only",
        }
        for item in container["env"]
    )
    assert any(
        item["name"] == "EVM_TRAINING_RUNTIME_CONFIG_SHA256" and len(item["value"]) == 64
        for item in container["env"]
    )
    assert any(
        item
        == {
            "name": "EVM_EXPECTED_COMPONENT_SOURCE_REVISION",
            "value": "3b7dde35910414434563afc46e0ca9aeb9ec6352",
        }
        for item in container["env"]
    )
    assert "component_source_revision_mismatch" in container["command"][2]
    data_mount = next(
        item for item in container["volumeMounts"] if item["name"] == "large-data"
    )
    assert data_mount["readOnly"] is True


def test_ct_bundle_uses_manual_training_usage_manifest(tmp_path, monkeypatch) -> None:
    run = lifecycle_run(tmp_path, monkeypatch)
    training_bundle = materialize_training_bundle(run)
    model = json.loads(Path(run.model_config_uri).read_text(encoding="utf-8"))
    artifact_root = Path(model["resources"]["artifact_root"])
    candidate_id = model["model_matrix"]["selected_candidate_id"]
    candidate_dir = artifact_root / run.run_id / candidate_id
    model_artifact = candidate_dir / "model.pt"
    model_artifact.parent.mkdir(parents=True, exist_ok=True)
    model_artifact.write_bytes(b"real-torch-checkpoint")
    write_json(
        candidate_dir / "candidate_summary.json",
        {
            "candidate_id": candidate_id,
            "dataset_version": "visa-open-data-e35d93d5561f",
            "status": "pass",
            "model_artifact": str(model_artifact),
        },
    )
    write_json(
        artifact_root / "latest_model_matrix.json",
        {
            "status": "pass",
            "candidates": [
                {"candidate_id": candidate_id, "artifact_uri": str(candidate_dir)}
            ],
        },
    )
    ct_root = tmp_path / "evm-ct"
    snapshot_root = ct_root / "snapshots" / "snapshot-test"
    snapshot_root.mkdir(parents=True)
    monkeypatch.setenv("EVM_HOST_CT_ROOT", str(ct_root))
    snapshot = CTDatasetSnapshot(
        snapshot_id="snapshot-test",
        lifecycle_run_id=run.run_id,
        profile_id=run.profile_id,
        profile_version=run.profile_version,
        profile_digest=run.profile_digest,
        dataset_version="visa-open-data-e35d93d5561f",
        split="test",
        record_count=1,
        byte_count=10,
        records_sha256="1" * 64,
        source_records_sha256="2" * 64,
        source_index_uri="F:/source/shard_index.json",
        source_index_sha256="3" * 64,
        source_identity_sha256="4" * 64,
        manifest_uri=str(snapshot_root / "holdout_manifest.jsonl"),
        manifest_sha256="5" * 64,
        snapshot_uri=str(snapshot_root / "snapshot.json"),
        snapshot_digest="6" * 64,
        isolation_root=str(snapshot_root),
        immutable=True,
        training_mount_isolated=True,
        status="pass",
        created_at="2026-07-13T00:00:00Z",
    )

    bundle = materialize_ct_bundle(run, snapshot)

    assert bundle.fold_manifest_path == training_bundle.manifest_dir / "fold_manifest.json"
    assert bundle.training_job_manifest_path == training_bundle.manifest_dir / "training-job.json"
    assert (bundle.manifest_dir / "ct-job.json").is_file()


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
    assert any(
        item["name"] == "EVM_TRAINING_PROGRESS_PATH"
        and item["value"].endswith(f"/{run.run_id}/training-progress.json")
        for item in container["env"]
    )

    assert "evm.pipelines.experiment_search.run" in container["command"][2]
    assert "--require-pass" in container["command"][2]
    assert any(
        item == {
            "name": "EVM_EXPERIMENT_RUN_ROOT",
            "value": "/mnt/evm-data/artifacts/w8/experiment_runs",
        }
        for item in container["env"]
    )


def test_runtime_commands_fail_closed_on_component_image_revision_skew() -> None:
    commands = [
        training_command("/mnt/evm-data/config.json", experiment_search=True),
        ct_evaluation_command("snapshot-1", "--threshold f1=0.3"),
        serving_command(),
    ]

    for command in commands:
        assert "EVM_IMAGE_SOURCE_REVISION" in command
        assert "EVM_EXPECTED_COMPONENT_SOURCE_REVISION" in command
        assert "component_source_revision_mismatch" in command


def test_training_bundle_rejects_lifecycle_shard_identity_drift(
    tmp_path,
    monkeypatch,
) -> None:
    run = lifecycle_run(tmp_path, monkeypatch)
    model = json.loads(Path(run.model_config_uri).read_text(encoding="utf-8"))
    data_root = Path(str(tmp_path / "evm-data"))
    lifecycle_index = Path(
        str(model["inputs"]["shard_index"]).replace("/mnt/evm-data", str(data_root))
    )
    payload = json.loads(lifecycle_index.read_text(encoding="utf-8"))
    payload["identity_sha256"] = "b" * 64
    write_json(lifecycle_index, payload)

    with pytest.raises(LifecycleKubernetesError, match="lifecycle_shard_identity_mismatch"):
        materialize_training_bundle(run)


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
    assert 'org.opencontainers.image.revision="${SOURCE_REVISION}"' in dockerfile
    assert "EVM_IMAGE_SOURCE_REVISION=${SOURCE_REVISION}" in dockerfile
    serving_dockerfile = Path("infra/docker/efficientnet-serving/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert 'org.opencontainers.image.revision="${SOURCE_REVISION}"' in serving_dockerfile
    assert "EVM_IMAGE_SOURCE_REVISION=${SOURCE_REVISION}" in serving_dockerfile
    build_script = Path("scripts/dev/build_efficientnet_training_image.ps1").read_text(
        encoding="utf-8"
    )
    assert "docker build --provenance=false" in build_script
    assert "SOURCE_REVISION=$sourceRevision" in build_script
    assert "catalog_training_repo_digest" in build_script
    assert "catalog_serving_repo_digest" in build_script
    assert "Preserve-CatalogImage" in build_script
    assert ":retained-$shortDigest" in build_script


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
