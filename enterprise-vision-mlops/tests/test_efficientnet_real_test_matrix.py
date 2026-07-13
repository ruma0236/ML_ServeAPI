from __future__ import annotations

import json
import importlib
import inspect
import sys
from pathlib import Path

import pytest

from evm.core.torch_efficientnet import (
    EfficientNetCandidateConfig,
    early_stop_reached,
    load_shard_records,
    optimal_f1_threshold,
    predictions_at_threshold,
    train_candidate,
)
from evm.pipelines.efficientnet_training.run import (
    candidate_artifact_root,
    matrix_status,
    merge_candidate_results,
    required_candidate_blockers,
    runtime_config_path,
    run,
    source_digest_blockers,
)

training_run_module = importlib.import_module("evm.pipelines.efficientnet_training.run")


def test_runtime_config_path_maps_host_data_root(tmp_path: Path, monkeypatch) -> None:
    mounted_root = tmp_path / "mnt" / "evm-data"
    monkeypatch.setenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    )
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", mounted_root.as_posix())
    config = {"_project_root": str(tmp_path)}

    resolved = runtime_config_path(
        config,
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7",
    )

    assert resolved == mounted_root / "artifacts" / "w7"


def test_validation_threshold_maximizes_anomaly_f1_without_test_labels() -> None:
    labels = [0, 0, 1, 1]
    scores = [0.9, 0.7, 0.8, 0.1]

    calibration = optimal_f1_threshold(labels, scores)

    assert calibration["threshold"] == 0.7
    assert calibration["f1"] == pytest.approx(0.8)
    assert predictions_at_threshold(scores, float(calibration["threshold"])) == [0, 0, 0, 1]


def test_expedited_early_stop_requires_minimum_epoch_and_accuracy() -> None:
    candidate = EfficientNetCandidateConfig(
        candidate_id="effnet-b0-expedited",
        architecture="efficientnet-b0",
        backbone="torchvision.models.efficientnet_b0",
        input_size=224,
        pretrained=True,
        freeze_backbone=False,
        optimizer="adamw",
        learning_rate=0.0001,
        batch_size=64,
        mixed_precision=True,
        resource_profile="gpu-rtx4080-b0-expedited",
        epochs=8,
        early_stop_accuracy=0.93,
        early_stop_min_epochs=2,
        class_weighted_loss=False,
    )

    assert early_stop_reached(candidate, epoch=1, validation_accuracy=0.95) is False
    assert early_stop_reached(candidate, epoch=2, validation_accuracy=0.92) is False
    assert early_stop_reached(candidate, epoch=2, validation_accuracy=0.93) is True


def test_training_uses_current_torch_amp_api() -> None:
    source = inspect.getsource(train_candidate)

    assert "torch.cuda.amp" not in source
    assert "torch.amp.GradScaler" in source
    assert "torch.amp.autocast" in source


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_project_files(root: Path) -> None:
    (root / "pyproject.toml").write_text("[project]\nname='tmp-evm'\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")


def test_cli_main_uses_the_explicit_kubernetes_config(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        training_run_module,
        "run",
        lambda config_path: calls.append(str(config_path)) or {"status": "pass"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["efficientnet-training", "/app/configs/w7_efficientnet_kubernetes.toml"],
    )

    training_run_module.main()

    assert calls == ["/app/configs/w7_efficientnet_kubernetes.toml"]
    assert json.loads(capsys.readouterr().out) == {"status": "pass"}


def test_cli_require_pass_fails_a_blocked_selected_candidate(monkeypatch, capsys) -> None:
    candidate_id = "effnet-b7-img600-finetune-adamw"
    monkeypatch.setenv("EVM_EFFICIENTNET_CANDIDATES", candidate_id)
    monkeypatch.setattr(
        training_run_module,
        "run",
        lambda _config_path: {
            "status": "warn",
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "status": "blocked",
                    "execution_blockers": ["cuda_missing"],
                }
            ],
        },
    )

    with pytest.raises(SystemExit) as exc_info:
        training_run_module.main(["config.toml", "--require-pass"])

    assert exc_info.value.code == 2
    assert "required_candidate_not_pass" in capsys.readouterr().err


def test_required_candidate_pass_needs_mlflow_evidence(monkeypatch) -> None:
    candidate_id = "effnet-b7-img600-finetune-adamw"
    monkeypatch.setenv("EVM_EFFICIENTNET_CANDIDATES", candidate_id)

    assert required_candidate_blockers(
        {
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "status": "pass",
                    "execution_blockers": [],
                }
            ]
        }
    ) == [f"required_candidate_mlflow_run_missing:{candidate_id}"]


def test_execution_run_id_scopes_candidate_artifacts(tmp_path) -> None:
    matrix_dir = tmp_path / "matrix"

    candidate_root = candidate_artifact_root(matrix_dir, "k8s-proof-123")

    assert candidate_root == matrix_dir / "runs" / "k8s-proof-123"


def test_efficientnet_pipeline_fails_closed_when_real_split_is_too_small(tmp_path, monkeypatch):
    _write_project_files(tmp_path)
    artifacts_root = tmp_path / "artifacts/w7/efficientnet"
    shards_dir = tmp_path / "data/validated/visa/shards"
    shard_paths = {
        "train": shards_dir / "train_shard_0000.jsonl",
        "validation": shards_dir / "validation_shard_0001.jsonl",
        "test": shards_dir / "test_shard_0002.jsonl",
    }
    for split, path in shard_paths.items():
        _write_jsonl(
            path,
            [
                {
                    "sample_id": f"{split}-1",
                    "split": split,
                    "label": "normal",
                    "label_type": "normal",
                    "image_path": str(tmp_path / "not-used.jpg"),
                }
            ],
        )
    _write_json(
        shards_dir / "shard_index.json",
        {
            "schema_version": "evm.dataset_shards.v1",
            "record_count": 3,
            "split_counts": {"train": 1, "validation": 1, "test": 1},
            "shards": [
                {"shard_id": "train-0000", "split": "train", "path": str(shard_paths["train"])},
                {
                    "shard_id": "validation-0001",
                    "split": "validation",
                    "path": str(shard_paths["validation"]),
                },
                {"shard_id": "test-0002", "split": "test", "path": str(shard_paths["test"])},
            ],
        },
    )
    config = tmp_path / "configs/w7_efficientnet_real_test.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"""
[model_matrix]
matrix_id = "w7-efficientnet-real-test-matrix"
dataset_version = "visa-open-data-test"
execution_mode = "parallel"
mock_allowed = false
smoke_allowed = false
requires_real_dataset = true
requires_real_training = true
minimum_records = 10821

[resources]
artifact_root = "{artifacts_root.as_posix()}"

[inputs]
shard_index = "{(shards_dir / 'shard_index.json').as_posix()}"
mlflow_tracking_uri = "http://localhost:5000"
mlflow_experiment_name = "enterprise-vision-test"

[execution]
num_workers = 0
pin_memory = false

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
seed = 20260709
min_total_records = 10821
min_train_images = 6504
min_validation_images = 2136
min_test_images = 2181
min_epochs_b0 = 5
min_epochs_b7 = 3
require_cuda_available = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EVM_EFFICIENTNET_CANDIDATES", raising=False)
    monkeypatch.setenv("EVM_EFFICIENTNET_SEED", "20260710")

    summary = run(str(config))

    assert summary["status"] == "blocked"
    assert summary["split_blockers"] == [
        "record_count<10821",
        "train_images<6504",
        "validation_images<2136",
        "test_images<2181",
    ]
    assert summary["candidates"][0]["status"] == "blocked"
    assert summary["candidates"][0]["conditions"]["seed"] == 20260710
    assert summary["execution_seed"] == 20260710
    latest = json.loads((artifacts_root / "latest_model_matrix.json").read_text(encoding="utf-8"))
    assert latest["candidate_count"] == 1
    assert latest["candidates"][0]["execution_blockers"] == summary["split_blockers"]


def test_partial_candidate_results_merge_existing_matrix_evidence():
    existing = {
        "candidates": [
            {
                "candidate_id": "effnet-b0-img224-freeze-adamw",
                "status": "pass",
                "mlflow_run_id": "run-freeze",
            }
        ]
    }
    incoming = [
        {
            "candidate_id": "effnet-b0-img224-finetune-sgd",
            "status": "pass",
            "mlflow_run_id": "run-finetune",
        }
    ]

    merged = merge_candidate_results(existing, incoming)

    assert {item["candidate_id"] for item in merged} == {
        "effnet-b0-img224-freeze-adamw",
        "effnet-b0-img224-finetune-sgd",
    }
    assert matrix_status(merged, configured_candidate_count=4) == "warn"


def test_shard_records_map_windows_f_drive_to_container_mount(tmp_path, monkeypatch):
    mounted_root = tmp_path / "mnt" / "evm-data"
    shard_path = mounted_root / "data" / "validated" / "visa" / "shards" / "train.jsonl"
    _write_jsonl(
        shard_path,
        [
            {
                "sample_id": "mapped-1",
                "split": "train",
                "label_type": "normal",
                "image_path": "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/raw/image.jpg",
            }
        ],
    )
    shard_index_path = mounted_root / "data" / "validated" / "visa" / "shards" / "index.json"
    _write_json(
        shard_index_path,
        {
            "shards": [
                {
                    "shard_id": "train-0000",
                    "split": "train",
                    "path": "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/validated/visa/shards/train.jsonl",
                }
            ]
        },
    )
    monkeypatch.setenv(
        "EVM_HOST_DATA_ROOT", "F:/EnterpriseMLOps_Data/enterprise-vision-mlops"
    )
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", mounted_root.as_posix())

    _, splits = load_shard_records(shard_index_path)

    assert len(splits["train"]) == 1
    assert splits["train"][0]["sample_id"] == "mapped-1"


def test_source_digest_mismatch_blocks_training() -> None:
    assert source_digest_blockers("actual", "expected") == [
        "shard_index_sha256_mismatch:expected=expected,actual=actual"
    ]
    assert source_digest_blockers("ABC123", "abc123") == []
