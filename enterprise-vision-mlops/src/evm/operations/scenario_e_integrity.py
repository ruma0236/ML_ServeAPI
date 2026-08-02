from __future__ import annotations

import base64
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evm.core.dataset import shard_index_identity_digest, stable_json_digest
from evm.operations.failure_evidence import sha256_file


SHA256_PATTERN = r"^[a-f0-9]{64}$"
REQUIRED_FILE_ROLES = {
    "shard_manifest",
    "split_manifest",
    "ct_manifest",
    "candidate_summary",
    "lineage",
    "model_card",
    "model_artifact",
    "policy",
}
BLOCKER_PRECEDENCE = (
    "trust_key_unknown",
    "trust_signature_invalid",
    "trust_manifest_id_mismatch",
    "trust_manifest_stale",
    "exception_invalid",
    "manifest_missing",
    "manifest_digest_mismatch",
    "manifest_schema_invalid",
    "shard_missing",
    "shard_digest_mismatch",
    "manifest_count_mismatch",
    "split_leakage_detected",
    "duplicate_record_identity",
    "duplicate_content_identity",
    "ct_identity_mismatch",
    "lineage_parent_missing",
    "dataset_identity_mismatch",
    "model_artifact_digest_mismatch",
    "model_identity_mismatch",
    "mlflow_identity_mismatch",
    "container_image_digest_mismatch",
    "evidence_digest_mismatch",
)


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntegrityIdentity(StrictModel):
    dataset_version: str = Field(min_length=1)
    shard_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    shard_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    split_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    ct_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_id: str = Field(min_length=1)
    model_digest: str = Field(pattern=SHA256_PATTERN)
    container_image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    mlflow_run_id: str = Field(min_length=1)
    candidate_summary_sha256: str = Field(pattern=SHA256_PATTERN)
    lineage_sha256: str = Field(pattern=SHA256_PATTERN)
    model_card_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    training_source_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")


class IntegrityCounts(StrictModel):
    record_count: int = Field(gt=0)
    shard_count: int = Field(gt=0)
    split_counts: dict[str, int]
    ct_record_count: int = Field(gt=0)


class TrustedFile(StrictModel):
    role: Literal[
        "shard_manifest",
        "split_manifest",
        "ct_manifest",
        "candidate_summary",
        "lineage",
        "model_card",
        "model_artifact",
        "policy",
    ]
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)


class IntegrityException(StrictModel):
    exception_id: str = Field(min_length=8)
    code: Literal["training_source_revision_missing"]
    requester: str = Field(min_length=1)
    approver: str = Field(min_length=1)
    reason: str = Field(min_length=12)
    subject_fingerprint: str = Field(pattern=SHA256_PATTERN)
    issued_at: datetime
    expires_at: datetime

    _validate_issued_at = field_validator("issued_at")(require_utc)
    _validate_expires_at = field_validator("expires_at")(require_utc)

    @model_validator(mode="after")
    def validate_exception(self) -> "IntegrityException":
        if self.requester == self.approver:
            raise ValueError("exception requester and approver must differ")
        if self.expires_at <= self.issued_at:
            raise ValueError("exception expiry must follow issuance")
        return self


class TrustManifest(StrictModel):
    schema_version: Literal["evm.scenario_e_trust_manifest.v1"]
    manifest_id: str = Field(pattern=SHA256_PATTERN)
    issue: Literal["SCRUM-176"]
    issuer: str = Field(min_length=1)
    key_id: str = Field(min_length=8)
    validator_source_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    issued_at: datetime
    expires_at: datetime
    identity: IntegrityIdentity
    expected_counts: IntegrityCounts
    files: list[TrustedFile] = Field(min_length=len(REQUIRED_FILE_ROLES))
    shard_digests: dict[str, str] = Field(min_length=1)
    lineage_parents: list[str] = Field(min_length=1)
    exceptions: list[IntegrityException] = Field(default_factory=list)

    _validate_issued_at = field_validator("issued_at")(require_utc)
    _validate_expires_at = field_validator("expires_at")(require_utc)

    @model_validator(mode="after")
    def validate_manifest_contract(self) -> "TrustManifest":
        if self.expires_at <= self.issued_at:
            raise ValueError("manifest expiry must follow issuance")
        roles = [item.role for item in self.files]
        if len(set(roles)) != len(roles):
            raise ValueError("trusted file roles must be unique")
        missing = sorted(REQUIRED_FILE_ROLES - set(roles))
        if missing:
            raise ValueError(f"trusted file roles missing: {missing}")
        for shard_id, digest in self.shard_digests.items():
            if not shard_id or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("shard digests must use stable IDs and lowercase SHA-256")
        return self


class SignedTrustManifest(StrictModel):
    schema_version: Literal["evm.scenario_e_signed_trust.v1"]
    signature_algorithm: Literal["ed25519"]
    key_id: str = Field(min_length=8)
    public_key_sha256: str = Field(pattern=SHA256_PATTERN)
    manifest: TrustManifest
    signature: str = Field(min_length=32)

    @model_validator(mode="after")
    def validate_key_binding(self) -> "SignedTrustManifest":
        if self.key_id != self.manifest.key_id:
            raise ValueError("envelope key does not match manifest key")
        return self


class MlflowObservation(StrictModel):
    run_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    artifact_uri: str = Field(min_length=1)


class IntegrityCheck(StrictModel):
    check_id: str = Field(min_length=1)
    passed: bool
    observed: dict[str, Any] = Field(default_factory=dict)
    blocker_codes: list[str] = Field(default_factory=list)


class IntegrityValidation(StrictModel):
    schema_version: Literal["evm.scenario_e_integrity_validation.v1"]
    decision: Literal["admitted", "blocked"]
    manifest_id: str
    identity_fingerprint: str
    decision_fingerprint: str = Field(pattern=SHA256_PATTERN)
    primary_blocker: str | None = None
    blockers: list[str] = Field(default_factory=list)
    checks: list[IntegrityCheck]
    counts: dict[str, Any]
    exceptions_applied: list[str] = Field(default_factory=list)
    validation_seconds: float = Field(ge=0)
    evaluated_at: datetime
    training_allowed: bool
    promotion_allowed: bool
    deployment_intent_allowed: bool

    _validate_evaluated_at = field_validator("evaluated_at")(require_utc)

    @model_validator(mode="after")
    def validate_decision(self) -> "IntegrityValidation":
        allowed = self.training_allowed or self.promotion_allowed or self.deployment_intent_allowed
        if self.decision == "admitted" and (self.blockers or not all(
            (self.training_allowed, self.promotion_allowed, self.deployment_intent_allowed)
        )):
            raise ValueError("admitted integrity result must allow all downstream boundaries")
        if self.decision == "blocked" and (not self.blockers or allowed):
            raise ValueError("blocked integrity result must fail closed")
        return self


class IntegrityAdmission(StrictModel):
    schema_version: Literal["evm.scenario_e_integrity_admission.v1"]
    decision: Literal["admitted"]
    manifest_id: str = Field(pattern=SHA256_PATTERN)
    identity_fingerprint: str = Field(pattern=SHA256_PATTERN)
    identity: IntegrityIdentity
    signed_manifest_uri: str = Field(min_length=1)
    signed_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    validation_uri: str = Field(min_length=1)
    validation_sha256: str = Field(pattern=SHA256_PATTERN)
    source_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    issued_at: datetime
    expires_at: datetime

    _validate_issued_at = field_validator("issued_at")(require_utc)
    _validate_expires_at = field_validator("expires_at")(require_utc)

    @model_validator(mode="after")
    def validate_admission(self) -> "IntegrityAdmission":
        if self.expires_at <= self.issued_at:
            raise ValueError("admission expiry must follow issuance")
        if self.identity_fingerprint != identity_fingerprint(self.identity):
            raise ValueError("admission identity fingerprint mismatch")
        return self


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def identity_fingerprint(identity: IntegrityIdentity) -> str:
    return hashlib.sha256(
        canonical_json_bytes(identity.model_dump(mode="json"))
    ).hexdigest()


def manifest_id(manifest: TrustManifest | dict[str, Any]) -> str:
    payload = (
        manifest.model_dump(mode="json")
        if isinstance(manifest, TrustManifest)
        else dict(manifest)
    )
    payload["manifest_id"] = ""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def public_key_fingerprint(public_key_pem: bytes) -> str:
    public_key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("scenario E requires an Ed25519 public key")
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def sign_manifest(manifest: TrustManifest, private_key_pem: bytes) -> SignedTrustManifest:
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("scenario E requires an Ed25519 private key")
    expected_id = manifest_id(manifest)
    if manifest.manifest_id != expected_id:
        raise ValueError("trust manifest ID does not match canonical contents")
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signature = private_key.sign(canonical_json_bytes(manifest.model_dump(mode="json")))
    key_fingerprint = public_key_fingerprint(public_pem)
    return SignedTrustManifest(
        schema_version="evm.scenario_e_signed_trust.v1",
        signature_algorithm="ed25519",
        key_id=manifest.key_id,
        public_key_sha256=key_fingerprint,
        manifest=manifest,
        signature=base64.b64encode(signature).decode("ascii"),
    )


def verify_signed_manifest(
    envelope: SignedTrustManifest,
    public_key_pem: bytes,
) -> list[str]:
    blockers: list[str] = []
    try:
        observed_fingerprint = public_key_fingerprint(public_key_pem)
    except (TypeError, ValueError):
        return ["trust_key_unknown"]
    if observed_fingerprint != envelope.public_key_sha256:
        blockers.append("trust_key_unknown")
    if envelope.manifest.manifest_id != manifest_id(envelope.manifest):
        blockers.append("trust_manifest_id_mismatch")
    try:
        public_key = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("wrong key type")
        signature = base64.b64decode(envelope.signature, validate=True)
        public_key.verify(
            signature,
            canonical_json_bytes(envelope.manifest.model_dump(mode="json")),
        )
    except (InvalidSignature, TypeError, ValueError):
        blockers.append("trust_signature_invalid")
    return order_blockers(blockers)


def order_blockers(blockers: list[str]) -> list[str]:
    unique = set(blockers)
    rank = {code: index for index, code in enumerate(BLOCKER_PRECEDENCE)}
    return sorted(unique, key=lambda code: (rank.get(code, len(rank)), code))


def _safe_resolve(path: str, allowed_roots: list[Path]) -> Path | None:
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    for root in allowed_roots:
        try:
            if resolved.is_relative_to(root.resolve(strict=False)):
                return resolved
        except (OSError, RuntimeError):
            continue
    return None


def _runtime_path(path: str, host_data_root: Path, host_ct_root: Path) -> Path:
    normalized = path.replace("\\", "/")
    if normalized.startswith("/mnt/evm-data/"):
        return host_data_root / normalized.removeprefix("/mnt/evm-data/")
    if normalized.startswith("/mnt/evm-ct/"):
        return host_ct_root / normalized.removeprefix("/mnt/evm-ct/")
    return Path(path)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("JSONL record must be an object")
            records.append(payload)
    return records


def _record_id(record: dict[str, Any]) -> str:
    return str(record.get("sample_id") or record.get("id") or "")


def _file_map(manifest: TrustManifest) -> dict[str, TrustedFile]:
    return {item.role: item for item in manifest.files}


def validate_integrity(
    envelope: SignedTrustManifest,
    *,
    public_key_pem: bytes,
    allowed_roots: list[Path],
    host_data_root: Path,
    host_ct_root: Path,
    observed_image_digest: str,
    mlflow: MlflowObservation,
    now: datetime | None = None,
) -> IntegrityValidation:
    started = time.perf_counter()
    evaluated_at = now or datetime.now(UTC)
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=UTC)
    evaluated_at = evaluated_at.astimezone(UTC)
    manifest = envelope.manifest
    fingerprint = identity_fingerprint(manifest.identity)
    checks: list[IntegrityCheck] = []
    blockers: list[str] = []
    counts: dict[str, Any] = {}
    exceptions_applied: list[str] = []

    def add_check(check_id: str, observed: dict[str, Any], codes: list[str]) -> None:
        ordered = order_blockers(codes)
        blockers.extend(ordered)
        checks.append(
            IntegrityCheck(
                check_id=check_id,
                passed=not ordered,
                observed=observed,
                blocker_codes=ordered,
            )
        )

    trust_blockers = verify_signed_manifest(envelope, public_key_pem)
    add_check(
        "trust_signature",
        {
            "key_id": envelope.key_id,
            "public_key_sha256": envelope.public_key_sha256,
            "manifest_id": manifest.manifest_id,
        },
        trust_blockers,
    )
    if trust_blockers:
        return _finalize_validation(
            manifest=manifest,
            identity_fingerprint_value=fingerprint,
            checks=checks,
            blockers=blockers,
            counts=counts,
            exceptions_applied=exceptions_applied,
            started=started,
            evaluated_at=evaluated_at,
        )

    freshness_blockers = []
    if evaluated_at < manifest.issued_at or evaluated_at >= manifest.expires_at:
        freshness_blockers.append("trust_manifest_stale")
    add_check(
        "trust_freshness",
        {
            "issued_at": manifest.issued_at.isoformat(),
            "expires_at": manifest.expires_at.isoformat(),
            "evaluated_at": evaluated_at.isoformat(),
        },
        freshness_blockers,
    )

    exception_blockers: list[str] = []
    if manifest.identity.training_source_revision is None:
        matching = [
            item
            for item in manifest.exceptions
            if item.code == "training_source_revision_missing"
            and item.subject_fingerprint == fingerprint
            and item.issued_at <= evaluated_at < item.expires_at
            and item.expires_at <= manifest.expires_at
        ]
        if len(matching) != 1:
            exception_blockers.append("exception_invalid")
        else:
            exceptions_applied.append(matching[0].exception_id)
    elif manifest.exceptions:
        exception_blockers.append("exception_invalid")
    add_check(
        "legacy_exception",
        {
            "training_source_revision": manifest.identity.training_source_revision,
            "exception_ids": [item.exception_id for item in manifest.exceptions],
            "applied": exceptions_applied,
        },
        exception_blockers,
    )
    if freshness_blockers or exception_blockers:
        return _finalize_validation(
            manifest=manifest,
            identity_fingerprint_value=fingerprint,
            checks=checks,
            blockers=blockers,
            counts=counts,
            exceptions_applied=exceptions_applied,
            started=started,
            evaluated_at=evaluated_at,
        )

    files = _file_map(manifest)
    resolved_files: dict[str, Path] = {}
    file_blockers: list[str] = []
    file_observed: dict[str, Any] = {}
    for role, trusted in files.items():
        path = _safe_resolve(trusted.path, allowed_roots)
        if path is None or not path.is_file():
            file_blockers.append("manifest_missing")
            file_observed[role] = {"path": trusted.path, "status": "missing_or_outside_root"}
            continue
        observed_sha = sha256_file(path)
        resolved_files[role] = path
        file_observed[role] = {
            "path": str(path),
            "expected_sha256": trusted.sha256,
            "observed_sha256": observed_sha,
        }
        if observed_sha != trusted.sha256:
            if role == "model_artifact":
                file_blockers.append("model_artifact_digest_mismatch")
            elif role in {"candidate_summary", "lineage", "model_card"}:
                file_blockers.append("evidence_digest_mismatch")
            else:
                file_blockers.append("manifest_digest_mismatch")
    add_check("trusted_files", file_observed, file_blockers)

    required_resolved = REQUIRED_FILE_ROLES.issubset(resolved_files)
    if not required_resolved:
        return _finalize_validation(
            manifest=manifest,
            identity_fingerprint_value=fingerprint,
            checks=checks,
            blockers=blockers,
            counts=counts,
            exceptions_applied=exceptions_applied,
            started=started,
            evaluated_at=evaluated_at,
        )

    try:
        shard_index = _read_json(resolved_files["shard_manifest"])
        split_manifest = _read_json(resolved_files["split_manifest"])
        candidate = _read_json(resolved_files["candidate_summary"])
        lineage = _read_json(resolved_files["lineage"])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        add_check("manifest_schema", {"error": type(exc).__name__}, ["manifest_schema_invalid"])
        return _finalize_validation(
            manifest=manifest,
            identity_fingerprint_value=fingerprint,
            checks=checks,
            blockers=blockers,
            counts=counts,
            exceptions_applied=exceptions_applied,
            started=started,
            evaluated_at=evaluated_at,
        )

    schema_blockers = []
    if shard_index.get("schema_version") != "evm.dataset_shards.v1":
        schema_blockers.append("manifest_schema_invalid")
    if split_manifest.get("schema_version") != "evm.w7.efficientnet_split_manifest.v1":
        schema_blockers.append("manifest_schema_invalid")
    add_check(
        "manifest_schema",
        {
            "shard_schema": shard_index.get("schema_version"),
            "split_schema": split_manifest.get("schema_version"),
        },
        schema_blockers,
    )

    semantic_digest = shard_index_identity_digest(shard_index)
    dataset_blockers: list[str] = []
    if semantic_digest != manifest.identity.shard_identity_sha256:
        dataset_blockers.append("dataset_identity_mismatch")
    if str(shard_index.get("identity_sha256") or "") != semantic_digest:
        dataset_blockers.append("dataset_identity_mismatch")

    records: list[dict[str, Any]] = []
    shard_blockers: list[str] = []
    shard_observed: dict[str, Any] = {}
    shards = shard_index.get("shards") if isinstance(shard_index.get("shards"), list) else []
    for descriptor in shards:
        if not isinstance(descriptor, dict):
            shard_blockers.append("manifest_schema_invalid")
            continue
        shard_id = str(descriptor.get("shard_id") or "")
        path = _runtime_path(str(descriptor.get("path") or ""), host_data_root, host_ct_root)
        expected_sha = manifest.shard_digests.get(shard_id)
        if not shard_id or expected_sha is None:
            shard_blockers.append("manifest_schema_invalid")
            continue
        if _safe_resolve(str(path), allowed_roots) is None or not path.is_file():
            shard_blockers.append("shard_missing")
            shard_observed[shard_id] = {"path": str(path), "status": "missing"}
            continue
        observed_sha = sha256_file(path)
        if observed_sha != expected_sha:
            shard_blockers.append("shard_digest_mismatch")
        try:
            shard_records = _read_jsonl(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            shard_blockers.append("manifest_schema_invalid")
            continue
        observed_count = len(shard_records)
        observed_first = _record_id(shard_records[0]) if shard_records else ""
        observed_last = _record_id(shard_records[-1]) if shard_records else ""
        if (
            observed_count != int(descriptor.get("record_count") or 0)
            or observed_first != str(descriptor.get("first_sample_id") or "")
            or observed_last != str(descriptor.get("last_sample_id") or "")
        ):
            shard_blockers.append("manifest_count_mismatch")
        descriptor_split = str(descriptor.get("split") or "")
        if any(str(row.get("split") or "") != descriptor_split for row in shard_records):
            shard_blockers.append("split_leakage_detected")
        records.extend(shard_records)
        shard_observed[shard_id] = {
            "path": str(path),
            "record_count": observed_count,
            "sha256": observed_sha,
        }
    if len(shards) != manifest.expected_counts.shard_count:
        shard_blockers.append("manifest_count_mismatch")
    add_check("shard_files", {"shards": shard_observed}, shard_blockers)

    sample_splits: dict[str, list[str]] = defaultdict(list)
    content_splits: dict[str, list[str]] = defaultdict(list)
    split_counts: Counter[str] = Counter()
    invalid_identity_count = 0
    for record in records:
        sample_id = _record_id(record)
        content_digest = str(record.get("content_sha256") or "")
        split = str(record.get("split") or "")
        dataset_version = str(record.get("dataset_version") or "")
        if not sample_id or len(content_digest) != 64 or dataset_version != manifest.identity.dataset_version:
            invalid_identity_count += 1
        sample_splits[sample_id].append(split)
        content_splits[content_digest].append(split)
        split_counts[split] += 1
    duplicate_ids = sorted(key for key, values in sample_splits.items() if key and len(values) > 1)
    duplicate_content = sorted(
        key for key, values in content_splits.items() if key and len(values) > 1
    )
    leaked_ids = sorted(
        key for key, values in sample_splits.items() if key and len(set(values)) > 1
    )
    leaked_content = sorted(
        key for key, values in content_splits.items() if key and len(set(values)) > 1
    )
    contract_blockers = list(dataset_blockers)
    if invalid_identity_count:
        contract_blockers.append("dataset_identity_mismatch")
    if len(records) != manifest.expected_counts.record_count:
        contract_blockers.append("manifest_count_mismatch")
    if dict(split_counts) != manifest.expected_counts.split_counts:
        contract_blockers.append("manifest_count_mismatch")
    if duplicate_ids:
        contract_blockers.append("duplicate_record_identity")
    if duplicate_content:
        contract_blockers.append("duplicate_content_identity")
    if leaked_ids or leaked_content:
        contract_blockers.append("split_leakage_detected")
    counts.update(
        {
            "record_count": len(records),
            "shard_count": len(shards),
            "split_counts": dict(split_counts),
            "duplicate_record_count": len(duplicate_ids),
            "duplicate_content_count": len(duplicate_content),
            "cross_split_record_count": len(leaked_ids),
            "cross_split_content_count": len(leaked_content),
            "invalid_identity_count": invalid_identity_count,
        }
    )
    add_check(
        "dataset_contract",
        {
            **counts,
            "shard_identity_sha256": semantic_digest,
            "duplicate_record_examples": duplicate_ids[:10],
            "duplicate_content_examples": duplicate_content[:10],
            "leak_examples": (leaked_ids + leaked_content)[:10],
        },
        contract_blockers,
    )

    ct_blockers: list[str] = []
    try:
        ct_records = _read_jsonl(resolved_files["ct_manifest"])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        ct_records = []
        ct_blockers.append("manifest_schema_invalid")
    ct_ids = [_record_id(record) for record in ct_records]
    test_ids = {key for key, values in sample_splits.items() if set(values) == {"test"}}
    if (
        len(ct_records) != manifest.expected_counts.ct_record_count
        or len(set(ct_ids)) != len(ct_ids)
        or set(ct_ids) != test_ids
        or any(str(record.get("split") or "") != "test" for record in ct_records)
        or any(
            str(record.get("dataset_version") or "") != manifest.identity.dataset_version
            for record in ct_records
        )
    ):
        ct_blockers.append("ct_identity_mismatch")
    counts["ct_record_count"] = len(ct_records)
    add_check(
        "ct_isolation",
        {
            "record_count": len(ct_records),
            "unique_ids": len(set(ct_ids)),
            "test_identity_count": len(test_ids),
            "exact_test_identity_match": set(ct_ids) == test_ids,
        },
        ct_blockers,
    )

    lineage_blockers: list[str] = []
    expected = manifest.identity
    lineage_join = {
        "candidate_id": {candidate.get("candidate_id"), lineage.get("candidate_id")},
        "dataset_version": {candidate.get("dataset_version"), lineage.get("dataset_version")},
        "model_digest": {candidate.get("model_sha256"), lineage.get("model_sha256")},
        "split_digest": {
            candidate.get("split_manifest_sha256"),
            lineage.get("split_manifest_sha256"),
        },
        "shard_identity": {
            candidate.get("source_shard_index_sha256"),
            lineage.get("source_shard_index_sha256"),
            split_manifest.get("source_shard_identity_sha256"),
        },
        "mlflow_run_id": {candidate.get("mlflow_run_id"), lineage.get("mlflow_run_id")},
    }
    expected_join = {
        "candidate_id": expected.candidate_id,
        "dataset_version": expected.dataset_version,
        "model_digest": expected.model_digest,
        "split_digest": expected.split_manifest_sha256,
        "shard_identity": expected.shard_identity_sha256,
        "mlflow_run_id": expected.mlflow_run_id,
    }
    for key, values in lineage_join.items():
        if values != {expected_join[key]}:
            lineage_blockers.append("model_identity_mismatch")
    if not all(str(lineage.get(key) or "") for key in manifest.lineage_parents):
        lineage_blockers.append("lineage_parent_missing")
    model_card = resolved_files["model_card"].read_text(encoding="utf-8-sig")
    if expected.candidate_id not in model_card or expected.mlflow_run_id not in model_card:
        lineage_blockers.append("model_identity_mismatch")
    add_check(
        "lineage_identity_join",
        {
            "expected": expected_join,
            "observed": {key: sorted(str(value) for value in values) for key, values in lineage_join.items()},
            "required_parents": manifest.lineage_parents,
        },
        lineage_blockers,
    )

    mlflow_blockers: list[str] = []
    if (
        mlflow.run_id != expected.mlflow_run_id
        or mlflow.status.upper() != "FINISHED"
        or mlflow.candidate_id != expected.candidate_id
        or mlflow.dataset_version != expected.dataset_version
        or str(candidate.get("artifact_uri") or "") != mlflow.artifact_uri
    ):
        mlflow_blockers.append("mlflow_identity_mismatch")
    add_check("mlflow_registry", mlflow.model_dump(mode="json"), mlflow_blockers)

    image_blockers = []
    if observed_image_digest != expected.container_image_digest:
        image_blockers.append("container_image_digest_mismatch")
    add_check(
        "container_identity",
        {"expected": expected.container_image_digest, "observed": observed_image_digest},
        image_blockers,
    )

    return _finalize_validation(
        manifest=manifest,
        identity_fingerprint_value=fingerprint,
        checks=checks,
        blockers=blockers,
        counts=counts,
        exceptions_applied=exceptions_applied,
        started=started,
        evaluated_at=evaluated_at,
    )


def _finalize_validation(
    *,
    manifest: TrustManifest,
    identity_fingerprint_value: str,
    checks: list[IntegrityCheck],
    blockers: list[str],
    counts: dict[str, Any],
    exceptions_applied: list[str],
    started: float,
    evaluated_at: datetime,
) -> IntegrityValidation:
    ordered = order_blockers(blockers)
    decision: Literal["admitted", "blocked"] = "blocked" if ordered else "admitted"
    material = {
        "manifest_id": manifest.manifest_id,
        "identity_fingerprint": identity_fingerprint_value,
        "decision": decision,
        "blockers": ordered,
        "checks": [
            {
                "check_id": item.check_id,
                "passed": item.passed,
                "blocker_codes": item.blocker_codes,
                # Keep audit time in evidence without making equivalent
                # decisions produce different fingerprints.
                "observed": (
                    {key: value for key, value in item.observed.items() if key != "evaluated_at"}
                    if item.check_id == "trust_freshness"
                    else item.observed
                ),
            }
            for item in checks
        ],
        "counts": counts,
        "exceptions_applied": exceptions_applied,
    }
    decision_fingerprint = stable_json_digest(material)
    allowed = decision == "admitted"
    return IntegrityValidation(
        schema_version="evm.scenario_e_integrity_validation.v1",
        decision=decision,
        manifest_id=manifest.manifest_id,
        identity_fingerprint=identity_fingerprint_value,
        decision_fingerprint=decision_fingerprint,
        primary_blocker=ordered[0] if ordered else None,
        blockers=ordered,
        checks=checks,
        counts=counts,
        exceptions_applied=exceptions_applied,
        validation_seconds=time.perf_counter() - started,
        evaluated_at=evaluated_at,
        training_allowed=allowed,
        promotion_allowed=allowed,
        deployment_intent_allowed=allowed,
    )


def build_integrity_admission(
    *,
    envelope: SignedTrustManifest,
    validation: IntegrityValidation,
    signed_manifest_path: Path,
    validation_path: Path,
    source_revision: str,
    signed_manifest_uri: str | None = None,
    validation_uri: str | None = None,
) -> IntegrityAdmission:
    if validation.decision != "admitted" or not validation.deployment_intent_allowed:
        raise ValueError("blocked integrity validation cannot create an admission")
    if validation.manifest_id != envelope.manifest.manifest_id:
        raise ValueError("validation does not match signed trust manifest")
    return IntegrityAdmission(
        schema_version="evm.scenario_e_integrity_admission.v1",
        decision="admitted",
        manifest_id=envelope.manifest.manifest_id,
        identity_fingerprint=validation.identity_fingerprint,
        identity=envelope.manifest.identity,
        signed_manifest_uri=signed_manifest_uri or str(signed_manifest_path.resolve()),
        signed_manifest_sha256=sha256_file(signed_manifest_path),
        validation_uri=validation_uri or str(validation_path.resolve()),
        validation_sha256=sha256_file(validation_path),
        source_revision=source_revision,
        issued_at=validation.evaluated_at,
        expires_at=envelope.manifest.expires_at,
    )


def validate_integrity_admission(
    admission_path: Path,
    *,
    public_key_path: Path,
    expected_candidate_id: str,
    expected_dataset_version: str,
    expected_model_digest: str,
    expected_image_digest: str,
    now: datetime | None = None,
) -> list[str]:
    from evm.control_panel.readiness_evaluator import runtime_path

    evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
    if not admission_path.is_file():
        return ["integrity_admission_missing"]
    try:
        admission = IntegrityAdmission.model_validate_json(
            admission_path.read_text(encoding="utf-8-sig")
        )
        signed_path = runtime_path(admission.signed_manifest_uri)
        validation_path = runtime_path(admission.validation_uri)
        envelope = SignedTrustManifest.model_validate_json(
            signed_path.read_text(encoding="utf-8-sig")
        )
        validation = IntegrityValidation.model_validate_json(
            validation_path.read_text(encoding="utf-8-sig")
        )
        public_key_pem = public_key_path.read_bytes()
    except (OSError, UnicodeDecodeError, ValueError):
        return ["integrity_admission_malformed"]

    blockers: list[str] = []
    if evaluated_at < admission.issued_at or evaluated_at >= admission.expires_at:
        blockers.append("integrity_admission_stale")
    if sha256_file(signed_path) != admission.signed_manifest_sha256:
        blockers.append("integrity_admission_evidence_mismatch")
    if sha256_file(validation_path) != admission.validation_sha256:
        blockers.append("integrity_admission_evidence_mismatch")
    if verify_signed_manifest(envelope, public_key_pem):
        blockers.append("integrity_admission_signature_invalid")
    if (
        validation.decision != "admitted"
        or not validation.deployment_intent_allowed
        or validation.manifest_id != admission.manifest_id
        or validation.identity_fingerprint != admission.identity_fingerprint
        or envelope.manifest.manifest_id != admission.manifest_id
        or envelope.manifest.identity != admission.identity
        or envelope.manifest.validator_source_revision != admission.source_revision
        or admission.expires_at != envelope.manifest.expires_at
        or admission.issued_at != validation.evaluated_at
    ):
        blockers.append("integrity_admission_evidence_mismatch")
    if evaluated_at < envelope.manifest.issued_at or evaluated_at >= envelope.manifest.expires_at:
        blockers.append("integrity_admission_stale")
    identity = admission.identity
    if (
        identity.candidate_id != expected_candidate_id
        or identity.dataset_version != expected_dataset_version
        or identity.model_digest != expected_model_digest
        or identity.container_image_digest != expected_image_digest
    ):
        blockers.append("integrity_admission_identity_mismatch")
    model_file = next(
        (
            runtime_path(item.path)
            for item in envelope.manifest.files
            if item.role == "model_artifact"
        ),
        None,
    )
    if model_file is None or not model_file.is_file() or sha256_file(model_file) != identity.model_digest:
        blockers.append("integrity_admission_identity_mismatch")
    return sorted(set(blockers))
