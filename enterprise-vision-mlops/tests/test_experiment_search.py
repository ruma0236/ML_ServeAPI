from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.experiment_runs import (
    ExperimentRun,
    cancellation_requested,
    read_experiment,
    request_cancellation,
    utc_now,
    write_experiment,
)
from evm.pipelines.experiment_search import run as experiment_search


class FakeMlflowClient:
    def __init__(self, _tracking_uri: str) -> None:
        self.params: list[tuple[str, str, object]] = []
        self.metrics: list[tuple[str, str, float]] = []
        self.terminated: list[tuple[str, str]] = []

    def health(self) -> bool:
        return True

    def get_or_create_experiment(self, _name: str) -> str:
        return "experiment-1"

    def create_run(self, _experiment_id: str, _run_name: str, *, tags=None) -> str:
        assert tags and tags["evm.run_role"] == "experiment_search_parent"
        return "parent-run-1"

    def log_param(self, run_id: str, key: str, value) -> bool:
        self.params.append((run_id, key, value))
        return True

    def log_metric(self, run_id: str, key: str, value: float, **_kwargs) -> bool:
        self.metrics.append((run_id, key, value))
        return True

    def terminate_run(self, run_id: str, status: str = "FINISHED") -> bool:
        self.terminated.append((run_id, status))
        return True


def write_search_config(tmp_path: Path) -> Path:
    shard_root = tmp_path / "shards"
    shard_root.mkdir(parents=True)
    shards = []
    for split, count in (("train", 6), ("validation", 4)):
        path = shard_root / f"{split}.jsonl"
        records = [
            {
                "record_id": f"{split}-{index}",
                "image_uri": str(tmp_path / "images" / f"{split}-{index}.png"),
                "label_type": "normal" if index % 2 == 0 else "anomaly",
            }
            for index in range(count)
        ]
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        shards.append({"shard_id": split, "split": split, "path": str(path)})
    shard_index = tmp_path / "shard_index.json"
    shard_index.write_text(
        json.dumps(
            {
                "schema_version": "evm.dataset_shards.v1",
                "identity_sha256": "a" * 64,
                "training_data_scope": "development-only",
                "excluded_split": "test",
                "ct_evidence_exposed": False,
                "shards": shards,
            }
        ),
        encoding="utf-8",
    )
    config = {
        "model_matrix": {
            "matrix_id": "test-search-matrix",
            "dataset_version": "test-dataset-v1",
            "selected_candidate_id": "efficientnet-b0-test",
        },
        "resources": {"artifact_root": str(tmp_path / "models")},
        "inputs": {
            "shard_index": str(shard_index),
            "shard_identity_sha256": "a" * 64,
            "mlflow_tracking_uri": "http://mlflow.test",
            "mlflow_experiment_name": "test-search",
        },
        "execution": {"num_workers": 0, "pin_memory": False},
        "candidates": [
            {
                "candidate_id": "efficientnet-b0-test",
                "architecture": "efficientnet-b0",
                "backbone": "torchvision.models.efficientnet_b0",
                "input_size": 224,
                "pretrained": False,
                "freeze_backbone": True,
                "optimizer": "adamw",
                "learning_rate": 0.0001,
                "weight_decay": 0.0001,
                "batch_size": 4,
                "mixed_precision": False,
                "class_weighted_loss": True,
                "resource_profile": "test-gpu",
                "epochs": 1,
                "early_stop_accuracy": 0.9,
                "early_stop_min_epochs": 1,
            }
        ],
        "acceptance": {
            "seed": 17,
            "promotion_min_accuracy": 0.1,
            "promotion_min_f1": 0.1,
            "promotion_min_auroc": 0.1,
        },
        "experiment_search": {
            "enabled": True,
            "mode": "grid",
            "folds": 2,
            "repeats": 1,
            "seed": 17,
            "primary_metric": "f1",
            "max_trials": 2,
            "max_parallel_trials": 1,
            "gpu_quota": 1,
            "holdout_split": "test",
            "allow_holdout_in_search": False,
            "final_refit": True,
            "search_space": {
                "learning_rates": [0.0001, 0.0003],
                "weight_decays": [0.0001],
                "batch_sizes": [4],
                "optimizers": ["adamw"],
                "freeze_backbone_options": [True],
            },
        },
        "control_plane": {
            "profile_name": "test-search-profile",
            "profile_digest": "f" * 64,
            "model": {"tuning_mode": "grid", "max_trials": 2},
            "data": {"split": {"cross_validation_folds": 2, "holdout_split": "test"}},
            "experiment": {"primary_metric": "f1", "repeats": 1},
            "resources": {"gpu_count": 1, "max_parallel_trials": 1},
        },
    }
    path = tmp_path / "search.runtime.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def deterministic_partitions(records, *, folds: int, seed: int):
    del seed
    return [
        (
            [record for index, record in enumerate(records) if index % folds != fold],
            [record for index, record in enumerate(records) if index % folds == fold],
        )
        for fold in range(folds)
    ]


def test_grid_search_seals_holdout_and_parent_child_lineage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_EXPERIMENT_RUN_ROOT", str(tmp_path / "experiments"))
    monkeypatch.setenv("EVM_EXPERIMENT_RUN_ID", "experiment-test-grid")
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(Path.cwd()))
    monkeypatch.setattr(experiment_search, "build_fold_partitions", deterministic_partitions)
    config_path = write_search_config(tmp_path)
    runtimes = []

    def trainer(candidate, _splits, _manifest, _acceptance, runtime, artifact_dir):
        runtimes.append(runtime)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        score = 0.91 if candidate.learning_rate == 0.0003 else 0.82
        assert runtime.progress_callback is not None
        runtime.progress_callback(
            {
                "phase": "training",
                "epoch": 1,
                "epochs": 2,
                "step": 3,
                "steps": 6,
                "optimizer_steps": 3,
                "train_loss": 0.2,
                "unit_progress": 0.25,
            }
        )
        return {
            "status": "pass",
            "metrics": {"accuracy": score, "f1": score, "auroc": score},
            "mlflow_run_id": f"child-{len(runtimes)}",
        }

    def final_trainer(path, *, progress_callback=None):
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        candidate = config["candidates"][0]
        artifact_dir = tmp_path / "final-candidate"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        assert progress_callback is not None
        progress_callback(
            {
                "phase": "completed",
                "epoch": 1,
                "epochs": 1,
                "step": 6,
                "steps": 6,
                "optimizer_steps": 6,
                "unit_progress": 1.0,
            }
        )
        return {
            "status": "pass",
            "candidates": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "status": "pass",
                    "artifact_uri": str(artifact_dir),
                    "mlflow_run_id": "final-child",
                    "promotion_blockers": [],
                    "execution_blockers": [],
                }
            ],
        }

    result = experiment_search.run(
        str(config_path),
        trainer=trainer,
        mlflow_factory=FakeMlflowClient,
        final_trainer=final_trainer,
    )

    assert result["state"] == "completed"
    assert result["completed_units"] == result["total_units"] == 5
    assert result["selected_parameters"]["learning_rate"] == 0.0003
    assert result["profile_digest"] == "f" * 64
    assert len(result["trials"]) == 2
    assert len(runtimes) == 4
    assert all(runtime.parent_run_id == "parent-run-1" for runtime in runtimes)
    assert all(runtime.run_tags["evm.holdout_used"] == "false" for runtime in runtimes)
    assert result["training_telemetry"]["unit_role"] == "final_refit"
    assert result["training_telemetry"]["phase"] == "completed"
    assert result["training_telemetry"]["step"] == 6
    manifest = json.loads(Path(result["fold_manifest_uri"]).read_text(encoding="utf-8"))
    assert manifest["holdout_used_for_selection"] is False
    assert manifest["development_records"] == 10
    assert manifest["holdout_access_policy"] == "isolated_control_plane_only"
    assert manifest["ct_evidence_exposed"] is False
    assert "holdout_records" not in manifest
    assert "holdout_sha256" not in manifest
    assert len(manifest["assignments"]) == 10


def test_experiment_cancellation_is_persistent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_EXPERIMENT_RUN_ROOT", str(tmp_path / "experiments"))
    now = utc_now()
    run = ExperimentRun(
        experiment_id="experiment-cancel-test",
        lifecycle_run_id="experiment-cancel-test",
        profile_name="profile",
        profile_digest="a" * 64,
        dataset_version="dataset-v1",
        source_manifest_sha256="b" * 64,
        holdout_split="test",
        holdout_sha256="c" * 64,
        mode="grid",
        primary_metric="f1",
        seed=17,
        folds=2,
        repeats=1,
        requested_trials=2,
        total_units=5,
        state="running",
        gpu_quota=1,
        scheduled_parallelism=1,
        created_at=now,
        updated_at=now,
    )
    write_experiment(run)

    updated = request_cancellation(
        run.experiment_id,
        actor="ml-engineer",
        reason="Stop bounded search after operator review",
    )

    assert updated is not None and updated.state == "cancelling"
    assert cancellation_requested(run.experiment_id) is True
    assert read_experiment(run.experiment_id).state == "cancelling"


def test_quality_regression_creates_repeat_guard_evidence(tmp_path: Path) -> None:
    now = utc_now()
    state = ExperimentRun(
        experiment_id="experiment-quality-review",
        lifecycle_run_id="experiment-quality-review",
        profile_name="quality-review-profile",
        profile_digest="a" * 64,
        dataset_version="visa-v1",
        source_manifest_sha256="b" * 64,
        holdout_split="test",
        holdout_sha256="c" * 64,
        mode="grid",
        primary_metric="f1",
        seed=17,
        folds=2,
        repeats=1,
        requested_trials=1,
        total_units=3,
        state="running",
        gpu_quota=1,
        scheduled_parallelism=1,
        selected_trial_id="trial-001",
        selected_parameters={"learning_rate": 0.0003, "freeze_backbone": True},
        created_at=now,
        updated_at=now,
    )
    matrix = {
        "candidates": [
            {
                "candidate_id": "efficientnet-b0-test",
                "metrics": {"accuracy": 0.876, "f1": 0.462, "auroc": 0.863},
                "promotion_blockers": ["f1<0.75"],
            }
        ]
    }

    review = experiment_search.build_quality_review(
        state,
        matrix,
        candidate_id="efficientnet-b0-test",
        root=tmp_path,
    )

    assert review is not None
    assert review.state == "review_required"
    assert review.observed_metrics["f1"] == 0.462
    assert review.policy_thresholds["f1"] == 0.75
    assert "unfreeze_backbone" in review.recommendations
    assert review.repeat_guard == "block_same_profile"
    assert (tmp_path / "model_quality_review.json").is_file()
