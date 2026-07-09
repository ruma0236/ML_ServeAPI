from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.aggregation import build_latest_cycle


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_project_files(root: Path) -> None:
    (root / "pyproject.toml").write_text("[project]\nname='tmp-evm'\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")


def _write_config(root: Path) -> Path:
    data_root = root / "data"
    artifacts_root = root / "artifacts"
    config = root / "configs" / "local_visa.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"""
[paths]
data_root = "{data_root.as_posix()}"
artifacts_root = "{artifacts_root.as_posix()}"
registry_root = "{(artifacts_root / 'registry').as_posix()}"

[mlflow]
tracking_uri = "http://localhost:5000"
experiment_name = "enterprise-vision-test"

[serving]
api_url = "http://localhost:8000"

[pipelines.data_validation]
dataset_metadata = "{(data_root / 'validated/visa/dataset_version.json').as_posix()}"

[pipelines.image_quality]
report_path = "{(data_root / 'validated/visa/mvi_quality_report.json').as_posix()}"

[pipelines.curation_workflow]
state_path = "{(data_root / 'validated/visa/curation/curation_state.json').as_posix()}"

[pipelines.lakehouse_probe]
probe_report = "{(artifacts_root / 'lakehouse/visa/lakehouse_probe.json').as_posix()}"

[pipelines.model_registry]
model_name = "vision-baseline"

[pipelines.model_lifecycle]
output_dir = "{(artifacts_root / 'lifecycle/vision-baseline').as_posix()}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "configs" / "w7_efficientnet_real_test.toml").write_text(
        """
[model_matrix]
matrix_id = "w7-efficientnet-real-test-matrix"
framework = "torch"
execution_mode = "parallel"
dataset_version = "visa-open-data-test"
mock_allowed = false
smoke_allowed = false
requires_real_dataset = true
requires_real_training = true
minimum_records = 10821

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
max_parallel_jobs = 2

[acceptance]
min_total_records = 10821
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config


def _write_evidence(root: Path) -> None:
    data_root = root / "data"
    artifacts_root = root / "artifacts"
    dataset = {
        "dataset_name": "visa-open-data",
        "dataset_version": "visa-open-data-test",
        "created_at": "2026-07-09T00:00:00Z",
        "record_count": 10821,
        "validated_parquet_uri": "s3://validated/visa-open-data-test/validated.parquet",
    }
    source_model = {
        "model_name": "vision-baseline",
        "model_type": "image_feature_centroid",
        "dataset": dataset,
        "training_records": 6504,
        "validation_records": 2136,
        "test_records": 2181,
        "metrics": {
            "accuracy": 0.58,
            "precision": 0.12,
            "recall": 0.51,
            "f1": 0.20,
            "auroc": 0.56,
        },
        "lifecycle": {
            "promotion_gate": "blocked",
            "promotion_decision": "shadow_only",
            "thresholds": {
                "accuracy": 0.7,
                "precision": 0.5,
                "recall": 0.7,
                "f1": 0.5,
                "auroc": 0.65,
            },
            "blockers": ["accuracy<0.7"],
        },
        "trace": {"pipeline_run_id": "training-test"},
    }
    _write_json(data_root / "validated/visa/dataset_version.json", dataset)
    _write_json(
        data_root / "validated/visa/mvi_quality_report.json",
        {"status": "pass", "gate_decision": {"blocking_count": 0}},
    )
    _write_json(
        data_root / "validated/visa/curation/curation_state.json",
        {"hitl_queue_count": 7},
    )
    _write_json(
        artifacts_root / "lakehouse/visa/lakehouse_probe.json",
        {"status": "pass", "row_count": 10821},
    )
    _write_json(
        artifacts_root / "registry/vision-baseline/latest.json",
        {
            "model_name": "vision-baseline",
            "version": 10,
            "stage": "Shadow",
            "registered_at": "2026-07-09T01:00:00Z",
            "source_model": source_model,
            "promotion_decision": "shadow_only",
        },
    )
    _write_json(
        artifacts_root / "lifecycle/vision-baseline/lifecycle_dashboard.json",
        {
            "promotion_policy": {
                "status": "blocked",
                "decision": "shadow_only",
                "thresholds": source_model["lifecycle"]["thresholds"],
            },
            "blockers": ["accuracy<0.7"],
        },
    )
    _write_json(
        artifacts_root / "lifecycle/vision-baseline/drift_special_case_queue.json",
        [{"case_id": "case-1"}],
    )


def test_build_latest_cycle_aggregates_local_evidence(tmp_path, monkeypatch):
    _write_project_files(tmp_path)
    config = _write_config(tmp_path)
    _write_evidence(tmp_path)
    monkeypatch.delenv("MODEL_REGISTRY_PATH", raising=False)

    cycle = build_latest_cycle(config_path=config)

    assert cycle.owner_issue == "EVM-224"
    assert cycle.dataset.version == "visa-open-data-test"
    assert cycle.dataset.record_count == 10821
    assert cycle.dataset.split == {"train": 6504, "validation": 2136, "test": 2181}
    assert cycle.model.version == "10"
    assert cycle.model.stage == "Shadow"
    assert cycle.promotion_gate is not None
    assert cycle.promotion_gate.status == "blocked"
    assert cycle.data_pipeline is not None
    assert cycle.data_pipeline.quality_status == "pass"
    assert cycle.model_matrix is not None
    assert cycle.model_matrix.real_test_policy.mock_allowed is False
    assert cycle.model_matrix.candidates
    assert any(artifact.name == "model_registry_latest" for artifact in cycle.artifacts)


def test_build_latest_cycle_marks_missing_upstream_evidence(tmp_path, monkeypatch):
    _write_project_files(tmp_path)
    config = _write_config(tmp_path)
    monkeypatch.delenv("MODEL_REGISTRY_PATH", raising=False)

    cycle = build_latest_cycle(config_path=config)

    assert cycle.model.version == ""
    assert cycle.model.stage == "unknown"
    assert cycle.serving.status == "blocked"
    assert cycle.data_pipeline is not None
    assert cycle.data_pipeline.quality_status == "unknown"
    assert cycle.experiment_pipeline is not None
    assert cycle.experiment_pipeline.registry_status == "blocked"
