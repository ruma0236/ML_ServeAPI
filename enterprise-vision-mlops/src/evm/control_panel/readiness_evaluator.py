from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from evm.control_panel.schemas import (
    ArtifactReadinessEvaluation,
    DataPipelineReadiness,
    ExperimentPipelineReadiness,
    OrgContext,
    ReadinessEvidenceCheck,
    State,
)
from evm.core.http import request_json
from evm.core.image_feature_model import resolve_image_path


MlflowRunLoader = Callable[[str, str], tuple[int, dict[str, Any] | list[Any] | str]]
REQUIRED_CONTRACT_FIELDS = {
    "dataset_id",
    "dataset_version",
    "sample_id",
    "image_uri",
    "split",
    "label",
    "content_sha256",
}
REQUIRED_METRICS = {"accuracy", "precision", "recall", "f1", "auroc"}


@dataclass(frozen=True)
class ReadinessInputs:
    contract_path: Path
    dataset_metadata_path: Path
    quality_report_path: Path
    source_shard_index_path: Path
    split_manifest_path: Path
    lineage_path: Path
    candidate_summary_path: Path
    model_card_path: Path
    registry_path: Path
    real_test_validation_path: Path
    kubernetes_evidence_path: Path | None
    mlflow_tracking_uri: str
    candidate_id: str
    dataset_version: str
    expected_record_count: int
    expected_source_digest: str
    metric_thresholds: dict[str, float]
    report_uri: str | None = None


@dataclass(frozen=True)
class ReadinessResult:
    evaluation: ArtifactReadinessEvaluation
    data_pipeline: DataPipelineReadiness
    experiment_pipeline: ExperimentPipelineReadiness


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@lru_cache(maxsize=256)
def _file_sha256_cached(path_value: str, size: int, mtime_ns: int) -> str:
    digest = hashlib.sha256()
    with Path(path_value).open("rb") as fp:
        while chunk := fp.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    stat = path.stat()
    return _file_sha256_cached(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def payload_sha256(payload: Any) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def runtime_path(value: str | Path) -> Path:
    path = Path(value)
    if path.exists():
        return path

    normalized = str(value).replace("\\", "/")
    host_root = os.getenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    ).replace("\\", "/").rstrip("/")
    mount_root = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data").replace("\\", "/").rstrip("/")
    if normalized.lower().startswith(mount_root.lower()):
        host_path = Path(f"{host_root}{normalized[len(mount_root):]}")
        if host_path.exists():
            return host_path

    mapped = resolve_image_path(
        str(value),
        host_data_root=host_root,
        data_mount_root=mount_root,
    )
    return mapped or path


def canonical_evidence_uri(path: Path) -> str:
    value = str(path).replace("\\", "/")
    mount_root = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data").replace("\\", "/").rstrip("/")
    host_root = os.getenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    ).replace("\\", "/").rstrip("/")
    if value.lower().startswith(mount_root.lower()):
        return f"{host_root}{value[len(mount_root):]}"
    if value.lower().startswith("/app/artifacts"):
        return f"{host_root}/artifacts{value[len('/app/artifacts'):]}"
    if value.lower().startswith("/app/domain_packs"):
        return value[len("/app/") :]
    return value


def read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, "evidence_missing"
    try:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("utf-16")
        payload = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}, "evidence_malformed"
    if not isinstance(payload, dict):
        return {}, "evidence_not_object"
    return payload, None


def evidence_check(
    *,
    check_id: str,
    category: str,
    path: Path | None = None,
    payload: Any = None,
    observed: dict[str, str | int | float | bool | None] | None = None,
    blockers: list[str] | None = None,
    required: bool = True,
) -> ReadinessEvidenceCheck:
    blocker_list = sorted(set(blockers or []))
    digest = None
    if path and path.exists() and path.is_file():
        digest = file_sha256(path)
    elif payload is not None:
        digest = payload_sha256(payload)
    return ReadinessEvidenceCheck(
        check_id=check_id,
        category=category,  # type: ignore[arg-type]
        status="blocked" if blocker_list else "pass",
        required=required,
        evidence_uri=canonical_evidence_uri(path) if path else None,
        evidence_digest=digest,
        observed=observed or {},
        blockers=blocker_list,
    )


def load_mlflow_run(tracking_uri: str, run_id: str) -> tuple[int, dict[str, Any] | list[Any] | str]:
    return request_json(
        "GET",
        f"{tracking_uri.rstrip('/')}/api/2.0/mlflow/runs/get?run_id={run_id}",
        timeout=3,
    )


def _missing_or_malformed(code_prefix: str, error: str | None) -> list[str]:
    if error == "evidence_missing":
        return [f"{code_prefix}_missing"]
    if error:
        return [f"{code_prefix}_malformed"]
    return []


def check_contract(path: Path) -> ReadinessEvidenceCheck:
    blockers: list[str] = []
    payload: dict[str, Any] = {}
    loaded = False
    if not path.exists():
        blockers.append("data_contract_missing")
    else:
        try:
            with path.open("rb") as fp:
                payload = tomllib.load(fp)
            loaded = True
        except (OSError, tomllib.TOMLDecodeError):
            blockers.append("data_contract_malformed")
    contract = payload.get("contract", {}) if isinstance(payload.get("contract"), dict) else {}
    fields = payload.get("fields", []) if isinstance(payload.get("fields"), list) else []
    field_names = {
        str(item.get("name"))
        for item in fields
        if isinstance(item, dict) and item.get("required") is True
    }
    missing_fields = sorted(REQUIRED_CONTRACT_FIELDS - field_names)
    if loaded and (not contract.get("id") or not contract.get("version")):
        blockers.append("data_contract_identity_missing")
    if loaded and missing_fields:
        blockers.append("data_contract_required_fields_missing")
    return evidence_check(
        check_id="data_contract",
        category="data",
        path=path,
        observed={
            "contract_id": str(contract.get("id", "")),
            "contract_version": str(contract.get("version", "")),
            "required_field_count": len(field_names),
            "missing_required_fields": ",".join(missing_fields),
        },
        blockers=blockers,
    )


def check_dataset_metadata(path: Path, dataset_version: str, record_count: int) -> ReadinessEvidenceCheck:
    payload, error = read_json(path)
    blockers = _missing_or_malformed("dataset_metadata", error)
    loaded = error is None
    observed_version = str(payload.get("dataset_version", ""))
    observed_count = int(payload.get("record_count") or 0)
    if loaded and observed_version != dataset_version:
        blockers.append("dataset_metadata_version_mismatch")
    if loaded and observed_count != record_count:
        blockers.append("dataset_metadata_record_count_mismatch")
    return evidence_check(
        check_id="dataset_metadata",
        category="data",
        path=path,
        observed={"dataset_version": observed_version, "record_count": observed_count},
        blockers=blockers,
    )


def check_source_shard(
    path: Path,
    expected_digest: str,
    expected_record_count: int,
) -> ReadinessEvidenceCheck:
    payload, error = read_json(path)
    blockers = _missing_or_malformed("source_shard_index", error)
    loaded = error is None
    actual_digest = file_sha256(path) if path.exists() and path.is_file() else ""
    observed_count = int(payload.get("record_count") or 0)
    if loaded and expected_digest and actual_digest.lower() != expected_digest.lower():
        blockers.append("source_shard_digest_mismatch")
    if loaded and observed_count != expected_record_count:
        blockers.append("source_shard_record_count_mismatch")
    return evidence_check(
        check_id="source_shard_index",
        category="data",
        path=path,
        observed={
            "schema_version": str(payload.get("schema_version", "")),
            "record_count": observed_count,
            "actual_sha256": actual_digest,
            "expected_sha256": expected_digest,
        },
        blockers=blockers,
    )


def check_split_manifest(
    path: Path,
    dataset_version: str,
    expected_record_count: int,
    expected_source_digest: str,
) -> ReadinessEvidenceCheck:
    payload, error = read_json(path)
    blockers = _missing_or_malformed("split_manifest", error)
    loaded = error is None
    observed_version = str(payload.get("dataset_version", ""))
    observed_count = int(payload.get("record_count") or 0)
    split_counts = payload.get("split_counts", {}) if isinstance(payload.get("split_counts"), dict) else {}
    split_total = sum(int(value) for value in split_counts.values() if isinstance(value, int))
    source_digest = str(payload.get("source_shard_index_sha256") or "")
    if loaded and observed_version != dataset_version:
        blockers.append("split_dataset_version_mismatch")
    if loaded and observed_count != expected_record_count:
        blockers.append("split_record_count_mismatch")
    if loaded and split_total != observed_count:
        blockers.append("split_count_sum_mismatch")
    if loaded and not source_digest:
        blockers.append("split_source_digest_missing")
    elif loaded and expected_source_digest and source_digest.lower() != expected_source_digest.lower():
        blockers.append("split_source_digest_mismatch")
    return evidence_check(
        check_id="split_manifest",
        category="data",
        path=path,
        observed={
            "dataset_version": observed_version,
            "record_count": observed_count,
            "split_total": split_total,
            "source_shard_index_sha256": source_digest,
        },
        blockers=blockers,
    )


def check_quality_gate(path: Path, dataset_version: str, record_count: int) -> ReadinessEvidenceCheck:
    payload, error = read_json(path)
    blockers = _missing_or_malformed("quality_report", error)
    loaded = error is None
    gate = payload.get("gate_decision", {}) if isinstance(payload.get("gate_decision"), dict) else {}
    observed_status = str(payload.get("status") or gate.get("status") or "")
    observed_version = str(payload.get("dataset_version", ""))
    observed_count = int(payload.get("record_count") or 0)
    if loaded and observed_status != "pass":
        blockers.append("quality_gate_not_passing")
    if loaded and observed_count <= 0:
        blockers.append("quality_evidence_empty")
    if loaded and observed_count != record_count:
        blockers.append("quality_record_count_mismatch")
    if loaded and observed_version != dataset_version:
        blockers.append("quality_dataset_version_mismatch")
    return evidence_check(
        check_id="quality_gate",
        category="data",
        path=path,
        observed={
            "status": observed_status,
            "dataset_version": observed_version,
            "record_count": observed_count,
            "blocking_count": int(gate.get("blocking_count") or 0),
        },
        blockers=blockers,
    )


def check_lineage(path: Path, candidate_id: str, dataset_version: str) -> ReadinessEvidenceCheck:
    payload, error = read_json(path)
    blockers = _missing_or_malformed("lineage", error)
    loaded = error is None
    observed_dataset = str(payload.get("dataset_version", ""))
    observed_model = str(payload.get("candidate_id") or payload.get("model_name") or "")
    if loaded and observed_dataset != dataset_version:
        blockers.append("lineage_dataset_version_mismatch")
    if loaded and observed_model != candidate_id:
        blockers.append("lineage_candidate_mismatch")
    if loaded and not (payload.get("source_shard_index_sha256") or payload.get("validated_parquet_uri")):
        blockers.append("lineage_source_identity_missing")
    return evidence_check(
        check_id="lineage",
        category="data",
        path=path,
        observed={"dataset_version": observed_dataset, "model_identity": observed_model},
        blockers=blockers,
    )


def check_candidate_summary(
    path: Path,
    candidate_id: str,
    dataset_version: str,
    thresholds: dict[str, float],
) -> tuple[ReadinessEvidenceCheck, dict[str, Any]]:
    payload, error = read_json(path)
    blockers = _missing_or_malformed("evaluation_report", error)
    loaded = error is None
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    if loaded and payload.get("candidate_id") != candidate_id:
        blockers.append("evaluation_candidate_mismatch")
    if loaded and payload.get("dataset_version") != dataset_version:
        blockers.append("evaluation_dataset_version_mismatch")
    if loaded and payload.get("status") != "pass":
        blockers.append("evaluation_status_not_passing")
    missing_metrics = sorted(REQUIRED_METRICS - set(metrics))
    if loaded and missing_metrics:
        blockers.append("evaluation_metrics_missing")
    if loaded:
        for name, threshold in thresholds.items():
            value = metrics.get(name)
            if not isinstance(value, int | float) or float(value) < threshold:
                blockers.append(f"evaluation_{name}_below_threshold")
    if loaded and payload.get("promotion_blockers"):
        blockers.append("evaluation_promotion_blockers_present")
    check = evidence_check(
        check_id="evaluation_report",
        category="model",
        path=path,
        observed={
            "candidate_id": str(payload.get("candidate_id", "")),
            "dataset_version": str(payload.get("dataset_version", "")),
            "status": str(payload.get("status", "")),
            "metric_count": len(metrics),
        },
        blockers=blockers,
    )
    return check, payload


def check_mlflow_run(
    tracking_uri: str,
    candidate: dict[str, Any],
    candidate_id: str,
    dataset_version: str,
    loader: MlflowRunLoader,
) -> ReadinessEvidenceCheck:
    run_id = str(candidate.get("mlflow_run_id") or "")
    blockers: list[str] = []
    payload: dict[str, Any] = {}
    status_code = 0
    if not run_id:
        blockers.append("mlflow_run_id_missing")
    else:
        status_code, response = loader(tracking_uri, run_id)
        payload = response if isinstance(response, dict) else {}
        if status_code != 200 or not payload:
            blockers.append("mlflow_run_unavailable")
    run = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
    info = run.get("info", {}) if isinstance(run.get("info"), dict) else {}
    data = run.get("data", {}) if isinstance(run.get("data"), dict) else {}
    params = {
        str(item.get("key")): str(item.get("value"))
        for item in data.get("params", [])
        if isinstance(item, dict) and item.get("key")
    }
    metrics = {
        str(item.get("key")): item.get("value")
        for item in data.get("metrics", [])
        if isinstance(item, dict) and item.get("key")
    }
    if payload and str(info.get("status", "")).upper() != "FINISHED":
        blockers.append("mlflow_run_not_finished")
    if payload and str(info.get("run_id") or info.get("run_uuid") or "") != run_id:
        blockers.append("mlflow_run_id_mismatch")
    if payload and str(info.get("run_name", "")) != candidate_id:
        blockers.append("mlflow_candidate_mismatch")
    if payload and params.get("candidate_id") != candidate_id:
        blockers.append("mlflow_candidate_param_mismatch")
    if payload and params.get("dataset_version") != dataset_version:
        blockers.append("mlflow_dataset_version_mismatch")
    if payload and not str(info.get("artifact_uri") or params.get("artifact_uri") or ""):
        blockers.append("mlflow_artifact_uri_missing")
    if payload and not REQUIRED_METRICS.issubset(metrics):
        blockers.append("mlflow_metrics_missing")
    return evidence_check(
        check_id="mlflow_run",
        category="model",
        payload=payload if payload else {"status_code": status_code, "run_id": run_id},
        observed={
            "run_id": run_id,
            "run_status": str(info.get("status", "")),
            "run_name": str(info.get("run_name", "")),
            "dataset_version": params.get("dataset_version", ""),
            "metric_count": len(metrics),
            "http_status": status_code,
        },
        blockers=blockers,
    )


def check_model_card(
    path: Path,
    candidate_id: str,
    dataset_version: str,
    run_id: str,
) -> ReadinessEvidenceCheck:
    blockers: list[str] = []
    content = ""
    if not path.exists():
        blockers.append("model_card_missing")
    else:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            blockers.append("model_card_malformed")
    if content and candidate_id not in content:
        blockers.append("model_card_candidate_mismatch")
    if content and dataset_version not in content:
        blockers.append("model_card_dataset_version_mismatch")
    if content and (not run_id or f"MLflow run id: `{run_id}`" not in content):
        blockers.append("model_card_mlflow_run_mismatch")
    return evidence_check(
        check_id="model_card",
        category="model",
        path=path,
        observed={
            "candidate_id": candidate_id if candidate_id in content else "",
            "dataset_version": dataset_version if dataset_version in content else "",
            "mlflow_run_id": run_id if run_id and f"`{run_id}`" in content else "",
        },
        blockers=blockers,
    )


def check_model_artifact(
    candidate: dict[str, Any],
    kubernetes_evidence: dict[str, Any],
) -> tuple[ReadinessEvidenceCheck, str]:
    raw_path = str(candidate.get("model_artifact") or "")
    path = runtime_path(raw_path) if raw_path else Path("__missing_model_artifact__")
    blockers: list[str] = []
    actual_digest = ""
    if not raw_path or not path.exists() or not path.is_file():
        blockers.append("model_artifact_missing")
    else:
        actual_digest = file_sha256(path)
        if path.stat().st_size <= 0:
            blockers.append("model_artifact_empty")
    expected_digest = str(
        kubernetes_evidence.get("trained_model_sha256")
        or kubernetes_evidence.get("source_model_sha256")
        or ""
    )
    if actual_digest and expected_digest and actual_digest.lower() != expected_digest.lower():
        blockers.append("model_artifact_digest_mismatch")
    return (
        evidence_check(
            check_id="model_artifact",
            category="model",
            path=path if raw_path else None,
            observed={
                "size_bytes": path.stat().st_size if path.exists() and path.is_file() else 0,
                "actual_sha256": actual_digest,
                "expected_sha256": expected_digest,
            },
            blockers=blockers,
        ),
        actual_digest,
    )


def check_real_test_validation(
    path: Path,
    candidate_id: str,
    dataset_version: str,
) -> ReadinessEvidenceCheck:
    payload, error = read_json(path)
    blockers = _missing_or_malformed("real_test_validation", error)
    loaded = error is None
    checked = payload.get("checked_candidates", []) if isinstance(payload.get("checked_candidates"), list) else []
    selected = next(
        (item for item in checked if isinstance(item, dict) and item.get("candidate_id") == candidate_id),
        {},
    )
    split = payload.get("split_manifest", {}) if isinstance(payload.get("split_manifest"), dict) else {}
    if loaded and payload.get("valid") is not True:
        blockers.append("real_test_validation_failed")
    if loaded and not selected:
        blockers.append("real_test_candidate_missing")
    if loaded and split.get("dataset_version") != dataset_version:
        blockers.append("real_test_dataset_version_mismatch")
    return evidence_check(
        check_id="real_test_validation",
        category="model",
        path=path,
        observed={
            "valid": bool(payload.get("valid")),
            "candidate_id": str(selected.get("candidate_id", "")),
            "dataset_version": str(split.get("dataset_version", "")),
        },
        blockers=blockers,
    )


def check_rollback_reference(
    path: Path,
    candidate_id: str,
    rollback_digest: str,
    current_model_digest: str,
) -> ReadinessEvidenceCheck:
    payload, error = read_json(path)
    blockers = _missing_or_malformed("rollback_reference", error)
    loaded = error is None
    source = payload.get("source_model", {}) if isinstance(payload.get("source_model"), dict) else {}
    observed_model = str(
        payload.get("candidate_id")
        or source.get("candidate_id")
        or payload.get("model_name")
        or source.get("model_name")
        or ""
    )
    observed_digest = str(payload.get("model_digest") or source.get("model_digest") or "")
    artifact_value = str(
        payload.get("model_artifact") or source.get("model_artifact") or ""
    )
    artifact_path = runtime_path(artifact_value) if artifact_value else None
    artifact_digest = (
        file_sha256(artifact_path)
        if artifact_path is not None and artifact_path.exists() and artifact_path.is_file()
        else ""
    )
    if loaded and observed_model != candidate_id:
        blockers.append("rollback_candidate_mismatch")
    if loaded and rollback_digest and observed_digest != rollback_digest:
        blockers.append("rollback_model_digest_mismatch")
    if loaded and not payload.get("version"):
        blockers.append("rollback_version_missing")
    if loaded and payload.get("status") != "approved":
        blockers.append("rollback_reference_not_approved")
    if loaded and payload.get("rollback_ready") is not True:
        blockers.append("rollback_reference_not_ready")
    if loaded and (artifact_path is None or not artifact_path.exists() or not artifact_path.is_file()):
        blockers.append("rollback_artifact_missing")
    elif loaded and observed_digest and artifact_digest != observed_digest:
        blockers.append("rollback_artifact_digest_mismatch")
    if loaded and rollback_digest and current_model_digest and rollback_digest == current_model_digest:
        blockers.append("rollback_reuses_current_model")
    return evidence_check(
        check_id="rollback_reference",
        category="model",
        path=path,
        observed={
            "model_identity": observed_model,
            "model_digest": observed_digest,
            "artifact_digest": artifact_digest,
            "current_model_digest": current_model_digest,
            "version": str(payload.get("version", "")),
        },
        blockers=blockers,
    )


def check_kubernetes_runtime(
    path: Path | None,
    candidate_id: str,
    dataset_version: str,
    run_id: str,
    model_digest: str,
) -> tuple[ReadinessEvidenceCheck, dict[str, Any]]:
    if path is None:
        return (
            evidence_check(
                check_id="kubernetes_runtime",
                category="runtime",
                blockers=["kubernetes_evidence_missing"],
            ),
            {},
        )
    payload, error = read_json(path)
    blockers = _missing_or_malformed("kubernetes_evidence", error)
    loaded = error is None
    if loaded and payload.get("status") != "pass":
        blockers.extend(str(item) for item in payload.get("blockers", []) if item)
        blockers.append("kubernetes_execution_not_passing")
    if loaded and payload.get("completion_claim_allowed") is not True:
        blockers.append("kubernetes_completion_claim_blocked")
    if loaded and payload.get("candidate_id") != candidate_id:
        blockers.append("kubernetes_candidate_mismatch")
    if loaded and payload.get("dataset_version") != dataset_version:
        blockers.append("kubernetes_dataset_version_mismatch")
    observed_run_id = payload.get("mlflow_run_id") or payload.get("source_mlflow_run_id")
    observed_model_digest = payload.get("trained_model_sha256") or payload.get(
        "source_model_sha256"
    )
    if loaded and observed_run_id != run_id:
        blockers.append("kubernetes_mlflow_run_mismatch")
    if loaded and model_digest and observed_model_digest != model_digest:
        blockers.append("kubernetes_model_digest_mismatch")
    return (
        evidence_check(
            check_id="kubernetes_runtime",
            category="runtime",
            path=path,
            observed={
                "status": str(payload.get("status", "")),
                "candidate_id": str(payload.get("candidate_id", "")),
                "dataset_version": str(payload.get("dataset_version", "")),
                "completion_claim_allowed": bool(payload.get("completion_claim_allowed")),
                "gpu_allocatable": str(payload.get("gpu_allocatable", "")),
                "training_image_digest": str(payload.get("training_image_digest", "")),
                "serving_image_digest": str(payload.get("serving_image_digest", "")),
            },
            blockers=blockers,
        ),
        payload,
    )


def category_status(checks: list[ReadinessEvidenceCheck], category: str) -> State:
    category_checks = [check for check in checks if check.category == category and check.required]
    return "pass" if category_checks and all(check.status == "pass" for check in category_checks) else "blocked"


def category_blockers(checks: list[ReadinessEvidenceCheck], category: str) -> list[str]:
    return sorted(
        {
            blocker
            for check in checks
            if check.category == category and check.required
            for blocker in check.blockers
        }
    )


def readiness_input_digest(checks: list[ReadinessEvidenceCheck]) -> str:
    stable_material = [
        {
            "check_id": check.check_id,
            "category": check.category,
            "status": check.status,
            "evidence_digest": check.evidence_digest,
            "blockers": check.blockers,
        }
        for check in checks
    ]
    return payload_sha256(stable_material)


def evaluate_artifact_readiness(
    inputs: ReadinessInputs,
    org_context: OrgContext | None,
    *,
    mlflow_loader: MlflowRunLoader = load_mlflow_run,
) -> ReadinessResult:
    contract_path = runtime_path(inputs.contract_path)
    dataset_metadata_path = runtime_path(inputs.dataset_metadata_path)
    quality_report_path = runtime_path(inputs.quality_report_path)
    source_shard_index_path = runtime_path(inputs.source_shard_index_path)
    split_manifest_path = runtime_path(inputs.split_manifest_path)
    lineage_path = runtime_path(inputs.lineage_path)
    candidate_summary_path = runtime_path(inputs.candidate_summary_path)
    model_card_path = runtime_path(inputs.model_card_path)
    registry_path = runtime_path(inputs.registry_path)
    real_test_validation_path = runtime_path(inputs.real_test_validation_path)
    kubernetes_evidence_path = (
        runtime_path(inputs.kubernetes_evidence_path) if inputs.kubernetes_evidence_path else None
    )

    candidate_check, candidate = check_candidate_summary(
        candidate_summary_path,
        inputs.candidate_id,
        inputs.dataset_version,
        inputs.metric_thresholds,
    )
    run_id = str(candidate.get("mlflow_run_id") or "")

    kubernetes_payload, _ = read_json(kubernetes_evidence_path) if kubernetes_evidence_path else ({}, None)
    model_artifact_check, model_digest = check_model_artifact(candidate, kubernetes_payload)
    kubernetes_check, _ = check_kubernetes_runtime(
        kubernetes_evidence_path,
        inputs.candidate_id,
        inputs.dataset_version,
        run_id,
        model_digest,
    )

    checks = [
        check_contract(contract_path),
        check_dataset_metadata(
            dataset_metadata_path,
            inputs.dataset_version,
            inputs.expected_record_count,
        ),
        check_source_shard(
            source_shard_index_path,
            inputs.expected_source_digest,
            inputs.expected_record_count,
        ),
        check_split_manifest(
            split_manifest_path,
            inputs.dataset_version,
            inputs.expected_record_count,
            inputs.expected_source_digest,
        ),
        check_quality_gate(
            quality_report_path,
            inputs.dataset_version,
            inputs.expected_record_count,
        ),
        check_lineage(lineage_path, inputs.candidate_id, inputs.dataset_version),
        candidate_check,
        check_mlflow_run(
            inputs.mlflow_tracking_uri,
            candidate,
            inputs.candidate_id,
            inputs.dataset_version,
            mlflow_loader,
        ),
        check_model_card(model_card_path, inputs.candidate_id, inputs.dataset_version, run_id),
        model_artifact_check,
        check_real_test_validation(
            real_test_validation_path,
            inputs.candidate_id,
            inputs.dataset_version,
        ),
        check_rollback_reference(
            registry_path,
            inputs.candidate_id,
            str(kubernetes_payload.get("source_model_sha256") or ""),
            model_digest,
        ),
        kubernetes_check,
    ]

    data_status = category_status(checks, "data")
    model_status = category_status(checks, "model")
    runtime_status = category_status(checks, "runtime")
    blockers = sorted({blocker for check in checks for blocker in check.blockers})
    input_digest = readiness_input_digest(checks)
    decision = "ready" if not blockers else "blocked"
    evaluation = ArtifactReadinessEvaluation(
        evaluation_id=f"readiness-{input_digest[:16]}",
        decision=decision,
        status="pass" if decision == "ready" else "blocked",
        data_status=data_status,
        model_status=model_status,
        runtime_status=runtime_status,
        candidate_id=inputs.candidate_id,
        dataset_version=inputs.dataset_version,
        evaluated_at=utc_now(),
        input_digest=input_digest,
        checks=checks,
        blockers=blockers,
        report_uri=(
            canonical_evidence_uri(Path(inputs.report_uri)) if inputs.report_uri else None
        ),
    )

    data_blockers = category_blockers(checks, "data")
    model_blockers = category_blockers(checks, "model") + category_blockers(checks, "runtime")
    model_blockers = sorted(set(model_blockers))
    data_owner = org_context.data_owner if org_context else None
    model_owner = org_context.model_owner if org_context else None
    data_pipeline = DataPipelineReadiness(
        contract_status=next(check.status for check in checks if check.check_id == "data_contract"),
        quality_status=next(check.status for check in checks if check.check_id == "quality_gate"),
        lineage_status=next(check.status for check in checks if check.check_id == "lineage"),
        replay_ready=next(check.status for check in checks if check.check_id == "split_manifest") == "pass",
        source_policy_uri=canonical_evidence_uri(contract_path),
        quality_report_uri=canonical_evidence_uri(quality_report_path),
        lineage_uri=canonical_evidence_uri(lineage_path),
        backfill_window="immutable-split-manifest",
        owner_approval_required=True,
        owner_approval_status="pass" if data_owner and not data_blockers else "blocked",
        owner_approval_actor=data_owner,
        blockers=data_blockers,
    )
    experiment_pipeline = ExperimentPipelineReadiness(
        tracking_status=next(check.status for check in checks if check.check_id == "mlflow_run"),
        evaluation_status=(
            "pass"
            if all(
                next(check.status for check in checks if check.check_id == check_id) == "pass"
                for check_id in (
                    "evaluation_report",
                    "model_card",
                    "model_artifact",
                    "real_test_validation",
                )
            )
            else "blocked"
        ),
        registry_status=next(
            check.status for check in checks if check.check_id == "rollback_reference"
        ),
        promotion_ready=decision == "ready",
        experiment_uri=str(candidate.get("run_uri") or inputs.mlflow_tracking_uri),
        model_card_uri=canonical_evidence_uri(model_card_path),
        evaluation_report_uri=canonical_evidence_uri(candidate_summary_path),
        rollback_ready=next(
            check.status for check in checks if check.check_id == "rollback_reference"
        )
        == "pass",
        owner_approval_required=True,
        owner_approval_status="pass" if model_owner and not model_blockers else "blocked",
        owner_approval_actor=model_owner,
        blockers=model_blockers,
    )
    return ReadinessResult(
        evaluation=evaluation,
        data_pipeline=data_pipeline,
        experiment_pipeline=experiment_pipeline,
    )
