from __future__ import annotations

import json
from pathlib import Path

from prometheus_client import generate_latest

from apps.api import main as api


def _write_registry(path: Path, *, version: int, stage: str, dataset_version: str) -> None:
    payload = {
        "model_name": "vision-baseline",
        "version": version,
        "stage": stage,
        "source_model": {
            "model_name": "vision-baseline",
            "model_type": "majority_class_baseline",
            "prediction": "normal",
            "metrics": {"baseline_accuracy": 0.5},
            "dataset": {
                "dataset_version": dataset_version,
                "validated_parquet_uri": f"s3://validated/{dataset_version}/validated_dataset.parquet",
            },
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_refresh_model_state_removes_stale_metric_labels(tmp_path):
    production_registry = tmp_path / "production.json"
    shadow_registry = tmp_path / "shadow.json"
    _write_registry(
        production_registry,
        version=8,
        stage="Production",
        dataset_version="dataset-production",
    )
    _write_registry(
        shadow_registry,
        version=10,
        stage="Shadow",
        dataset_version="dataset-shadow",
    )

    api.MODEL_NAME = "vision-baseline"
    api.MODEL_STAGE = "Production"
    api.MODEL_REGISTRY_PATH = production_registry
    api.refresh_model_state()
    first_metrics = generate_latest().decode("utf-8")
    assert 'evm_serving_model_loaded{model_name="vision-baseline",model_stage="Production"} 1.0' in first_metrics

    api.MODEL_STAGE = "Shadow"
    api.MODEL_REGISTRY_PATH = shadow_registry
    api.refresh_model_state()
    refreshed_metrics = generate_latest().decode("utf-8")

    assert 'evm_serving_model_loaded{model_name="vision-baseline",model_stage="Production"}' not in refreshed_metrics
    assert 'evm_serving_model_version{model_name="vision-baseline",model_stage="Production"}' not in refreshed_metrics
    assert 'model_stage="Production",model_version="8"' not in refreshed_metrics
    assert 'evm_serving_model_loaded{model_name="vision-baseline",model_stage="Shadow"} 1.0' in refreshed_metrics
    assert 'evm_serving_model_version{model_name="vision-baseline",model_stage="Shadow"} 10.0' in refreshed_metrics
    assert (
        'evm_serving_model_info{dataset_version="dataset-shadow",model_name="vision-baseline",'
        'model_stage="Shadow",model_version="10"} 1.0'
    ) in refreshed_metrics
