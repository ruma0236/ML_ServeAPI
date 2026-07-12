from __future__ import annotations

import hashlib
import math
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


FEATURE_NAMES = [
    "width",
    "height",
    "aspect_ratio",
    "megapixels",
    "file_size_mb",
    "byte_mean",
    "byte_std",
    "byte_entropy",
    *[f"byte_bin_{idx:02d}" for idx in range(16)],
]


def resolve_image_path(
    image_uri: str | None,
    *,
    image_path: str | None = None,
    host_data_root: str | None = None,
    data_mount_root: str | None = None,
) -> Path | None:
    raw_value = image_path or image_uri
    if not raw_value:
        return None

    if raw_value.startswith("file://"):
        parsed = urlparse(raw_value)
        raw_value = unquote(parsed.path)
        if parsed.netloc and len(parsed.netloc) == 2 and parsed.netloc.endswith(":"):
            raw_value = f"{parsed.netloc}{raw_value}"
        if raw_value.startswith("/") and len(raw_value) > 3 and raw_value[2] == ":":
            raw_value = raw_value[1:]

    mapped_value = raw_value.replace("\\", "/")
    if host_data_root and data_mount_root:
        host_root = host_data_root.replace("\\", "/").rstrip("/")
        mount_root = data_mount_root.replace("\\", "/").rstrip("/")
        if mapped_value.lower().startswith(host_root.lower()):
            mapped_value = f"{mount_root}{mapped_value[len(host_root):]}"

    return Path(mapped_value)


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None

    offset = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset + 9 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += segment_length
    return None


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def infer_image_dimensions(data: bytes) -> tuple[int, int] | None:
    return _jpeg_dimensions(data) or _png_dimensions(data)


def _byte_stats(sample: bytes) -> tuple[float, float, float, list[float]]:
    if not sample:
        return 0.0, 0.0, 0.0, [0.0] * 16

    count = len(sample)
    mean = sum(sample) / count
    variance = sum((value - mean) ** 2 for value in sample) / count
    bins = [0] * 16
    for value in sample:
        bins[min(value // 16, 15)] += 1
    entropy = 0.0
    histogram = []
    for bin_count in bins:
        ratio = bin_count / count
        histogram.append(ratio)
        if ratio > 0:
            entropy -= ratio * math.log2(ratio)
    return mean / 255.0, math.sqrt(variance) / 255.0, entropy / 4.0, histogram


def extract_image_features(
    path: Path,
    *,
    width: int | float | None = None,
    height: int | float | None = None,
    sample_bytes: int = 65536,
) -> dict[str, float]:
    file_size = path.stat().st_size if path.exists() else 0
    with path.open("rb") as fp:
        header = fp.read(max(sample_bytes, 64))

    inferred_dimensions = infer_image_dimensions(header)
    if inferred_dimensions:
        inferred_width, inferred_height = inferred_dimensions
        width = width or inferred_width
        height = height or inferred_height

    width_value = float(width or 0)
    height_value = float(height or 0)
    aspect_ratio = width_value / height_value if height_value else 0.0
    megapixels = (width_value * height_value) / 1_000_000 if width_value and height_value else 0.0
    byte_mean, byte_std, byte_entropy, histogram = _byte_stats(header[:sample_bytes])

    features = {
        "width": width_value,
        "height": height_value,
        "aspect_ratio": aspect_ratio,
        "megapixels": megapixels,
        "file_size_mb": file_size / 1_000_000,
        "byte_mean": byte_mean,
        "byte_std": byte_std,
        "byte_entropy": byte_entropy,
    }
    for idx, value in enumerate(histogram):
        features[f"byte_bin_{idx:02d}"] = value
    return features


def vector_from_features(features: dict[str, Any], feature_names: list[str]) -> list[float]:
    vector = []
    for name in feature_names:
        value = features.get(name, 0.0)
        try:
            vector.append(float(value))
        except (TypeError, ValueError):
            vector.append(0.0)
    return vector


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float], mean: float) -> float:
    if not values:
        return 1.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) or 1.0


def _standardize(vector: list[float], means: list[float], stds: list[float]) -> list[float]:
    return [(value - means[idx]) / stds[idx] for idx, value in enumerate(vector)]


def _distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((left[idx] - right[idx]) ** 2 for idx in range(len(left))))


def _softmax_from_distances(distances: dict[str, float]) -> dict[str, float]:
    if not distances:
        return {}
    scaled = {label: -value for label, value in distances.items()}
    max_score = max(scaled.values())
    exps = {label: math.exp(value - max_score) for label, value in scaled.items()}
    total = sum(exps.values()) or 1.0
    return {label: value / total for label, value in exps.items()}


def predict_with_model(model_payload: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    feature_names = [str(item) for item in model_payload.get("feature_names", FEATURE_NAMES)]
    vector = vector_from_features(features, feature_names)
    preprocessing = model_payload.get("preprocessing", {})
    means = [float(value) for value in preprocessing.get("feature_means", [0.0] * len(vector))]
    stds = [float(value) or 1.0 for value in preprocessing.get("feature_stds", [1.0] * len(vector))]
    standardized = _standardize(vector, means, stds)
    centroids = model_payload.get("centroids", {})
    distances = {
        str(label): _distance(standardized, [float(value) for value in centroid])
        for label, centroid in centroids.items()
        if isinstance(centroid, list)
    }
    probabilities = _softmax_from_distances(distances)
    prediction = max(probabilities, key=probabilities.get) if probabilities else "unknown"
    return {
        "prediction": prediction,
        "confidence": probabilities.get(prediction, 0.0),
        "scores": probabilities,
        "distances": distances,
        "feature_names": feature_names,
    }


def _binary_metrics(
    labels: list[str],
    predictions: list[str],
    scores: list[float],
    positive_label: str,
) -> dict[str, float]:
    tp = sum(1 for idx, label in enumerate(labels) if label == positive_label and predictions[idx] == label)
    fp = sum(
        1
        for idx, label in enumerate(labels)
        if label != positive_label and predictions[idx] == positive_label
    )
    fn = sum(
        1
        for idx, label in enumerate(labels)
        if label == positive_label and predictions[idx] != positive_label
    )
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    positives = [score for idx, score in enumerate(scores) if labels[idx] == positive_label]
    negatives = [score for idx, score in enumerate(scores) if labels[idx] != positive_label]
    auc_pairs = 0.0
    for pos_score in positives:
        for neg_score in negatives:
            if pos_score > neg_score:
                auc_pairs += 1.0
            elif pos_score == neg_score:
                auc_pairs += 0.5
    auroc = auc_pairs / (len(positives) * len(negatives)) if positives and negatives else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "auroc": auroc}


def _evaluate_rows(
    rows: list[dict[str, Any]],
    model_payload: dict[str, Any],
    positive_label: str,
) -> dict[str, Any]:
    labels: list[str] = []
    predictions: list[str] = []
    positive_scores: list[float] = []
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    sample_predictions = []
    false_predictions = []

    for row in rows:
        label = str(row["label"])
        result = predict_with_model(model_payload, row["features"])
        prediction = str(result["prediction"])
        labels.append(label)
        predictions.append(prediction)
        positive_scores.append(float(result.get("scores", {}).get(positive_label, 0.0)))
        confusion[label][prediction] += 1
        if len(sample_predictions) < 8:
            sample_predictions.append(
                {
                    "sample_id": row.get("sample_id", ""),
                    "label": label,
                    "prediction": prediction,
                    "confidence": round(float(result["confidence"]), 6),
                    "image_uri": row.get("image_uri", ""),
                }
            )
        if label != prediction and len(false_predictions) < 100:
            false_predictions.append(
                {
                    "sample_id": row.get("sample_id", ""),
                    "label": label,
                    "prediction": prediction,
                    "confidence": round(float(result["confidence"]), 6),
                    "image_uri": row.get("image_uri", ""),
                }
            )

    accuracy = sum(1 for idx, label in enumerate(labels) if predictions[idx] == label) / len(labels) if labels else 0.0
    binary = _binary_metrics(labels, predictions, positive_scores, positive_label)
    return {
        "records": len(rows),
        "accuracy": accuracy,
        **binary,
        "confusion_matrix": {
            label: dict(pred_counts) for label, pred_counts in sorted(confusion.items())
        },
        "sample_predictions": sample_predictions,
        "false_predictions": false_predictions,
    }


def train_centroid_classifier(
    rows: list[dict[str, Any]],
    *,
    model_name: str,
    dataset_metadata: dict[str, Any],
    positive_label: str = "anomaly",
) -> dict[str, Any]:
    train_rows = [row for row in rows if str(row.get("split", "train")) == "train"]
    validation_rows = [row for row in rows if str(row.get("split", "")) in {"validation", "val"}]
    test_rows = [row for row in rows if str(row.get("split", "")) == "test"]
    if not train_rows:
        train_rows = rows

    train_vectors = [vector_from_features(row["features"], FEATURE_NAMES) for row in train_rows]
    feature_columns = list(zip(*train_vectors, strict=False)) if train_vectors else []
    feature_means = [_mean(list(column)) for column in feature_columns]
    feature_stds = [_std(list(column), feature_means[idx]) for idx, column in enumerate(feature_columns)]
    standardized_train = [_standardize(vector, feature_means, feature_stds) for vector in train_vectors]

    grouped: dict[str, list[list[float]]] = defaultdict(list)
    for idx, row in enumerate(train_rows):
        grouped[str(row["label"])].append(standardized_train[idx])

    centroids = {}
    for label, vectors in grouped.items():
        centroids[label] = [
            _mean([vector[feature_idx] for vector in vectors])
            for feature_idx in range(len(FEATURE_NAMES))
        ]

    label_counts = Counter(str(row["label"]) for row in train_rows)
    total_train = sum(label_counts.values()) or 1
    provisional_model = {
        "model_name": model_name,
        "model_type": "image_feature_centroid",
        "feature_names": FEATURE_NAMES,
        "preprocessing": {
            "feature_means": feature_means,
            "feature_stds": feature_stds,
        },
        "centroids": centroids,
        "classes": sorted(centroids),
        "class_priors": {
            label: count / total_train for label, count in sorted(label_counts.items())
        },
        "positive_label": positive_label,
    }

    train_eval = _evaluate_rows(train_rows, provisional_model, positive_label)
    validation_eval = _evaluate_rows(validation_rows, provisional_model, positive_label)
    test_eval = _evaluate_rows(test_rows, provisional_model, positive_label)
    selected_eval = test_eval if test_eval["records"] else validation_eval if validation_eval["records"] else train_eval

    model_payload = {
        **provisional_model,
        "schema_version": "evm.image_feature_centroid.v1",
        "dataset": dataset_metadata,
        "training_records": len(train_rows),
        "validation_records": len(validation_rows),
        "test_records": len(test_rows),
        "label_counts": dict(Counter(str(row["label"]) for row in rows)),
        "prediction": max(label_counts, key=label_counts.get) if label_counts else "unknown",
        "metrics": {
            "accuracy": selected_eval["accuracy"],
            "precision": selected_eval["precision"],
            "recall": selected_eval["recall"],
            "f1": selected_eval["f1"],
            "auroc": selected_eval["auroc"],
        },
        "evaluation": {
            "selected_split": "test"
            if test_eval["records"]
            else "validation"
            if validation_eval["records"]
            else "train",
            "train": train_eval,
            "validation": validation_eval,
            "test": test_eval,
        },
    }
    return model_payload


def collect_local_resource_profile() -> dict[str, Any]:
    profile: dict[str, Any] = {
        "cpu_count": os.cpu_count(),
        "process_id": os.getpid(),
        "accelerator_used": "cpu",
        "gpu_detected": False,
        "gpu": [],
    }
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        profile["gpu_probe_error"] = f"{type(exc).__name__}: {exc}"
        return profile

    if result.returncode != 0:
        profile["gpu_probe_error"] = result.stderr.strip()
        return profile

    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 5:
            profile["gpu"].append(
                {
                    "name": parts[0],
                    "memory_total_mib": parts[1],
                    "memory_used_mib": parts[2],
                    "memory_free_mib": parts[3],
                    "driver_version": parts[4],
                }
            )
    profile["gpu_detected"] = bool(profile["gpu"])
    return profile


def model_digest(model_payload: dict[str, Any]) -> str:
    material = repr(
        {
            "model_type": model_payload.get("model_type"),
            "feature_names": model_payload.get("feature_names"),
            "preprocessing": model_payload.get("preprocessing"),
            "centroids": model_payload.get("centroids"),
            "dataset": model_payload.get("dataset"),
        }
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
