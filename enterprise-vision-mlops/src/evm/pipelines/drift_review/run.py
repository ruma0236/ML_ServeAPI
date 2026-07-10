from __future__ import annotations

import hashlib
import json
import math
import os
import random
import tomllib
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.config import get_nested, load_config, resolve_path
from evm.core.image_feature_model import resolve_image_path
from evm.core.pipeline import run_id, utc_now, write_json, write_jsonl
from evm.core.torch_efficientnet import (
    CLASS_NAMES,
    EfficientNetCandidateConfig,
    VisaImageDataset,
    build_model,
    load_shard_records,
)
from evm.core.traceability import TraceContext


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_path(value: str | Path) -> Path:
    mapped = resolve_image_path(
        str(value),
        host_data_root=os.getenv("EVM_HOST_DATA_ROOT"),
        data_mount_root=os.getenv("EVM_DATA_MOUNT_ROOT"),
    )
    return mapped or Path(value)


def canonical_uri(root: str, *parts: str) -> str:
    base = root.replace("\\", "/").rstrip("/")
    suffix = "/".join(part.replace("\\", "/").strip("/") for part in parts)
    return f"{base}/{suffix}" if suffix else base


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0.0, min(1.0, probability)) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def probability_distribution(values: list[str], categories: list[str]) -> dict[str, float]:
    counts = Counter(values)
    total = len(values) or 1
    return {category: counts.get(category, 0) / total for category in categories}


def jensen_shannon_divergence(reference: dict[str, float], current: dict[str, float]) -> float:
    categories = sorted(set(reference) | set(current))
    midpoint = {
        category: (reference.get(category, 0.0) + current.get(category, 0.0)) / 2
        for category in categories
    }

    def kl_divergence(source: dict[str, float]) -> float:
        value = 0.0
        for category in categories:
            probability = source.get(category, 0.0)
            middle = midpoint[category]
            if probability > 0 and middle > 0:
                value += probability * math.log2(probability / middle)
        return value

    return (kl_divergence(reference) + kl_divergence(current)) / 2


def confidence_histogram(values: list[float], bin_count: int = 10) -> list[float]:
    counts = [0 for _ in range(bin_count)]
    for value in values:
        index = min(bin_count - 1, max(0, int(value * bin_count)))
        counts[index] += 1
    total = len(values) or 1
    return [count / total for count in counts]


def population_stability_index(reference: list[float], current: list[float]) -> float:
    epsilon = 1e-6
    reference_histogram = confidence_histogram(reference)
    current_histogram = confidence_histogram(current)
    return sum(
        (max(current_value, epsilon) - max(reference_value, epsilon))
        * math.log(max(current_value, epsilon) / max(reference_value, epsilon))
        for reference_value, current_value in zip(
            reference_histogram,
            current_histogram,
            strict=True,
        )
    )


def confidence_summary(values: list[float], low_confidence_threshold: float) -> dict[str, Any]:
    total = len(values) or 1
    return {
        "mean": sum(values) / total,
        "quantiles": {
            "p10": percentile(values, 0.10),
            "p50": percentile(values, 0.50),
            "p90": percentile(values, 0.90),
        },
        "low_confidence_threshold": low_confidence_threshold,
        "low_confidence_rate": (
            sum(1 for value in values if value < low_confidence_threshold) / total
        ),
        "histogram": confidence_histogram(values),
    }


def evaluate_measured_drift(
    *,
    reference_predictions: list[dict[str, Any]],
    current_predictions: list[dict[str, Any]],
    thresholds: dict[str, float],
    low_confidence_threshold: float,
) -> dict[str, Any]:
    if not reference_predictions or not current_predictions:
        raise ValueError("reference and current prediction windows must be non-empty")

    reference_confidence = [float(item["confidence"]) for item in reference_predictions]
    current_confidence = [float(item["confidence"]) for item in current_predictions]
    reference_summary = confidence_summary(reference_confidence, low_confidence_threshold)
    current_summary = confidence_summary(current_confidence, low_confidence_threshold)

    category_names = sorted(
        {
            str(item["class_name"])
            for item in [*reference_predictions, *current_predictions]
        }
    )
    prediction_names = sorted(
        {
            str(item["predicted_label"])
            for item in [*reference_predictions, *current_predictions]
        }
    )
    reference_categories = probability_distribution(
        [str(item["class_name"]) for item in reference_predictions],
        category_names,
    )
    current_categories = probability_distribution(
        [str(item["class_name"]) for item in current_predictions],
        category_names,
    )
    reference_prediction_distribution = probability_distribution(
        [str(item["predicted_label"]) for item in reference_predictions],
        prediction_names,
    )
    current_prediction_distribution = probability_distribution(
        [str(item["predicted_label"]) for item in current_predictions],
        prediction_names,
    )

    metrics = {
        "input_category_js": jensen_shannon_divergence(
            reference_categories,
            current_categories,
        ),
        "predicted_class_js": jensen_shannon_divergence(
            reference_prediction_distribution,
            current_prediction_distribution,
        ),
        "confidence_psi": population_stability_index(
            reference_confidence,
            current_confidence,
        ),
        "mean_confidence_drop": max(
            0.0,
            float(reference_summary["mean"]) - float(current_summary["mean"]),
        ),
        "low_confidence_rate_increase": max(
            0.0,
            float(current_summary["low_confidence_rate"])
            - float(reference_summary["low_confidence_rate"]),
        ),
    }
    triggered_rules = [
        metric
        for metric, value in metrics.items()
        if value >= float(thresholds[metric])
    ]
    return {
        "decision": "review_required" if triggered_rules else "within_policy",
        "triggered_rules": triggered_rules,
        "metrics": metrics,
        "reference": {
            "record_count": len(reference_predictions),
            "confidence": reference_summary,
            "input_category_distribution": reference_categories,
            "predicted_class_distribution": reference_prediction_distribution,
        },
        "current": {
            "record_count": len(current_predictions),
            "confidence": current_summary,
            "input_category_distribution": current_categories,
            "predicted_class_distribution": current_prediction_distribution,
        },
    }


def select_window(
    records: list[dict[str, Any]],
    *,
    class_names: list[str],
    max_records: int,
    seed: int,
) -> list[dict[str, Any]]:
    selected = [
        record
        for record in records
        if not class_names or str(record.get("class_name")) in class_names
    ]
    selected.sort(key=lambda record: str(record.get("sample_id") or record.get("id") or ""))
    if max_records and len(selected) > max_records:
        rng = random.Random(seed)
        selected = sorted(
            rng.sample(selected, max_records),
            key=lambda record: str(record.get("sample_id") or record.get("id") or ""),
        )
    return selected


def load_checkpoint(model_path: Path, require_cuda: bool) -> tuple[Any, Any, dict[str, Any]]:
    import torch

    if require_cuda and not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for W7 measured B7 drift evidence")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(model_path, map_location=device, weights_only=True)
    if not isinstance(checkpoint, dict):
        raise ValueError("model checkpoint must be a mapping")
    architecture = str(checkpoint.get("architecture") or "")
    if architecture != "efficientnet-b7":
        raise ValueError(f"selected checkpoint is not EfficientNet-B7: {architecture}")
    candidate = EfficientNetCandidateConfig(
        candidate_id=str(checkpoint.get("candidate_id") or "unknown"),
        architecture=architecture,
        backbone="torchvision.models.efficientnet_b7",
        input_size=int(checkpoint.get("input_size") or 600),
        pretrained=False,
        freeze_backbone=False,
        optimizer="adamw",
        learning_rate=0.0,
        batch_size=1,
        mixed_precision=False,
        resource_profile="gpu-drift-review",
        epochs=0,
    )
    model = build_model(candidate, len(checkpoint.get("class_names") or CLASS_NAMES))
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device)
    model.eval()
    return model, device, checkpoint


def predict_window(
    records: list[dict[str, Any]],
    *,
    model: Any,
    device: Any,
    input_size: int,
    batch_size: int,
    num_workers: int,
) -> list[dict[str, Any]]:
    import torch
    from torch.utils.data import DataLoader

    dataset = VisaImageDataset(records, input_size)
    loader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=device.type == "cuda",
    )
    predictions: list[dict[str, Any]] = []
    offset = 0
    with torch.no_grad():
        for images, targets in loader:
            images = images.to(device, non_blocking=True)
            logits = model(images)
            probabilities = torch.softmax(logits, dim=1).detach().cpu()
            target_values = targets.detach().cpu().tolist()
            for row_index, row in enumerate(probabilities):
                record = records[offset + row_index]
                predicted_index = int(row.argmax().item())
                predictions.append(
                    {
                        "sample_id": str(record.get("sample_id") or record.get("id") or ""),
                        "content_sha256": str(record.get("content_sha256") or ""),
                        "image_uri": str(record.get("image_uri") or ""),
                        "source_observed_at": str(record.get("quality_checked_at") or ""),
                        "class_name": str(record.get("class_name") or "unknown"),
                        "actual_label": CLASS_NAMES[int(target_values[row_index])],
                        "predicted_label": CLASS_NAMES[predicted_index],
                        "confidence": float(row[predicted_index].item()),
                        "anomaly_score": float(row[0].item()),
                    }
                )
            offset += len(target_values)
    return predictions


def queue_records(
    predictions: list[dict[str, Any]],
    *,
    event_id: str,
    triggered_rules: list[str],
    low_confidence_threshold: float,
    max_records: int,
) -> list[dict[str, Any]]:
    ordered = sorted(
        predictions,
        key=lambda item: (float(item["confidence"]), str(item["sample_id"])),
    )
    return [
        {
            **item,
            "event_id": event_id,
            "review_state": "pending_label_review",
            "approval_required": True,
            "reasons": [
                *triggered_rules,
                *(
                    ["low_confidence"]
                    if float(item["confidence"]) < low_confidence_threshold
                    else []
                ),
            ],
        }
        for item in ordered[:max_records]
    ]


def real_input_validation(
    reference_predictions: list[dict[str, Any]],
    current_predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    reference_ids = {str(item["sample_id"]) for item in reference_predictions}
    current_ids = {str(item["sample_id"]) for item in current_predictions}
    combined = [*reference_predictions, *current_predictions]
    total = len(combined) or 1
    return {
        "reference_unique_sample_ids": len(reference_ids),
        "current_unique_sample_ids": len(current_ids),
        "sample_overlap_count": len(reference_ids & current_ids),
        "content_sha256_coverage_rate": (
            sum(1 for item in combined if str(item.get("content_sha256") or "")) / total
        ),
        "image_uri_coverage_rate": (
            sum(1 for item in combined if str(item.get("image_uri") or "").startswith("file:///"))
            / total
        ),
        "valid": (
            len(reference_ids) == len(reference_predictions)
            and len(current_ids) == len(current_predictions)
            and not reference_ids.intersection(current_ids)
            and all(str(item.get("content_sha256") or "") for item in combined)
            and all(str(item.get("image_uri") or "").startswith("file:///") for item in combined)
        ),
    }


def window_observation_range(records: list[dict[str, Any]]) -> dict[str, str | None]:
    timestamps = sorted(
        str(record.get("quality_checked_at") or "")
        for record in records
        if record.get("quality_checked_at")
    )
    return {
        "observed_from": timestamps[0] if timestamps else None,
        "observed_to": timestamps[-1] if timestamps else None,
    }


def run(config_path: str = "configs/local_visa.toml") -> dict[str, Any]:
    import torch

    config = load_config(config_path)
    project_root = Path(str(config["_project_root"]))
    pipeline_config = get_nested(config, "pipelines.drift_review", {})
    policy_path = resolve_path(
        config,
        str(pipeline_config.get("policy", "configs/b7_drift_policy.toml")),
    )
    with policy_path.open("rb") as fp:
        policy = tomllib.load(fp)

    identity = policy["identity"]
    inputs = policy["inputs"]
    windows = policy["windows"]
    policy_values = policy["policy"]
    execution = policy["execution"]
    outputs = policy["outputs"]

    shard_index_path = runtime_path(str(inputs["shard_index"]))
    model_path = runtime_path(str(inputs["model_path"]))
    model_sha256 = file_sha256(model_path)
    shard_index_sha256 = file_sha256(shard_index_path)
    if model_sha256.lower() != str(inputs["model_sha256"]).lower():
        raise ValueError("model_sha256 does not match the pinned B7 checkpoint")
    if shard_index_sha256.lower() != str(inputs["shard_index_sha256"]).lower():
        raise ValueError("shard_index_sha256 does not match the pinned split source")
    artifact_root_raw = str(outputs["artifact_root"])
    artifact_root = runtime_path(artifact_root_raw)
    rid = run_id("drift-review")
    run_dir = artifact_root / rid
    run_dir.mkdir(parents=True, exist_ok=True)

    trace = TraceContext.from_environment("drift_review", rid)
    _shard_index, splits = load_shard_records(shard_index_path)
    seed = int(execution.get("seed", 20260710))
    reference_config = windows["reference"]
    current_config = windows["current"]
    reference_records = select_window(
        splits[str(reference_config["split"])],
        class_names=[str(value) for value in reference_config.get("class_names", [])],
        max_records=int(reference_config.get("max_records", 0)),
        seed=seed,
    )
    current_records = select_window(
        splits[str(current_config["split"])],
        class_names=[str(value) for value in current_config.get("class_names", [])],
        max_records=int(current_config.get("max_records", 0)),
        seed=seed + 1,
    )
    if len(reference_records) < int(reference_config["min_records"]):
        raise ValueError("reference window does not satisfy min_records")
    if len(current_records) < int(current_config["min_records"]):
        raise ValueError("current window does not satisfy min_records")
    reference_ids = {str(item.get("sample_id") or item.get("id")) for item in reference_records}
    current_ids = {str(item.get("sample_id") or item.get("id")) for item in current_records}
    if reference_ids & current_ids:
        raise ValueError("reference and current windows must be disjoint")

    model, device, checkpoint = load_checkpoint(
        model_path,
        require_cuda=bool(execution.get("require_cuda", True)),
    )
    input_size = int(checkpoint.get("input_size") or 600)
    reference_predictions = predict_window(
        reference_records,
        model=model,
        device=device,
        input_size=input_size,
        batch_size=int(execution.get("batch_size", 8)),
        num_workers=int(execution.get("num_workers", 4)),
    )
    current_predictions = predict_window(
        current_records,
        model=model,
        device=device,
        input_size=input_size,
        batch_size=int(execution.get("batch_size", 8)),
        num_workers=int(execution.get("num_workers", 4)),
    )

    thresholds = {
        key: float(policy_values[key])
        for key in (
            "input_category_js",
            "predicted_class_js",
            "confidence_psi",
            "mean_confidence_drop",
            "low_confidence_rate_increase",
        )
    }
    low_confidence_threshold = float(policy_values["low_confidence_threshold"])
    evaluation = evaluate_measured_drift(
        reference_predictions=reference_predictions,
        current_predictions=current_predictions,
        thresholds=thresholds,
        low_confidence_threshold=low_confidence_threshold,
    )
    input_validation = real_input_validation(reference_predictions, current_predictions)
    if not input_validation["valid"]:
        raise ValueError("real input lineage validation failed")
    event_id = f"drift-{hashlib.sha256(f'{rid}:{model_sha256}'.encode()).hexdigest()[:16]}"
    review_required = evaluation["decision"] == "review_required"
    queue = (
        queue_records(
            current_predictions,
            event_id=event_id,
            triggered_rules=evaluation["triggered_rules"],
            low_confidence_threshold=low_confidence_threshold,
            max_records=int(policy_values.get("max_queue_records", 128)),
        )
        if review_required
        else []
    )

    reference_window_id = str(reference_config["window_id"])
    current_window_id = str(current_config["window_id"])
    reference_predictions_path = run_dir / "reference_predictions.jsonl"
    current_predictions_path = run_dir / "current_predictions.jsonl"
    queue_path = run_dir / "label_review_queue.jsonl"
    report_path = run_dir / "drift_report.json"
    event_path = run_dir / "review_event.json"
    write_jsonl(reference_predictions_path, reference_predictions)
    write_jsonl(current_predictions_path, current_predictions)
    write_jsonl(queue_path, queue)

    reference_dataset_version = f"{identity['dataset_version']}:{reference_window_id}"
    current_dataset_version = f"{identity['dataset_version']}:{current_window_id}"
    canonical_report_uri = canonical_uri(artifact_root_raw, rid, report_path.name)
    canonical_event_uri = canonical_uri(artifact_root_raw, rid, event_path.name)
    canonical_queue_uri = canonical_uri(artifact_root_raw, rid, queue_path.name)
    event = {
        "schema_version": "evm.w7.drift_review_event.v1",
        "event_id": event_id,
        "event_type": "review_required" if review_required else "within_policy",
        "status": "open" if review_required else "closed",
        "action": "label_review" if review_required else "none",
        "approval_state": "pending" if review_required else "not_required",
        "approval_required": review_required,
        "automatic_retraining": False,
        "automatic_deployment": False,
        "automatic_promotion": False,
        "candidate_id": str(identity["candidate_id"]),
        "model_sha256": model_sha256,
        "dataset_version": str(identity["dataset_version"]),
        "reference_window_id": reference_window_id,
        "current_window_id": current_window_id,
        "triggered_rules": evaluation["triggered_rules"],
        "thresholds": thresholds,
        "evidence_uri": canonical_report_uri,
        "label_review_queue_uri": canonical_queue_uri,
        "created_at": utc_now(),
    }
    report = {
        "schema_version": "evm.w7.measured_drift_report.v1",
        "run_id": rid,
        "trace": trace.to_dict(),
        "status": "review_required" if review_required else "pass",
        "decision": evaluation["decision"],
        "candidate_id": str(identity["candidate_id"]),
        "architecture": "efficientnet-b7",
        "model_sha256": event["model_sha256"],
        "model_artifact": str(inputs["model_path"]),
        "dataset_version": str(identity["dataset_version"]),
        "source_shard_index": str(inputs["shard_index"]),
        "source_shard_index_sha256": shard_index_sha256,
        "reference_dataset_version": reference_dataset_version,
        "current_dataset_version": current_dataset_version,
        "reference_window_id": reference_window_id,
        "current_window_id": current_window_id,
        "reference_split": str(reference_config["split"]),
        "current_split": str(current_config["split"]),
        "current_class_names": [str(value) for value in current_config.get("class_names", [])],
        "reference_window": {
            "window_id": reference_window_id,
            "split": str(reference_config["split"]),
            "class_names": [str(value) for value in reference_config.get("class_names", [])],
            "record_count": len(reference_records),
            **window_observation_range(reference_records),
        },
        "current_window": {
            "window_id": current_window_id,
            "split": str(current_config["split"]),
            "class_names": [str(value) for value in current_config.get("class_names", [])],
            "record_count": len(current_records),
            **window_observation_range(current_records),
        },
        "thresholds": thresholds,
        "low_confidence_threshold": low_confidence_threshold,
        "triggered_rules": evaluation["triggered_rules"],
        "metrics": evaluation["metrics"],
        "reference": evaluation["reference"],
        "current": evaluation["current"],
        "review_queue_count": len(queue),
        "review_event_id": event_id,
        "review_event_uri": canonical_event_uri,
        "label_review_queue_uri": canonical_queue_uri,
        "automatic_retraining": False,
        "automatic_deployment": False,
        "automatic_promotion": False,
        "real_input_validation": input_validation,
        "runtime": {
            "device": str(device),
            "cuda_available": device.type == "cuda",
            "cuda_device_name": (
                torch.cuda.get_device_name(0)
                if device.type == "cuda"
                else None
            ),
            "input_size": input_size,
            "batch_size": int(execution.get("batch_size", 8)),
        },
        "created_at": utc_now(),
    }
    write_json(event_path, event)
    write_json(report_path, report)
    write_json(artifact_root / "latest_review_event.json", event)
    write_json(artifact_root / "latest_drift_report.json", report)
    write_jsonl(artifact_root / "latest_label_review_queue.jsonl", queue)
    evidence_index = {
        "schema_version": "evm.w7.drift_review_evidence_index.v1",
        "run_id": rid,
        "status": report["status"],
        "completion_claim_allowed": (
            review_required
            and bool(queue)
            and bool(input_validation["valid"])
            and device.type == "cuda"
        ),
        "no_mock_no_smoke": True,
        "model_sha256": event["model_sha256"],
        "source_shard_index_sha256": shard_index_sha256,
        "reference_record_count": len(reference_predictions),
        "current_record_count": len(current_predictions),
        "real_input_validation": input_validation,
        "files": {
            path.name: {
                "uri": canonical_uri(artifact_root_raw, rid, path.name),
                "sha256": file_sha256(path),
            }
            for path in (
                reference_predictions_path,
                current_predictions_path,
                queue_path,
                report_path,
                event_path,
            )
        },
        "created_at": utc_now(),
    }
    write_json(run_dir / "evidence-index.json", evidence_index)
    return {
        "pipeline": "drift-review",
        "run_id": rid,
        "status": report["status"],
        "decision": report["decision"],
        "candidate_id": report["candidate_id"],
        "reference_records": evaluation["reference"]["record_count"],
        "current_records": evaluation["current"]["record_count"],
        "triggered_rules": evaluation["triggered_rules"],
        "review_queue_count": len(queue),
        "automatic_retraining": False,
        "report_uri": canonical_report_uri,
        "event_uri": canonical_event_uri,
        "evidence_index": canonical_uri(artifact_root_raw, rid, "evidence-index.json"),
        "project_root": str(project_root),
    }


def main(argv: Sequence[str] | None = None) -> None:
    import sys

    config_path = argv[0] if argv else (
        sys.argv[1] if len(sys.argv) > 1 else "configs/local_visa.toml"
    )
    print(json.dumps(run(config_path), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
