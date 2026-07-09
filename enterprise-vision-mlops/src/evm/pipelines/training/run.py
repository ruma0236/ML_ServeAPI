from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Sequence

from evm.core.config import get_nested
from evm.core.image_feature_model import (
    collect_local_resource_profile,
    extract_image_features,
    model_digest,
    resolve_image_path,
    train_centroid_classifier,
)
from evm.core.mlflow_client import MlflowRestClient
from evm.core.model_promotion import evaluate_promotion, metric_thresholds
from evm.core.pipeline import (
    build_context,
    display_path,
    read_jsonl,
    utc_now,
    write_json,
    write_markdown_report,
)


def _feature_rows(
    records: list[dict[str, object]],
    *,
    feature_sample_bytes: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rows = []
    skipped_records = []
    for record in records:
        image_path = resolve_image_path(
            str(record.get("image_uri") or ""),
            image_path=str(record.get("image_path") or ""),
        )
        sample_id = str(record.get("sample_id") or record.get("id") or "")
        if image_path is None or not image_path.exists():
            skipped_records.append(
                {
                    "sample_id": sample_id,
                    "reason": "image_path_missing",
                    "image_uri": record.get("image_uri", ""),
                    "image_path": str(image_path) if image_path else "",
                }
            )
            continue
        if not record.get("label"):
            skipped_records.append(
                {
                    "sample_id": sample_id,
                    "reason": "label_missing",
                    "image_uri": record.get("image_uri", ""),
                    "image_path": str(image_path),
                }
            )
            continue

        features = extract_image_features(
            image_path,
            width=record.get("width"),
            height=record.get("height"),
            sample_bytes=feature_sample_bytes,
        )
        rows.append(
            {
                "sample_id": sample_id,
                "image_uri": record.get("image_uri", ""),
                "image_path": str(image_path),
                "split": record.get("split", "train"),
                "label": str(record.get("label")),
                "class_name": record.get("class_name", ""),
                "features": features,
            }
        )
    return rows, skipped_records


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("training", config_path)
    cfg = ctx.pipeline_config()
    input_manifest = ctx.path(str(cfg.get("input_manifest", "data/validated/validated_manifest.jsonl")))
    dataset_metadata_path = ctx.path(
        str(cfg.get("dataset_metadata", "data/validated/dataset_version.json"))
    )
    model_name = str(cfg.get("model_name", "vision-baseline"))
    model_type = str(cfg.get("model_type", "image_feature_centroid"))
    metric_name = str(cfg.get("metric_name", "accuracy"))
    positive_label = str(cfg.get("positive_label", "anomaly"))
    feature_sample_bytes = int(cfg.get("feature_sample_bytes", 8192))
    max_records = int(cfg.get("max_records", 0))
    thresholds = metric_thresholds(cfg)
    models_root = ctx.path(get_nested(ctx.config, "paths.models_root", "artifacts/models"))
    model_dir = models_root / model_name

    records = read_jsonl(input_manifest)
    if max_records > 0:
        records = records[:max_records]
    dataset_metadata: dict[str, object] = {}
    if dataset_metadata_path.exists():
        dataset_metadata = json.loads(dataset_metadata_path.read_text(encoding="utf-8"))
    dataset_version = str(dataset_metadata.get("dataset_version", "unversioned"))

    if model_type != "image_feature_centroid":
        raise RuntimeError(f"unsupported training model_type: {model_type}")

    resource_profile = collect_local_resource_profile()
    started = time.perf_counter()
    rows, skipped_records = _feature_rows(records, feature_sample_bytes=feature_sample_bytes)
    if not rows:
        raise RuntimeError("training has no feature rows; verify validated manifest image paths")

    model_payload = train_centroid_classifier(
        rows,
        model_name=model_name,
        dataset_metadata=dataset_metadata,
        positive_label=positive_label,
    )
    duration_seconds = round(time.perf_counter() - started, 3)
    selected_split = str(model_payload["evaluation"]["selected_split"])
    selected_eval = model_payload["evaluation"][selected_split]
    gate_metric = float(model_payload["metrics"].get(metric_name, 0.0))
    promotion_policy = evaluate_promotion(model_payload["metrics"], thresholds)
    model_payload.update(
        {
            "trained_at": utc_now(),
            "feature_sample_bytes": feature_sample_bytes,
            "records_seen": len(records),
            "records_used": len(rows),
            "skipped_records": skipped_records[:100],
            "skipped_record_count": len(skipped_records),
            "resource_profile": resource_profile,
            "training_duration_seconds": duration_seconds,
            "lifecycle": {
                "state": "Validated",
                "promotion_gate": promotion_policy["status"],
                "promotion_decision": promotion_policy["decision"],
                "metric_name": metric_name,
                "metric_value": gate_metric,
                "thresholds": promotion_policy["thresholds"],
                "blockers": promotion_policy["blockers"],
                "evidence_split": selected_split,
            },
            "promotion_policy": promotion_policy,
            "trace": ctx.trace.to_dict(),
        }
    )

    sample_predictions = selected_eval.get("sample_predictions", [])
    sample_inference = sample_predictions[0] if sample_predictions else {}
    matched_feature_row = (
        next(
            (row for row in rows if row.get("sample_id") == sample_inference.get("sample_id")),
            rows[0],
        )
        if rows
        else {}
    )
    model_payload["sample_inference"] = {
        "sample_id": matched_feature_row.get("sample_id", ""),
        "image_uri": matched_feature_row.get("image_uri", ""),
        "image_path": matched_feature_row.get("image_path", ""),
        "features": matched_feature_row.get("features", {}),
        "label": matched_feature_row.get("label", ""),
    }
    model_payload["model_digest"] = model_digest(model_payload)

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
                client.log_param(mlflow_run_id, "model_type", model_payload["model_type"])
                client.log_param(mlflow_run_id, "dataset_version", dataset_version)
                client.log_param(mlflow_run_id, "records_used", len(rows))
                client.log_param(mlflow_run_id, "feature_sample_bytes", feature_sample_bytes)
                client.log_param(mlflow_run_id, "accelerator_used", resource_profile["accelerator_used"])
                client.log_param(mlflow_run_id, "gpu_detected", resource_profile["gpu_detected"])
                if dataset_metadata:
                    client.log_param(
                        mlflow_run_id,
                        "validated_parquet_uri",
                        str(dataset_metadata.get("validated_parquet_uri", "")),
                    )
                for key, value in ctx.trace.mlflow_params().items():
                    client.log_param(mlflow_run_id, key, value)
                for key, value in model_payload["metrics"].items():
                    if isinstance(value, int | float):
                        client.log_metric(mlflow_run_id, key, float(value))
                client.terminate_run(mlflow_run_id)
                mlflow_status = "logged"

    label_counts = Counter(str(record.get("label")) for record in records if record.get("label"))
    summary = {
        "records_seen": len(records),
        "records_used": len(rows),
        "skipped_record_count": len(skipped_records),
        "dataset_version": dataset_version,
        "dataset_metadata": display_path(dataset_metadata_path, ctx.project_root),
        "validated_parquet_uri": str(dataset_metadata.get("validated_parquet_uri", "")),
        "model_name": model_name,
        "model_type": model_payload["model_type"],
        "model_path": display_path(model_path, ctx.project_root),
        "selected_eval_split": selected_split,
        metric_name: round(gate_metric, 6),
        "precision": round(float(model_payload["metrics"].get("precision", 0.0)), 6),
        "recall": round(float(model_payload["metrics"].get("recall", 0.0)), 6),
        "f1": round(float(model_payload["metrics"].get("f1", 0.0)), 6),
        "auroc": round(float(model_payload["metrics"].get("auroc", 0.0)), 6),
        "label_counts": dict(label_counts),
        "model_digest": model_payload["model_digest"],
        "training_duration_seconds": duration_seconds,
        "promotion_gate": promotion_policy["status"],
        "promotion_decision": promotion_policy["decision"],
        "promotion_blockers": promotion_policy["blockers"],
        "accelerator_used": resource_profile["accelerator_used"],
        "gpu_detected": resource_profile["gpu_detected"],
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
            "- Input: validated image manifest with labels and image paths.",
            "- Output: image feature model artifact, split metrics, resource profile, and MLflow metrics.",
            "- Next: `model_registry` versions the selected model artifact.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
