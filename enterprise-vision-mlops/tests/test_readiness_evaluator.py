from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel import readiness_evaluator
from evm.control_panel.org_context import build_default_org_context
from evm.control_panel.readiness_evaluator import (
    ReadinessInputs,
    canonical_evidence_uri,
    check_source_shard,
    evaluate_artifact_readiness,
    file_sha256,
    runtime_path,
)
from evm.control_panel.validate_readiness import validate_evaluation


CANDIDATE_ID = "effnet-b7-img600-finetune-adamw"
DATASET_VERSION = "visa-open-data-f1f1c9ee9922"
RUN_ID = "a4e2763b28ae494ea67944084edd4b3f"


def test_source_shard_accepts_embedded_identity_digest(tmp_path: Path) -> None:
    source = tmp_path / "shard-index.json"
    digest = "6" * 64
    source.write_text(
        json.dumps(
            {
                "schema_version": "evm.dataset_shards.v1",
                "record_count": 10821,
                "identity_sha256": digest,
            }
        ),
        encoding="utf-8",
    )

    check = check_source_shard(source, digest, 10821)

    assert check.status == "pass"
    assert check.observed["actual_sha256"] == digest


def test_canonical_evidence_uri_maps_container_mount_to_f_drive(monkeypatch):
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    )

    assert canonical_evidence_uri(Path("/mnt/evm-data/artifacts/w7/readiness.json")) == (
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/readiness.json"
    )
    assert canonical_evidence_uri(Path("/app/artifacts/registry/model/latest.json")) == (
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/registry/model/latest.json"
    )
    assert canonical_evidence_uri(Path("/app/domain_packs/mvi/data_contract.toml")) == (
        "domain_packs/mvi/data_contract.toml"
    )


def test_runtime_path_maps_container_mount_to_existing_host_artifact(tmp_path, monkeypatch):
    host_root = tmp_path / "evm-data"
    artifact = host_root / "artifacts" / "candidate_summary.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(host_root))

    assert runtime_path("/mnt/evm-data/artifacts/candidate_summary.json") == artifact


def test_runtime_path_preserves_new_write_target_when_host_root_exists(tmp_path, monkeypatch):
    host_root = tmp_path / "evm-data"
    host_root.mkdir()
    target = host_root / "artifacts" / "new-evidence.json"
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(host_root))

    assert runtime_path(target) == target


def test_runtime_path_maps_api_artifact_uri_to_existing_host_root(tmp_path, monkeypatch):
    host_root = tmp_path / "evm-data"
    host_root.mkdir()
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(host_root))

    assert runtime_path("/app/artifacts/w7/lifecycle/run.json") == (
        host_root / "artifacts" / "w7" / "lifecycle" / "run.json"
    )


def test_runtime_path_prefers_writable_application_artifact_mount(monkeypatch):
    target = Path("/app/artifacts/w7/ci/latest_ci_validation.json")
    monkeypatch.setattr(
        readiness_evaluator,
        "application_artifacts_available",
        lambda: True,
    )

    assert runtime_path(target) == target


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_fixture(tmp_path: Path) -> tuple[ReadinessInputs, dict[str, object]]:
    contract = tmp_path / "data_contract.toml"
    contract.write_text(
        """
[contract]
id = "mvi_image_manifest_contract_v1"
version = "2026.07"

[[fields]]
name = "dataset_id"
required = true
[[fields]]
name = "dataset_version"
required = true
[[fields]]
name = "sample_id"
required = true
[[fields]]
name = "image_uri"
required = true
[[fields]]
name = "split"
required = true
[[fields]]
name = "label"
required = true
[[fields]]
name = "content_sha256"
required = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    dataset_metadata = tmp_path / "dataset_version.json"
    write_json(
        dataset_metadata,
        {"dataset_version": DATASET_VERSION, "valid_records": 6},
    )
    quality = tmp_path / "quality_report.json"
    write_json(
        quality,
        {
            "status": "pass",
            "dataset_version": DATASET_VERSION,
            "record_count": 6,
            "gate_decision": {"status": "pass", "blocking_count": 0},
        },
    )
    shard = tmp_path / "shard_index.json"
    write_json(shard, {"schema_version": "evm.dataset_shards.v1", "record_count": 6})
    shard_digest = file_sha256(shard)
    split = tmp_path / "split_manifest.json"
    write_json(
        split,
        {
            "schema_version": "evm.w7.efficientnet_split_manifest.v1",
            "dataset_version": DATASET_VERSION,
            "record_count": 6,
            "split_counts": {"train": 2, "validation": 2, "test": 2},
            "source_shard_index_sha256": shard_digest,
        },
    )
    lineage = tmp_path / "lineage.json"
    write_json(
        lineage,
        {
            "schema_version": "evm.model_lineage_matrix.v1",
            "candidate_id": CANDIDATE_ID,
            "dataset_version": DATASET_VERSION,
            "source_shard_index_sha256": shard_digest,
        },
    )
    model = tmp_path / "model.pt"
    model.write_bytes(b"real-model-weights")
    model_digest = file_sha256(model)
    candidate = tmp_path / "candidate_summary.json"
    metrics = {
        "accuracy": 0.96,
        "precision": 0.79,
        "recall": 0.84,
        "f1": 0.81,
        "auroc": 0.97,
    }
    write_json(
        candidate,
        {
            "candidate_id": CANDIDATE_ID,
            "dataset_version": DATASET_VERSION,
            "status": "pass",
            "metrics": metrics,
            "promotion_blockers": [],
            "artifact_uri": str(tmp_path),
            "model_artifact": str(model),
            "mlflow_run_id": RUN_ID,
            "run_uri": f"http://mlflow/#/runs/{RUN_ID}",
        },
    )
    model_card = tmp_path / "model_card.md"
    model_card.write_text(
        f"# {CANDIDATE_ID} Model Card\n"
        f"- Dataset version: `{DATASET_VERSION}`\n"
        f"- MLflow run id: `{RUN_ID}`\n",
        encoding="utf-8",
    )
    rollback_model = tmp_path / "rollback-model.pt"
    rollback_model.write_bytes(b"rollback-model")
    rollback_digest = file_sha256(rollback_model)
    registry = tmp_path / "registry.json"
    write_json(
        registry,
        {
            "version": "1",
            "model_name": "effnet-b0-previous-production",
            "model_digest": rollback_digest,
            "model_artifact": str(rollback_model),
            "status": "approved",
            "rollback_ready": True,
        },
    )
    real_test = tmp_path / "real_test.json"
    write_json(
        real_test,
        {
            "valid": True,
            "split_manifest": {"dataset_version": DATASET_VERSION},
            "checked_candidates": [{"candidate_id": CANDIDATE_ID, "status": "pass"}],
        },
    )
    kubernetes = tmp_path / "kubernetes.json"
    write_json(
        kubernetes,
        {
            "status": "pass",
            "completion_claim_allowed": True,
            "candidate_id": CANDIDATE_ID,
            "dataset_version": DATASET_VERSION,
            "source_mlflow_run_id": RUN_ID,
            "source_model_sha256": rollback_digest,
            "mlflow_run_id": RUN_ID,
            "trained_model_sha256": model_digest,
            "gpu_allocatable": "1",
            "blockers": [],
        },
    )
    mlflow_payload: dict[str, object] = {
        "run": {
            "info": {
                "run_id": RUN_ID,
                "run_name": CANDIDATE_ID,
                "status": "FINISHED",
                "artifact_uri": f"mlflow-artifacts:/4/{RUN_ID}/artifacts",
            },
            "data": {
                "params": [
                    {"key": "candidate_id", "value": CANDIDATE_ID},
                    {"key": "dataset_version", "value": DATASET_VERSION},
                    {"key": "artifact_uri", "value": str(tmp_path)},
                ],
                "metrics": [
                    {"key": key, "value": value} for key, value in metrics.items()
                ],
            },
        }
    }
    return (
        ReadinessInputs(
            contract_path=contract,
            dataset_metadata_path=dataset_metadata,
            quality_report_path=quality,
            source_shard_index_path=shard,
            split_manifest_path=split,
            lineage_path=lineage,
            candidate_summary_path=candidate,
            model_card_path=model_card,
            registry_path=registry,
            real_test_validation_path=real_test,
            kubernetes_evidence_path=kubernetes,
            mlflow_tracking_uri="http://mlflow:5000",
            candidate_id=CANDIDATE_ID,
            dataset_version=DATASET_VERSION,
            expected_record_count=6,
            expected_source_digest=shard_digest,
            metric_thresholds={"accuracy": 0.8, "f1": 0.75, "auroc": 0.8},
            report_uri=str(tmp_path / "readiness.json"),
        ),
        mlflow_payload,
    )


def test_artifact_readiness_returns_ready_only_when_all_content_agrees(tmp_path: Path):
    inputs, mlflow_payload = build_fixture(tmp_path)
    result = evaluate_artifact_readiness(
        inputs,
        build_default_org_context(),
        mlflow_loader=lambda _uri, _run_id: (200, mlflow_payload),
    )

    assert result.evaluation.decision == "ready"
    assert result.evaluation.status == "pass"
    assert result.evaluation.blockers == []
    assert len(result.evaluation.checks) == 13
    assert result.data_pipeline.blockers == []
    assert result.experiment_pipeline.promotion_ready is True
    assert validate_evaluation(result.evaluation)["valid"] is True


def test_artifact_readiness_blocks_stale_empty_quality_with_deterministic_id(tmp_path: Path):
    inputs, mlflow_payload = build_fixture(tmp_path)
    write_json(
        inputs.quality_report_path,
        {
            "status": "pass",
            "dataset_version": "visa-open-data-2026.07",
            "record_count": 0,
            "gate_decision": {"status": "pass", "blocking_count": 0},
        },
    )
    first = evaluate_artifact_readiness(
        inputs,
        build_default_org_context(),
        mlflow_loader=lambda _uri, _run_id: (200, mlflow_payload),
    )
    second = evaluate_artifact_readiness(
        inputs,
        build_default_org_context(),
        mlflow_loader=lambda _uri, _run_id: (200, mlflow_payload),
    )

    assert first.evaluation.decision == "blocked"
    assert "quality_evidence_empty" in first.evaluation.blockers
    assert "quality_record_count_mismatch" in first.evaluation.blockers
    assert "quality_dataset_version_mismatch" in first.evaluation.blockers
    assert first.evaluation.evaluation_id == second.evaluation.evaluation_id
    assert first.evaluation.input_digest == second.evaluation.input_digest
    assert first.data_pipeline.quality_status == "blocked"


def test_artifact_readiness_blocks_mlflow_run_without_metrics(tmp_path: Path):
    inputs, mlflow_payload = build_fixture(tmp_path)
    run = mlflow_payload["run"]
    assert isinstance(run, dict)
    data = run["data"]
    assert isinstance(data, dict)
    data["metrics"] = []

    result = evaluate_artifact_readiness(
        inputs,
        build_default_org_context(),
        mlflow_loader=lambda _uri, _run_id: (200, mlflow_payload),
    )

    assert result.evaluation.decision == "blocked"
    assert "mlflow_metrics_missing" in result.evaluation.blockers
    assert result.experiment_pipeline.tracking_status == "blocked"


def test_artifact_readiness_blocks_path_existence_without_valid_content(tmp_path: Path):
    inputs, mlflow_payload = build_fixture(tmp_path)
    inputs.split_manifest_path.write_text("{}\n", encoding="utf-8")

    result = evaluate_artifact_readiness(
        inputs,
        build_default_org_context(),
        mlflow_loader=lambda _uri, _run_id: (200, mlflow_payload),
    )

    split_check = next(
        check for check in result.evaluation.checks if check.check_id == "split_manifest"
    )
    assert split_check.status == "blocked"
    assert "split_record_count_mismatch" in split_check.blockers
    assert "split_source_digest_missing" in split_check.blockers
