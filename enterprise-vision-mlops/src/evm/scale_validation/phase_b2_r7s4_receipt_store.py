"""Canonical local one-shot receipt store for the pre-r8 r7s4 framework.

The store is intentionally not a global or hostile-administrator authority. It
adds a fail-closed local replay latch after an independent verifier has already
authenticated exact receipt and approval-request bytes.
"""

from __future__ import annotations

import hashlib
import ntpath
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol

from evm.scale_validation.phase_b2_r7s4_authority import (
    ReceiptExpectation,
    VerifiedExternalReceipt,
    _revalidate_external_receipt_for_consumption_for_test,
    canonical_json_bytes,
    revalidate_external_receipt_for_consumption,
)


CONSUMPTION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.receipt-consumption.v1"
CANONICAL_RECEIPT_CONSUMPTION_ROOT = (
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s4-receipt-consumption"
)
ZERO_DOWNSTREAM_CALLS = {
    "repo_reads": 0,
    "process_spawn": 0,
    "service_calls": 0,
    "live_wsl": 0,
    "r8": 0,
    "automatic_retry": 0,
    "force_kill": 0,
}
HEX32_RE = re.compile(r"[0-9a-f]{32}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
SID_RE = re.compile(r"S-\d+(?:-\d+)+")
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
PUBLICATION_FIELDS = {
    "final_path",
    "temporary_leaf",
    "sha256",
    "bytes",
    "identity",
    "directory_identity",
    "file_flush_count",
    "directory_flush_count",
    "directory_flush_succeeded",
    "replace_if_exists",
    "same_handle_readback",
    "file_identity_stable_across_rename",
    "power_loss_durability_proven",
    "same_token_hostile_admin_protected",
    "go_evidence_eligible",
}
IDENTITY_FIELDS = {
    "final_path",
    "volume_serial_number",
    "file_id_hex",
    "size",
    "link_count",
    "attributes",
    "reparse_tag",
    "file_type",
    "owner_sid",
    "security_descriptor_sha256",
    "dacl_present",
    "dacl_protected",
}


class R7S4ReceiptStoreError(RuntimeError):
    """Raised when local one-shot consumption cannot be proven."""


class ReceiptPublicationAmbiguousError(R7S4ReceiptStoreError):
    """Publication may be partial or already committed; retry is forbidden."""

    manual_intervention_required = True
    automatic_retry_allowed = False
    downstream_calls_allowed = False
    downstream_call_counts = ZERO_DOWNSTREAM_CALLS


@dataclass(frozen=True, slots=True)
class ReceiptConsumptionResult:
    """Read-back evidence for one local, production-ineligible consumption."""

    marker_leaf: str
    execution_identity_sha256: str
    raw: bytes
    sha256: str
    bytes: int
    publication: Mapping[str, Any]


class BoundReceiptPublisher(Protocol):
    """Small seam implemented by r7s4 handle-bound publication."""

    def publish(
        self,
        *,
        directory: str,
        final_leaf: str,
        raw: bytes,
        run_uuid: str,
    ) -> object: ...


class _LazyR7S4HandleBoundPublisher:
    def publish(
        self,
        *,
        directory: str,
        final_leaf: str,
        raw: bytes,
        run_uuid: str,
    ) -> object:
        try:
            from evm.scale_validation.phase_b2_r7s4_handle_io import (
                publish_bound_no_replace_durable,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise R7S4ReceiptStoreError("r7s4_handle_bound_publisher_unavailable") from exc
        return publish_bound_no_replace_durable(
            directory,
            final_leaf,
            raw,
            run_uuid=run_uuid,
            require_protected_dacl=True,
        )


def _publication_mapping(publication: object) -> dict[str, Any]:
    if hasattr(publication, "to_dict"):
        value = publication.to_dict()
    elif isinstance(publication, Mapping):
        value = dict(publication)
    else:
        raise R7S4ReceiptStoreError("receipt_publication_evidence_object_required")
    if not isinstance(value, Mapping):
        raise R7S4ReceiptStoreError("receipt_publication_evidence_mapping_required")
    result = dict(value)
    if set(result) != PUBLICATION_FIELDS:
        raise R7S4ReceiptStoreError("receipt_publication_evidence_fields_mismatch")
    return result


def _normal_windows_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or not ntpath.isabs(value):
        raise R7S4ReceiptStoreError(f"{label}_absolute_windows_path_required")
    normalized = value
    if normalized.startswith("\\\\?\\UNC\\"):
        normalized = "\\\\" + normalized[8:]
    elif normalized.startswith("\\\\?\\"):
        normalized = normalized[4:]
    return ntpath.normcase(ntpath.normpath(normalized))


def _identity_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != IDENTITY_FIELDS:
        raise R7S4ReceiptStoreError(f"{label}_fields_mismatch")
    identity = dict(value)
    for name in ("volume_serial_number", "size", "link_count", "attributes", "reparse_tag"):
        item = identity[name]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise R7S4ReceiptStoreError(f"{label}_{name}_invalid")
    if identity["volume_serial_number"] == 0:
        raise R7S4ReceiptStoreError(f"{label}_volume_serial_number_invalid")
    if type(identity["file_type"]) is not int or identity["file_type"] != 1:
        raise R7S4ReceiptStoreError(f"{label}_file_type_invalid")
    if (
        not isinstance(identity["file_id_hex"], str)
        or HEX32_RE.fullmatch(identity["file_id_hex"]) is None
    ):
        raise R7S4ReceiptStoreError(f"{label}_file_id_invalid")
    if (
        not isinstance(identity["security_descriptor_sha256"], str)
        or HEX64_RE.fullmatch(identity["security_descriptor_sha256"]) is None
    ):
        raise R7S4ReceiptStoreError(f"{label}_security_descriptor_invalid")
    if (
        not isinstance(identity["owner_sid"], str)
        or SID_RE.fullmatch(identity["owner_sid"]) is None
    ):
        raise R7S4ReceiptStoreError(f"{label}_owner_sid_invalid")
    if identity["dacl_present"] is not True or identity["dacl_protected"] is not True:
        raise R7S4ReceiptStoreError(f"{label}_dacl_not_present_and_protected")
    return identity


def _validate_publication_contract(
    publication_object: object,
    *,
    raw: bytes,
    marker_leaf: str,
    run_uuid: str,
) -> dict[str, Any]:
    publication = _publication_mapping(publication_object)
    expected_path = ntpath.join(CANONICAL_RECEIPT_CONSUMPTION_ROOT, marker_leaf)
    expected_temporary_leaf = f".{marker_leaf}.{run_uuid}.partial"
    if (
        not isinstance(publication["sha256"], str)
        or HEX64_RE.fullmatch(publication["sha256"]) is None
        or publication["sha256"] != hashlib.sha256(raw).hexdigest()
    ):
        raise R7S4ReceiptStoreError("receipt_publication_sha256_mismatch")
    if type(publication["bytes"]) is not int or publication["bytes"] != len(raw):
        raise R7S4ReceiptStoreError("receipt_publication_bytes_mismatch")
    if not isinstance(publication["temporary_leaf"], str):
        raise R7S4ReceiptStoreError("receipt_publication_temporary_leaf_mismatch")
    if _normal_windows_path(publication["final_path"], "receipt_publication_final_path") != (
        _normal_windows_path(expected_path, "expected_receipt_publication_final_path")
    ):
        raise R7S4ReceiptStoreError("receipt_publication_path_mismatch")
    if publication["temporary_leaf"] != expected_temporary_leaf:
        raise R7S4ReceiptStoreError("receipt_publication_temporary_leaf_mismatch")
    exact_contract = {
        "file_flush_count": 2,
        "directory_flush_count": 1,
        "directory_flush_succeeded": True,
        "replace_if_exists": False,
        "same_handle_readback": True,
        "file_identity_stable_across_rename": True,
        "power_loss_durability_proven": False,
        "same_token_hostile_admin_protected": False,
        "go_evidence_eligible": False,
    }
    for field, expected_value in exact_contract.items():
        actual_value = publication[field]
        if isinstance(expected_value, bool):
            matches = actual_value is expected_value
        else:
            matches = type(actual_value) is int and actual_value == expected_value
        if not matches:
            raise R7S4ReceiptStoreError(f"receipt_publication_contract_mismatch:{field}")

    file_identity = _identity_mapping(publication["identity"], "receipt_publication_file_identity")
    directory_identity = _identity_mapping(
        publication["directory_identity"], "receipt_publication_directory_identity"
    )
    if _normal_windows_path(file_identity["final_path"], "receipt_file_identity_path") != (
        _normal_windows_path(expected_path, "expected_receipt_file_identity_path")
    ):
        raise R7S4ReceiptStoreError("receipt_publication_file_identity_path_mismatch")
    if _normal_windows_path(
        directory_identity["final_path"], "receipt_directory_identity_path"
    ) != _normal_windows_path(
        CANONICAL_RECEIPT_CONSUMPTION_ROOT, "expected_receipt_directory_identity_path"
    ):
        raise R7S4ReceiptStoreError("receipt_publication_directory_identity_path_mismatch")
    if (
        file_identity["size"] != len(raw)
        or file_identity["link_count"] != 1
        or file_identity["reparse_tag"] != 0
        or file_identity["attributes"] & FILE_ATTRIBUTE_DIRECTORY
    ):
        raise R7S4ReceiptStoreError("receipt_publication_file_identity_invariant_mismatch")
    if (
        directory_identity["link_count"] < 1
        or directory_identity["reparse_tag"] != 0
        or not directory_identity["attributes"] & FILE_ATTRIBUTE_DIRECTORY
    ):
        raise R7S4ReceiptStoreError("receipt_publication_directory_identity_invariant_mismatch")
    if (
        file_identity["volume_serial_number"] != directory_identity["volume_serial_number"]
        or file_identity["file_id_hex"] == directory_identity["file_id_hex"]
    ):
        raise R7S4ReceiptStoreError("receipt_publication_file_directory_identity_mismatch")
    return publication


def _publish_consumption_record(
    capability: VerifiedExternalReceipt,
    *,
    observed_time: datetime,
    publisher: BoundReceiptPublisher,
) -> ReceiptConsumptionResult:
    now = observed_time.astimezone(UTC)
    execution_identity = {
        "global_run_id": capability.global_run_id,
        "domain_run_id": capability.domain_run_id,
        "domain": capability.domain,
        "run_uuid": capability.run_uuid,
        "attempt_uuid": capability.attempt_uuid,
        "execution_mode": capability.execution_mode,
    }
    execution_identity_raw = canonical_json_bytes(execution_identity)
    execution_identity_sha = hashlib.sha256(execution_identity_raw).hexdigest()
    marker_leaf = f"r7s4-{execution_identity_sha}.json"
    record = {
        "schema": CONSUMPTION_SCHEMA,
        "status": "consumed_once_local_append_only",
        "execution_identity": execution_identity,
        "execution_identity_sha256": execution_identity_sha,
        "request_binding": {
            "approval_request_sha256": capability.approval_request_sha256,
            "approval_request_id": capability.approval_request_id,
            "subject_sha256": capability.subject_sha256,
        },
        "approval_instance": {
            "receipt_sha256": capability.receipt_sha256,
            "approval_id": capability.approval_id,
            "reviewer_identity": capability.reviewer_identity,
            "authority_key_id": capability.authority_key_id,
            "verifier_identity": capability.verifier_identity,
        },
        "consumed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "independent_authority_verified": True,
        "local_one_shot_consumed": True,
        "production_entry_enabled": False,
        "same_token_hostile_admin_protected": False,
        "multi_host_global_one_shot_provided": False,
        "call_counts_before_publication": dict(ZERO_DOWNSTREAM_CALLS),
    }
    raw = canonical_json_bytes(record)
    try:
        publication_object = publisher.publish(
            directory=CANONICAL_RECEIPT_CONSUMPTION_ROOT,
            final_leaf=marker_leaf,
            raw=raw,
            run_uuid=capability.run_uuid,
        )
        publication = _validate_publication_contract(
            publication_object,
            raw=raw,
            marker_leaf=marker_leaf,
            run_uuid=capability.run_uuid,
        )
    except Exception as exc:
        if isinstance(exc, ReceiptPublicationAmbiguousError):
            raise
        raise ReceiptPublicationAmbiguousError(
            "receipt_consumption_publication_ambiguous_manual_intervention_required"
        ) from exc
    return ReceiptConsumptionResult(
        marker_leaf=marker_leaf,
        execution_identity_sha256=execution_identity_sha,
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
        publication=publication,
    )


def consume_external_receipt_once(
    receipt_raw: bytes,
    approval_request_raw: bytes,
    verified: VerifiedExternalReceipt,
    *,
    expected: ReceiptExpectation,
) -> ReceiptConsumptionResult:
    """Consume at the sole canonical root using the r7s4 handle-bound publisher.

    There is intentionally no root or publisher argument on this production-facing
    function. Any ambiguous result forbids retry and every downstream call.
    """

    capability = revalidate_external_receipt_for_consumption(
        verified,
        receipt_raw,
        approval_request_raw,
        expected=expected,
    )
    return _publish_consumption_record(
        capability,
        observed_time=datetime.now(UTC),
        publisher=_LazyR7S4HandleBoundPublisher(),
    )


def _consume_external_receipt_once_for_test(
    receipt_raw: bytes,
    approval_request_raw: bytes,
    verified: VerifiedExternalReceipt,
    *,
    expected: ReceiptExpectation,
    validation_time: datetime,
    publisher: BoundReceiptPublisher,
) -> ReceiptConsumptionResult:
    """Private dependency seam; never used by the production root gate."""

    capability = _revalidate_external_receipt_for_consumption_for_test(
        verified,
        receipt_raw,
        approval_request_raw,
        expected=expected,
        validation_time=validation_time,
    )
    return _publish_consumption_record(
        capability,
        observed_time=validation_time,
        publisher=publisher,
    )


def receipt_store_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.receipt-store-contract.v1",
        "canonical_root": CANONICAL_RECEIPT_CONSUMPTION_ROOT,
        "caller_selectable_root": False,
        "handle_bound_publisher_required": True,
        "publication_evidence_exact_fields": sorted(PUBLICATION_FIELDS),
        "file_flush_count_required": 2,
        "directory_flush_count_required": 1,
        "temporary_leaf_bound_to_run_uuid": True,
        "marker_collision_key_scope": [
            "global_run_id",
            "domain_run_id",
            "domain",
            "run_uuid",
            "attempt_uuid",
            "execution_mode",
        ],
        "marker_collision_key_excludes_request_subject_receipt_approval": True,
        "same_execution_identity_reissued_request_or_receipt_collides": True,
        "file_link_count_required": 1,
        "file_and_directory_identity_readback_required": True,
        "file_and_directory_volume_serial_positive_required": True,
        "replace_if_exists_required": False,
        "same_handle_readback_required": True,
        "file_identity_stable_across_rename_required": True,
        "power_loss_durability_may_be_claimed": False,
        "go_evidence_may_be_claimed": False,
        "automatic_retry_allowed": False,
        "downstream_after_ambiguous_publication_allowed": False,
        "same_token_hostile_admin_protected": False,
        "multi_host_global_one_shot_provided": False,
        "production_entry_enabled": False,
        "public_caller_validation_time_allowed": False,
        "production_clock_source": "system_wall_clock",
    }


__all__ = [
    "CANONICAL_RECEIPT_CONSUMPTION_ROOT",
    "CONSUMPTION_SCHEMA",
    "R7S4ReceiptStoreError",
    "ReceiptConsumptionResult",
    "ReceiptPublicationAmbiguousError",
    "consume_external_receipt_once",
    "receipt_store_contract",
]
