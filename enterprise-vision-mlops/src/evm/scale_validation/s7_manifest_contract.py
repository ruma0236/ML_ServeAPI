from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4


MANIFEST_FAMILIES = ("image", "vlm", "llm")
MANIFEST_SNAPSHOT_CONTRACT_SCHEMA = "evm.s7_manifest_snapshot_contract.v1"
TRUSTED_MANIFEST_ENVELOPE_SCHEMA = "evm.s7_manifest_trusted_envelope.v1"
MANIFEST_SEMANTIC_CONTRACT = {
    "schema_version": "evm.s7_manifest_semantic_identity.v1",
    "encoding": "utf-8",
    "record_order": "preserved",
    "json_canonicalization": "sorted-keys-compact-utf8-lf",
    "excluded_paths": ["curation.curated_at"],
}


class S7ManifestContractError(ValueError):
    pass


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def manifest_semantic_identity(raw: bytes) -> tuple[str, int]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise S7ManifestContractError("manifest_snapshot_not_utf8") from exc
    digest = hashlib.sha256()
    record_count = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise S7ManifestContractError(f"manifest_snapshot_blank_record:{line_number}")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise S7ManifestContractError(f"manifest_snapshot_invalid_json:{line_number}") from exc
        if not isinstance(record, Mapping):
            raise S7ManifestContractError(f"manifest_snapshot_record_not_object:{line_number}")
        projected = dict(record)
        curation = projected.get("curation")
        if isinstance(curation, Mapping):
            projected_curation = dict(curation)
            projected_curation.pop("curated_at", None)
            projected["curation"] = projected_curation
        canonical = json.dumps(
            projected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        digest.update(canonical)
        digest.update(b"\n")
        record_count += 1
    if record_count == 0:
        raise S7ManifestContractError("manifest_snapshot_empty")
    return digest.hexdigest(), record_count


def manifest_snapshot_binding_sha256(contract: Mapping[str, Any]) -> str:
    return canonical_sha256(contract)


def _canonical_root(path: Path) -> str:
    return path.resolve(strict=True).as_posix()


def _snapshot_relative_path(family: str) -> str:
    if family not in MANIFEST_FAMILIES:
        raise S7ManifestContractError(f"manifest_snapshot_family:{family}")
    return f"manifest-snapshots/{family}.jsonl"


def _write_exclusive(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def publish_exclusive_atomic_bytes(path: Path, raw: bytes) -> dict[str, Any]:
    """Publish complete bytes without ever replacing an existing final path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path = str(path.parent.resolve(strict=True) / path.name)
    identity = {
        "path": canonical_path,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.publish")
    _write_exclusive(temporary, raw)
    if temporary.read_bytes() != raw:
        temporary.unlink(missing_ok=True)
        raise S7ManifestContractError("exclusive_publish_precommit_readback")
    linked = False
    temporary_cleanup_error: str | None = None
    try:
        os.link(temporary, path)
        linked = True
    finally:
        try:
            temporary.unlink()
        except OSError as exc:
            if not linked:
                raise S7ManifestContractError("exclusive_publish_temp_cleanup") from exc
            temporary_cleanup_error = type(exc).__name__
    return {
        **identity,
        "temporary_cleanup_error": temporary_cleanup_error,
    }


def build_trusted_manifest_envelope(
    *,
    suite_id: str,
    source_revision: str,
    manifest_snapshot_binding_sha256: str,
    private_evidence_index_sha256: str,
    public_evidence_sha256: str,
) -> dict[str, Any]:
    values = (
        manifest_snapshot_binding_sha256,
        private_evidence_index_sha256,
        public_evidence_sha256,
    )
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in values
    ):
        raise S7ManifestContractError("trusted_manifest_envelope_sha256")
    if len(source_revision) != 40 or any(
        character not in "0123456789abcdef" for character in source_revision
    ):
        raise S7ManifestContractError("trusted_manifest_envelope_revision")
    if not suite_id:
        raise S7ManifestContractError("trusted_manifest_envelope_suite")
    return {
        "schema_version": TRUSTED_MANIFEST_ENVELOPE_SCHEMA,
        "suite_id": suite_id,
        "source_revision": source_revision,
        "manifest_snapshot_binding_sha256": manifest_snapshot_binding_sha256,
        "private_evidence_index_sha256": private_evidence_index_sha256,
        "public_evidence_sha256": public_evidence_sha256,
        "acceptance_credit": False,
    }


def validate_trusted_manifest_envelope(
    envelope: Mapping[str, Any],
    *,
    suite_id: str,
    source_revision: str,
) -> dict[str, Any]:
    if set(envelope) != {
        "schema_version",
        "suite_id",
        "source_revision",
        "manifest_snapshot_binding_sha256",
        "private_evidence_index_sha256",
        "public_evidence_sha256",
        "acceptance_credit",
    }:
        raise S7ManifestContractError("trusted_manifest_envelope_keys")
    expected = build_trusted_manifest_envelope(
        suite_id=suite_id,
        source_revision=source_revision,
        manifest_snapshot_binding_sha256=str(
            envelope.get("manifest_snapshot_binding_sha256") or ""
        ),
        private_evidence_index_sha256=str(envelope.get("private_evidence_index_sha256") or ""),
        public_evidence_sha256=str(envelope.get("public_evidence_sha256") or ""),
    )
    if dict(envelope) != expected:
        raise S7ManifestContractError("trusted_manifest_envelope_identity")
    return expected


def create_run_scoped_manifest_snapshots(
    *,
    suite_root: Path,
    suite_id: str,
    sources: Mapping[str, Path],
    expected_raw_sha256: Mapping[str, str],
) -> dict[str, Any]:
    if suite_root.name != suite_id:
        raise S7ManifestContractError("manifest_snapshot_suite_leaf_mismatch")
    if set(sources) != set(MANIFEST_FAMILIES) or set(expected_raw_sha256) != set(MANIFEST_FAMILIES):
        raise S7ManifestContractError("manifest_snapshot_family_set")
    snapshot_root = suite_root / "manifest-snapshots"
    snapshot_root.mkdir(parents=False, exist_ok=False)
    identities: dict[str, dict[str, Any]] = {}
    for family in MANIFEST_FAMILIES:
        source = sources[family]
        if source.is_symlink() or not source.is_file():
            raise S7ManifestContractError(f"manifest_snapshot_source_invalid:{family}")
        raw = source.read_bytes()
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        if raw_sha256 != expected_raw_sha256[family]:
            raise S7ManifestContractError(f"manifest_snapshot_source_identity:{family}")
        semantic_sha256, record_count = manifest_semantic_identity(raw)
        relative = _snapshot_relative_path(family)
        destination = suite_root / relative
        _write_exclusive(destination, raw)
        readback = destination.read_bytes()
        if readback != raw:
            raise S7ManifestContractError(f"manifest_snapshot_write_readback:{family}")
        identities[family] = {
            "path": relative,
            "bytes": len(raw),
            "raw_sha256": raw_sha256,
            "semantic_sha256": semantic_sha256,
            "record_count": record_count,
        }
    return {
        "schema_version": MANIFEST_SNAPSHOT_CONTRACT_SCHEMA,
        "suite_id": suite_id,
        "private_root": _canonical_root(suite_root),
        "semantic_contract": MANIFEST_SEMANTIC_CONTRACT,
        "families": identities,
    }


def validate_manifest_snapshot_contract(
    *,
    suite_root: Path,
    suite_id: str,
    contract: Mapping[str, Any],
    indexed_artifacts: list[Mapping[str, Any]],
    trusted_binding_sha256: str | None = None,
) -> dict[str, Any]:
    if set(contract) != {
        "schema_version",
        "suite_id",
        "private_root",
        "semantic_contract",
        "families",
    }:
        raise S7ManifestContractError("manifest_snapshot_contract_keys")
    if contract.get("schema_version") != MANIFEST_SNAPSHOT_CONTRACT_SCHEMA:
        raise S7ManifestContractError("manifest_snapshot_contract_schema")
    if suite_root.name != suite_id or contract.get("suite_id") != suite_id:
        raise S7ManifestContractError("manifest_snapshot_suite_identity")
    if contract.get("private_root") != _canonical_root(suite_root):
        raise S7ManifestContractError("manifest_snapshot_private_root_replay")
    if contract.get("semantic_contract") != MANIFEST_SEMANTIC_CONTRACT:
        raise S7ManifestContractError("manifest_snapshot_semantic_contract")
    families = contract.get("families")
    if not isinstance(families, Mapping) or set(families) != set(MANIFEST_FAMILIES):
        raise S7ManifestContractError("manifest_snapshot_family_set")
    artifacts_by_path = {
        str(item.get("path")): item for item in indexed_artifacts if isinstance(item, Mapping)
    }
    validated: dict[str, dict[str, Any]] = {}
    for family in MANIFEST_FAMILIES:
        identity = families[family]
        if not isinstance(identity, Mapping) or set(identity) != {
            "path",
            "bytes",
            "raw_sha256",
            "semantic_sha256",
            "record_count",
        }:
            raise S7ManifestContractError(f"manifest_snapshot_identity_keys:{family}")
        relative = _snapshot_relative_path(family)
        if identity.get("path") != relative:
            raise S7ManifestContractError(f"manifest_snapshot_path:{family}")
        snapshot = suite_root / relative
        if snapshot.is_symlink() or not snapshot.is_file():
            raise S7ManifestContractError(f"manifest_snapshot_missing:{family}")
        raw = snapshot.read_bytes()
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        semantic_sha256, record_count = manifest_semantic_identity(raw)
        expected = {
            "path": relative,
            "bytes": len(raw),
            "raw_sha256": raw_sha256,
            "semantic_sha256": semantic_sha256,
            "record_count": record_count,
        }
        if dict(identity) != expected:
            raise S7ManifestContractError(f"manifest_snapshot_identity:{family}")
        artifact = artifacts_by_path.get(relative)
        if artifact is None or {
            "path": artifact.get("path"),
            "bytes": artifact.get("bytes"),
            "sha256": artifact.get("sha256"),
        } != {"path": relative, "bytes": len(raw), "sha256": raw_sha256}:
            raise S7ManifestContractError(f"manifest_snapshot_private_index:{family}")
        validated[family] = expected
    binding_sha256 = manifest_snapshot_binding_sha256(contract)
    if trusted_binding_sha256 is not None and binding_sha256 != trusted_binding_sha256:
        raise S7ManifestContractError("manifest_snapshot_trusted_binding")
    return {
        "status": "valid",
        "suite_id": suite_id,
        "binding_sha256": binding_sha256,
        "families": validated,
    }


def classify_live_manifest_drift(
    *, snapshot_identity: Mapping[str, Any], live_manifest: Path
) -> dict[str, Any]:
    if not live_manifest.is_file() or live_manifest.is_symlink():
        return {
            "status": "remediation_required",
            "classification": "live_manifest_missing_or_unsafe",
        }
    try:
        raw = live_manifest.read_bytes()
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        semantic_sha256, record_count = manifest_semantic_identity(raw)
    except (OSError, S7ManifestContractError) as exc:
        return {
            "status": "remediation_required",
            "classification": "live_manifest_unreadable_or_invalid",
            "error_type": type(exc).__name__,
        }
    observed = {
        "bytes": len(raw),
        "raw_sha256": raw_sha256,
        "semantic_sha256": semantic_sha256,
        "record_count": record_count,
    }
    if raw_sha256 == snapshot_identity.get("raw_sha256"):
        return {
            "status": "exact_match",
            "classification": "raw_and_semantic_match",
            "observed": observed,
        }
    if semantic_sha256 == snapshot_identity.get(
        "semantic_sha256"
    ) and record_count == snapshot_identity.get("record_count"):
        return {
            "status": "drift_classified",
            "classification": "volatile_curated_at_only",
            "observed": observed,
        }
    return {
        "status": "remediation_required",
        "classification": "semantic_manifest_drift",
        "observed": observed,
    }
