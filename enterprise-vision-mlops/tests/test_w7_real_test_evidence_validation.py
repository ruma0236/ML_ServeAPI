from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.real_test_policy import validate_real_test_evidence
from evm.control_panel.schemas import (
    CycleRun,
    DatasetVersion,
    Metric,
    ModelCandidate,
    ModelExperimentMatrix,
    ModelVersion,
    RealTestPolicy,
    ServingState,
)


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_project_files(root: Path) -> None:
    (root / "pyproject.toml").write_text("[project]\nname='tmp-evm'\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")


def _write_config(root: Path, artifact_root: Path) -> Path:
    config = root / "configs" / "w7_efficientnet_real_test.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"""
[model_matrix]
matrix_id = "w7-efficientnet-real-test-matrix"
dataset_version = "visa-open-data-test"
mock_allowed = false
smoke_allowed = false
requires_real_dataset = true
requires_real_training = true
minimum_records = 10821

[resources]
artifact_root = "{artifact_root.as_posix()}"

[[candidates]]
candidate_id = "effnet-b0-img224-freeze-adamw"
architecture = "efficientnet-b0"
backbone = "torchvision.models.efficientnet_b0"
input_size = 224
pretrained = true
freeze_backbone = true
optimizer = "adamw"
learning_rate = 0.0003
batch_size = 32
mixed_precision = true
resource_profile = "gpu-rtx4080-b0-parallel"

[acceptance]
required_metrics = ["accuracy", "f1", "auroc", "latency_p95_ms", "gpu_memory_peak_mb"]
promotion_min_accuracy = 0.8
promotion_min_f1 = 0.75
promotion_min_auroc = 0.8
seed = 20260709
min_total_records = 10821
min_train_images = 6504
min_validation_images = 2136
min_test_images = 2181
min_epochs_b0 = 5
min_epochs_b7 = 3
require_cuda_available = true
require_cuda_device_name = true
require_gpu_profile = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config


def _write_candidate_artifacts(artifact_root: Path) -> Path:
    matrix_dir = artifact_root / "w7-efficientnet-real-test-matrix"
    candidate_dir = matrix_dir / "effnet-b0-img224-freeze-adamw"
    split_manifest = {
        "record_count": 10821,
        "split_counts": {"train": 6504, "validation": 2136, "test": 2181},
    }
    summary = {
        "candidate_id": "effnet-b0-img224-freeze-adamw",
        "status": "pass",
        "mlflow_run_id": "run-1",
        "model_artifact": str(candidate_dir / "model.pt"),
        "optimizer_step_count": 100,
        "metrics": {
            "accuracy": 0.91,
            "f1": 0.81,
            "auroc": 0.92,
            "latency_p95_ms": 4.0,
            "gpu_memory_peak_mb": 1000.0,
        },
        "promotion_blockers": [],
    }
    _write_json(candidate_dir / "candidate_summary.json", summary)
    _write_json(candidate_dir / "training_history.json", [{"epoch": idx} for idx in range(1, 6)])
    _write_json(candidate_dir / "confusion_matrix.json", {"labels": ["anomaly", "normal"], "matrix": [[1, 0], [0, 1]]})
    _write_json(candidate_dir / "gpu_profile.json", {"cuda_memory_peak_mb": 1000.0})
    _write_json(candidate_dir / "environment_report.json", {"cuda_available": True, "cuda_device_name": "RTX"})
    _write_json(candidate_dir / "split_manifest.json", split_manifest)
    (candidate_dir / "confusion_matrix.png").write_bytes(b"png")
    (candidate_dir / "model.pt").write_bytes(b"model")
    (candidate_dir / "model_card.md").write_text("# model card\n", encoding="utf-8")
    _write_json(matrix_dir / "split_manifest.json", split_manifest)
    _write_json(
        artifact_root / "latest_model_matrix.json",
        {
            "matrix_id": "w7-efficientnet-real-test-matrix",
            "status": "pass",
            "candidate_count": 1,
            "configured_candidate_count": 1,
            "split_manifest": str(matrix_dir / "split_manifest.json"),
            "candidates": [{**summary, "artifact_uri": str(candidate_dir), "run_uri": "http://mlflow/#/runs/run-1"}],
        },
    )
    return candidate_dir


def _cycle(candidate_dir: Path) -> CycleRun:
    return CycleRun(
        cycle_id="cycle-w7-test",
        status="pass",
        started_at="2026-07-10T00:00:00Z",
        stages=[],
        resources=[],
        dataset=DatasetVersion(
            dataset_id="visa-open-data",
            version="visa-open-data-test",
            record_count=10821,
            storage_uri="s3://validated",
            quality_status="pass",
        ),
        model=ModelVersion(
            model_name="vision-baseline",
            version="10",
            stage="Shadow",
            model_type="image_feature_centroid",
            registry_uri="registry/latest.json",
        ),
        serving=ServingState(
            status="pass",
            endpoint="http://localhost:8000",
            model_loaded=True,
            model_version="10",
        ),
        model_matrix=ModelExperimentMatrix(
            matrix_id="w7-efficientnet-real-test-matrix",
            status="pass",
            execution_mode="parallel",
            framework="torch",
            real_test_policy=RealTestPolicy(
                mock_allowed=False,
                smoke_allowed=False,
                requires_real_dataset=True,
                requires_real_training=True,
                minimum_records=10821,
                dataset_version="visa-open-data-test",
            ),
            candidates=[
                ModelCandidate(
                    candidate_id="effnet-b0-img224-freeze-adamw",
                    framework="torch",
                    architecture="efficientnet-b0",
                    backbone="torchvision.models.efficientnet_b0",
                    status="pass",
                    dataset_version="visa-open-data-test",
                    resource_profile="gpu-rtx4080-b0-parallel",
                    conditions={},
                    run_uri="http://mlflow/#/runs/run-1",
                    artifact_uri=str(candidate_dir),
                    metrics=[
                        Metric(name="accuracy", value=0.91),
                        Metric(name="f1", value=0.81),
                        Metric(name="auroc", value=0.92),
                        Metric(name="latency_p95_ms", value=4.0),
                        Metric(name="gpu_memory_peak_mb", value=1000.0),
                    ],
                )
            ],
        ),
    )


def test_real_test_evidence_validation_passes_complete_candidate(tmp_path):
    _write_project_files(tmp_path)
    artifact_root = tmp_path / "artifacts/w7/efficientnet"
    config = _write_config(tmp_path, artifact_root)
    candidate_dir = _write_candidate_artifacts(artifact_root)

    report = validate_real_test_evidence(_cycle(candidate_dir), config)

    assert report["valid"] is True
    assert report["checked_candidate_count"] == 1
    assert report["violations"] == []


def test_real_test_evidence_validation_blocks_missing_artifact(tmp_path):
    _write_project_files(tmp_path)
    artifact_root = tmp_path / "artifacts/w7/efficientnet"
    config = _write_config(tmp_path, artifact_root)
    candidate_dir = _write_candidate_artifacts(artifact_root)
    (candidate_dir / "gpu_profile.json").unlink()

    report = validate_real_test_evidence(_cycle(candidate_dir), config)

    assert report["valid"] is False
    assert any(item["code"] == "candidate_missing_artifacts" for item in report["violations"])


def test_real_test_evidence_validation_blocks_missing_split_manifest_path(tmp_path):
    _write_project_files(tmp_path)
    artifact_root = tmp_path / "artifacts/w7/efficientnet"
    config = _write_config(tmp_path, artifact_root)
    candidate_dir = _write_candidate_artifacts(artifact_root)
    matrix_path = artifact_root / "latest_model_matrix.json"
    matrix_payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix_payload.pop("split_manifest")
    _write_json(matrix_path, matrix_payload)

    report = validate_real_test_evidence(_cycle(candidate_dir), config)

    assert report["valid"] is False
    assert any(item["code"] == "split_manifest_missing" for item in report["violations"])
