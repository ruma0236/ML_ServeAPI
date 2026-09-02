"""Append-only r7s4 review evidence built on handle-bound publication.

All caller-owned JSON is serialized before the output directory is created.
The output directory and any successfully published files are preserved on
failure.  This writer has no retry or cleanup path and never creates a Phase B2
success/completion marker.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn

from evm.scale_validation import phase_b2_r7s3_handle_io as r7s3
from evm.scale_validation.phase_b2_r7s4_handle_io import (
    DurableBoundPublication,
    DurableHandleApi,
    DurablePublicationError,
    HandleBoundIoError,
    publish_bound_no_replace_durable,
    validate_strict_windows_leaf,
)


FORBIDDEN_SUCCESS_LEAVES = frozenset(
    {
        "completion-marker.json",
        "private-success-index.json",
        "success-index.json",
        "phase-b2-success.json",
    }
)
AGGREGATE_MANIFEST_LEAF = "aggregate-review-manifest.json"
AGGREGATE_INDEX_LEAF = "aggregate-review-index.json"
ATOMIC_FAILURE_SEAL_LEAF = "atomic-failure-seal.json"
EMERGENCY_SEAL_LEAF = "emergency-seal.json"
RESERVATION_FAILURE_SEAL_LEAF = "reservation-failure-seal.json"
RUN_RESERVATION_PREFIX = ".r7s4-run-"
RUN_RESERVATION_SUFFIX = ".reservation"
SID_RE = re.compile(r"S-\d+(?:-\d+)+\Z")
RESERVED_CONTROL_LEAVES = frozenset(
    {
        AGGREGATE_MANIFEST_LEAF,
        AGGREGATE_INDEX_LEAF,
        ATOMIC_FAILURE_SEAL_LEAF,
        EMERGENCY_SEAL_LEAF,
        RESERVATION_FAILURE_SEAL_LEAF,
    }
)


class R7S4EvidenceError(RuntimeError):
    """Base error for a fail-closed review-only evidence batch."""


class R7S4EvidencePublicationError(R7S4EvidenceError):
    """Publication failed after zero or more immutable files were created."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        output_directory: Path,
        run_reservation_directory: Path,
        attempted_leaf: str | None,
        publications: tuple[DurableBoundPublication, ...],
        failure_seal_directory: Path | None = None,
        failure_seal_publication: DurableBoundPublication | None = None,
        failure_seal_error_type: str | None = None,
        emergency_seal_directory: Path | None = None,
        emergency_seal_publication: DurableBoundPublication | None = None,
        emergency_seal_error_type: str | None = None,
        failure_seal_attempt_count: int = 0,
        emergency_seal_attempt_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.output_directory = output_directory
        self.run_reservation_directory = run_reservation_directory
        self.attempted_leaf = attempted_leaf
        self.publications = publications
        self.failure_seal_directory = failure_seal_directory
        self.failure_seal_attempt_count = failure_seal_attempt_count
        self.failure_seal_publication = failure_seal_publication
        self.failure_seal_error_type = failure_seal_error_type
        self.emergency_seal_attempt_count = emergency_seal_attempt_count
        self.emergency_seal_directory = emergency_seal_directory
        self.emergency_seal_publication = emergency_seal_publication
        self.emergency_seal_error_type = emergency_seal_error_type
        self.retry_count = 0
        self.automatic_retry_count = 0
        self.downstream_call_count = 0
        self.manual_intervention_required = True
        self.publication_outcome = "unknown"
        self.success_marker_created = False
        self.go_evidence_eligible = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.publication-failure.v1",
            "stage": self.stage,
            "output_directory": str(self.output_directory),
            "run_reservation_directory": str(self.run_reservation_directory),
            "attempted_leaf": self.attempted_leaf,
            "published": [item.to_dict() for item in self.publications],
            "failure_seal_directory": (
                str(self.failure_seal_directory)
                if self.failure_seal_directory is not None
                else None
            ),
            "failure_seal_attempt_count": self.failure_seal_attempt_count,
            "failure_seal_publication": (
                self.failure_seal_publication.to_dict()
                if self.failure_seal_publication is not None
                else None
            ),
            "failure_seal_error_type": self.failure_seal_error_type,
            "emergency_seal_attempt_count": self.emergency_seal_attempt_count,
            "emergency_seal_directory": (
                str(self.emergency_seal_directory)
                if self.emergency_seal_directory is not None
                else None
            ),
            "emergency_seal_publication": (
                self.emergency_seal_publication.to_dict()
                if self.emergency_seal_publication is not None
                else None
            ),
            "emergency_seal_error_type": self.emergency_seal_error_type,
            "retry_count": self.retry_count,
            "automatic_retry_count": self.automatic_retry_count,
            "downstream_call_count": self.downstream_call_count,
            "manual_intervention_required": self.manual_intervention_required,
            "publication_outcome": self.publication_outcome,
            "success_marker_created": self.success_marker_created,
            "go_evidence_eligible": self.go_evidence_eligible,
        }


@dataclass(frozen=True)
class PreparedJsonArtifact:
    leaf: str
    raw: bytes
    role: str = "document"


@dataclass(frozen=True)
class ReviewEvidenceBatch:
    output_directory: Path
    run_reservation_directory: Path
    run_reservation_identity_sha256: str
    run_uuid: str
    publications: tuple[DurableBoundPublication, ...]
    aggregate_manifest_publication: DurableBoundPublication
    aggregate_index_publication: DurableBoundPublication
    status: str = "review_pending"
    retry_count: int = 0
    success_marker_created: bool = False
    production_go_enabled: bool = False
    go_evidence_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.review-evidence-batch.v1",
            "output_directory": str(self.output_directory),
            "run_reservation_directory": str(self.run_reservation_directory),
            "run_reservation_identity_sha256": self.run_reservation_identity_sha256,
            "run_uuid": self.run_uuid,
            "status": self.status,
            "publications": [item.to_dict() for item in self.publications],
            "aggregate_manifest_publication": self.aggregate_manifest_publication.to_dict(),
            "aggregate_index_publication": self.aggregate_index_publication.to_dict(),
            "retry_count": self.retry_count,
            "success_marker_created": self.success_marker_created,
            "production_go_enabled": self.production_go_enabled,
            "go_evidence_eligible": self.go_evidence_eligible,
        }


Publisher = Callable[..., DurableBoundPublication]
ApiFactory = Callable[[str], DurableHandleApi]


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize strict canonical JSON and reject NaN or unsupported values."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _leaf(value: str, *, label: str) -> str:
    try:
        return validate_strict_windows_leaf(value)
    except (HandleBoundIoError, TypeError):
        raise R7S4EvidenceError(f"{label}_invalid")


def prepare_json_batch(documents: Mapping[str, Any]) -> tuple[PreparedJsonArtifact, ...]:
    """Validate names and serialize every document without touching disk."""

    if not isinstance(documents, Mapping) or not documents:
        raise R7S4EvidenceError("documents_nonempty_mapping_required")
    normalized: dict[str, str] = {}
    prepared: list[PreparedJsonArtifact] = []
    for requested_leaf, value in documents.items():
        leaf = _leaf(requested_leaf, label="document_leaf")
        comparable = ntpath.normcase(leaf)
        if comparable in normalized:
            raise R7S4EvidenceError("case_insensitive_document_leaf_collision")
        if comparable in {ntpath.normcase(item) for item in FORBIDDEN_SUCCESS_LEAVES}:
            raise R7S4EvidenceError("phase_b2_success_leaf_forbidden")
        if comparable in {ntpath.normcase(item) for item in RESERVED_CONTROL_LEAVES}:
            raise R7S4EvidenceError("review_control_leaf_reserved")
        normalized[comparable] = leaf
        prepared.append(PreparedJsonArtifact(leaf=leaf, raw=canonical_json_bytes(value)))
    return tuple(sorted(prepared, key=lambda item: ntpath.normcase(item.leaf)))


def _descriptor(artifact: PreparedJsonArtifact) -> dict[str, Any]:
    return {
        "leaf": artifact.leaf,
        "role": artifact.role,
        "sha256": hashlib.sha256(artifact.raw).hexdigest(),
        "bytes": len(artifact.raw),
    }


def _prepare_aggregate_artifacts(
    documents: tuple[PreparedJsonArtifact, ...],
    *,
    run_uuid: str,
    run_reservation: Mapping[str, Any],
) -> tuple[PreparedJsonArtifact, PreparedJsonArtifact]:
    document_descriptors = [_descriptor(item) for item in documents]
    manifest = PreparedJsonArtifact(
        leaf=AGGREGATE_MANIFEST_LEAF,
        role="aggregate_review_manifest",
        raw=canonical_json_bytes(
            {
                "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.aggregate-review-manifest.v1",
                "status": "review_pending",
                "run_uuid": run_uuid,
                "run_reservation": dict(run_reservation),
                "documents": document_descriptors,
                "publication_order": [item["leaf"] for item in document_descriptors],
                "completion_or_success_marker_created": False,
                "production_go_enabled": False,
                "go_evidence_eligible": False,
            }
        ),
    )
    index = PreparedJsonArtifact(
        leaf=AGGREGATE_INDEX_LEAF,
        role="aggregate_review_index",
        raw=canonical_json_bytes(
            {
                "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.aggregate-review-index.v1",
                "status": "review_pending",
                "run_uuid": run_uuid,
                "run_reservation": dict(run_reservation),
                "documents": document_descriptors,
                "aggregate_manifest": _descriptor(manifest),
                "index_is_final_review_artifact": True,
                "completion_or_success_marker_created": False,
                "production_go_enabled": False,
                "go_evidence_eligible": False,
            }
        ),
    )
    return manifest, index


def _exception_type(exc: BaseException) -> str:
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


def _host_path(value: str | os.PathLike[str]) -> str:
    return r7s3._comparable_path(os.path.abspath(os.fspath(value)))


def _run_reservation_leaf(run_uuid: str) -> str:
    return _leaf(
        f"{RUN_RESERVATION_PREFIX}{run_uuid}{RUN_RESERVATION_SUFFIX}",
        label="run_reservation_leaf",
    )


def _run_reservation_descriptor(parent: Path, run_uuid: str) -> dict[str, Any]:
    reservation_leaf = _run_reservation_leaf(run_uuid)
    reservation_path = parent / reservation_leaf
    identity_payload = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.run-reservation-identity.v1",
        "parent_directory": _host_path(parent),
        "reservation_leaf": reservation_leaf,
        "run_uuid": run_uuid,
    }
    return {
        **identity_payload,
        "reservation_path": str(reservation_path),
        "logical_identity_sha256": hashlib.sha256(
            canonical_json_bytes(identity_payload)
        ).hexdigest(),
        "exclusive_create_attempt_count": 1,
        "writer_preserves_after_success_or_failure": True,
        "writer_cleanup_or_removal_attempted": False,
        "scope": "resolved_parent_directory_only",
        "physical_handle_identity_proven": False,
        "power_loss_durability_proven": False,
        "same_token_deletion_protected": False,
        "global_one_shot_proven": False,
        "go_evidence_eligible": False,
    }


def _temporary_leaf(final_leaf: str, run_uuid: str) -> str:
    try:
        return validate_strict_windows_leaf(f".{final_leaf}.{run_uuid}.partial")
    except (HandleBoundIoError, TypeError):
        raise R7S4EvidenceError(f"temporary_leaf_not_feasible:{final_leaf}")


def _reservation_failure_directory_leaf(output_leaf: str, run_uuid: str) -> str:
    return _leaf(
        f".{output_leaf}.{run_uuid}.reservation-failure-seal",
        label="reservation_failure_directory_leaf",
    )


def _reservation_emergency_directory_leaf(output_leaf: str, run_uuid: str) -> str:
    return _leaf(
        f".{output_leaf}.{run_uuid}.reservation-emergency-seal",
        label="reservation_emergency_directory_leaf",
    )


def _publication_emergency_directory_leaf(output_leaf: str, run_uuid: str) -> str:
    return _leaf(
        f".{output_leaf}.{run_uuid}.emergency-seal",
        label="publication_emergency_directory_leaf",
    )


def _validate_planned_leaf_space(
    artifacts: tuple[PreparedJsonArtifact, ...],
    *,
    output_leaf: str,
    run_uuid: str,
) -> None:
    """Reject every deterministic leaf conflict before creating a directory."""

    final_leaves = [item.leaf for item in artifacts]
    artifact_names = {ntpath.normcase(item) for item in final_leaves}
    final_leaves.extend(
        item for item in RESERVED_CONTROL_LEAVES if ntpath.normcase(item) not in artifact_names
    )
    normalized_finals = {ntpath.normcase(item): item for item in final_leaves}
    if len(normalized_finals) != len(final_leaves):
        raise R7S4EvidenceError("planned_final_leaf_collision")

    normalized_temporaries: dict[str, str] = {}
    for final_leaf in final_leaves:
        temporary_leaf = _temporary_leaf(final_leaf, run_uuid)
        normalized = ntpath.normcase(temporary_leaf)
        if normalized in normalized_temporaries:
            raise R7S4EvidenceError("planned_temporary_leaf_collision")
        normalized_temporaries[normalized] = temporary_leaf

    overlap = set(normalized_finals).intersection(normalized_temporaries)
    if overlap:
        colliding = normalized_finals[sorted(overlap)[0]]
        raise R7S4EvidenceError(f"planned_final_temporary_leaf_collision:{colliding}")

    parent_control_leaves = {
        ntpath.normcase(_run_reservation_leaf(run_uuid)),
        ntpath.normcase(_reservation_failure_directory_leaf(output_leaf, run_uuid)),
        ntpath.normcase(_reservation_emergency_directory_leaf(output_leaf, run_uuid)),
        ntpath.normcase(_publication_emergency_directory_leaf(output_leaf, run_uuid)),
    }
    if len(parent_control_leaves) != 4 or ntpath.normcase(output_leaf) in parent_control_leaves:
        raise R7S4EvidenceError("output_leaf_parent_control_collision")


def _exact_lower_hex(value: str, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _exact_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _exact_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _valid_sid(value: object) -> bool:
    return isinstance(value, str) and SID_RE.fullmatch(value) is not None


def _validated_publication(
    publication: DurableBoundPublication,
    *,
    directory: Path,
    artifact: PreparedJsonArtifact,
    run_uuid: str,
) -> DurableBoundPublication:
    expected_path = _host_path(directory / artifact.leaf)
    expected_directory = _host_path(directory)
    expected_sha256 = hashlib.sha256(artifact.raw).hexdigest()
    file_identity = publication.identity
    directory_identity = publication.directory_identity
    if (
        not isinstance(publication.final_path, str)
        or _host_path(publication.final_path) != expected_path
        or not isinstance(publication.temporary_leaf, str)
        or publication.temporary_leaf != f".{artifact.leaf}.{run_uuid}.partial"
        or not _exact_lower_hex(publication.sha256, 64)
        or publication.sha256 != expected_sha256
        or type(publication.bytes) is not int
        or publication.bytes != len(artifact.raw)
        or type(publication.file_flush_count) is not int
        or publication.file_flush_count != 2
        or type(publication.directory_flush_count) is not int
        or publication.directory_flush_count != 1
        or publication.directory_flush_succeeded is not True
        or publication.replace_if_exists is not False
        or publication.same_handle_readback is not True
        or publication.file_identity_stable_across_rename is not True
        or not isinstance(file_identity.final_path, str)
        or _host_path(file_identity.final_path) != expected_path
        or not _exact_positive_int(file_identity.volume_serial_number)
        or not _exact_nonnegative_int(file_identity.size)
        or file_identity.size != len(artifact.raw)
        or type(file_identity.link_count) is not int
        or file_identity.link_count != 1
        or not _exact_nonnegative_int(file_identity.attributes)
        or file_identity.attributes & 0x10 != 0
        or type(file_identity.reparse_tag) is not int
        or file_identity.reparse_tag != 0
        or type(file_identity.file_type) is not int
        or file_identity.file_type != 1
        or not _exact_lower_hex(file_identity.file_id_hex, 32)
        or not _exact_lower_hex(file_identity.security_descriptor_sha256, 64)
        or not _valid_sid(file_identity.owner_sid)
        or file_identity.dacl_present is not True
        or file_identity.dacl_protected is not True
        or not isinstance(directory_identity.final_path, str)
        or _host_path(directory_identity.final_path) != expected_directory
        or not _exact_positive_int(directory_identity.volume_serial_number)
        or not _exact_nonnegative_int(directory_identity.size)
        or not _exact_positive_int(directory_identity.link_count)
        or not _exact_nonnegative_int(directory_identity.attributes)
        or directory_identity.attributes & 0x10 == 0
        or type(directory_identity.reparse_tag) is not int
        or directory_identity.reparse_tag != 0
        or type(directory_identity.file_type) is not int
        or directory_identity.file_type != 1
        or not _exact_lower_hex(directory_identity.file_id_hex, 32)
        or not _exact_lower_hex(directory_identity.security_descriptor_sha256, 64)
        or not _valid_sid(directory_identity.owner_sid)
        or directory_identity.dacl_present is not True
        or type(directory_identity.dacl_protected) is not bool
        or file_identity.volume_serial_number != directory_identity.volume_serial_number
        or file_identity.file_id_hex == directory_identity.file_id_hex
        or publication.power_loss_durability_proven is not False
        or publication.same_token_hostile_admin_protected is not False
        or publication.go_evidence_eligible is not False
    ):
        raise R7S4EvidenceError("publisher_contract_mismatch")
    return publication


def _publish_one(
    publisher: Publisher,
    directory: Path,
    artifact: PreparedJsonArtifact,
    *,
    run_uuid: str,
    api_factory: ApiFactory | None,
) -> DurableBoundPublication:
    api = api_factory(artifact.leaf) if api_factory is not None else None
    publication = publisher(
        directory,
        artifact.leaf,
        artifact.raw,
        run_uuid=run_uuid,
        api=api,
    )
    return _validated_publication(
        publication,
        directory=directory,
        artifact=artifact,
        run_uuid=run_uuid,
    )


def _failure_seal_artifact(
    *,
    run_uuid: str,
    output_directory: Path,
    stage: str,
    attempted: PreparedJsonArtifact,
    publications: tuple[DurableBoundPublication, ...],
    cause: BaseException,
) -> PreparedJsonArtifact:
    return PreparedJsonArtifact(
        leaf=ATOMIC_FAILURE_SEAL_LEAF,
        role="atomic_failure_seal",
        raw=canonical_json_bytes(
            {
                "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.atomic-failure-seal.v1",
                "status": "manual_intervention_required",
                "run_uuid": run_uuid,
                "output_directory": str(output_directory),
                "failure_stage": stage,
                "exception_type": _exception_type(cause),
                "failed_publication_observation": (
                    cause.observation.to_dict()
                    if isinstance(cause, DurablePublicationError)
                    else {
                        "observation_status": "unknown_non_handle_publisher_exception",
                        "manual_intervention_required": True,
                        "go_evidence_eligible": False,
                    }
                ),
                "attempted_artifact": _descriptor(attempted),
                "published": [item.to_dict() for item in publications],
                "retry_count": 0,
                "success_marker_created": False,
                "process_residue": "not_observed_by_evidence_writer",
                "production_go_enabled": False,
                "go_evidence_eligible": False,
            }
        ),
    )


def _emergency_seal_artifact(
    *,
    run_uuid: str,
    output_directory: Path,
    stage: str,
    attempted: PreparedJsonArtifact,
    publications: tuple[DurableBoundPublication, ...],
    cause: BaseException,
    failure_seal: PreparedJsonArtifact,
    failure_seal_error: BaseException,
) -> PreparedJsonArtifact:
    return PreparedJsonArtifact(
        leaf=EMERGENCY_SEAL_LEAF,
        role="emergency_failure_seal",
        raw=canonical_json_bytes(
            {
                "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.emergency-seal.v1",
                "status": "manual_intervention_required",
                "run_uuid": run_uuid,
                "failed_output_directory": str(output_directory),
                "failure_stage": stage,
                "original_exception_type": _exception_type(cause),
                "attempted_artifact": _descriptor(attempted),
                "already_published_count": len(publications),
                "already_published": [item.to_dict() for item in publications],
                "failed_atomic_seal": _descriptor(failure_seal),
                "atomic_seal_exception_type": _exception_type(failure_seal_error),
                "atomic_seal_failure_observation": (
                    failure_seal_error.observation.to_dict()
                    if isinstance(failure_seal_error, DurablePublicationError)
                    else {
                        "observation_status": "unknown_non_handle_publisher_exception",
                        "manual_intervention_required": True,
                        "go_evidence_eligible": False,
                    }
                ),
                "atomic_seal_publication_ambiguous": True,
                "retry_count": 0,
                "success_marker_created": False,
                "process_residue": "not_observed_by_evidence_writer",
                "production_go_enabled": False,
                "go_evidence_eligible": False,
            }
        ),
    )


def _reservation_failure_seal_artifact(
    *,
    run_uuid: str,
    output_directory: Path,
    run_reservation_directory: Path,
    failed_path: Path,
    failure_stage: str,
    cause: BaseException,
) -> PreparedJsonArtifact:
    try:
        preexisting: bool | None = failed_path.exists()
    except OSError:
        preexisting = None
    return PreparedJsonArtifact(
        leaf=RESERVATION_FAILURE_SEAL_LEAF,
        role="reservation_failure_seal",
        raw=canonical_json_bytes(
            {
                "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.reservation-failure-seal.v1",
                "status": "manual_intervention_required",
                "run_uuid": run_uuid,
                "output_directory": str(output_directory),
                "run_reservation_directory": str(run_reservation_directory),
                "failed_path": str(failed_path),
                "failed_path_preexisting": preexisting,
                "failure_stage": failure_stage,
                "exception_type": _exception_type(cause),
                "retry_count": 0,
                "automatic_retry_count": 0,
                "downstream_call_count": 0,
                "manual_intervention_required": True,
                "cleanup_or_overwrite_attempted": False,
                "success_marker_created": False,
                "production_go_enabled": False,
                "go_evidence_eligible": False,
            }
        ),
    )


def _reservation_emergency_seal_artifact(
    *,
    run_uuid: str,
    output_directory: Path,
    run_reservation_directory: Path,
    failed_path: Path,
    failure_stage: str,
    cause: BaseException,
    failure_seal: PreparedJsonArtifact,
    failure_seal_error: BaseException,
) -> PreparedJsonArtifact:
    return PreparedJsonArtifact(
        leaf=EMERGENCY_SEAL_LEAF,
        role="reservation_emergency_seal",
        raw=canonical_json_bytes(
            {
                "schema": ("evm.s8-v4.x1.phase-b2.pre-r8-r7s4.reservation-emergency-seal.v1"),
                "status": "manual_intervention_required",
                "run_uuid": run_uuid,
                "output_directory": str(output_directory),
                "run_reservation_directory": str(run_reservation_directory),
                "failed_path": str(failed_path),
                "failure_stage": failure_stage,
                "original_exception_type": _exception_type(cause),
                "failed_reservation_seal": _descriptor(failure_seal),
                "reservation_seal_exception_type": _exception_type(failure_seal_error),
                "reservation_seal_publication_ambiguous": True,
                "retry_count": 0,
                "automatic_retry_count": 0,
                "downstream_call_count": 0,
                "manual_intervention_required": True,
                "cleanup_or_overwrite_attempted": False,
                "success_marker_created": False,
                "production_go_enabled": False,
                "go_evidence_eligible": False,
            }
        ),
    )


def _raise_reservation_failure(
    *,
    parent: Path,
    output_leaf: str,
    output_directory: Path,
    run_reservation_directory: Path,
    run_uuid: str,
    failed_path: Path,
    failure_stage: str,
    cause: BaseException,
    failure_publisher: Publisher,
    emergency_publisher: Publisher,
    api_factory: ApiFactory | None,
) -> NoReturn:
    failure_directory = parent / _reservation_failure_directory_leaf(output_leaf, run_uuid)
    failure_artifact = _reservation_failure_seal_artifact(
        run_uuid=run_uuid,
        output_directory=output_directory,
        run_reservation_directory=run_reservation_directory,
        failed_path=failed_path,
        failure_stage=failure_stage,
        cause=cause,
    )
    failure_publication: DurableBoundPublication | None = None
    failure_error: BaseException | None = None
    emergency_directory: Path | None = None
    emergency_publication: DurableBoundPublication | None = None
    emergency_error: BaseException | None = None
    try:
        os.mkdir(failure_directory)
        failure_publication = _publish_one(
            failure_publisher,
            failure_directory,
            failure_artifact,
            run_uuid=run_uuid,
            api_factory=api_factory,
        )
    except Exception as exc:
        failure_error = exc
        emergency_directory = parent / _reservation_emergency_directory_leaf(output_leaf, run_uuid)
        try:
            os.mkdir(emergency_directory)
            emergency_publication = _publish_one(
                emergency_publisher,
                emergency_directory,
                _reservation_emergency_seal_artifact(
                    run_uuid=run_uuid,
                    output_directory=output_directory,
                    run_reservation_directory=run_reservation_directory,
                    failed_path=failed_path,
                    failure_stage=failure_stage,
                    cause=cause,
                    failure_seal=failure_artifact,
                    failure_seal_error=exc,
                ),
                run_uuid=run_uuid,
                api_factory=api_factory,
            )
        except Exception as exc2:
            emergency_error = exc2
    error = R7S4EvidencePublicationError(
        f"exclusive_reservation_failed:{failure_stage}",
        stage=failure_stage,
        output_directory=output_directory,
        run_reservation_directory=run_reservation_directory,
        attempted_leaf=None,
        publications=(),
        failure_seal_directory=failure_directory,
        failure_seal_publication=failure_publication,
        failure_seal_error_type=(
            _exception_type(failure_error) if failure_error is not None else None
        ),
        emergency_seal_directory=emergency_directory,
        emergency_seal_publication=emergency_publication,
        emergency_seal_error_type=(
            _exception_type(emergency_error) if emergency_error is not None else None
        ),
        failure_seal_attempt_count=1,
        emergency_seal_attempt_count=int(failure_error is not None),
    )
    raise error from cause


def _raise_sealed_publication_failure(
    *,
    parent: Path,
    output_leaf: str,
    output_directory: Path,
    run_reservation_directory: Path,
    run_uuid: str,
    stage: str,
    attempted: PreparedJsonArtifact,
    publications: tuple[DurableBoundPublication, ...],
    cause: BaseException,
    failure_publisher: Publisher,
    emergency_publisher: Publisher,
    api_factory: ApiFactory | None,
) -> NoReturn:
    failure_artifact = _failure_seal_artifact(
        run_uuid=run_uuid,
        output_directory=output_directory,
        stage=stage,
        attempted=attempted,
        publications=publications,
        cause=cause,
    )
    failure_publication: DurableBoundPublication | None = None
    failure_error: BaseException | None = None
    emergency_directory: Path | None = None
    emergency_publication: DurableBoundPublication | None = None
    emergency_error: BaseException | None = None
    try:
        failure_publication = _publish_one(
            failure_publisher,
            output_directory,
            failure_artifact,
            run_uuid=run_uuid,
            api_factory=api_factory,
        )
    except Exception as exc:
        failure_error = exc
        emergency_directory = parent / _publication_emergency_directory_leaf(output_leaf, run_uuid)
        try:
            os.mkdir(emergency_directory)
            emergency_artifact = _emergency_seal_artifact(
                run_uuid=run_uuid,
                output_directory=output_directory,
                stage=stage,
                attempted=attempted,
                publications=publications,
                cause=cause,
                failure_seal=failure_artifact,
                failure_seal_error=exc,
            )
            emergency_publication = _publish_one(
                emergency_publisher,
                emergency_directory,
                emergency_artifact,
                run_uuid=run_uuid,
                api_factory=api_factory,
            )
        except Exception as exc2:
            emergency_error = exc2

    error = R7S4EvidencePublicationError(
        f"artifact_publication_failed:{attempted.leaf}",
        stage=stage,
        output_directory=output_directory,
        run_reservation_directory=run_reservation_directory,
        attempted_leaf=attempted.leaf,
        publications=publications,
        failure_seal_directory=output_directory,
        failure_seal_publication=failure_publication,
        failure_seal_error_type=(
            _exception_type(failure_error) if failure_error is not None else None
        ),
        emergency_seal_directory=emergency_directory,
        emergency_seal_publication=emergency_publication,
        emergency_seal_error_type=(
            _exception_type(emergency_error) if emergency_error is not None else None
        ),
        failure_seal_attempt_count=1,
        emergency_seal_attempt_count=int(failure_error is not None),
    )
    raise error from cause


def _publish_review_json_batch(
    parent_directory: str | os.PathLike[str],
    output_leaf: str,
    documents: Mapping[str, Any],
    *,
    run_uuid: str | uuid.UUID,
    publisher: Publisher,
    failure_publisher: Publisher,
    emergency_publisher: Publisher,
    api_factory: ApiFactory | None = None,
) -> ReviewEvidenceBatch:
    """Publish one deterministic, review-only batch with no retries.

    Serialization is deliberately completed before ``os.mkdir``.  A failure
    after directory creation is wrapped with the exact already-published
    identities; the directory and all partial/final files remain untouched.
    """

    prepared_documents = prepare_json_batch(documents)
    leaf = _leaf(output_leaf, label="output_leaf")
    try:
        run_value = str(uuid.UUID(str(run_uuid)))
    except ValueError as exc:
        raise R7S4EvidenceError("run_uuid_invalid") from exc
    parent = Path(parent_directory).resolve()
    run_reservation = _run_reservation_descriptor(parent, run_value)
    aggregate_manifest, aggregate_index = _prepare_aggregate_artifacts(
        prepared_documents,
        run_uuid=run_value,
        run_reservation=run_reservation,
    )
    prepared = (*prepared_documents, aggregate_manifest, aggregate_index)
    _validate_planned_leaf_space(prepared, output_leaf=leaf, run_uuid=run_value)

    run_reservation_directory = parent / run_reservation["reservation_leaf"]
    output_directory = parent / leaf
    try:
        os.mkdir(run_reservation_directory)
    except Exception as exc:
        _raise_reservation_failure(
            parent=parent,
            output_leaf=leaf,
            output_directory=output_directory,
            run_reservation_directory=run_reservation_directory,
            run_uuid=run_value,
            failed_path=run_reservation_directory,
            failure_stage="run_uuid_reservation_create",
            cause=exc,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
        )
    try:
        os.mkdir(output_directory)
    except Exception as exc:
        _raise_reservation_failure(
            parent=parent,
            output_leaf=leaf,
            output_directory=output_directory,
            run_reservation_directory=run_reservation_directory,
            run_uuid=run_value,
            failed_path=output_directory,
            failure_stage="output_directory_create",
            cause=exc,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
        )

    published: list[DurableBoundPublication] = []
    for artifact in prepared:
        try:
            publication = _publish_one(
                publisher,
                output_directory,
                artifact,
                run_uuid=run_value,
                api_factory=api_factory,
            )
            published.append(publication)
        except Exception as exc:
            stage = {
                "document": "artifact_publication",
                "aggregate_review_manifest": "aggregate_manifest_publication",
                "aggregate_review_index": "aggregate_index_publication",
            }[artifact.role]
            _raise_sealed_publication_failure(
                parent=parent,
                output_leaf=leaf,
                output_directory=output_directory,
                run_reservation_directory=run_reservation_directory,
                run_uuid=run_value,
                stage=stage,
                attempted=artifact,
                publications=tuple(published),
                cause=exc,
                failure_publisher=failure_publisher,
                emergency_publisher=emergency_publisher,
                api_factory=api_factory,
            )

    return ReviewEvidenceBatch(
        output_directory=output_directory,
        run_reservation_directory=run_reservation_directory,
        run_reservation_identity_sha256=run_reservation["logical_identity_sha256"],
        run_uuid=run_value,
        publications=tuple(published),
        aggregate_manifest_publication=published[-2],
        aggregate_index_publication=published[-1],
    )


def publish_review_json_batch(
    parent_directory: str | os.PathLike[str],
    output_leaf: str,
    documents: Mapping[str, Any],
    *,
    run_uuid: str | uuid.UUID,
) -> ReviewEvidenceBatch:
    """Fixed production-facing review writer; no caller-selected I/O seams."""

    return _publish_review_json_batch(
        parent_directory,
        output_leaf,
        documents,
        run_uuid=run_uuid,
        publisher=publish_bound_no_replace_durable,
        failure_publisher=publish_bound_no_replace_durable,
        emergency_publisher=publish_bound_no_replace_durable,
        api_factory=None,
    )


def _publish_review_json_batch_for_test(
    parent_directory: str | os.PathLike[str],
    output_leaf: str,
    documents: Mapping[str, Any],
    *,
    run_uuid: str | uuid.UUID,
    publisher: Publisher,
    failure_publisher: Publisher | None = None,
    emergency_publisher: Publisher | None = None,
    api_factory: ApiFactory | None = None,
) -> ReviewEvidenceBatch:
    """Private dependency seam for deterministic failure injection only."""

    return _publish_review_json_batch(
        parent_directory,
        output_leaf,
        documents,
        run_uuid=run_uuid,
        publisher=publisher,
        failure_publisher=failure_publisher or publisher,
        emergency_publisher=emergency_publisher or publisher,
        api_factory=api_factory,
    )


def source_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.evidence-writer.v1",
        "all_json_serialized_before_output_directory": False,
        "all_planned_success_json_serialized_before_output_directory": True,
        "dynamic_failure_seal_serialized_only_after_failure": True,
        "exclusive_output_directory": True,
        "canonical_parent_run_uuid_reservation": True,
        "same_parent_run_uuid_different_output_leaf_rejected": True,
        "run_reservation_writer_preserves_after_success_or_failure": True,
        "run_reservation_cleanup_or_removal_attempts": 0,
        "run_reservation_physical_handle_identity_proven": False,
        "run_reservation_same_token_deletion_protected": False,
        "run_reservation_bound_into_aggregate": True,
        "temporary_leaf_feasibility_checked_before_directory_create": True,
        "planned_final_temporary_collision_checked_before_directory_create": True,
        "reservation_collision_manual_intervention_required": True,
        "reservation_collision_downstream_call_count": 0,
        "reservation_failure_seal_attempts": 1,
        "reservation_failure_upper_emergency_seal_attempts": 1,
        "fixed_global_reservation_root_wired": False,
        "parent_directory_change_global_one_shot_proven": False,
        "handle_bound_relative_publication": True,
        "production_facing_publisher_injectable": False,
        "publication_path_sha_bytes_identity_invariants_required": True,
        "identity_numeric_exact_type_required": True,
        "identity_volume_serial_positive_and_equal_required": True,
        "file_directory_file_id_distinct_required": True,
        "identity_owner_sid_grammar_required": True,
        "file_dacl_protected_required": True,
        "directory_dacl_present_required": True,
        "directory_dacl_protected_required": False,
        "directory_dacl_protected_boolean_observation_required": True,
        "directory_flush_required_per_artifact": True,
        "aggregate_review_manifest_published_penultimate": True,
        "aggregate_review_index_published_last": True,
        "aggregate_persists_logical_sha_bytes_inventory": True,
        "aggregate_persists_handle_flush_publication_evidence": False,
        "handle_flush_publication_evidence_available_in_return_object_only": True,
        "atomic_failure_seal_attempts": 1,
        "emergency_seal_after_atomic_failure_attempts": 1,
        "upper_emergency_persists_prior_validated_publications": True,
        "reservation_failure_parent_seal_attempts": 1,
        "retry_count": 0,
        "cleanup_or_overwrite_on_failure": False,
        "phase_b2_success_marker_supported": False,
        "power_loss_durability_proven": False,
        "same_token_hostile_admin_protected": False,
        "review_only_dead_path": True,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


__all__ = [
    "AGGREGATE_INDEX_LEAF",
    "AGGREGATE_MANIFEST_LEAF",
    "ATOMIC_FAILURE_SEAL_LEAF",
    "EMERGENCY_SEAL_LEAF",
    "FORBIDDEN_SUCCESS_LEAVES",
    "PreparedJsonArtifact",
    "RESERVATION_FAILURE_SEAL_LEAF",
    "R7S4EvidenceError",
    "R7S4EvidencePublicationError",
    "ReviewEvidenceBatch",
    "RUN_RESERVATION_PREFIX",
    "RUN_RESERVATION_SUFFIX",
    "canonical_json_bytes",
    "prepare_json_batch",
    "publish_review_json_batch",
    "source_contract",
]
