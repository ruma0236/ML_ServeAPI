"""Fail-closed external-authority boundary for the pre-r8 r7s4 candidate.

This module validates bytes and an attestation returned by an external verifier.
It does not provide that verifier, a reviewer key, or a production entry point.
In particular, a caller-provided digest is never treated as independent authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol


APPROVAL_REQUEST_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.approval-request.v1"
APPROVAL_RECEIPT_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.external-approval.v1"
EXTERNAL_ATTESTATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.external-verifier-attestation.v1"
APPROVAL_TTL_SECONDS = 1_800
HEX40_RE = re.compile(r"[0-9a-f]{40}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,191}")
LOCAL_AUTHORITY_MECHANISMS = {
    "caller_sha256",
    "jira",
    "local_self_sign",
    "notion",
    "reviewer_text",
}

# These are statement-of-fact boundaries, not feature switches.
PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED = False
PRODUCTION_RECEIPT_ACCEPTANCE_ENABLED = False
SAME_TOKEN_HOSTILE_ADMIN_PROTECTED = False
MULTI_HOST_GLOBAL_ONE_SHOT_PROVIDED = False
PRODUCTION_AUTHORITY_BLOCKERS = (
    "independent_external_trust_root_unavailable",
    "verifier_identity_allowlist_not_implemented",
    "authority_key_allowlist_not_implemented",
    "reviewer_key_revocation_not_implemented",
)


class R7S4AuthorityError(RuntimeError):
    """Raised when receipt provenance or an exact binding is not proven."""


@dataclass(frozen=True, slots=True)
class ReceiptExpectation:
    """Exact run identity that the external receipt must authorize."""

    approval_request_id: str
    global_run_id: str
    domain_run_id: str
    domain: str
    run_uuid: str
    attempt_uuid: str
    execution_mode: str


@dataclass(frozen=True, slots=True)
class ExternalAuthorityAttestation:
    """Result returned by an independently implemented verifier.

    Merely constructing this value is not authorization. ``verify_external_receipt``
    binds it to canonical receipt/request bytes and returns the sealed capability
    consumed by the local framework.
    """

    schema: str
    status: str
    receipt_sha256: str
    approval_request_sha256: str
    subject_sha256: str
    approval_id: str
    reviewer_identity: str
    authority_key_id: str
    verifier_identity: str
    independent_authority_verified: bool
    authorize_exact_candidate_once: bool


@dataclass(frozen=True, slots=True)
class VerifiedExternalReceipt:
    """Factory-sealed exact receipt binding; never accepted as a Mapping."""

    receipt_sha256: str
    approval_request_sha256: str
    subject_sha256: str
    approval_request_id: str
    approval_id: str
    reviewer_identity: str
    authority_key_id: str
    verifier_identity: str
    global_run_id: str
    domain_run_id: str
    domain: str
    run_uuid: str
    attempt_uuid: str
    execution_mode: str
    issued_at_utc: str
    expires_at_utc: str
    verified_at_utc: str
    independent_authority_verified: bool
    authorize_exact_candidate_once: bool
    production_entry_enabled: bool = False
    same_token_hostile_admin_protected: bool = False
    multi_host_global_one_shot_provided: bool = False
    _integrity_mac: str = field(default="", repr=False, compare=False)


class ExternalReceiptVerifier(Protocol):
    """Adapter supplied by an out-of-band authority, not by CLI data."""

    def verify(
        self,
        *,
        receipt_raw: bytes,
        approval_request_raw: bytes,
        receipt_sha256: str,
        approval_request_sha256: str,
        subject_sha256: str,
    ) -> ExternalAuthorityAttestation: ...


_RESULT_MAC_KEY = os.urandom(32)


def canonical_json_bytes(value: Any) -> bytes:
    """Return the sole accepted UTF-8/LF JSON representation."""

    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise R7S4AuthorityError("canonical_json_value_rejected") from exc
    return (text + "\n").encode("utf-8")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise R7S4AuthorityError(f"canonical_json_duplicate_key:{key}")
        value[key] = item
    return value


def _reject_nonfinite(value: Any, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise R7S4AuthorityError(f"{label}_nonfinite_number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_nonfinite(item, f"{label}[{index}]")


def strict_canonical_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    """Decode strict canonical JSON and reject duplicate keys, BOM, CR, and NaN."""

    if not isinstance(raw, bytes) or not raw:
        raise R7S4AuthorityError(f"{label}_bytes_required")
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
        raise R7S4AuthorityError(f"{label}_utf8_lf_required")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise R7S4AuthorityError(f"{label}_single_terminal_lf_required")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise R7S4AuthorityError(f"{label}_utf8_required") from exc

    def reject_constant(value: str) -> None:
        raise R7S4AuthorityError(f"{label}_nonfinite_number:{value}")

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=reject_constant,
        )
    except R7S4AuthorityError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise R7S4AuthorityError(f"{label}_json_invalid") from exc
    if not isinstance(parsed, dict):
        raise R7S4AuthorityError(f"{label}_object_required")
    _reject_nonfinite(parsed, label)
    if canonical_json_bytes(parsed) != raw:
        raise R7S4AuthorityError(f"{label}_noncanonical_bytes")
    return parsed


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = ",".join(sorted(expected - actual)) or "none"
        unknown = ",".join(sorted(actual - expected)) or "none"
        raise R7S4AuthorityError(f"{label}_keys_mismatch:missing={missing}:unknown={unknown}")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R7S4AuthorityError(f"{label}_object_required")
    return dict(value)


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise R7S4AuthorityError(f"{label}_nonempty_string_required")
    return value


def _hex(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise R7S4AuthorityError(f"{label}_invalid")
    return value


def _canonical_uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise R7S4AuthorityError(f"{label}_uuid_required")
    try:
        canonical = str(uuid.UUID(value))
    except ValueError as exc:
        raise R7S4AuthorityError(f"{label}_uuid_invalid") from exc
    if value != canonical:
        raise R7S4AuthorityError(f"{label}_uuid_noncanonical")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise R7S4AuthorityError(f"{label}_canonical_utc_required")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise R7S4AuthorityError(f"{label}_time_invalid") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise R7S4AuthorityError(f"{label}_utc_required")
    canonical = parsed.astimezone(UTC)
    if _canonical_utc(canonical) != value:
        raise R7S4AuthorityError(f"{label}_exact_canonical_utc_required")
    return canonical


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_expectation(value: ReceiptExpectation) -> ReceiptExpectation:
    if type(value) is not ReceiptExpectation:
        raise R7S4AuthorityError("receipt_expectation_typed_value_required")
    request_id = _nonempty(value.approval_request_id, "expected_approval_request_id")
    if SAFE_ID_RE.fullmatch(request_id) is None:
        raise R7S4AuthorityError("expected_approval_request_id_invalid")
    global_run_id = _nonempty(value.global_run_id, "expected_global_run_id")
    domain = _nonempty(value.domain, "expected_domain")
    if value.domain_run_id != f"{global_run_id}-{domain}":
        raise R7S4AuthorityError("expected_domain_run_id_binding_mismatch")
    _canonical_uuid(value.run_uuid, "expected_run_uuid")
    _canonical_uuid(value.attempt_uuid, "expected_attempt_uuid")
    if value.run_uuid == value.attempt_uuid:
        raise R7S4AuthorityError("expected_run_and_attempt_uuid_must_differ")
    _nonempty(value.execution_mode, "expected_execution_mode")
    return value


def _validate_pin(value: Any, label: str) -> dict[str, Any]:
    pin = _mapping(value, label)
    _exact_keys(pin, {"path", "sha256", "bytes"}, label)
    _nonempty(pin["path"], f"{label}_path")
    _hex(pin["sha256"], HEX64_RE, f"{label}_sha256")
    if isinstance(pin["bytes"], bool) or not isinstance(pin["bytes"], int) or pin["bytes"] < 1:
        raise R7S4AuthorityError(f"{label}_bytes_positive_integer_required")
    return pin


def _validate_run_identity(value: Any, expected: ReceiptExpectation, label: str) -> dict[str, Any]:
    identity = _mapping(value, label)
    _exact_keys(
        identity,
        {
            "global_run_id",
            "domain_run_id",
            "domain",
            "run_uuid",
            "attempt_uuid",
            "execution_mode",
        },
        label,
    )
    wanted = {
        "global_run_id": expected.global_run_id,
        "domain_run_id": expected.domain_run_id,
        "domain": expected.domain,
        "run_uuid": expected.run_uuid,
        "attempt_uuid": expected.attempt_uuid,
        "execution_mode": expected.execution_mode,
    }
    if identity != wanted:
        raise R7S4AuthorityError(f"{label}_exact_binding_mismatch")
    return identity


def _validate_documents(
    receipt_raw: bytes,
    approval_request_raw: bytes,
    *,
    expected: ReceiptExpectation,
    validation_time: datetime,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], datetime, datetime]:
    expected = _validate_expectation(expected)
    request = strict_canonical_json_bytes(approval_request_raw, "approval_request")
    _exact_keys(
        request,
        {
            "schema",
            "status",
            "decision",
            "approval_request_id",
            "created_at_utc",
            "expires_at_utc",
            "subject",
            "production_entry_enabled",
        },
        "approval_request",
    )
    if (
        request["schema"] != APPROVAL_REQUEST_SCHEMA
        or request["status"] != "review_pending"
        or request["decision"] != "not_approved"
        or request["approval_request_id"] != expected.approval_request_id
        or request["production_entry_enabled"] is not False
    ):
        raise R7S4AuthorityError("approval_request_fail_closed_state_or_id_mismatch")
    request_created = _utc(request["created_at_utc"], "approval_request_created")
    request_expires = _utc(request["expires_at_utc"], "approval_request_expires")
    if (request_expires - request_created).total_seconds() != APPROVAL_TTL_SECONDS:
        raise R7S4AuthorityError("approval_request_ttl_exact_mismatch")

    subject = _mapping(request["subject"], "approval_request_subject")
    _exact_keys(
        subject,
        {
            "bootstrap",
            "bootstrap_argv",
            "work_order",
            "root_orchestrator",
            "canonical_revision",
            "run_identity",
        },
        "approval_request_subject",
    )
    for role in ("bootstrap", "bootstrap_argv", "work_order", "root_orchestrator"):
        _validate_pin(subject[role], f"approval_request_{role}")
    revision = _mapping(subject["canonical_revision"], "approval_request_revision")
    _exact_keys(revision, {"commit", "tree"}, "approval_request_revision")
    _hex(revision["commit"], HEX40_RE, "approval_request_commit")
    _hex(revision["tree"], HEX40_RE, "approval_request_tree")
    run_identity = _validate_run_identity(
        subject["run_identity"], expected, "approval_request_run_identity"
    )

    receipt = strict_canonical_json_bytes(receipt_raw, "external_receipt")
    _exact_keys(
        receipt,
        {
            "schema",
            "status",
            "decision",
            "approval_request_id",
            "issued_at_utc",
            "expires_at_utc",
            "authority",
            "approval_request",
            "subject",
            "run_identity",
        },
        "external_receipt",
    )
    if (
        receipt["schema"] != APPROVAL_RECEIPT_SCHEMA
        or receipt["status"] != "approved"
        or receipt["decision"] != "approve_exact_candidate_once"
        or receipt["approval_request_id"] != expected.approval_request_id
        or receipt["subject"] != subject
        or receipt["run_identity"] != run_identity
    ):
        raise R7S4AuthorityError("external_receipt_subject_decision_or_identity_mismatch")
    authority = _mapping(receipt["authority"], "external_receipt_authority")
    _exact_keys(
        authority,
        {"mechanism", "reviewer_identity", "approval_id", "key_id"},
        "external_receipt_authority",
    )
    mechanism = _nonempty(authority["mechanism"], "external_receipt_authority_mechanism")
    if mechanism in LOCAL_AUTHORITY_MECHANISMS or mechanism != "independent_external_verifier":
        raise R7S4AuthorityError("external_receipt_authority_mechanism_not_independent")
    for name in ("reviewer_identity", "approval_id", "key_id"):
        text = _nonempty(authority[name], f"external_receipt_{name}")
        if SAFE_ID_RE.fullmatch(text) is None:
            raise R7S4AuthorityError(f"external_receipt_{name}_invalid")

    request_pin = _mapping(receipt["approval_request"], "external_receipt_request_pin")
    _exact_keys(request_pin, {"sha256", "bytes"}, "external_receipt_request_pin")
    request_sha = hashlib.sha256(approval_request_raw).hexdigest()
    if request_pin != {"sha256": request_sha, "bytes": len(approval_request_raw)}:
        raise R7S4AuthorityError("external_receipt_approval_request_pin_mismatch")

    issued = _utc(receipt["issued_at_utc"], "external_receipt_issued")
    expires = _utc(receipt["expires_at_utc"], "external_receipt_expires")
    now = validation_time.astimezone(UTC)
    if issued < request_created or issued > now:
        raise R7S4AuthorityError("external_receipt_issuance_time_invalid")
    if expires <= issued or expires > request_expires or now >= expires:
        raise R7S4AuthorityError("external_receipt_expired_or_window_invalid")
    return request, receipt, subject, issued, expires


def _verified_payload(value: VerifiedExternalReceipt) -> dict[str, Any]:
    payload = asdict(value)
    payload.pop("_integrity_mac", None)
    return payload


def _mac_for_verified(value: VerifiedExternalReceipt) -> str:
    return hmac.new(
        _RESULT_MAC_KEY, canonical_json_bytes(_verified_payload(value)), hashlib.sha256
    ).hexdigest()


def require_verified_external_receipt(value: object) -> VerifiedExternalReceipt:
    """Reject arbitrary mappings, hand-built objects, and mutated sealed results."""

    if type(value) is not VerifiedExternalReceipt:
        raise R7S4AuthorityError("typed_external_verifier_result_required")
    assert isinstance(value, VerifiedExternalReceipt)
    if (
        not isinstance(value._integrity_mac, str)
        or HEX64_RE.fullmatch(value._integrity_mac) is None
        or not hmac.compare_digest(value._integrity_mac, _mac_for_verified(value))
    ):
        raise R7S4AuthorityError("external_verifier_result_integrity_mismatch")
    if (
        value.independent_authority_verified is not True
        or value.authorize_exact_candidate_once is not True
        or value.production_entry_enabled is not False
        or value.same_token_hostile_admin_protected is not False
        or value.multi_host_global_one_shot_provided is not False
    ):
        raise R7S4AuthorityError("external_verifier_result_boundary_mismatch")
    return value


def _verify_external_receipt_at_time(
    receipt_raw: bytes,
    approval_request_raw: bytes,
    *,
    expected: ReceiptExpectation,
    verifier: ExternalReceiptVerifier,
    observed_time: datetime,
) -> VerifiedExternalReceipt:
    """Validate exact bytes and bind them to one external-verifier response.

    There is deliberately no ``expected_receipt_sha256`` argument. The verifier
    receives locally computed hashes and must authenticate the raw bytes through
    its own out-of-band trust root.
    """

    if verifier is None:
        raise R7S4AuthorityError("external_verifier_required")
    now = observed_time.astimezone(UTC)
    _request, receipt, subject, issued, expires = _validate_documents(
        receipt_raw,
        approval_request_raw,
        expected=expected,
        validation_time=now,
    )
    receipt_sha = hashlib.sha256(receipt_raw).hexdigest()
    request_sha = hashlib.sha256(approval_request_raw).hexdigest()
    subject_sha = hashlib.sha256(canonical_json_bytes(subject)).hexdigest()
    attestation = verifier.verify(
        receipt_raw=receipt_raw,
        approval_request_raw=approval_request_raw,
        receipt_sha256=receipt_sha,
        approval_request_sha256=request_sha,
        subject_sha256=subject_sha,
    )
    if type(attestation) is not ExternalAuthorityAttestation:
        raise R7S4AuthorityError("typed_external_authority_attestation_required")
    assert isinstance(attestation, ExternalAuthorityAttestation)
    authority = receipt["authority"]
    expected_attestation = {
        "schema": EXTERNAL_ATTESTATION_SCHEMA,
        "status": "authenticated",
        "receipt_sha256": receipt_sha,
        "approval_request_sha256": request_sha,
        "subject_sha256": subject_sha,
        "approval_id": authority["approval_id"],
        "reviewer_identity": authority["reviewer_identity"],
        "authority_key_id": authority["key_id"],
        "independent_authority_verified": True,
        "authorize_exact_candidate_once": True,
    }
    actual_attestation = asdict(attestation)
    for name, value in expected_attestation.items():
        if actual_attestation[name] != value:
            raise R7S4AuthorityError(f"external_authority_attestation_mismatch:{name}")
    verifier_identity = _nonempty(attestation.verifier_identity, "external_verifier_identity")
    if SAFE_ID_RE.fullmatch(verifier_identity) is None:
        raise R7S4AuthorityError("external_verifier_identity_invalid")

    unsealed = VerifiedExternalReceipt(
        receipt_sha256=receipt_sha,
        approval_request_sha256=request_sha,
        subject_sha256=subject_sha,
        approval_request_id=expected.approval_request_id,
        approval_id=authority["approval_id"],
        reviewer_identity=authority["reviewer_identity"],
        authority_key_id=authority["key_id"],
        verifier_identity=verifier_identity,
        global_run_id=expected.global_run_id,
        domain_run_id=expected.domain_run_id,
        domain=expected.domain,
        run_uuid=expected.run_uuid,
        attempt_uuid=expected.attempt_uuid,
        execution_mode=expected.execution_mode,
        issued_at_utc=_canonical_utc(issued),
        expires_at_utc=_canonical_utc(expires),
        verified_at_utc=_canonical_utc(now),
        independent_authority_verified=True,
        authorize_exact_candidate_once=True,
    )
    sealed = VerifiedExternalReceipt(
        **_verified_payload(unsealed), _integrity_mac=_mac_for_verified(unsealed)
    )
    return require_verified_external_receipt(sealed)


def verify_external_receipt(
    receipt_raw: bytes,
    approval_request_raw: bytes,
    *,
    expected: ReceiptExpectation,
    verifier: ExternalReceiptVerifier,
) -> VerifiedExternalReceipt:
    """Fail closed until a repository-pinned external adapter is provisioned.

    A caller-provided object that implements ``ExternalReceiptVerifier`` is not
    an independent trust root.  Keeping that object injectable on this public
    entry point would let the same caller mint both the receipt and its alleged
    verification.  The deterministic private seam below remains available for
    contract tests, but it never enables production admission.
    """

    del receipt_raw, approval_request_raw, expected, verifier
    raise R7S4AuthorityError("production_external_authority_adapter_unprovisioned")


def _verify_external_receipt_for_test(
    receipt_raw: bytes,
    approval_request_raw: bytes,
    *,
    expected: ReceiptExpectation,
    verifier: ExternalReceiptVerifier,
    validation_time: datetime,
) -> VerifiedExternalReceipt:
    """Private deterministic-clock seam for tests; never a production entry."""

    return _verify_external_receipt_at_time(
        receipt_raw,
        approval_request_raw,
        expected=expected,
        verifier=verifier,
        observed_time=validation_time,
    )


def _revalidate_external_receipt_at_time(
    verified: object,
    receipt_raw: bytes,
    approval_request_raw: bytes,
    *,
    expected: ReceiptExpectation,
    observed_time: datetime,
) -> VerifiedExternalReceipt:
    """Re-bind raw bytes, identity, hashes, and TTL immediately before consumption."""

    capability = require_verified_external_receipt(verified)
    now = observed_time.astimezone(UTC)
    _request, receipt, subject, _issued, expires = _validate_documents(
        receipt_raw,
        approval_request_raw,
        expected=expected,
        validation_time=now,
    )
    actual = {
        "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "approval_request_sha256": hashlib.sha256(approval_request_raw).hexdigest(),
        "subject_sha256": hashlib.sha256(canonical_json_bytes(subject)).hexdigest(),
        "approval_request_id": expected.approval_request_id,
        "approval_id": receipt["authority"]["approval_id"],
        "reviewer_identity": receipt["authority"]["reviewer_identity"],
        "authority_key_id": receipt["authority"]["key_id"],
        "global_run_id": expected.global_run_id,
        "domain_run_id": expected.domain_run_id,
        "domain": expected.domain,
        "run_uuid": expected.run_uuid,
        "attempt_uuid": expected.attempt_uuid,
        "execution_mode": expected.execution_mode,
        "expires_at_utc": _canonical_utc(expires),
    }
    for name, value in actual.items():
        if getattr(capability, name) != value:
            raise R7S4AuthorityError(f"external_receipt_consumption_rebind_mismatch:{name}")
    verified_at = _utc(capability.verified_at_utc, "external_receipt_verified_at")
    if now < verified_at:
        raise R7S4AuthorityError("external_receipt_consumption_clock_backward")
    return capability


def revalidate_external_receipt_for_consumption(
    verified: object,
    receipt_raw: bytes,
    approval_request_raw: bytes,
    *,
    expected: ReceiptExpectation,
) -> VerifiedExternalReceipt:
    """Fail closed while the production external authority is unprovisioned."""

    del verified, receipt_raw, approval_request_raw, expected
    raise R7S4AuthorityError("production_external_authority_adapter_unprovisioned")


def _revalidate_external_receipt_for_consumption_for_test(
    verified: object,
    receipt_raw: bytes,
    approval_request_raw: bytes,
    *,
    expected: ReceiptExpectation,
    validation_time: datetime,
) -> VerifiedExternalReceipt:
    """Private deterministic-clock consumption seam for tests."""

    return _revalidate_external_receipt_at_time(
        verified,
        receipt_raw,
        approval_request_raw,
        expected=expected,
        observed_time=validation_time,
    )


def authority_contract() -> dict[str, Any]:
    """Machine-readable non-overclaim contract for the bounded local framework."""

    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.authority-contract.v1",
        "caller_supplied_receipt_sha_is_authority": False,
        "arbitrary_validation_mapping_accepted": False,
        "raw_bytes_revalidated_at_consumption": True,
        "production_external_authority_configured": PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED,
        "production_receipt_acceptance_enabled": PRODUCTION_RECEIPT_ACCEPTANCE_ENABLED,
        "verifier_identity_allowlist_implemented": False,
        "authority_key_allowlist_implemented": False,
        "independent_external_trust_root_available": False,
        "production_authority_blockers": list(PRODUCTION_AUTHORITY_BLOCKERS),
        "production_clock_source": "system_wall_clock",
        "caller_validation_time_allowed": False,
        "caller_supplied_verifier_allowed_on_public_entry": False,
        "public_entry_without_pinned_external_adapter": "fail_closed",
        "same_token_hostile_admin_protected": SAME_TOKEN_HOSTILE_ADMIN_PROTECTED,
        "multi_host_global_one_shot_provided": MULTI_HOST_GLOBAL_ONE_SHOT_PROVIDED,
    }


__all__ = [
    "APPROVAL_RECEIPT_SCHEMA",
    "APPROVAL_REQUEST_SCHEMA",
    "EXTERNAL_ATTESTATION_SCHEMA",
    "ExternalAuthorityAttestation",
    "ExternalReceiptVerifier",
    "R7S4AuthorityError",
    "ReceiptExpectation",
    "VerifiedExternalReceipt",
    "authority_contract",
    "canonical_json_bytes",
    "require_verified_external_receipt",
    "revalidate_external_receipt_for_consumption",
    "strict_canonical_json_bytes",
    "verify_external_receipt",
]
