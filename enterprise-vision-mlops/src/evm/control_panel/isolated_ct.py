from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from evm.control_panel.readiness_evaluator import file_sha256, payload_sha256, runtime_path
from evm.control_panel.schemas import CTDatasetSnapshot, CTEvaluation, State
from evm.core.image_feature_model import resolve_image_path


CT_SNAPSHOT_SCHEMA = "evm.ct_dataset_snapshot.v1"
CT_EVALUATION_SCHEMA = "evm.ct_evaluation.v1"
TRAINING_VIEW_SCHEMA = "evm.training_data_view.v1"


@dataclass(frozen=True)
class TrainingDataView:
    root: Path
    shard_index_path: Path
    manifest_path: Path
    record_count: int
    linked_image_count: int
    identity_sha256: str


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def configured_host_data_root() -> Path:
    return Path(
        os.getenv(
            "EVM_HOST_DATA_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
        )
    )


def data_mount_root() -> str:
    return os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data").replace("\\", "/").rstrip("/")


def host_data_root() -> Path:
    configured = configured_host_data_root()
    mounted = Path(data_mount_root())
    if configured.exists() or not mounted.exists():
        return configured
    return mounted


def configured_host_ct_root() -> Path:
    return Path(
        os.getenv(
            "EVM_HOST_CT_ROOT",
            "F:/EnterpriseMLOps_CT/enterprise-vision-mlops",
        )
    )


def ct_mount_root() -> str:
    return os.getenv("EVM_CT_MOUNT_ROOT", "/mnt/evm-ct").replace("\\", "/").rstrip("/")


def host_ct_root() -> Path:
    configured = configured_host_ct_root()
    mounted = Path(ct_mount_root())
    if configured.exists() or not mounted.exists():
        return configured
    return mounted


def ct_runtime_path(value: str | Path) -> Path:
    normalized = str(value).replace("\\", "/")
    configured_root = str(configured_host_ct_root()).replace("\\", "/").rstrip("/")
    runtime_root = str(host_ct_root()).replace("\\", "/").rstrip("/")
    mount_root = ct_mount_root()
    if normalized.lower().startswith(runtime_root.lower()):
        return Path(value)
    if normalized.lower().startswith(mount_root.lower()):
        suffix = normalized[len(mount_root) :]
        return Path(f"{runtime_root}{suffix}")
    if normalized.lower().startswith(configured_root.lower()):
        suffix = normalized[len(configured_root) :]
        return Path(f"{runtime_root}{suffix}")
    path = Path(value)
    return path


def canonical_ct_uri(path: Path) -> str:
    normalized = str(path).replace("\\", "/")
    host_root = str(configured_host_ct_root()).replace("\\", "/").rstrip("/")
    runtime_root = str(host_ct_root()).replace("\\", "/").rstrip("/")
    mount_root = ct_mount_root()
    if normalized.lower().startswith(mount_root.lower()):
        return f"{host_root}{normalized[len(mount_root):]}"
    if normalized.lower().startswith(runtime_root.lower()):
        return f"{host_root}{normalized[len(runtime_root):]}"
    return normalized


def ct_container_uri(path: Path) -> str:
    normalized = str(path).replace("\\", "/")
    configured_root = str(configured_host_ct_root()).replace("\\", "/").rstrip("/")
    runtime_root = str(host_ct_root()).replace("\\", "/").rstrip("/")
    if normalized.lower().startswith(configured_root.lower()):
        return f"{ct_mount_root()}{normalized[len(configured_root):]}"
    if normalized.lower().startswith(runtime_root.lower()):
        return f"{ct_mount_root()}{normalized[len(runtime_root):]}"
    return normalized


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"jsonl_object_required:{path}:{line_number}")
            records.append(payload)
    return records


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def atomic_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def immutable_file(path: Path) -> None:
    try:
        path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        return


def record_id(record: dict[str, Any]) -> str:
    source = next(
        (
            str(record.get(key) or "")
            for key in (
                "ct_record_id",
                "record_id",
                "image_id",
                "image_uri",
                "path",
                "source_uri",
            )
            if record.get(key)
        ),
        "",
    )
    if not source:
        source = payload_sha256(
            {key: value for key, value in record.items() if not key.startswith("_")}
        )
    label = str(record.get("label_type") or record.get("label") or "").lower()
    normalized_label = "normal" if label == "normal" else "anomaly"
    return hashlib.sha256(f"{source}|{normalized_label}".encode()).hexdigest()


def records_sha256(records: list[dict[str, Any]]) -> str:
    return payload_sha256(sorted(record_id(record) for record in records))


def source_records_sha256_from_snapshot(records: list[dict[str, Any]]) -> str:
    identities = [str(record.get("ct_record_id") or "") for record in records]
    if not identities or any(not identity for identity in identities):
        return ""
    return payload_sha256(sorted(identities))


def snapshot_digest_material(snapshot: CTDatasetSnapshot) -> dict[str, Any]:
    return snapshot.model_dump(
        mode="json",
        exclude={"snapshot_digest"},
        exclude_none=True,
    )


def resolve_data_path(value: str, *, image_path: str | None = None) -> Path:
    raw_value = image_path or value
    normalized = raw_value.replace("\\", "/")
    if normalized.startswith("file://"):
        normalized = normalized[len("file://") :]
    mount_root = data_mount_root()
    host_root = str(host_data_root()).replace("\\", "/").rstrip("/")
    if normalized.lower().startswith(mount_root.lower()):
        return Path(f"{host_root}{normalized[len(mount_root):]}")
    if normalized.lower().startswith(host_root.lower()):
        return Path(normalized)
    resolved = resolve_image_path(
        value,
        image_path=image_path,
        host_data_root=str(host_data_root()),
        data_mount_root=data_mount_root(),
    )
    if resolved is None:
        raise FileNotFoundError(f"data_path_unresolved:{value}")
    return resolved


def load_split_records(
    source_index_path: Path,
    split: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    index = read_json(source_index_path)
    records: list[dict[str, Any]] = []
    for shard in index.get("shards", []):
        if not isinstance(shard, dict) or str(shard.get("split")) != split:
            continue
        shard_path = resolve_data_path(str(shard.get("path") or ""))
        records.extend(read_jsonl(shard_path))
    return index, records


def create_ct_snapshot(
    source_shard_index_uri: str | Path,
    *,
    lifecycle_run_id: str,
    profile_id: str,
    profile_version: int,
    profile_digest: str,
    split: str = "test",
) -> CTDatasetSnapshot:
    if split not in {"validation", "test"}:
        raise ValueError("ct_split_must_be_validation_or_test")
    source_index_path = runtime_path(source_shard_index_uri)
    source_index, source_records = load_split_records(source_index_path, split)
    if not source_records:
        raise ValueError("ct_snapshot_source_empty")
    dataset_version = str(source_index.get("dataset_version") or "")
    if not dataset_version:
        first = source_records[0]
        dataset_version = str(first.get("dataset_version") or "unknown")
    source_records_digest = records_sha256(source_records)
    snapshot_id = f"ct-{safe_name(dataset_version)}-{split}-{source_records_digest[:12]}"
    snapshot_root = host_ct_root() / "snapshots" / snapshot_id
    snapshot_path = snapshot_root / "snapshot.json"
    if snapshot_path.exists():
        existing = CTDatasetSnapshot.model_validate(read_json(snapshot_path))
        existing_manifest_path = ct_runtime_path(existing.manifest_uri)
        existing_records = (
            read_jsonl(existing_manifest_path) if existing_manifest_path.exists() else []
        )
        existing_source_digest = (
            existing.source_records_sha256
            or source_records_sha256_from_snapshot(existing_records)
        )
        snapshot_integrity_valid = (
            existing.snapshot_id == snapshot_id
            and existing.dataset_version == dataset_version
            and existing.split == split
            and existing.record_count == len(source_records)
            and existing_source_digest == source_records_digest
            and bool(existing_records)
            and len(existing_records) == existing.record_count
            and records_sha256(existing_records) == existing.records_sha256
            and file_sha256(existing_manifest_path) == existing.manifest_sha256
            and payload_sha256(snapshot_digest_material(existing))
            == existing.snapshot_digest
        )
        if not snapshot_integrity_valid:
            raise ValueError("ct_snapshot_identity_collision")
        atomic_write_json(
            host_ct_root() / "snapshots" / "latest.json",
            existing.model_dump(mode="json"),
        )
        return existing

    object_root = snapshot_root / "objects"
    object_root.mkdir(parents=True, exist_ok=True)
    snapshot_records: list[dict[str, Any]] = []
    byte_count = 0
    blockers: list[str] = []
    for record in sorted(source_records, key=record_id):
        identity = record_id(record)
        source_image = resolve_data_path(
            str(record.get("image_uri") or ""),
            image_path=str(record.get("image_path") or ""),
        )
        if not source_image.exists():
            blockers.append(f"ct_source_image_missing:{identity}")
            continue
        expected_content = str(record.get("content_sha256") or "")
        observed_content = file_sha256(source_image)
        if expected_content and expected_content != observed_content:
            blockers.append(f"ct_source_content_digest_mismatch:{identity}")
            continue
        suffix = source_image.suffix.lower() or ".bin"
        target = object_root / f"{identity}{suffix}"
        if not target.exists():
            shutil.copy2(source_image, target)
        copied_digest = file_sha256(target)
        if copied_digest != observed_content:
            blockers.append(f"ct_copy_content_digest_mismatch:{identity}")
            continue
        byte_count += target.stat().st_size
        clean = {key: value for key, value in record.items() if not key.startswith("_")}
        container_path = ct_container_uri(target)
        clean.update(
            {
                "ct_record_id": identity,
                "image_uri": f"file://{container_path}",
                "image_path": container_path,
                "content_sha256": observed_content,
                "split": split,
            }
        )
        snapshot_records.append(clean)

    if blockers:
        raise ValueError(",".join(blockers[:10]))
    if len(snapshot_records) != len(source_records):
        raise ValueError("ct_snapshot_record_count_mismatch")
    manifest_path = snapshot_root / "holdout_manifest.jsonl"
    atomic_write_jsonl(manifest_path, snapshot_records)
    manifest_sha256 = file_sha256(manifest_path)
    source_index_sha256 = file_sha256(source_index_path)
    host_root_normalized = str(host_data_root().resolve()).lower()
    ct_root_normalized = str(host_ct_root().resolve()).lower()
    training_mount_isolated = not ct_root_normalized.startswith(host_root_normalized)
    blockers = [] if training_mount_isolated else ["ct_root_inside_training_data_root"]
    created_at = utc_now()
    payload: dict[str, Any] = {
        "schema_version": CT_SNAPSHOT_SCHEMA,
        "snapshot_id": snapshot_id,
        "lifecycle_run_id": lifecycle_run_id,
        "profile_id": profile_id,
        "profile_version": profile_version,
        "profile_digest": profile_digest,
        "dataset_version": dataset_version,
        "split": split,
        "record_count": len(snapshot_records),
        "byte_count": byte_count,
        "records_sha256": records_sha256(snapshot_records),
        "source_records_sha256": source_records_digest,
        "source_index_uri": str(source_index_path),
        "source_index_sha256": source_index_sha256,
        "source_identity_sha256": str(source_index.get("identity_sha256") or ""),
        "manifest_uri": canonical_ct_uri(manifest_path),
        "manifest_sha256": manifest_sha256,
        "snapshot_uri": canonical_ct_uri(snapshot_path),
        "snapshot_digest": "",
        "isolation_root": canonical_ct_uri(snapshot_root),
        "immutable": True,
        "training_mount_isolated": training_mount_isolated,
        "status": "pass" if not blockers else "blocked",
        "blockers": blockers,
        "created_at": created_at,
    }
    payload["snapshot_digest"] = payload_sha256(
        {key: value for key, value in payload.items() if key != "snapshot_digest"}
    )
    snapshot = CTDatasetSnapshot.model_validate(payload)
    atomic_write_json(snapshot_path, snapshot.model_dump(mode="json"))
    atomic_write_json(
        host_ct_root() / "snapshots" / "latest.json",
        snapshot.model_dump(mode="json"),
    )
    immutable_file(manifest_path)
    immutable_file(snapshot_path)
    for target in object_root.iterdir():
        if target.is_file():
            immutable_file(target)
    return snapshot


def load_ct_snapshot(path: str | Path | None = None) -> CTDatasetSnapshot | None:
    target = ct_runtime_path(path or host_ct_root() / "snapshots" / "latest.json")
    if not target.exists():
        return None
    try:
        return CTDatasetSnapshot.model_validate(read_json(target))
    except (json.JSONDecodeError, ValueError):
        return None


def load_ct_evaluation(path: str | Path | None = None) -> CTEvaluation | None:
    target = ct_runtime_path(path or host_ct_root() / "evaluations" / "latest.json")
    if not target.exists():
        return None
    try:
        return CTEvaluation.model_validate(read_json(target))
    except (json.JSONDecodeError, ValueError):
        return None


def evaluate_ct_snapshot(
    snapshot_uri: str | Path,
    *,
    fold_manifest_uri: str | Path,
    training_job_manifest_uri: str | Path,
    candidate_summary_uri: str | Path,
    model_artifact_uri: str | Path,
    metric_thresholds: dict[str, float] | None = None,
    require_cuda: bool = True,
    batch_size: int = 64,
    num_workers: int = 2,
) -> CTEvaluation:
    snapshot_path = ct_runtime_path(snapshot_uri)
    snapshot = CTDatasetSnapshot.model_validate(read_json(snapshot_path))
    manifest_path = ct_runtime_path(snapshot.manifest_uri)
    fold_path = runtime_path(fold_manifest_uri)
    training_job_path = runtime_path(training_job_manifest_uri)
    candidate_path = runtime_path(candidate_summary_uri)
    model_path = runtime_path(model_artifact_uri)
    checks: dict[str, State] = {}
    blockers: list[str] = []

    snapshot_material = snapshot_digest_material(snapshot)
    checks["snapshot_digest"] = state(
        snapshot.snapshot_digest == payload_sha256(snapshot_material)
    )
    observed_manifest_sha256 = file_sha256(manifest_path) if manifest_path.exists() else ""
    checks["manifest_digest"] = state(
        bool(observed_manifest_sha256)
        and observed_manifest_sha256 == snapshot.manifest_sha256
    )
    records = read_jsonl(manifest_path) if manifest_path.exists() else []
    observed_records_sha256 = records_sha256(records) if records else ""
    checks["records_digest"] = state(observed_records_sha256 == snapshot.records_sha256)
    checks["record_count"] = state(len(records) == snapshot.record_count and bool(records))
    checks["split_identity"] = state(
        bool(records) and all(str(record.get("split")) == snapshot.split for record in records)
    )
    content_valid = bool(records)
    for record in records:
        image_path = ct_runtime_path(str(record.get("image_path") or ""))
        expected = str(record.get("content_sha256") or "")
        if not image_path.exists() or not expected or file_sha256(image_path) != expected:
            content_valid = False
            break
    checks["image_content"] = state(content_valid)

    fold_payload = safe_read_json(fold_path)
    assignments = fold_payload.get("assignments")
    training_ids = {
        str(item.get("record_id"))
        for item in assignments
        if isinstance(item, dict) and item.get("record_id")
    } if isinstance(assignments, list) else set()
    ct_ids = {record_id(record) for record in records}
    overlap = sorted(training_ids.intersection(ct_ids))
    checks["fold_manifest"] = state(bool(fold_payload) and bool(training_ids))
    checks["dataset_identity"] = state(
        str(fold_payload.get("dataset_version") or "") == snapshot.dataset_version
    )
    checks["training_ct_overlap"] = state(not overlap)
    checks["ct_evidence_not_exposed"] = state(
        not training_evidence_exposed(snapshot, fold_payload)
    )

    training_job = safe_read_json(training_job_path)
    isolated = training_job_isolated(training_job, snapshot)
    checks["training_mount_isolation"] = state(isolated)
    checks["model_artifact"] = state(model_path.exists() and model_path.is_file())
    candidate = safe_read_json(candidate_path)
    checks["candidate_summary"] = state(bool(candidate))
    checks["candidate_dataset_identity"] = state(
        str(candidate.get("dataset_version") or "") == snapshot.dataset_version
    )

    metrics: dict[str, float] = {}
    model_sha256 = file_sha256(model_path) if model_path.exists() else None
    device: str | None = None
    inference_blocker: str | None = None
    if checks["model_artifact"] == "pass" and records:
        try:
            metrics, device = evaluate_model(
                records,
                candidate,
                model_path,
                require_cuda=require_cuda,
                batch_size=batch_size,
                num_workers=num_workers,
            )
        except Exception as exc:
            inference_blocker = f"ct_model_inference_failed:{exc}"
    checks["gpu_model_evaluation"] = state(bool(metrics) and not inference_blocker)

    thresholds = metric_thresholds or {}
    for name, threshold in sorted(thresholds.items()):
        observed = metrics.get(name)
        checks[f"metric_{name}"] = state(
            observed is not None and float(observed) >= float(threshold)
        )

    for check_id, status_value in checks.items():
        if status_value != "pass":
            blockers.append(f"ct_{check_id}_failed")
    if inference_blocker:
        blockers.append(inference_blocker)
    blockers = sorted(set(blockers))
    mutated = any(
        checks.get(check_id) != "pass"
        for check_id in ("snapshot_digest", "manifest_digest", "records_digest", "image_content")
    )
    evaluation_material = {
        "snapshot_digest": snapshot.snapshot_digest,
        "candidate_id": str(candidate.get("candidate_id") or model_path.stem),
        "model_sha256": model_sha256 or "",
        "checks": checks,
        "metrics": metrics,
        "thresholds": thresholds,
    }
    evaluation_id = f"ct-eval-{payload_sha256(evaluation_material)[:16]}"
    report_path = host_ct_root() / "evaluations" / evaluation_id / "ct_evaluation.json"
    evaluation = CTEvaluation(
        evaluation_id=evaluation_id,
        lifecycle_run_id=str(fold_payload.get("experiment_id") or snapshot.lifecycle_run_id),
        snapshot_id=snapshot.snapshot_id,
        candidate_id=str(candidate.get("candidate_id") or model_path.stem),
        dataset_version=snapshot.dataset_version,
        status="pass" if not blockers else "blocked",
        decision="pass" if not blockers else "block",
        evaluated_at=utc_now(),
        snapshot_digest=snapshot.snapshot_digest,
        expected_manifest_sha256=snapshot.manifest_sha256,
        observed_manifest_sha256=observed_manifest_sha256,
        expected_records_sha256=snapshot.records_sha256,
        observed_records_sha256=observed_records_sha256,
        ct_record_count=len(records),
        training_record_count=len(training_ids),
        overlap_count=len(overlap),
        mutated=mutated,
        training_mount_isolated=isolated,
        model_artifact_uri=str(model_path) if model_path.exists() else None,
        model_sha256=model_sha256,
        device=device,
        metrics=metrics,
        metric_thresholds=thresholds,
        checks=checks,
        blockers=blockers,
        snapshot_uri=snapshot.snapshot_uri,
        fold_manifest_uri=str(fold_path) if fold_path.exists() else None,
        training_job_manifest_uri=(
            str(training_job_path) if training_job_path.exists() else None
        ),
        report_uri=canonical_ct_uri(report_path),
    )
    atomic_write_json(report_path, evaluation.model_dump(mode="json"))
    atomic_write_json(
        host_ct_root() / "evaluations" / "latest.json",
        evaluation.model_dump(mode="json"),
    )
    return evaluation


def evaluate_model(
    records: list[dict[str, Any]],
    candidate: dict[str, Any],
    model_path: Path,
    *,
    require_cuda: bool,
    batch_size: int,
    num_workers: int,
) -> tuple[dict[str, float], str]:
    import torch
    from torch.utils.data import DataLoader

    from evm.core.torch_efficientnet import (
        EfficientNetCandidateConfig,
        VisaImageDataset,
        build_model,
        collect_inference_outputs,
        metrics_from_outputs,
    )

    if require_cuda and not torch.cuda.is_available():
        raise RuntimeError("cuda_required")
    checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("state_dict"), dict):
        raise ValueError("ct_model_checkpoint_invalid")
    architecture = str(checkpoint.get("architecture") or candidate.get("architecture") or "")
    input_size = int(
        checkpoint.get("input_size")
        or object_value(candidate, "conditions").get("input_size")
        or 224
    )
    model_config = EfficientNetCandidateConfig(
        candidate_id=str(candidate.get("candidate_id") or model_path.stem),
        architecture=architecture,
        backbone=architecture,
        input_size=input_size,
        pretrained=False,
        freeze_backbone=False,
        optimizer="adamw",
        learning_rate=0.0,
        batch_size=batch_size,
        mixed_precision=False,
        resource_profile="ct-evaluator",
        epochs=1,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_model(model_config, 2)
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    model.to(device)
    model.eval()
    prepared = []
    for record in records:
        item = dict(record)
        label = str(item.get("label_type") or item.get("label") or "").lower()
        item["_normalized_label"] = "normal" if label == "normal" else "anomaly"
        prepared.append(item)
    dataset = VisaImageDataset(prepared, input_size)
    loader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=device.type == "cuda",
    )
    threshold = float(
        checkpoint.get("decision_threshold")
        or candidate.get("decision_threshold")
        or 0.5
    )
    with temporary_environment(
        EVM_HOST_DATA_ROOT=str(host_ct_root()),
        EVM_DATA_MOUNT_ROOT=ct_mount_root(),
    ):
        raw = metrics_from_outputs(
            collect_inference_outputs(model, loader, device),
            threshold,
        )
    metrics = {
        name: float(raw[name])
        for name in ("accuracy", "precision", "recall", "f1", "auroc", "latency_p95_ms")
    }
    if device.type == "cuda":
        metrics["gpu_memory_peak_mb"] = float(
            torch.cuda.max_memory_allocated(device) / 1_048_576
        )
    return metrics, str(device)


def training_evidence_exposed(
    snapshot: CTDatasetSnapshot,
    fold_payload: dict[str, Any],
) -> bool:
    forbidden_keys = {
        "holdout_sha256",
        "immutable_holdout_sha256",
        "selection_holdout_sha256",
        "ct_snapshot_digest",
    }
    if forbidden_keys.intersection(fold_payload):
        return True
    serialized = json.dumps(fold_payload, sort_keys=True)
    return any(
        secret and secret in serialized
        for secret in (
            snapshot.snapshot_digest,
            snapshot.records_sha256,
            snapshot.manifest_sha256,
            snapshot.snapshot_id,
        )
    ) or fold_payload.get("ct_evidence_exposed") is not False


def training_job_isolated(
    training_job: dict[str, Any],
    snapshot: CTDatasetSnapshot,
) -> bool:
    if not training_job:
        return False
    serialized = json.dumps(training_job, sort_keys=True).lower()
    forbidden = (
        ct_mount_root().lower(),
        str(host_ct_root()).replace("\\", "/").lower(),
        snapshot.snapshot_digest.lower(),
        snapshot.records_sha256.lower(),
        snapshot.manifest_sha256.lower(),
        snapshot.snapshot_id.lower(),
        "evm_host_ct_root",
        "evm_ct_mount_root",
    )
    if any(value and value in serialized for value in forbidden):
        return False
    return (
        '"evm_training_data_scope", "value": "development-only"' in serialized
        and '"mountpath": "/mnt/evm-data/data", "name": "large-data", "readonly": true'
        in serialized
    )


def materialize_training_data_view(
    source_shard_index_uri: str | Path,
    *,
    lifecycle_run_id: str,
    holdout_split: str = "test",
) -> TrainingDataView:
    source_index_path = runtime_path(source_shard_index_uri)
    source_index = read_json(source_index_path)
    view_root = host_data_root() / "artifacts" / "w8" / "training_views" / lifecycle_run_id
    relative_index = source_index_path.resolve().relative_to(host_data_root().resolve())
    target_index_path = view_root / relative_index
    target_index_path.parent.mkdir(parents=True, exist_ok=True)
    development_shards: list[dict[str, Any]] = []
    record_count = 0
    linked_images = 0
    split_counts: dict[str, int] = {}
    for shard in source_index.get("shards", []):
        if not isinstance(shard, dict):
            continue
        split = str(shard.get("split") or "")
        if split == holdout_split:
            continue
        if split not in {"train", "validation"}:
            continue
        source_shard = resolve_data_path(str(shard.get("path") or ""))
        relative_shard = source_shard.resolve().relative_to(host_data_root().resolve())
        target_shard = view_root / relative_shard
        target_shard.parent.mkdir(parents=True, exist_ok=True)
        if not target_shard.exists():
            shutil.copy2(source_shard, target_shard)
        records = read_jsonl(source_shard)
        for record in records:
            linked_images += link_training_asset(record, "image_uri", "image_path", view_root)
            if record.get("mask_uri"):
                linked_images += link_training_asset(record, "mask_uri", None, view_root)
        copied = dict(shard)
        copied["record_count"] = len(records)
        development_shards.append(copied)
        record_count += len(records)
        split_counts[split] = split_counts.get(split, 0) + len(records)

    if not development_shards or not record_count:
        raise ValueError("training_data_view_empty")
    view_index = {
        key: value
        for key, value in source_index.items()
        if key not in {"shards", "identity_sha256", "record_count", "split_counts", "shard_count"}
    }
    view_index.update(
        {
            "schema_version": "evm.training_dataset_shards.v1",
            "record_count": record_count,
            "shard_count": len(development_shards),
            "split_counts": split_counts,
            "shards": development_shards,
            "training_data_scope": "development-only",
            "excluded_split": holdout_split,
            "ct_evidence_exposed": False,
            "source_index_sha256": file_sha256(source_index_path),
        }
    )
    view_index["identity_sha256"] = payload_sha256(view_index)
    atomic_write_json(target_index_path, view_index)
    manifest_path = view_root / "training_data_view.json"
    manifest = {
        "schema_version": TRAINING_VIEW_SCHEMA,
        "lifecycle_run_id": lifecycle_run_id,
        "root": str(view_root),
        "shard_index_uri": str(target_index_path),
        "record_count": record_count,
        "linked_image_count": linked_images,
        "identity_sha256": view_index["identity_sha256"],
        "training_data_scope": "development-only",
        "excluded_split": holdout_split,
        "ct_evidence_exposed": False,
        "created_at": utc_now(),
    }
    atomic_write_json(manifest_path, manifest)
    return TrainingDataView(
        root=view_root,
        shard_index_path=target_index_path,
        manifest_path=manifest_path,
        record_count=record_count,
        linked_image_count=linked_images,
        identity_sha256=str(view_index["identity_sha256"]),
    )


def link_training_asset(
    record: dict[str, Any],
    uri_key: str,
    path_key: str | None,
    view_root: Path,
) -> int:
    value = str(record.get(uri_key) or "")
    image_path = str(record.get(path_key) or "") if path_key else None
    source = resolve_data_path(value, image_path=image_path)
    if not source.exists():
        raise FileNotFoundError(f"training_asset_missing:{source}")
    relative = source.resolve().relative_to(host_data_root().resolve())
    target = view_root / relative
    if target.exists():
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return 1


def docker_desktop_path(path: Path) -> str:
    normalized = str(path.resolve()).replace("\\", "/")
    if normalized.startswith("/"):
        return normalized
    if len(normalized) < 3 or normalized[1:3] != ":/":
        raise ValueError(f"absolute_host_path_required:{normalized}")
    drive = normalized[0].lower()
    return f"/run/desktop/mnt/host/{drive}/{normalized[3:]}"


def safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def object_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


def state(passed: bool) -> State:
    return "pass" if passed else "blocked"


def safe_name(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower())
    return "-".join(part for part in cleaned.split("-") if part)[:64] or "dataset"


@contextmanager
def temporary_environment(**values: str) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def parse_thresholds(values: list[str]) -> dict[str, float]:
    thresholds: dict[str, float] = {}
    for value in values:
        name, separator, threshold = value.partition("=")
        if not separator or not name.strip():
            raise ValueError(f"invalid_threshold:{value}")
        thresholds[name.strip()] = float(threshold)
    return thresholds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and evaluate isolated CT datasets.")
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--source-shard-index", required=True)
    snapshot.add_argument("--lifecycle-run-id", required=True)
    snapshot.add_argument("--profile-id", required=True)
    snapshot.add_argument("--profile-version", required=True, type=int)
    snapshot.add_argument("--profile-digest", required=True)
    snapshot.add_argument("--split", default="test", choices=("validation", "test"))
    view = commands.add_parser("training-view")
    view.add_argument("--source-shard-index", required=True)
    view.add_argument("--lifecycle-run-id", required=True)
    view.add_argument("--holdout-split", default="test")
    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument("--snapshot", required=True)
    evaluate_parser.add_argument("--fold-manifest", required=True)
    evaluate_parser.add_argument("--training-job-manifest", required=True)
    evaluate_parser.add_argument("--candidate-summary", required=True)
    evaluate_parser.add_argument("--model-artifact", required=True)
    evaluate_parser.add_argument("--threshold", action="append", default=[])
    evaluate_parser.add_argument("--allow-cpu", action="store_true")
    evaluate_parser.add_argument("--batch-size", type=int, default=64)
    evaluate_parser.add_argument("--num-workers", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "snapshot":
        result: Any = create_ct_snapshot(
            args.source_shard_index,
            lifecycle_run_id=args.lifecycle_run_id,
            profile_id=args.profile_id,
            profile_version=args.profile_version,
            profile_digest=args.profile_digest,
            split=args.split,
        )
        payload = result.model_dump(mode="json")
    elif args.command == "training-view":
        result = materialize_training_data_view(
            args.source_shard_index,
            lifecycle_run_id=args.lifecycle_run_id,
            holdout_split=args.holdout_split,
        )
        payload = {
            "root": str(result.root),
            "shard_index_path": str(result.shard_index_path),
            "manifest_path": str(result.manifest_path),
            "record_count": result.record_count,
            "linked_image_count": result.linked_image_count,
            "identity_sha256": result.identity_sha256,
        }
    else:
        result = evaluate_ct_snapshot(
            args.snapshot,
            fold_manifest_uri=args.fold_manifest,
            training_job_manifest_uri=args.training_job_manifest,
            candidate_summary_uri=args.candidate_summary,
            model_artifact_uri=args.model_artifact,
            metric_thresholds=parse_thresholds(args.threshold),
            require_cuda=not args.allow_cpu,
            batch_size=args.batch_size,
            num_workers=args.num_workers,
        )
        payload = result.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
