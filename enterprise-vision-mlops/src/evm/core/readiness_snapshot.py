from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SNAPSHOT_SCHEMA_VERSION = "evm.readiness_inputs_snapshot.v1"
REQUIRED_SOURCES = ("dataset_metadata", "quality_report", "source_shard")


@dataclass(frozen=True)
class ReadinessEvidenceSelection:
    manifest_path: Path
    dataset_metadata_path: Path | None = None
    quality_report_path: Path | None = None
    source_shard_path: Path | None = None
    expected_digests: dict[str, str] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_path(value: str | Path) -> Path:
    path = Path(value)
    normalized = str(value).replace("\\", "/")
    host_root = os.getenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    ).replace("\\", "/").rstrip("/")
    mount_root = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data").replace(
        "\\",
        "/",
    ).rstrip("/")
    if normalized.lower().startswith(host_root.lower()):
        if Path(host_root).exists():
            return path
        mapped = Path(f"{mount_root}{normalized[len(host_root):]}")
        if Path(mount_root).exists():
            return mapped
    if normalized.lower().startswith(mount_root.lower()):
        mapped = Path(f"{host_root}{normalized[len(mount_root):]}")
        if Path(host_root).exists():
            return mapped
        if Path(mount_root).exists():
            return path

    if path.exists():
        return path
    return path


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def evidence_record_count(payload: dict[str, Any]) -> int:
    return int(payload.get("record_count") or payload.get("valid_records") or 0)


def source_shard_digest(path: Path, payload: dict[str, Any]) -> tuple[str, str]:
    embedded = str(payload.get("source_shard_index_sha256") or "")
    if embedded:
        return embedded, "split_manifest_snapshot"
    identity = str(payload.get("identity_sha256") or "")
    if identity:
        return identity, "shard_index_identity"
    return file_sha256(path), "shard_index"


def _validate_source_identity(
    *,
    dataset_payload: dict[str, Any],
    quality_payload: dict[str, Any],
    shard_path: Path,
    shard_payload: dict[str, Any],
    dataset_version: str,
    expected_record_count: int,
    expected_source_digest: str,
) -> list[str]:
    blockers: list[str] = []
    if str(dataset_payload.get("dataset_version") or "") != dataset_version:
        blockers.append("readiness_snapshot_dataset_version_mismatch")
    if evidence_record_count(dataset_payload) != expected_record_count:
        blockers.append("readiness_snapshot_dataset_record_count_mismatch")
    if str(quality_payload.get("dataset_version") or "") != dataset_version:
        blockers.append("readiness_snapshot_quality_version_mismatch")
    if evidence_record_count(quality_payload) != expected_record_count:
        blockers.append("readiness_snapshot_quality_record_count_mismatch")
    if str(quality_payload.get("status") or "") != "pass":
        blockers.append("readiness_snapshot_quality_not_passing")
    if evidence_record_count(shard_payload) != expected_record_count:
        blockers.append("readiness_snapshot_source_record_count_mismatch")
    observed_source_digest, _ = source_shard_digest(shard_path, shard_payload)
    if observed_source_digest.lower() != expected_source_digest.lower():
        blockers.append("readiness_snapshot_source_digest_mismatch")
    return blockers


def capture_readiness_snapshot(
    *,
    output_dir: str | Path,
    candidate_id: str,
    dataset_version: str,
    expected_record_count: int,
    expected_source_digest: str,
    dataset_metadata_path: str | Path,
    quality_report_path: str | Path,
    source_shard_path: str | Path,
) -> Path:
    sources = {
        "dataset_metadata": runtime_path(dataset_metadata_path),
        "quality_report": runtime_path(quality_report_path),
        "source_shard": runtime_path(source_shard_path),
    }
    missing = [name for name, path in sources.items() if not path.is_file()]
    if missing:
        raise ValueError(f"readiness snapshot sources missing: {','.join(sorted(missing))}")

    dataset_payload = read_json(sources["dataset_metadata"])
    quality_payload = read_json(sources["quality_report"])
    shard_payload = read_json(sources["source_shard"])
    blockers = _validate_source_identity(
        dataset_payload=dataset_payload,
        quality_payload=quality_payload,
        shard_path=sources["source_shard"],
        shard_payload=shard_payload,
        dataset_version=dataset_version,
        expected_record_count=expected_record_count,
        expected_source_digest=expected_source_digest,
    )
    if blockers:
        raise ValueError(";".join(sorted(blockers)))

    target_dir = runtime_path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target_dir / "readiness_inputs_manifest.json"
    if manifest_path.is_file():
        existing = read_json(manifest_path)
        existing_sources = existing.get("sources")
        existing_sources = existing_sources if isinstance(existing_sources, dict) else {}
        identity_matches = (
            existing.get("schema_version") == SNAPSHOT_SCHEMA_VERSION
            and existing.get("candidate_id") == candidate_id
            and existing.get("dataset_version") == dataset_version
            and int(existing.get("record_count") or 0) == expected_record_count
            and str(existing.get("source_shard_index_sha256") or "").lower()
            == expected_source_digest.lower()
        )
        sources_match = all(
            isinstance(existing_sources.get(name), dict)
            and str(existing_sources[name].get("source_sha256") or "").lower()
            == file_sha256(source).lower()
            and (
                target := runtime_path(str(existing_sources[name].get("path") or ""))
            ).is_file()
            and str(existing_sources[name].get("sha256") or "").lower()
            == file_sha256(target).lower()
            for name, source in sources.items()
        )
        if identity_matches and sources_match:
            return manifest_path
        raise ValueError("readiness_snapshot_immutable_conflict")

    _source_digest, source_kind = source_shard_digest(
        sources["source_shard"],
        shard_payload,
    )
    names = {
        "dataset_metadata": "dataset_metadata.json",
        "quality_report": "quality_report.json",
        "source_shard": (
            "source_shard_snapshot.json"
            if source_kind == "split_manifest_snapshot"
            else "source_shard_index.json"
        ),
    }
    source_entries: dict[str, dict[str, str]] = {}
    for name, source in sources.items():
        target = target_dir / names[name]
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        source_entries[name] = {
            "kind": source_kind if name == "source_shard" else name,
            "path": str(target).replace("\\", "/"),
            "sha256": file_sha256(target),
            "source_path": str(source).replace("\\", "/"),
            "source_sha256": file_sha256(source),
        }

    write_json(
        manifest_path,
        {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "dataset_version": dataset_version,
            "record_count": expected_record_count,
            "source_shard_index_sha256": expected_source_digest,
            "created_at": utc_now(),
            "sources": source_entries,
        },
    )
    return manifest_path


def load_readiness_snapshot(
    manifest_path: str | Path,
    *,
    candidate_id: str,
    dataset_version: str,
    expected_record_count: int,
    expected_source_digest: str,
    expected_manifest_digest: str = "",
    required: bool = False,
) -> ReadinessEvidenceSelection | None:
    path = runtime_path(manifest_path)
    if not path.is_file():
        if not required:
            return None
        return ReadinessEvidenceSelection(
            manifest_path=path,
            blockers=("readiness_snapshot_manifest_missing",),
        )

    blockers: list[str] = []
    actual_manifest_digest = file_sha256(path)
    if required and not expected_manifest_digest:
        blockers.append("readiness_snapshot_manifest_digest_missing")
    if (
        expected_manifest_digest
        and actual_manifest_digest.lower() != expected_manifest_digest.lower()
    ):
        blockers.append("readiness_snapshot_manifest_digest_mismatch")
    try:
        payload = read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return ReadinessEvidenceSelection(
            manifest_path=path,
            blockers=tuple(sorted(set(blockers + ["readiness_snapshot_manifest_malformed"]))),
        )

    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        blockers.append("readiness_snapshot_schema_mismatch")
    if payload.get("candidate_id") != candidate_id:
        blockers.append("readiness_snapshot_candidate_mismatch")
    if payload.get("dataset_version") != dataset_version:
        blockers.append("readiness_snapshot_dataset_version_mismatch")
    if int(payload.get("record_count") or 0) != expected_record_count:
        blockers.append("readiness_snapshot_record_count_mismatch")
    if str(payload.get("source_shard_index_sha256") or "").lower() != (
        expected_source_digest.lower()
    ):
        blockers.append("readiness_snapshot_source_digest_mismatch")

    entries = payload.get("sources")
    entries = entries if isinstance(entries, dict) else {}
    resolved: dict[str, Path] = {}
    expected_digests: dict[str, str] = {}
    for name in REQUIRED_SOURCES:
        entry = entries.get(name)
        if not isinstance(entry, dict):
            blockers.append(f"readiness_snapshot_{name}_entry_missing")
            continue
        source_path = runtime_path(str(entry.get("path") or ""))
        resolved[name] = source_path
        expected_digest = str(entry.get("sha256") or "")
        expected_digests[name] = expected_digest
        if not source_path.is_file():
            blockers.append(f"readiness_snapshot_{name}_missing")
            continue
        if not expected_digest:
            blockers.append(f"readiness_snapshot_{name}_digest_missing")
        elif file_sha256(source_path).lower() != expected_digest.lower():
            blockers.append(f"readiness_snapshot_{name}_digest_mismatch")

    return ReadinessEvidenceSelection(
        manifest_path=path,
        dataset_metadata_path=resolved.get("dataset_metadata"),
        quality_report_path=resolved.get("quality_report"),
        source_shard_path=resolved.get("source_shard"),
        expected_digests=expected_digests,
        blockers=tuple(sorted(set(blockers))),
    )
