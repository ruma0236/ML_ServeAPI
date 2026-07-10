from __future__ import annotations

import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evm.core.image_feature_model import resolve_image_path
from evm.core.pipeline import utc_now, write_json


CLASS_NAMES = ["anomaly", "normal"]
POSITIVE_CLASS = "anomaly"


@dataclass(frozen=True)
class TorchRuntimeConfig:
    seed: int
    require_cuda: bool
    num_workers: int
    pin_memory: bool
    mlflow_tracking_uri: str
    mlflow_experiment_name: str


@dataclass(frozen=True)
class EfficientNetCandidateConfig:
    candidate_id: str
    architecture: str
    backbone: str
    input_size: int
    pretrained: bool
    freeze_backbone: bool
    optimizer: str
    learning_rate: float
    batch_size: int
    mixed_precision: bool
    resource_profile: str
    epochs: int


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    records.append(payload)
    return records


def set_torch_seed(seed: int) -> None:
    random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.benchmark = True
    except Exception:
        return


def normalized_label(record: dict[str, Any]) -> str:
    label_type = str(record.get("label_type") or record.get("label") or "").lower()
    if label_type == "normal":
        return "normal"
    return "anomaly"


def load_shard_records(shard_index_path: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    shard_index = read_json(shard_index_path)
    splits: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for shard in shard_index.get("shards", []):
        if not isinstance(shard, dict):
            continue
        split = str(shard.get("split") or "")
        if split not in splits:
            continue
        shard_path = Path(str(shard.get("path") or ""))
        if not shard_path.is_absolute():
            shard_path = shard_index_path.parent / shard_path
        records = read_jsonl(shard_path)
        for record in records:
            record = dict(record)
            record["_shard_id"] = shard.get("shard_id")
            record["_normalized_label"] = normalized_label(record)
            splits[split].append(record)
    return shard_index, splits


def split_manifest_snapshot(
    shard_index: dict[str, Any],
    splits: dict[str, list[dict[str, Any]]],
    *,
    dataset_version: str,
    seed: int,
) -> dict[str, Any]:
    split_counts = {name: len(records) for name, records in splits.items()}
    label_counts = {
        name: dict(Counter(str(record["_normalized_label"]) for record in records))
        for name, records in splits.items()
    }
    return {
        "schema_version": "evm.w7.efficientnet_split_manifest.v1",
        "dataset_version": dataset_version,
        "seed": seed,
        "source_shard_index": shard_index.get("schema_version", "evm.dataset_shards.v1"),
        "record_count": sum(split_counts.values()),
        "split_counts": split_counts,
        "label_counts": label_counts,
        "shard_count": len(shard_index.get("shards", [])),
        "shards": shard_index.get("shards", []),
        "created_at": utc_now(),
    }


def validate_acceptance_split(
    manifest: dict[str, Any],
    acceptance: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    split_counts = manifest.get("split_counts", {})
    total = int(manifest.get("record_count") or 0)
    requirements = {
        "total": int(acceptance.get("min_total_records") or 0),
        "train": int(acceptance.get("min_train_images") or 0),
        "validation": int(acceptance.get("min_validation_images") or 0),
        "test": int(acceptance.get("min_test_images") or 0),
    }
    if requirements["total"] and total < requirements["total"]:
        blockers.append(f"record_count<{requirements['total']}")
    for split in ("train", "validation", "test"):
        required = requirements[split]
        actual = int(split_counts.get(split) or 0)
        if required and actual < required:
            blockers.append(f"{split}_images<{required}")
    return blockers


def environment_report(device: Any) -> dict[str, Any]:
    import torch
    import torchvision

    report: dict[str, Any] = {
        "schema_version": "evm.w7.efficientnet_environment.v1",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "torch": torch.__version__,
        "torchvision": torchvision.__version__,
        "cuda_available": torch.cuda.is_available(),
        "cuda_version": torch.version.cuda,
        "device": str(device),
        "created_at": utc_now(),
    }
    if torch.cuda.is_available():
        index = torch.cuda.current_device()
        report.update(
            {
                "cuda_device_index": index,
                "cuda_device_name": torch.cuda.get_device_name(index),
                "cuda_device_capability": list(torch.cuda.get_device_capability(index)),
                "cuda_memory_total": torch.cuda.get_device_properties(index).total_memory,
            }
        )
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
            check=False,
            timeout=10,
        )
        report["nvidia_smi"] = result.stdout.strip()
        report["nvidia_smi_exit_code"] = result.returncode
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        report["nvidia_smi_error"] = str(exc)
    return report


class VisaImageDataset:
    def __init__(self, records: list[dict[str, Any]], input_size: int) -> None:
        from PIL import Image
        from torchvision import transforms

        self.records = records
        self.image_module = Image
        self.label_to_index = {label: idx for idx, label in enumerate(CLASS_NAMES)}
        self.transform = transforms.Compose(
            [
                transforms.Resize((input_size, input_size)),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225],
                ),
            ]
        )

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> tuple[Any, int]:
        record = self.records[index]
        path = resolve_image_path(
            str(record.get("image_uri") or ""),
            image_path=str(record.get("image_path") or ""),
        )
        if path is None or not path.exists():
            sample_id = record.get("sample_id") or record.get("id") or index
            raise FileNotFoundError(f"missing image for sample {sample_id}: {path}")
        with self.image_module.open(path) as image:
            tensor = self.transform(image.convert("RGB"))
        label = self.label_to_index[str(record["_normalized_label"])]
        return tensor, label


def build_model(candidate: EfficientNetCandidateConfig, num_classes: int) -> Any:
    import torch
    from torch import nn
    from torchvision import models

    if candidate.architecture == "efficientnet-b0":
        weights = models.EfficientNet_B0_Weights.DEFAULT if candidate.pretrained else None
        model = models.efficientnet_b0(weights=weights)
    elif candidate.architecture == "efficientnet-b7":
        weights = models.EfficientNet_B7_Weights.DEFAULT if candidate.pretrained else None
        model = models.efficientnet_b7(weights=weights)
    else:
        raise ValueError(f"unsupported architecture: {candidate.architecture}")

    if candidate.freeze_backbone:
        for parameter in model.features.parameters():
            parameter.requires_grad = False

    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, num_classes)
    return model


def build_optimizer(candidate: EfficientNetCandidateConfig, parameters: Iterable[Any]) -> Any:
    import torch

    if candidate.optimizer.lower() == "adamw":
        return torch.optim.AdamW(parameters, lr=candidate.learning_rate)
    if candidate.optimizer.lower() == "sgd":
        return torch.optim.SGD(parameters, lr=candidate.learning_rate, momentum=0.9)
    raise ValueError(f"unsupported optimizer: {candidate.optimizer}")


def class_weights(records: list[dict[str, Any]], device: Any) -> Any:
    import torch

    counts = Counter(str(record["_normalized_label"]) for record in records)
    total = sum(counts.values()) or 1
    values = []
    for label in CLASS_NAMES:
        count = counts.get(label, 0) or 1
        values.append(total / (len(CLASS_NAMES) * count))
    return torch.tensor(values, dtype=torch.float32, device=device)


def binary_auroc(labels: list[int], positive_scores: list[float]) -> float:
    positives = [score for idx, score in enumerate(positive_scores) if labels[idx] == 0]
    negatives = [score for idx, score in enumerate(positive_scores) if labels[idx] != 0]
    if not positives or not negatives:
        return 0.0
    pairs = 0.0
    for pos_score in positives:
        for neg_score in negatives:
            if pos_score > neg_score:
                pairs += 1.0
            elif math.isclose(pos_score, neg_score):
                pairs += 0.5
    return pairs / (len(positives) * len(negatives))


def classification_metrics(
    labels: list[int],
    predictions: list[int],
    positive_scores: list[float],
    latency_samples_ms: list[float],
) -> dict[str, Any]:
    confusion = [[0 for _ in CLASS_NAMES] for _ in CLASS_NAMES]
    for label, prediction in zip(labels, predictions, strict=False):
        confusion[label][prediction] += 1

    total = len(labels) or 1
    correct = sum(1 for idx, label in enumerate(labels) if predictions[idx] == label)
    per_class: dict[str, Any] = {}
    for class_idx, class_name in enumerate(CLASS_NAMES):
        tp = confusion[class_idx][class_idx]
        fp = sum(confusion[row][class_idx] for row in range(len(CLASS_NAMES)) if row != class_idx)
        fn = sum(confusion[class_idx][col] for col in range(len(CLASS_NAMES)) if col != class_idx)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
        per_class[class_name] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(confusion[class_idx]),
        }

    positive = per_class[POSITIVE_CLASS]
    return {
        "accuracy": correct / total,
        "precision": positive["precision"],
        "recall": positive["recall"],
        "f1": positive["f1"],
        "auroc": binary_auroc(labels, positive_scores),
        "latency_p95_ms": percentile(latency_samples_ms, 95),
        "confusion_matrix": {
            "labels": CLASS_NAMES,
            "matrix": confusion,
        },
        "per_class": per_class,
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil((pct / 100) * len(ordered)) - 1))
    return ordered[index]


def evaluate(model: Any, loader: Any, device: Any) -> dict[str, Any]:
    import torch

    model.eval()
    labels: list[int] = []
    predictions: list[int] = []
    positive_scores: list[float] = []
    latency_samples_ms: list[float] = []
    with torch.no_grad():
        for images, targets in loader:
            images = images.to(device, non_blocking=True)
            targets = targets.to(device, non_blocking=True)
            started = time.perf_counter()
            logits = model(images)
            if device.type == "cuda":
                torch.cuda.synchronize()
            elapsed_ms = (time.perf_counter() - started) * 1000
            probs = torch.softmax(logits, dim=1)
            preds = probs.argmax(dim=1)
            batch_size = int(images.shape[0])
            per_image_ms = elapsed_ms / max(batch_size, 1)
            latency_samples_ms.extend([per_image_ms] * batch_size)
            labels.extend(int(item) for item in targets.detach().cpu().tolist())
            predictions.extend(int(item) for item in preds.detach().cpu().tolist())
            positive_scores.extend(float(item) for item in probs[:, 0].detach().cpu().tolist())
    return classification_metrics(labels, predictions, positive_scores, latency_samples_ms)


def save_confusion_png(confusion_payload: dict[str, Any], path: Path) -> None:
    try:
        import matplotlib.pyplot as plt

        matrix = confusion_payload["matrix"]
        labels = confusion_payload["labels"]
        fig, ax = plt.subplots(figsize=(4, 4))
        ax.imshow(matrix, cmap="Blues")
        ax.set_xticks(range(len(labels)), labels)
        ax.set_yticks(range(len(labels)), labels)
        ax.set_xlabel("Predicted")
        ax.set_ylabel("Actual")
        for row_idx, row in enumerate(matrix):
            for col_idx, value in enumerate(row):
                ax.text(col_idx, row_idx, str(value), ha="center", va="center", color="black")
        fig.tight_layout()
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path)
        plt.close(fig)
    except Exception as exc:
        path.with_suffix(".txt").write_text(str(exc), encoding="utf-8")


def metric_blockers(metrics: dict[str, Any], acceptance: dict[str, Any]) -> list[str]:
    thresholds = {
        "accuracy": acceptance.get("promotion_min_accuracy"),
        "f1": acceptance.get("promotion_min_f1"),
        "auroc": acceptance.get("promotion_min_auroc"),
    }
    blockers = []
    for name, threshold in thresholds.items():
        if isinstance(threshold, int | float) and float(metrics.get(name, 0.0)) < float(threshold):
            blockers.append(f"{name}<{threshold}")
    return blockers


def gpu_profile(device: Any) -> dict[str, Any]:
    import torch

    profile: dict[str, Any] = {
        "schema_version": "evm.w7.efficientnet_gpu_profile.v1",
        "device": str(device),
        "created_at": utc_now(),
    }
    if device.type == "cuda":
        index = torch.cuda.current_device()
        profile.update(
            {
                "cuda_device_index": index,
                "cuda_device_name": torch.cuda.get_device_name(index),
                "cuda_memory_allocated_mb": round(torch.cuda.memory_allocated(index) / 1_048_576, 3),
                "cuda_memory_reserved_mb": round(torch.cuda.memory_reserved(index) / 1_048_576, 3),
                "cuda_memory_peak_mb": round(torch.cuda.max_memory_allocated(index) / 1_048_576, 3),
            }
        )
    return profile


def write_model_card(path: Path, summary: dict[str, Any]) -> None:
    metrics = summary.get("metrics", {})
    lines = [
        f"# {summary['candidate_id']} Model Card",
        "",
        f"- Status: `{summary['status']}`",
        f"- Architecture: `{summary['architecture']}`",
        f"- Dataset version: `{summary['dataset_version']}`",
        f"- MLflow run id: `{summary.get('mlflow_run_id', '')}`",
        f"- Artifact URI: `{summary.get('artifact_uri', '')}`",
        "",
        "## Metrics",
        "",
    ]
    for key in ("accuracy", "precision", "recall", "f1", "auroc", "latency_p95_ms"):
        if key in metrics:
            lines.append(f"- `{key}`: `{round(float(metrics[key]), 6)}`")
    blockers = summary.get("promotion_blockers", [])
    lines.extend(["", "## Promotion Blockers", ""])
    if blockers:
        lines.extend(f"- `{blocker}`" for blocker in blockers)
    else:
        lines.append("- None")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def log_mlflow_run(
    candidate: EfficientNetCandidateConfig,
    candidate_dir: Path,
    summary: dict[str, Any],
    runtime: TorchRuntimeConfig,
) -> tuple[str, str | None]:
    try:
        import mlflow

        mlflow.set_tracking_uri(runtime.mlflow_tracking_uri)
        mlflow.set_experiment(runtime.mlflow_experiment_name)
        with mlflow.start_run(run_name=candidate.candidate_id) as active_run:
            run_id = active_run.info.run_id
            mlflow.log_params(
                {
                    "candidate_id": candidate.candidate_id,
                    "architecture": candidate.architecture,
                    "backbone": candidate.backbone,
                    "input_size": candidate.input_size,
                    "pretrained": candidate.pretrained,
                    "freeze_backbone": candidate.freeze_backbone,
                    "optimizer": candidate.optimizer,
                    "learning_rate": candidate.learning_rate,
                    "batch_size": candidate.batch_size,
                    "mixed_precision": candidate.mixed_precision,
                    "epochs": candidate.epochs,
                    "seed": runtime.seed,
                    "dataset_version": summary["dataset_version"],
                }
            )
            for key, value in summary.get("metrics", {}).items():
                if isinstance(value, int | float):
                    mlflow.log_metric(key, float(value))
            mlflow.log_artifacts(str(candidate_dir))
        return "logged", run_id
    except Exception as exc:
        return "blocked", None if not str(exc) else str(exc)


def train_candidate(
    candidate: EfficientNetCandidateConfig,
    splits: dict[str, list[dict[str, Any]]],
    split_manifest: dict[str, Any],
    acceptance: dict[str, Any],
    runtime: TorchRuntimeConfig,
    candidate_dir: Path,
) -> dict[str, Any]:
    import torch
    from torch import nn
    from torch.cuda.amp import GradScaler, autocast
    from torch.utils.data import DataLoader

    if runtime.require_cuda and not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for W7 EfficientNet acceptance")

    set_torch_seed(runtime.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    train_dataset = VisaImageDataset(splits["train"], candidate.input_size)
    validation_dataset = VisaImageDataset(splits["validation"], candidate.input_size)
    test_dataset = VisaImageDataset(splits["test"], candidate.input_size)
    generator = torch.Generator()
    generator.manual_seed(runtime.seed)
    train_loader = DataLoader(
        train_dataset,
        batch_size=candidate.batch_size,
        shuffle=True,
        num_workers=runtime.num_workers,
        pin_memory=runtime.pin_memory,
        generator=generator,
    )
    validation_loader = DataLoader(
        validation_dataset,
        batch_size=candidate.batch_size,
        shuffle=False,
        num_workers=runtime.num_workers,
        pin_memory=runtime.pin_memory,
    )
    test_loader = DataLoader(
        test_dataset,
        batch_size=candidate.batch_size,
        shuffle=False,
        num_workers=runtime.num_workers,
        pin_memory=runtime.pin_memory,
    )

    model = build_model(candidate, len(CLASS_NAMES)).to(device)
    trainable_parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = build_optimizer(candidate, trainable_parameters)
    criterion = nn.CrossEntropyLoss(weight=class_weights(splits["train"], device))
    scaler = GradScaler(enabled=candidate.mixed_precision and device.type == "cuda")

    history: list[dict[str, Any]] = []
    optimizer_step_count = 0
    started_at = time.perf_counter()
    for epoch in range(1, candidate.epochs + 1):
        model.train()
        losses: list[float] = []
        for images, targets in train_loader:
            images = images.to(device, non_blocking=True)
            targets = targets.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with autocast(enabled=candidate.mixed_precision and device.type == "cuda"):
                logits = model(images)
                loss = criterion(logits, targets)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            optimizer_step_count += 1
            losses.append(float(loss.detach().cpu().item()))
        validation_metrics = evaluate(model, validation_loader, device)
        history.append(
            {
                "epoch": epoch,
                "optimizer_steps": optimizer_step_count,
                "train_loss": statistics.mean(losses) if losses else 0.0,
                "validation": {
                    key: validation_metrics[key]
                    for key in ("accuracy", "precision", "recall", "f1", "auroc", "latency_p95_ms")
                },
            }
        )

    test_metrics = evaluate(model, test_loader, device)
    duration_seconds = round(time.perf_counter() - started_at, 3)
    profile = gpu_profile(device)
    environment = environment_report(device)
    metrics = {
        key: float(test_metrics[key])
        for key in ("accuracy", "precision", "recall", "f1", "auroc", "latency_p95_ms")
    }
    metrics["gpu_memory_peak_mb"] = float(profile.get("cuda_memory_peak_mb", 0.0))
    blockers = metric_blockers(metrics, acceptance)

    candidate_dir.mkdir(parents=True, exist_ok=True)
    model_path = candidate_dir / "model.pt"
    torch.save(
        {
            "schema_version": "evm.w7.efficientnet_model.v1",
            "candidate_id": candidate.candidate_id,
            "architecture": candidate.architecture,
            "class_names": CLASS_NAMES,
            "state_dict": model.state_dict(),
            "input_size": candidate.input_size,
            "dataset_version": split_manifest["dataset_version"],
        },
        model_path,
    )
    write_json(candidate_dir / "training_history.json", history)
    write_json(candidate_dir / "confusion_matrix.json", test_metrics["confusion_matrix"])
    save_confusion_png(test_metrics["confusion_matrix"], candidate_dir / "confusion_matrix.png")
    write_json(candidate_dir / "gpu_profile.json", profile)
    write_json(candidate_dir / "environment_report.json", environment)
    write_json(candidate_dir / "split_manifest.json", split_manifest)

    summary = {
        "schema_version": "evm.w7.efficientnet_candidate.v1",
        "candidate_id": candidate.candidate_id,
        "status": "pass",
        "architecture": candidate.architecture,
        "backbone": candidate.backbone,
        "dataset_version": split_manifest["dataset_version"],
        "resource_profile": candidate.resource_profile,
        "conditions": {
            "input_size": candidate.input_size,
            "pretrained": candidate.pretrained,
            "freeze_backbone": candidate.freeze_backbone,
            "optimizer": candidate.optimizer,
            "learning_rate": candidate.learning_rate,
            "batch_size": candidate.batch_size,
            "mixed_precision": candidate.mixed_precision,
            "epochs": candidate.epochs,
            "seed": runtime.seed,
        },
        "metrics": metrics,
        "per_class": test_metrics["per_class"],
        "promotion_blockers": blockers,
        "artifact_uri": str(candidate_dir),
        "model_artifact": str(model_path),
        "training_duration_seconds": duration_seconds,
        "optimizer_step_count": optimizer_step_count,
        "created_at": utc_now(),
    }
    write_model_card(candidate_dir / "model_card.md", summary)
    mlflow_status, mlflow_result = log_mlflow_run(candidate, candidate_dir, summary, runtime)
    summary["mlflow_status"] = mlflow_status
    if mlflow_status == "logged":
        summary["mlflow_run_id"] = mlflow_result
        summary["run_uri"] = f"{runtime.mlflow_tracking_uri}/#/runs/{mlflow_result}"
    else:
        summary["status"] = "blocked"
        summary["mlflow_error"] = mlflow_result
        summary["execution_blockers"] = ["mlflow_run_missing"]
    write_json(candidate_dir / "candidate_summary.json", summary)
    return summary
