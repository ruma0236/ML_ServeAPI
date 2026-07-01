from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence

from evm.core.config import get_nested
from evm.core.mlflow_client import MlflowRestClient
from evm.core.pipeline import build_context, read_jsonl, utc_now, write_json, write_markdown_report


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("training", config_path)
    cfg = ctx.pipeline_config()
    input_manifest = ctx.path(str(cfg.get("input_manifest", "data/validated/validated_manifest.jsonl")))
    dataset_metadata_path = ctx.path(
        str(cfg.get("dataset_metadata", "data/validated/dataset_version.json"))
    )
    model_name = str(cfg.get("model_name", "vision-baseline"))
    metric_name = str(cfg.get("metric_name", "baseline_accuracy"))
    models_root = ctx.path(get_nested(ctx.config, "paths.models_root", "artifacts/models"))
    model_dir = models_root / model_name

    records = read_jsonl(input_manifest)
    dataset_metadata: dict[str, object] = {}
    if dataset_metadata_path.exists():
        dataset_metadata = json.loads(dataset_metadata_path.read_text(encoding="utf-8"))
    dataset_version = str(dataset_metadata.get("dataset_version", "unversioned"))
    labels = [str(record.get("label")) for record in records if record.get("label")]
    label_counts = Counter(labels)
    majority_label, majority_count = ("unknown", 0)
    if label_counts:
        majority_label, majority_count = label_counts.most_common(1)[0]
    baseline_accuracy = majority_count / len(labels) if labels else 0.0

    model_payload = {
        "model_name": model_name,
        "model_type": "majority_class_baseline",
        "trained_at": utc_now(),
        "training_records": len(records),
        "dataset": dataset_metadata,
        "label_counts": dict(label_counts),
        "prediction": majority_label,
        "metrics": {metric_name: baseline_accuracy},
        "trace": ctx.trace.to_dict(),
    }
    model_path = model_dir / "model.json"
    write_json(model_path, model_payload)

    mlflow_tracking_uri = get_nested(ctx.config, "mlflow.tracking_uri", "http://localhost:5000")
    experiment_name = get_nested(ctx.config, "mlflow.experiment_name", "enterprise-vision-local-mvp")
    mlflow_run_id = None
    mlflow_status = "skipped"
    client = MlflowRestClient(str(mlflow_tracking_uri))
    if client.health():
        experiment_id = client.get_or_create_experiment(str(experiment_name))
        if experiment_id:
            mlflow_run_id = client.create_run(experiment_id, ctx.run_id)
            if mlflow_run_id:
                client.log_param(mlflow_run_id, "model_name", model_name)
                client.log_param(mlflow_run_id, "model_type", "majority_class_baseline")
                client.log_param(mlflow_run_id, "dataset_version", dataset_version)
                if dataset_metadata:
                    client.log_param(
                        mlflow_run_id,
                        "validated_parquet_uri",
                        str(dataset_metadata.get("validated_parquet_uri", "")),
                    )
                for key, value in ctx.trace.mlflow_params().items():
                    client.log_param(mlflow_run_id, key, value)
                client.log_metric(mlflow_run_id, metric_name, baseline_accuracy)
                client.terminate_run(mlflow_run_id)
                mlflow_status = "logged"

    summary = {
        "records": len(records),
        "dataset_version": dataset_version,
        "dataset_metadata": str(dataset_metadata_path.relative_to(ctx.project_root)),
        "validated_parquet_uri": str(dataset_metadata.get("validated_parquet_uri", "")),
        "model_name": model_name,
        "model_path": str(model_path.relative_to(ctx.project_root)),
        metric_name: round(baseline_accuracy, 6),
        "mlflow_status": mlflow_status,
        "mlflow_run_id": mlflow_run_id or "",
        "trace_id": ctx.trace.trace_id,
        "pipeline_run_id": ctx.run_id,
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "Training Pipeline",
        summary,
        [
            "",
            "## Contract",
            "",
            "- Input: `data/validated/validated_manifest.jsonl`.",
            "- Output: local model artifact and MLflow metrics.",
            "- Next: `model_registry` versions the selected model artifact.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
