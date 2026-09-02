"""Pre-serialized, append-only review evidence for the r7s6 hardening identity.

The final output directory is not published until every success-path JSON
artifact has been serialized, durably published in a same-filesystem staging
directory, and catalogued.  The staging directory is then renamed once, with
no replacement.  A failed staging batch is never edited or removed; a sibling
failure seal references its immutable SHA inventory instead.

This module creates no completion marker and enables no production or r8 gate.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn, Sequence

from evm.scale_validation import phase_b2_r7s3_handle_io as r7s3
from evm.scale_validation import phase_b2_r7s4_evidence as r7s4
from evm.scale_validation import phase_b2_r7s5_evidence as r7s5
from evm.scale_validation.phase_b2_r7s4_handle_io import (
    DurableBoundPublication,
    DurableHandleApi,
    DurablePublicationError,
    WindowsHandleApi,
    publish_bound_no_replace_durable,
)


IDENTITY_MANIFEST_LEAF = "aggregate-publication-identity-manifest.json"
IDENTITY_INDEX_LEAF = "aggregate-publication-identity-index.json"
FAILURE_SEAL_LEAF = "r7s6-atomic-failure-seal.json"
EMERGENCY_SEAL_LEAF = "r7s6-emergency-seal.json"
RUN_RESERVATION_LEAF = "r7s6-run-reservation.json"
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FORBIDDEN_SUCCESS_LEAVES = r7s5.FORBIDDEN_SUCCESS_LEAVES
CONTROL_LEAVES = frozenset(
    {
        IDENTITY_MANIFEST_LEAF,
        IDENTITY_INDEX_LEAF,
        FAILURE_SEAL_LEAF,
        EMERGENCY_SEAL_LEAF,
        RUN_RESERVATION_LEAF,
    }
)


Publisher = Callable[..., DurableBoundPublication]
Serializer = Callable[[Any], bytes]
ApiFactory = Callable[[str], DurableHandleApi]
DirectoryPublisher = Callable[[Path, Path], None]
FinalVerifier = Callable[
    [
        Path,
        tuple[r7s4.PreparedJsonArtifact, ...],
        tuple[DurableBoundPublication, ...],
    ],
    "FinalDirectoryVerification",
]


class R7S6EvidenceError(RuntimeError):
    """The pre-serialized append-only evidence contract was not satisfied."""


class R7S6EvidencePublicationError(R7S6EvidenceError):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        output_directory: Path,
        staging_directory: Path,
        partial_inventory: tuple[dict[str, Any], ...],
        reservation_directory: Path,
        reservation_inventory: tuple[dict[str, Any], ...],
        reservation_publication: DurableBoundPublication | None,
        writer_owned_staging_inventory: tuple[dict[str, Any], ...],
        writer_owned_final_inventory: tuple[dict[str, Any], ...],
        last_known_pre_rename_inventory: tuple[dict[str, Any], ...],
        untrusted_output_observation: Mapping[str, Any],
        partial_artifact_evidence: Mapping[str, Any],
        failure_seal_directory: Path | None,
        failure_seal_publication: DurableBoundPublication | None,
        failure_seal_error_type: str | None,
        failure_seal_partial_inventory: tuple[dict[str, Any], ...],
        emergency_seal_directory: Path | None,
        emergency_seal_publication: DurableBoundPublication | None,
        emergency_seal_error_type: str | None,
        emergency_seal_partial_inventory: tuple[dict[str, Any], ...],
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.output_directory = output_directory
        self.staging_directory = staging_directory
        self.partial_inventory = partial_inventory
        self.reservation_directory = reservation_directory
        self.reservation_inventory = reservation_inventory
        self.reservation_publication = reservation_publication
        self.writer_owned_staging_inventory = writer_owned_staging_inventory
        self.writer_owned_final_inventory = writer_owned_final_inventory
        self.last_known_pre_rename_inventory = last_known_pre_rename_inventory
        self.untrusted_output_observation = dict(untrusted_output_observation)
        self.partial_artifact_evidence = dict(partial_artifact_evidence)
        self.failure_seal_directory = failure_seal_directory
        self.failure_seal_publication = failure_seal_publication
        self.failure_seal_error_type = failure_seal_error_type
        self.failure_seal_partial_inventory = failure_seal_partial_inventory
        self.emergency_seal_directory = emergency_seal_directory
        self.emergency_seal_publication = emergency_seal_publication
        self.emergency_seal_error_type = emergency_seal_error_type
        self.emergency_seal_partial_inventory = emergency_seal_partial_inventory
        self.automatic_retry_count = 0
        self.cleanup_or_overwrite_attempted = False
        self.success_marker_created = False
        self.manual_intervention_required = True
        self.go_evidence_eligible = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s6.publication-failure.v1",
            "status": "manual_intervention_required",
            "credit": "zero_credit",
            "stage": self.stage,
            "output_directory": str(self.output_directory),
            "staging_directory": str(self.staging_directory),
            "partial_inventory": list(self.partial_inventory),
            "run_reservation": {
                "directory": str(self.reservation_directory),
                "inventory": list(self.reservation_inventory),
                "publication": (
                    self.reservation_publication.to_dict()
                    if self.reservation_publication is not None
                    else None
                ),
            },
            "writer_owned_staging_inventory": list(self.writer_owned_staging_inventory),
            "writer_owned_final_inventory": list(self.writer_owned_final_inventory),
            "last_known_pre_rename_inventory": list(self.last_known_pre_rename_inventory),
            "untrusted_output_observation": self.untrusted_output_observation,
            "partial_artifact_evidence": self.partial_artifact_evidence,
            "failure_seal_directory": (
                str(self.failure_seal_directory)
                if self.failure_seal_directory is not None
                else None
            ),
            "failure_seal_publication": (
                self.failure_seal_publication.to_dict()
                if self.failure_seal_publication is not None
                else None
            ),
            "failure_seal_error_type": self.failure_seal_error_type,
            "failure_seal_partial_inventory": list(self.failure_seal_partial_inventory),
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
            "emergency_seal_partial_inventory": list(self.emergency_seal_partial_inventory),
            "automatic_retry_count": self.automatic_retry_count,
            "cleanup_or_overwrite_attempted": self.cleanup_or_overwrite_attempted,
            "success_marker_created": self.success_marker_created,
            "manual_intervention_required": self.manual_intervention_required,
            "go_evidence_eligible": self.go_evidence_eligible,
        }


@dataclass(frozen=True)
class FinalDirectoryVerification:
    inventory: tuple[dict[str, Any], ...]
    directory_identity: Mapping[str, Any] | None
    file_identities: tuple[dict[str, Any], ...]
    root_lstat_reparse_checked: bool
    child_lstat_reparse_checked: bool
    directory_handle_no_delete_share: bool
    directory_handle_no_write_share: bool
    child_handles_held_through_inventory: bool
    handle_bound_content_readback: bool
    staging_to_final_identity_crosscheck: bool
    kernel_handle_bound: bool
    test_only_path_readback: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "inventory": list(self.inventory),
            "directory_identity": (
                dict(self.directory_identity) if self.directory_identity is not None else None
            ),
            "file_identities": list(self.file_identities),
            "root_lstat_reparse_checked": self.root_lstat_reparse_checked,
            "child_lstat_reparse_checked": self.child_lstat_reparse_checked,
            "directory_handle_no_delete_share": self.directory_handle_no_delete_share,
            "directory_handle_no_write_share": self.directory_handle_no_write_share,
            "child_handles_held_through_inventory": (self.child_handles_held_through_inventory),
            "handle_bound_content_readback": self.handle_bound_content_readback,
            "staging_to_final_identity_crosscheck": (self.staging_to_final_identity_crosscheck),
            "kernel_handle_bound": self.kernel_handle_bound,
            "test_only_path_readback": self.test_only_path_readback,
        }


@dataclass(frozen=True)
class PreSerializedBatch:
    output_directory: Path
    staging_directory: Path
    reservation_directory: Path
    run_uuid: str
    reservation_publication: DurableBoundPublication
    document_publications: tuple[DurableBoundPublication, ...]
    manifest_publication: DurableBoundPublication
    index_publication: DurableBoundPublication
    final_verification: FinalDirectoryVerification
    final_directory_published_once: bool = True
    all_success_json_serialized_before_final_directory: bool = True
    publication_identity_scope: str = "staging_same_handle_before_parent_directory_rename"
    publication_paths_rebased_to_final: bool = False
    post_rename_handle_identity_verified: bool = False
    final_content_sha_readback_verified: bool = True
    retry_count: int = 0
    success_marker_created: bool = False
    production_go_enabled: bool = False
    go_evidence_eligible: bool = False

    @property
    def publications(self) -> tuple[DurableBoundPublication, ...]:
        return (*self.document_publications, self.manifest_publication, self.index_publication)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s6.pre-serialized-batch.v1",
            "status": "review_pending",
            "output_directory": str(self.output_directory),
            "staging_directory": str(self.staging_directory),
            "reservation_directory": str(self.reservation_directory),
            "run_uuid": self.run_uuid,
            "reservation_publication": self.reservation_publication.to_dict(),
            "document_publications": [item.to_dict() for item in self.document_publications],
            "manifest_publication": self.manifest_publication.to_dict(),
            "index_publication": self.index_publication.to_dict(),
            "final_verification": self.final_verification.to_dict(),
            "final_directory_published_once": self.final_directory_published_once,
            "all_success_json_serialized_before_final_directory": (
                self.all_success_json_serialized_before_final_directory
            ),
            "publication_identity_scope": self.publication_identity_scope,
            "publication_paths_rebased_to_final": self.publication_paths_rebased_to_final,
            "post_rename_handle_identity_verified": self.post_rename_handle_identity_verified,
            "final_content_sha_readback_verified": self.final_content_sha_readback_verified,
            "retry_count": self.retry_count,
            "success_marker_created": self.success_marker_created,
            "production_go_enabled": self.production_go_enabled,
            "go_evidence_eligible": self.go_evidence_eligible,
        }


@dataclass
class _PostReservationState:
    """Conservative state allocated before the run reservation mutates disk."""

    stage: str = "post_reservation_state_initialization"
    final_directory_rename_completed: bool = False
    final_verification_succeeded: bool = False
    final_verification: FinalDirectoryVerification | None = None


def canonical_json_bytes(value: Any) -> bytes:
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


def _exception_type(exc: BaseException) -> str:
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


def _normal_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _staging_publication(
    publication: DurableBoundPublication,
    *,
    staging_directory: Path,
) -> DurableBoundPublication:
    """Validate and retain the actual pre-rename handle-identity scope.

    Moving the parent directory does not give this layer a still-open handle
    with which to re-bind every child path.  Rewriting only ``final_path``
    would therefore manufacture a post-rename identity observation.  Keep the
    actual staging paths and label their scope in aggregate evidence instead.
    """

    if type(publication) is not DurableBoundPublication:
        raise R7S6EvidenceError("r7s6_exact_publication_type_required")
    actual_parent = ntpath.dirname(publication.final_path)
    actual_leaf = ntpath.basename(publication.final_path)
    if r7s3._comparable_path(actual_parent) != r7s3._comparable_path(
        str(staging_directory.absolute())
    ):
        raise R7S6EvidenceError("r7s6_publication_escaped_staging_directory")
    if not actual_leaf or ntpath.basename(actual_leaf) != actual_leaf:
        raise R7S6EvidenceError("r7s6_publication_leaf_required")
    if r7s3._comparable_path(publication.directory_identity.final_path) != r7s3._comparable_path(
        str(staging_directory.absolute())
    ):
        raise R7S6EvidenceError("r7s6_publication_directory_identity_mismatch")
    return publication


def _scoped_publication_snapshot(
    publication: DurableBoundPublication,
    *,
    intended_final_path: Path,
) -> dict[str, Any]:
    return {
        "identity_scope": "staging_same_handle_before_parent_directory_rename",
        "publication_paths_rebased_to_final": False,
        "post_rename_handle_identity_verified": False,
        "intended_final_path": str(intended_final_path.absolute()),
        "staging_publication": _publication_snapshot(publication),
    }


def _descriptor(artifact: r7s4.PreparedJsonArtifact) -> dict[str, Any]:
    return {
        "leaf": artifact.leaf,
        "role": artifact.role,
        "sha256": hashlib.sha256(artifact.raw).hexdigest(),
        "bytes": len(artifact.raw),
    }


def _last_known_pre_rename_inventory(
    artifacts: tuple[r7s4.PreparedJsonArtifact, ...],
    publications: tuple[DurableBoundPublication, ...],
    *,
    staging_directory: Path,
    output_directory: Path,
) -> tuple[dict[str, Any], ...]:
    """Describe writer-owned bytes without following a raced final path."""

    result: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        publication = publications[index] if index < len(publications) else None
        entry = {
            "leaf": artifact.leaf,
            "role": artifact.role,
            "sha256": hashlib.sha256(artifact.raw).hexdigest(),
            "bytes": len(artifact.raw),
            "last_known_path": str(staging_directory / artifact.leaf),
            "intended_final_path": str(output_directory / artifact.leaf),
            "state": (
                "durably_published_in_staging_before_rename"
                if publication is not None
                else "serialized_not_yet_published"
            ),
            "path_followed_after_rename_failure": False,
            "publication": (
                _publication_snapshot(publication) if publication is not None else None
            ),
        }
        result.append(entry)
    return tuple(result)


def _publication_snapshot(publication: DurableBoundPublication) -> dict[str, Any]:
    raw = canonical_json_bytes(publication.to_dict())
    parsed = json.loads(raw)
    if type(parsed) is not dict:
        raise R7S6EvidenceError("r7s6_publication_snapshot_mapping_required")
    return parsed


def _publish_one(
    publisher: Publisher,
    directory: Path,
    artifact: r7s4.PreparedJsonArtifact,
    *,
    run_uuid: str,
    api_factory: ApiFactory | None,
) -> DurableBoundPublication:
    return r7s5._publish_one(
        publisher,
        directory,
        artifact,
        run_uuid=run_uuid,
        api_factory=api_factory,
    )


def _stat_is_reparse(value: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(value.st_mode)
        or getattr(value, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _checked_lstat(path: Path, *, expect_directory: bool) -> os.stat_result:
    observed = os.lstat(path)
    if _stat_is_reparse(observed):
        raise R7S6EvidenceError("r7s6_final_lstat_reparse_point_rejected")
    expected_type = stat.S_ISDIR if expect_directory else stat.S_ISREG
    if not expected_type(observed.st_mode):
        raise R7S6EvidenceError("r7s6_final_lstat_object_type_mismatch")
    return observed


def _file_identity_fingerprint(identity: r7s3.HandleIdentity) -> tuple[Any, ...]:
    return (
        identity.volume_serial_number,
        identity.file_id_hex,
        identity.size,
        identity.link_count,
        identity.reparse_tag,
        identity.file_type,
        identity.owner_sid,
        identity.security_descriptor_sha256,
        identity.dacl_present,
        identity.dacl_protected,
    )


def _directory_identity_fingerprint(identity: r7s3.HandleIdentity) -> tuple[Any, ...]:
    return (
        identity.volume_serial_number,
        identity.file_id_hex,
        identity.link_count,
        identity.reparse_tag,
        identity.file_type,
        identity.owner_sid,
        identity.security_descriptor_sha256,
        identity.dacl_present,
        identity.dacl_protected,
    )


class _NoMutationWindowsHandleApi(WindowsHandleApi):
    """Open the final directory without write/delete sharing during verification."""

    def open_directory(self, path: str) -> int:
        return self._create_file(
            path,
            self._FILE_LIST_DIRECTORY | self._READ_CONTROL | self._SYNCHRONIZE,
            self._FILE_SHARE_READ,
            self._FILE_FLAG_OPEN_REPARSE_POINT | self._FILE_FLAG_BACKUP_SEMANTICS,
        )


def _verify_final_handle_bound(
    output_directory: Path,
    artifacts: tuple[r7s4.PreparedJsonArtifact, ...],
    publications: tuple[DurableBoundPublication, ...],
    *,
    api: DurableHandleApi,
) -> FinalDirectoryVerification:
    if len(artifacts) != len(publications):
        raise R7S6EvidenceError("r7s6_final_verification_count_mismatch")
    expected_by_leaf = {artifact.leaf: artifact for artifact in artifacts}
    publication_by_leaf = {
        ntpath.basename(publication.final_path): publication for publication in publications
    }
    if len(expected_by_leaf) != len(artifacts) or set(publication_by_leaf) != set(expected_by_leaf):
        raise R7S6EvidenceError("r7s6_final_verification_leaf_space_mismatch")

    _checked_lstat(output_directory, expect_directory=True)
    directory_handle: int | None = None
    file_handles: list[tuple[str, int]] = []
    inventory: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    try:
        directory_handle = api.open_directory(str(output_directory.absolute()))
        final_directory_identity = api.identity(directory_handle)
        r7s3._reject_unsafe_directory_identity(
            final_directory_identity,
            expected_path=str(output_directory.absolute()),
        )
        staging_directory_identity = publications[0].directory_identity
        if any(
            _directory_identity_fingerprint(item.directory_identity)
            != _directory_identity_fingerprint(staging_directory_identity)
            for item in publications
        ):
            raise R7S6EvidenceError("r7s6_staging_directory_identity_inconsistent")
        if _directory_identity_fingerprint(final_directory_identity) != (
            _directory_identity_fingerprint(staging_directory_identity)
        ):
            raise R7S6EvidenceError("r7s6_final_directory_identity_changed")

        for leaf in sorted(expected_by_leaf, key=ntpath.normcase):
            artifact = expected_by_leaf[leaf]
            publication = publication_by_leaf[leaf]
            final_path = output_directory / leaf
            _checked_lstat(final_path, expect_directory=False)
            handle = api.open_read(str(final_path.absolute()))
            file_handles.append((leaf, handle))
            final_identity = api.identity(handle)
            r7s3._reject_unsafe_identity(
                final_identity,
                expected_path=str(final_path.absolute()),
                require_protected_dacl=True,
            )
            if _file_identity_fingerprint(final_identity) != _file_identity_fingerprint(
                publication.identity
            ):
                raise R7S6EvidenceError("r7s6_final_child_identity_changed")
            if publication.sha256 != hashlib.sha256(
                artifact.raw
            ).hexdigest() or publication.bytes != len(artifact.raw):
                raise R7S6EvidenceError("r7s6_staging_publication_descriptor_mismatch")
            first_read = api.read_all(handle, final_identity.size)
            if first_read != artifact.raw:
                raise R7S6EvidenceError("r7s6_final_handle_content_mismatch")
            identities.append(
                {
                    "leaf": leaf,
                    "staging_identity": publication.identity.to_dict(),
                    "final_identity": final_identity.to_dict(),
                }
            )

        observed_leaves: list[str] = []
        with os.scandir(output_directory) as entries:
            for entry in entries:
                entry_stat = entry.stat(follow_symlinks=False)
                if _stat_is_reparse(entry_stat) or not stat.S_ISREG(entry_stat.st_mode):
                    raise R7S6EvidenceError("r7s6_final_child_reparse_or_non_regular")
                observed_leaves.append(entry.name)
        if sorted(observed_leaves, key=ntpath.normcase) != sorted(
            expected_by_leaf, key=ntpath.normcase
        ):
            raise R7S6EvidenceError("r7s6_final_directory_leaf_set_mismatch")

        # Re-read while every child handle and the non-write/non-delete-shared
        # directory handle remain open.  The first verified child cannot be
        # replaced while later children or the exact leaf set are inspected.
        for leaf, handle in file_handles:
            artifact = expected_by_leaf[leaf]
            identity_after = api.identity(handle)
            if _file_identity_fingerprint(identity_after) != _file_identity_fingerprint(
                publication_by_leaf[leaf].identity
            ):
                raise R7S6EvidenceError("r7s6_final_child_identity_changed_during_readback")
            second_read = api.read_all(handle, identity_after.size)
            if second_read != artifact.raw:
                raise R7S6EvidenceError("r7s6_final_child_content_changed_during_readback")
            inventory.append(
                {
                    "leaf": leaf,
                    "status": "handle_bound_read_back",
                    "sha256": hashlib.sha256(second_read).hexdigest(),
                    "bytes": len(second_read),
                }
            )

        return FinalDirectoryVerification(
            inventory=tuple(inventory),
            directory_identity=final_directory_identity.to_dict(),
            file_identities=tuple(identities),
            root_lstat_reparse_checked=True,
            child_lstat_reparse_checked=True,
            directory_handle_no_delete_share=True,
            directory_handle_no_write_share=True,
            child_handles_held_through_inventory=True,
            handle_bound_content_readback=True,
            staging_to_final_identity_crosscheck=True,
            kernel_handle_bound=True,
            test_only_path_readback=False,
        )
    finally:
        for _, handle in reversed(file_handles):
            api.close(handle)
        api.close(directory_handle)


def _verify_final_windows(
    output_directory: Path,
    artifacts: tuple[r7s4.PreparedJsonArtifact, ...],
    publications: tuple[DurableBoundPublication, ...],
) -> FinalDirectoryVerification:
    if os.name != "nt":
        raise R7S6EvidenceError("r7s6_windows_final_handle_verifier_required")
    return _verify_final_handle_bound(
        output_directory,
        artifacts,
        publications,
        api=_NoMutationWindowsHandleApi(),
    )


def _verify_final_test_only(
    output_directory: Path,
    artifacts: tuple[r7s4.PreparedJsonArtifact, ...],
    publications: tuple[DurableBoundPublication, ...],
) -> FinalDirectoryVerification:
    del publications
    _checked_lstat(output_directory, expect_directory=True)
    for artifact in artifacts:
        _checked_lstat(output_directory / artifact.leaf, expect_directory=False)
    return FinalDirectoryVerification(
        inventory=_inventory(output_directory),
        directory_identity=None,
        file_identities=(),
        root_lstat_reparse_checked=True,
        child_lstat_reparse_checked=True,
        directory_handle_no_delete_share=False,
        directory_handle_no_write_share=False,
        child_handles_held_through_inventory=False,
        handle_bound_content_readback=False,
        staging_to_final_identity_crosscheck=False,
        kernel_handle_bound=False,
        test_only_path_readback=True,
    )


def _inventory(directory: Path) -> tuple[dict[str, Any], ...]:
    try:
        directory_stat = os.lstat(directory)
    except FileNotFoundError:
        return ()
    if not stat.S_ISDIR(directory_stat.st_mode) or _stat_is_reparse(directory_stat):
        return (
            {
                "leaf": None,
                "status": "inventory_root_not_regular_directory",
                "sha256": None,
                "bytes": None,
            },
        )
    result: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir(), key=lambda item: ntpath.normcase(item.name)):
        try:
            path_stat = os.lstat(path)
        except OSError:
            path_stat = None
        if path_stat is None or not stat.S_ISREG(path_stat.st_mode) or _stat_is_reparse(path_stat):
            result.append(
                {
                    "leaf": path.name,
                    "status": "unreadable_or_non_regular",
                    "sha256": None,
                    "bytes": None,
                }
            )
            continue
        raw = path.read_bytes()
        result.append(
            {
                "leaf": path.name,
                "status": "read_back",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        )
    return tuple(result)


def _safe_inventory(directory: Path) -> tuple[dict[str, Any], ...]:
    try:
        return _inventory(directory)
    except BaseException as exc:
        return (
            {
                "leaf": None,
                "status": "inventory_read_failed",
                "sha256": None,
                "bytes": None,
                "error_type": _exception_type(exc),
            },
        )


def _inventory_sha_evidence(inventory: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    referenced_count = sum(
        1
        for item in inventory
        if isinstance(item.get("sha256"), str)
        and len(item["sha256"]) == 64
        and all(character in "0123456789abcdef" for character in item["sha256"])
        and type(item.get("bytes")) is int
        and item["bytes"] >= 0
    )
    complete = referenced_count == len(inventory)
    return {
        "observed_entry_count": len(inventory),
        "sha_referenced_entry_count": referenced_count,
        "sha_reference_complete": complete,
        "unproven_entry_count": len(inventory) - referenced_count,
    }


def _publication_failure_observation(cause: BaseException) -> dict[str, Any]:
    if not isinstance(cause, DurablePublicationError):
        return {
            "record_available": False,
            "status": "not_available_for_failure_type",
            "failure_type": _exception_type(cause),
            "observation": None,
        }
    try:
        observation = cause.observation.to_dict()
        canonical_json_bytes(observation)
    except BaseException as exc:
        return {
            "record_available": False,
            "status": "observation_serialization_unproven",
            "failure_type": _exception_type(cause),
            "observation_error_type": _exception_type(exc),
            "observation": None,
        }
    return {
        "record_available": True,
        "status": "durable_publication_failure_observation_preserved",
        "failure_type": _exception_type(cause),
        "observation": observation,
    }


def _partial_artifact_evidence(
    *,
    partial_inventory: tuple[dict[str, Any], ...],
    staging_path_present: bool,
    final_path_observation: Mapping[str, Any],
    last_known_pre_rename_inventory: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    partial_sha_evidence = _inventory_sha_evidence(partial_inventory)
    last_known_sha_evidence = _inventory_sha_evidence(last_known_pre_rename_inventory)
    final_status = final_path_observation.get("status")
    final_object_type = final_path_observation.get("object_type")

    if staging_path_present:
        if not partial_inventory:
            policy = "writer_owned_staging_directory_present_no_file_entries_observed"
        elif partial_sha_evidence["sha_reference_complete"] is True:
            if final_status == "absent":
                policy = "preserved_unmodified_and_sha_referenced"
            else:
                policy = (
                    "writer_owned_staging_preserved_sha_referenced_final_path_separately_untrusted"
                )
        else:
            policy = "writer_no_cleanup_or_overwrite_sha_reference_unproven"
    elif final_status == "absent":
        policy = "no_partial_artifacts_observed"
    elif final_status == "lstat_observed" and final_object_type == "directory":
        if (
            last_known_pre_rename_inventory
            and last_known_sha_evidence["sha_reference_complete"] is True
        ):
            policy = (
                "final_directory_present_content_inventory_unproven_"
                "last_known_pre_rename_sha_available"
            )
        else:
            policy = "final_directory_present_content_inventory_unproven"
    elif final_status == "lstat_observed":
        policy = "final_path_present_non_directory_content_inventory_unproven"
    else:
        policy = "final_path_state_and_content_inventory_unproven"

    return {
        "policy": policy,
        "writer_owned_staging_path_present": staging_path_present,
        "partial_inventory_sha_evidence": partial_sha_evidence,
        "last_known_pre_rename_inventory_sha_evidence": last_known_sha_evidence,
        "final_path_observation": dict(final_path_observation),
        "no_partial_artifacts_claim_requires_both_paths_absent": True,
    }


def _lstat_untrusted_observation(path: Path) -> dict[str, Any]:
    """Observe an unexpected output path without following it or reading children."""

    value: dict[str, Any] = {
        "path": str(path.absolute()),
        "scope": "untrusted_path_lstat_only",
        "followed_path": False,
        "children_enumerated": False,
        "content_read": False,
        "sha256": None,
    }
    try:
        observed = os.lstat(path)
    except FileNotFoundError:
        return {**value, "status": "absent", "object_type": None, "bytes": None}
    except BaseException as exc:
        return {
            **value,
            "status": "lstat_failed",
            "object_type": None,
            "bytes": None,
            "error_type": _exception_type(exc),
        }
    if _stat_is_reparse(observed):
        object_type = "symbolic_link_or_reparse"
    elif stat.S_ISDIR(observed.st_mode):
        object_type = "directory"
    elif stat.S_ISREG(observed.st_mode):
        object_type = "regular_file"
    else:
        object_type = "other"
    return {
        **value,
        "status": "lstat_observed",
        "object_type": object_type,
        "bytes": observed.st_size,
        "mode": stat.S_IMODE(observed.st_mode),
        "device": observed.st_dev,
        "inode": observed.st_ino,
    }


def _reservation_reference(
    reservation_directory: Path,
    reservation_inventory: tuple[dict[str, Any], ...],
    reservation_publication: DurableBoundPublication | None,
) -> dict[str, Any]:
    return {
        "key_scope": "parent_global_run_uuid_only",
        "directory": str(reservation_directory),
        "inventory": list(reservation_inventory),
        "publication": (
            _publication_snapshot(reservation_publication)
            if reservation_publication is not None
            else None
        ),
        "writer_policy_immutable": True,
        "same_token_hostile_admin_protected": False,
        "released_or_deleted": False,
    }


def _preflight_paths(*paths: Path) -> None:
    normalized = [_normal_path(path) for path in paths]
    if len(set(normalized)) != len(normalized):
        raise R7S6EvidenceError("r7s6_control_path_collision")
    if any(os.path.lexists(path) for path in paths):
        raise R7S6EvidenceError("r7s6_append_only_path_exists")


def _validate_planned_file_leaf_space(
    final_leaves: tuple[str, ...],
    *,
    run_uuid: str,
    namespace: str,
) -> None:
    """Prove every final/temporary leaf is feasible and disjoint before I/O."""

    normalized_finals: dict[str, str] = {}
    for final_leaf in final_leaves:
        try:
            validated = r7s4._leaf(final_leaf, label="r7s6_planned_final_leaf")
        except r7s4.R7S4EvidenceError as exc:
            raise R7S6EvidenceError(
                f"r7s6_planned_final_leaf_invalid:{namespace}:{final_leaf}"
            ) from exc
        normalized = ntpath.normcase(validated)
        if normalized in normalized_finals:
            raise R7S6EvidenceError(f"r7s6_planned_final_leaf_collision:{namespace}")
        normalized_finals[normalized] = validated

    normalized_temporaries: dict[str, str] = {}
    for final_leaf in final_leaves:
        try:
            temporary_leaf = r7s4._temporary_leaf(final_leaf, run_uuid)
        except r7s4.R7S4EvidenceError as exc:
            raise R7S6EvidenceError(
                f"r7s6_planned_temporary_leaf_invalid:{namespace}:{final_leaf}"
            ) from exc
        normalized = ntpath.normcase(temporary_leaf)
        if normalized in normalized_temporaries:
            raise R7S6EvidenceError(f"r7s6_planned_temporary_leaf_collision:{namespace}")
        normalized_temporaries[normalized] = temporary_leaf

    overlap = set(normalized_finals).intersection(normalized_temporaries)
    if overlap:
        colliding = normalized_finals[sorted(overlap)[0]]
        raise R7S6EvidenceError(
            f"r7s6_planned_final_temporary_leaf_collision:{namespace}:{colliding}"
        )


def _planned_directory_leaves(output_leaf: str, run_uuid: str) -> dict[str, str]:
    requested = {
        "output": output_leaf,
        "staging": f".{output_leaf}.{run_uuid}.r7s6-staging",
        "failure": f".{output_leaf}.{run_uuid}.r7s6-failure",
        "emergency": f".{output_leaf}.{run_uuid}.r7s6-emergency",
        "reservation": f".r7s6-run-{run_uuid}.reservation",
    }
    validated: dict[str, str] = {}
    normalized: dict[str, str] = {}
    for role, requested_leaf in requested.items():
        try:
            actual = r7s4._leaf(requested_leaf, label=f"r7s6_{role}_directory_leaf")
        except r7s4.R7S4EvidenceError as exc:
            raise R7S6EvidenceError(f"r7s6_control_directory_leaf_invalid:{role}") from exc
        comparable = ntpath.normcase(actual)
        if comparable in normalized:
            raise R7S6EvidenceError(
                f"r7s6_control_directory_leaf_collision:{normalized[comparable]}:{role}"
            )
        normalized[comparable] = role
        validated[role] = actual
    return validated


def planned_parent_directory_leaves(
    output_leaf: str,
    run_uuid: str | uuid.UUID,
) -> dict[str, str]:
    """Return the complete validated parent namespace without touching disk."""

    try:
        leaf = r7s4._leaf(output_leaf, label="output_leaf")
        run_value = str(uuid.UUID(str(run_uuid)))
    except (r7s4.R7S4EvidenceError, ValueError) as exc:
        raise R7S6EvidenceError("r7s6_planned_parent_namespace_input_invalid") from exc
    return _planned_directory_leaves(leaf, run_value)


def _validate_planned_namespace(
    prepared_documents: tuple[r7s4.PreparedJsonArtifact, ...],
    *,
    output_leaf: str,
    run_uuid: str,
) -> dict[str, str]:
    """Validate every deterministic child and parent leaf before the first write."""

    _validate_planned_file_leaf_space(
        tuple(item.leaf for item in prepared_documents)
        + (IDENTITY_MANIFEST_LEAF, IDENTITY_INDEX_LEAF),
        run_uuid=run_uuid,
        namespace="staging",
    )
    _validate_planned_file_leaf_space(
        (RUN_RESERVATION_LEAF,),
        run_uuid=run_uuid,
        namespace="reservation",
    )
    _validate_planned_file_leaf_space(
        (FAILURE_SEAL_LEAF,),
        run_uuid=run_uuid,
        namespace="failure",
    )
    _validate_planned_file_leaf_space(
        (EMERGENCY_SEAL_LEAF,),
        run_uuid=run_uuid,
        namespace="emergency",
    )
    return planned_parent_directory_leaves(output_leaf, run_uuid)


def _rename_staging_no_replace(staging_directory: Path, output_directory: Path) -> None:
    if os.name != "nt":
        raise R7S6EvidenceError("r7s6_windows_atomic_directory_publish_required")
    if staging_directory.parent != output_directory.parent:
        raise R7S6EvidenceError("r7s6_same_filesystem_parent_required")
    if os.path.lexists(output_directory):
        raise FileExistsError(output_directory)
    os.rename(staging_directory, output_directory)
    api = WindowsHandleApi()
    handle: int | None = None
    try:
        handle = api.open_directory(str(output_directory.parent.resolve(strict=True)))
        api.flush_directory(handle)
    finally:
        api.close(handle)


def _failure_payload(
    *,
    run_uuid: str,
    stage: str,
    output_directory: Path,
    staging_directory: Path,
    cause: BaseException,
    partial_inventory: tuple[dict[str, Any], ...],
    reservation_directory: Path,
    reservation_inventory: tuple[dict[str, Any], ...],
    reservation_publication: DurableBoundPublication | None,
    writer_owned_staging_inventory: tuple[dict[str, Any], ...],
    writer_owned_final_inventory: tuple[dict[str, Any], ...],
    last_known_pre_rename_inventory: tuple[dict[str, Any], ...],
    untrusted_output_observation: Mapping[str, Any],
    partial_artifact_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s6.atomic-failure-seal.v1",
        "status": "manual_intervention_required",
        "credit": "zero_credit",
        "run_uuid": run_uuid,
        "failure_stage": stage,
        "exception_type": _exception_type(cause),
        "output_directory": str(output_directory),
        "staging_directory": str(staging_directory),
        "run_reservation": _reservation_reference(
            reservation_directory,
            reservation_inventory,
            reservation_publication,
        ),
        "partial_artifact_policy": partial_artifact_evidence["policy"],
        "partial_inventory_sha_evidence": dict(
            partial_artifact_evidence["partial_inventory_sha_evidence"]
        ),
        "partial_artifact_evidence": dict(partial_artifact_evidence),
        "failed_publication_handle_observation": _publication_failure_observation(cause),
        "partial_inventory": list(partial_inventory),
        "writer_owned_staging_inventory": list(writer_owned_staging_inventory),
        "writer_owned_final_inventory": list(writer_owned_final_inventory),
        "last_known_pre_rename_inventory": list(last_known_pre_rename_inventory),
        "untrusted_output_observation": dict(untrusted_output_observation),
        "residual_processes": "not_observed_by_evidence_writer",
        "automatic_retry_count": 0,
        "cleanup_or_overwrite_attempted": False,
        "success_marker_created": False,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


def _prepare_failure_seal_context(
    *,
    output_directory: Path,
    staging_directory: Path,
    reservation_directory: Path,
    final_directory_rename_completed: bool,
    final_verification_succeeded: bool,
    last_known_pre_rename_inventory: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Collect fallible failure evidence inside the primary-seal attempt."""

    staging_presence_error_type: str | None = None
    try:
        staging_present = os.path.lexists(staging_directory)
    except BaseException as observation_exc:
        staging_present = True
        staging_presence_error_type = _exception_type(observation_exc)
    writer_owned_staging_inventory = _safe_inventory(staging_directory)
    output_lstat = _lstat_untrusted_observation(output_directory)
    output_is_unexpected = output_lstat["status"] != "absent" and (
        not final_directory_rename_completed
        or not final_verification_succeeded
        or staging_present
        or output_lstat.get("object_type") != "directory"
    )
    untrusted_output_observation = (
        output_lstat
        if output_is_unexpected or output_lstat["status"] == "absent"
        else {
            "path": str(output_directory.absolute()),
            "scope": "not_applicable_no_unexpected_output",
            "status": "not_applicable",
            "followed_path": False,
            "children_enumerated": False,
            "content_read": False,
            "sha256": None,
        }
    )
    writer_owned_final_inventory = (
        _safe_inventory(output_directory)
        if final_directory_rename_completed and not staging_present and not output_is_unexpected
        else ()
    )
    partial_inventory = (
        writer_owned_staging_inventory if staging_present else writer_owned_final_inventory
    )
    partial_artifact_evidence = _partial_artifact_evidence(
        partial_inventory=partial_inventory,
        staging_path_present=staging_present,
        final_path_observation=output_lstat,
        last_known_pre_rename_inventory=last_known_pre_rename_inventory,
    )
    partial_artifact_evidence["staging_path_presence_observation"] = {
        "status": (
            "unproven_observation_failed" if staging_presence_error_type is not None else "observed"
        ),
        "conservative_possible_presence": staging_present,
        "error_type": staging_presence_error_type,
    }
    return {
        "writer_owned_staging_inventory": writer_owned_staging_inventory,
        "writer_owned_final_inventory": writer_owned_final_inventory,
        "partial_inventory": partial_inventory,
        "partial_artifact_evidence": partial_artifact_evidence,
        "reservation_inventory": _safe_inventory(reservation_directory),
        "untrusted_output_observation": untrusted_output_observation,
    }


def _observed_directory_or_none(path: Path) -> Path | None:
    try:
        observation = _lstat_untrusted_observation(path)
    except BaseException:
        return None
    return path if observation.get("object_type") == "directory" else None


def _raise_sealed_failure(
    *,
    stage: str,
    cause: BaseException,
    run_uuid: str,
    output_directory: Path,
    staging_directory: Path,
    reservation_directory: Path,
    reservation_publication: DurableBoundPublication | None,
    failure_directory: Path,
    emergency_directory: Path,
    failure_publisher: Publisher,
    emergency_publisher: Publisher,
    api_factory: ApiFactory | None,
    final_directory_rename_completed: bool = False,
    final_verification_succeeded: bool = False,
    last_known_pre_rename_inventory: tuple[dict[str, Any], ...] = (),
    prepared_success_artifacts: Sequence[r7s4.PreparedJsonArtifact] | None = None,
    staging_publications: Sequence[DurableBoundPublication] | None = None,
) -> NoReturn:
    # Defaults are deliberately non-affirmative.  All fallible observation and
    # primary payload construction happens in the primary-seal try below.  If
    # it is interrupted, the independent emergency attempt can still record
    # the original failure without claiming that partial evidence was read.
    writer_owned_staging_inventory: tuple[dict[str, Any], ...] = ()
    writer_owned_final_inventory: tuple[dict[str, Any], ...] = ()
    partial_inventory: tuple[dict[str, Any], ...] = ()
    reservation_inventory: tuple[dict[str, Any], ...] = ()
    untrusted_output_observation: dict[str, Any] = {
        "path": str(output_directory),
        "scope": "failure_context_preparation_unproven",
        "status": "unproven",
        "followed_path": False,
        "children_enumerated": False,
        "content_read": False,
        "sha256": None,
    }
    partial_artifact_evidence: dict[str, Any] = {
        "policy": "failure_context_preparation_unproven_partial_preserved",
        "partial_inventory_sha_evidence": {
            "observed_entry_count": 0,
            "sha_referenced_entry_count": 0,
            "sha_reference_complete": False,
            "unproven_entry_count": 1,
        },
        "staging_path_presence_observation": {
            "status": "unproven",
            "conservative_possible_presence": True,
            "error_type": None,
        },
    }
    failure_publication: DurableBoundPublication | None = None
    failure_error: BaseException | None = None
    failure_seal_partial_inventory: tuple[dict[str, Any], ...] = ()
    emergency_publication: DurableBoundPublication | None = None
    emergency_error: BaseException | None = None
    emergency_seal_partial_inventory: tuple[dict[str, Any], ...] = ()
    try:
        if prepared_success_artifacts is not None and staging_publications is not None:
            last_known_pre_rename_inventory = _last_known_pre_rename_inventory(
                tuple(prepared_success_artifacts),
                tuple(staging_publications),
                staging_directory=staging_directory,
                output_directory=output_directory,
            )
        context = _prepare_failure_seal_context(
            output_directory=output_directory,
            staging_directory=staging_directory,
            reservation_directory=reservation_directory,
            final_directory_rename_completed=final_directory_rename_completed,
            final_verification_succeeded=final_verification_succeeded,
            last_known_pre_rename_inventory=last_known_pre_rename_inventory,
        )
        writer_owned_staging_inventory = context["writer_owned_staging_inventory"]
        writer_owned_final_inventory = context["writer_owned_final_inventory"]
        partial_inventory = context["partial_inventory"]
        partial_artifact_evidence = context["partial_artifact_evidence"]
        reservation_inventory = context["reservation_inventory"]
        untrusted_output_observation = context["untrusted_output_observation"]
        os.mkdir(failure_directory)
        failure_artifact = r7s4.PreparedJsonArtifact(
            leaf=FAILURE_SEAL_LEAF,
            role="r7s6_atomic_failure_seal",
            raw=canonical_json_bytes(
                _failure_payload(
                    run_uuid=run_uuid,
                    stage=stage,
                    output_directory=output_directory,
                    staging_directory=staging_directory,
                    cause=cause,
                    partial_inventory=partial_inventory,
                    reservation_directory=reservation_directory,
                    reservation_inventory=reservation_inventory,
                    reservation_publication=reservation_publication,
                    writer_owned_staging_inventory=writer_owned_staging_inventory,
                    writer_owned_final_inventory=writer_owned_final_inventory,
                    last_known_pre_rename_inventory=last_known_pre_rename_inventory,
                    untrusted_output_observation=untrusted_output_observation,
                    partial_artifact_evidence=partial_artifact_evidence,
                )
            ),
        )
        failure_publication = _publish_one(
            failure_publisher,
            failure_directory,
            failure_artifact,
            run_uuid=run_uuid,
            api_factory=api_factory,
        )
    except BaseException as exc:
        failure_error = exc
        failure_seal_partial_inventory = _safe_inventory(failure_directory)
        try:
            os.mkdir(emergency_directory)
            emergency_prepublication_inventory = _safe_inventory(emergency_directory)
            emergency_artifact = r7s4.PreparedJsonArtifact(
                leaf=EMERGENCY_SEAL_LEAF,
                role="r7s6_emergency_seal",
                raw=canonical_json_bytes(
                    {
                        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s6.emergency-seal.v1",
                        "status": "manual_intervention_required",
                        "credit": "zero_credit",
                        "run_uuid": run_uuid,
                        "failure_stage": stage,
                        "original_exception_type": _exception_type(cause),
                        "failure_seal_exception_type": _exception_type(exc),
                        "output_directory": str(output_directory),
                        "staging_directory": str(staging_directory),
                        "run_reservation": _reservation_reference(
                            reservation_directory,
                            reservation_inventory,
                            reservation_publication,
                        ),
                        "partial_inventory": list(partial_inventory),
                        "partial_inventory_sha_evidence": _inventory_sha_evidence(
                            partial_inventory
                        ),
                        "partial_artifact_evidence": dict(partial_artifact_evidence),
                        "original_failed_publication_handle_observation": (
                            _publication_failure_observation(cause)
                        ),
                        "writer_owned_staging_inventory": list(writer_owned_staging_inventory),
                        "writer_owned_final_inventory": list(writer_owned_final_inventory),
                        "last_known_pre_rename_inventory": list(last_known_pre_rename_inventory),
                        "untrusted_output_observation": dict(untrusted_output_observation),
                        "failure_seal_directory": str(failure_directory),
                        "failure_seal_partial_inventory": list(failure_seal_partial_inventory),
                        "failure_seal_partial_inventory_sha_evidence": (
                            _inventory_sha_evidence(failure_seal_partial_inventory)
                        ),
                        "failure_seal_publication_handle_observation": (
                            _publication_failure_observation(exc)
                        ),
                        "emergency_seal_directory": str(emergency_directory),
                        "emergency_seal_directory_prepublication_inventory": list(
                            emergency_prepublication_inventory
                        ),
                        "automatic_retry_count": 0,
                        "cleanup_or_overwrite_attempted": False,
                        "success_marker_created": False,
                        "go_evidence_eligible": False,
                    }
                ),
            )
            emergency_publication = _publish_one(
                emergency_publisher,
                emergency_directory,
                emergency_artifact,
                run_uuid=run_uuid,
                api_factory=api_factory,
            )
        except BaseException as exc2:
            emergency_error = exc2
            emergency_seal_partial_inventory = _safe_inventory(emergency_directory)
    raise R7S6EvidencePublicationError(
        f"r7s6_evidence_publication_failed:{stage}",
        stage=stage,
        output_directory=output_directory,
        staging_directory=staging_directory,
        partial_inventory=partial_inventory,
        reservation_directory=reservation_directory,
        reservation_inventory=reservation_inventory,
        reservation_publication=reservation_publication,
        writer_owned_staging_inventory=writer_owned_staging_inventory,
        writer_owned_final_inventory=writer_owned_final_inventory,
        last_known_pre_rename_inventory=last_known_pre_rename_inventory,
        untrusted_output_observation=untrusted_output_observation,
        partial_artifact_evidence=partial_artifact_evidence,
        failure_seal_directory=_observed_directory_or_none(failure_directory),
        failure_seal_publication=failure_publication,
        failure_seal_error_type=(
            _exception_type(failure_error) if failure_error is not None else None
        ),
        failure_seal_partial_inventory=failure_seal_partial_inventory,
        emergency_seal_directory=_observed_directory_or_none(emergency_directory),
        emergency_seal_publication=emergency_publication,
        emergency_seal_error_type=(
            _exception_type(emergency_error) if emergency_error is not None else None
        ),
        emergency_seal_partial_inventory=emergency_seal_partial_inventory,
    ) from cause


def _prepared_success_artifact_list(
    prepared_documents: Sequence[r7s4.PreparedJsonArtifact],
) -> list[r7s4.PreparedJsonArtifact]:
    """Make the mutable aggregate list at an injectable protected boundary."""

    return list(prepared_documents)


def _publish_after_reservation(
    *,
    prepared_documents: tuple[r7s4.PreparedJsonArtifact, ...],
    prepared_success_artifacts: list[r7s4.PreparedJsonArtifact],
    staging_publications: list[DurableBoundPublication],
    state: _PostReservationState,
    run_value: str,
    publisher: Publisher,
    failure_publisher: Publisher,
    emergency_publisher: Publisher,
    aggregate_serializer: Serializer,
    directory_publisher: DirectoryPublisher,
    final_verifier: FinalVerifier,
    api_factory: ApiFactory | None,
    output_directory: Path,
    staging_directory: Path,
    reservation_directory: Path,
    reservation_publication: DurableBoundPublication,
    failure_directory: Path,
    emergency_directory: Path,
) -> PreSerializedBatch:
    """Publish after reservation; the caller protects entry into this continuation."""

    try:
        prepared_success_artifacts.extend(_prepared_success_artifact_list(prepared_documents))
        state.stage = "staging_directory_create"
        try:
            os.mkdir(staging_directory)
        except FileExistsError:
            state.stage = "staging_directory_create_collision"
            raise
        state.stage = "document_publication"
        for artifact in prepared_documents:
            actual = _publish_one(
                publisher,
                staging_directory,
                artifact,
                run_uuid=run_value,
                api_factory=api_factory,
            )
            staging_publications.append(
                _staging_publication(actual, staging_directory=staging_directory)
            )

        state.stage = "document_publication_catalog"
        document_entries = [
            {
                "sequence": sequence,
                "leaf": artifact.leaf,
                "role": artifact.role,
                "artifact": _descriptor(artifact),
                "publication": _scoped_publication_snapshot(
                    publication,
                    intended_final_path=output_directory / artifact.leaf,
                ),
            }
            for sequence, (artifact, publication) in enumerate(
                zip(prepared_documents, staging_publications, strict=True),
                start=1,
            )
        ]
        state.stage = "aggregate_manifest_serialization"
        manifest = r7s4.PreparedJsonArtifact(
            leaf=IDENTITY_MANIFEST_LEAF,
            role="r7s6_aggregate_identity_manifest",
            raw=aggregate_serializer(
                {
                    "schema": ("evm.s8-v4.x1.phase-b2.pre-r8-r7s6.aggregate-identity-manifest.v1"),
                    "status": "review_pending",
                    "run_uuid": run_value,
                    "run_reservation": _reservation_reference(
                        reservation_directory,
                        _safe_inventory(reservation_directory),
                        reservation_publication,
                    ),
                    "documents": document_entries,
                    "publication_identity_scope": (
                        "staging_same_handle_before_parent_directory_rename"
                    ),
                    "publication_paths_rebased_to_final": False,
                    "post_rename_handle_identity_verified": False,
                    "all_documents_serialized_before_final_directory": True,
                    "final_directory_publish_pending_at_serialization": True,
                    "completion_or_success_marker_created": False,
                    "production_go_enabled": False,
                    "go_evidence_eligible": False,
                }
            ),
        )
        prepared_success_artifacts.append(manifest)
        state.stage = "aggregate_manifest_publication"
        actual_manifest = _publish_one(
            publisher,
            staging_directory,
            manifest,
            run_uuid=run_value,
            api_factory=api_factory,
        )
        staging_manifest = _staging_publication(
            actual_manifest,
            staging_directory=staging_directory,
        )
        staging_publications.append(staging_manifest)

        state.stage = "aggregate_index_serialization"
        index = r7s4.PreparedJsonArtifact(
            leaf=IDENTITY_INDEX_LEAF,
            role="r7s6_aggregate_identity_index",
            raw=aggregate_serializer(
                {
                    "schema": ("evm.s8-v4.x1.phase-b2.pre-r8-r7s6.aggregate-identity-index.v1"),
                    "status": "review_pending",
                    "run_uuid": run_value,
                    "run_reservation": _reservation_reference(
                        reservation_directory,
                        _safe_inventory(reservation_directory),
                        reservation_publication,
                    ),
                    "documents": document_entries,
                    "aggregate_manifest": {
                        "artifact": _descriptor(manifest),
                        "publication": _scoped_publication_snapshot(
                            staging_manifest,
                            intended_final_path=(output_directory / IDENTITY_MANIFEST_LEAF),
                        ),
                    },
                    "publication_identity_scope": (
                        "staging_same_handle_before_parent_directory_rename"
                    ),
                    "publication_paths_rebased_to_final": False,
                    "post_rename_handle_identity_verified": False,
                    "all_success_json_serialized_before_final_directory": True,
                    "index_serialized_and_published_last_in_staging": True,
                    "terminal_index_identity_requires_external_readback": True,
                    "completion_or_success_marker_created": False,
                    "production_go_enabled": False,
                    "go_evidence_eligible": False,
                }
            ),
        )
        prepared_success_artifacts.append(index)
        state.stage = "aggregate_index_publication"
        actual_index = _publish_one(
            publisher,
            staging_directory,
            index,
            run_uuid=run_value,
            api_factory=api_factory,
        )
        staging_index = _staging_publication(
            actual_index,
            staging_directory=staging_directory,
        )
        staging_publications.append(staging_index)

        if len(prepared_success_artifacts) != len(prepared_documents) + 2:
            raise R7S6EvidenceError("r7s6_success_serialization_count_mismatch")
        state.stage = "final_directory_publish"
        directory_publisher(staging_directory, output_directory)
        state.final_directory_rename_completed = True
        final_lstat = os.lstat(output_directory)
        if (
            os.path.lexists(staging_directory)
            or not stat.S_ISDIR(final_lstat.st_mode)
            or _stat_is_reparse(final_lstat)
        ):
            raise R7S6EvidenceError("r7s6_final_directory_publish_unproven")
        state.stage = "final_directory_handle_readback"
        state.final_verification = final_verifier(
            output_directory,
            tuple(prepared_success_artifacts),
            tuple(staging_publications),
        )
        if type(state.final_verification) is not FinalDirectoryVerification:
            raise R7S6EvidenceError("r7s6_final_verification_type_invalid")
        observed = tuple(
            {
                "leaf": item.get("leaf"),
                "sha256": item.get("sha256"),
                "bytes": item.get("bytes"),
            }
            for item in state.final_verification.inventory
        )
        expected = tuple(
            {
                "leaf": artifact.leaf,
                "sha256": hashlib.sha256(artifact.raw).hexdigest(),
                "bytes": len(artifact.raw),
            }
            for artifact in sorted(
                prepared_success_artifacts,
                key=lambda item: ntpath.normcase(item.leaf),
            )
        )
        if observed != expected:
            raise R7S6EvidenceError("r7s6_final_readback_inventory_mismatch")
        state.final_verification_succeeded = True
        state.stage = "terminal_batch_construction"
        return PreSerializedBatch(
            output_directory=output_directory,
            staging_directory=staging_directory,
            reservation_directory=reservation_directory,
            run_uuid=run_value,
            reservation_publication=reservation_publication,
            document_publications=tuple(staging_publications[:-2]),
            manifest_publication=staging_publications[-2],
            index_publication=staging_publications[-1],
            final_verification=state.final_verification,
            post_rename_handle_identity_verified=(state.final_verification.kernel_handle_bound),
            final_content_sha_readback_verified=True,
        )
    except R7S6EvidencePublicationError:
        raise
    except BaseException as exc:
        _raise_sealed_failure(
            stage=state.stage,
            cause=exc,
            run_uuid=run_value,
            output_directory=output_directory,
            staging_directory=staging_directory,
            reservation_directory=reservation_directory,
            reservation_publication=reservation_publication,
            failure_directory=failure_directory,
            emergency_directory=emergency_directory,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
            final_directory_rename_completed=(state.final_directory_rename_completed),
            final_verification_succeeded=state.final_verification_succeeded,
            prepared_success_artifacts=prepared_success_artifacts,
            staging_publications=staging_publications,
        )


def _publish_pre_serialized_batch(
    parent_directory: str | os.PathLike[str],
    output_leaf: str,
    documents: Mapping[str, Any],
    *,
    run_uuid: str | uuid.UUID,
    publisher: Publisher,
    failure_publisher: Publisher,
    emergency_publisher: Publisher,
    aggregate_serializer: Serializer,
    directory_publisher: DirectoryPublisher,
    final_verifier: FinalVerifier,
    api_factory: ApiFactory | None,
) -> PreSerializedBatch:
    try:
        leaf = r7s4._leaf(output_leaf, label="output_leaf")
        run_value = str(uuid.UUID(str(run_uuid)))
    except (r7s4.R7S4EvidenceError, ValueError) as exc:
        raise R7S6EvidenceError("r7s6_input_or_serialization_invalid") from exc
    forbidden = {ntpath.normcase(item) for item in (*FORBIDDEN_SUCCESS_LEAVES, *CONTROL_LEAVES)}
    if isinstance(documents, Mapping):
        for requested_leaf in documents:
            try:
                normalized_leaf = ntpath.normcase(r7s4._leaf(requested_leaf, label="document_leaf"))
            except r7s4.R7S4EvidenceError as exc:
                raise R7S6EvidenceError("r7s6_input_or_serialization_invalid") from exc
            if normalized_leaf in forbidden:
                raise R7S6EvidenceError("r7s6_reserved_or_success_leaf_forbidden")
    try:
        prepared_documents = r7s4.prepare_json_batch(documents)
    except (r7s4.R7S4EvidenceError, ValueError) as exc:
        raise R7S6EvidenceError("r7s6_input_or_serialization_invalid") from exc

    directory_leaves = _validate_planned_namespace(
        prepared_documents,
        output_leaf=leaf,
        run_uuid=run_value,
    )
    parent = Path(parent_directory).resolve()
    output_directory = parent / directory_leaves["output"]
    staging_directory = parent / directory_leaves["staging"]
    failure_directory = parent / directory_leaves["failure"]
    emergency_directory = parent / directory_leaves["emergency"]
    reservation_directory = parent / directory_leaves["reservation"]
    if os.path.lexists(reservation_directory):
        raise R7S6EvidenceError("r7s6_parent_global_run_uuid_already_reserved")
    _preflight_paths(
        output_directory,
        staging_directory,
        failure_directory,
        emergency_directory,
        reservation_directory,
    )

    # Conservative state exists before the first mutation.  Every fallible
    # post-reservation transition that changes it is inside the main seal try.
    staging_publications: list[DurableBoundPublication] = []
    prepared_success_artifacts: list[r7s4.PreparedJsonArtifact] = []
    post_reservation_state = _PostReservationState()
    reservation_publication: DurableBoundPublication | None = None
    reservation_stage = "run_uuid_reservation_create"
    try:
        # The directory name is keyed solely by run UUID, not output leaf.
        # mkdir is the parent-global, create-exclusive one-shot reservation.
        os.mkdir(reservation_directory)
        reservation_stage = "run_uuid_reservation_serialization"
        reservation_artifact = r7s4.PreparedJsonArtifact(
            leaf=RUN_RESERVATION_LEAF,
            role="r7s6_parent_global_run_uuid_reservation",
            raw=canonical_json_bytes(
                {
                    "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s6.run-reservation.v1",
                    "run_uuid": run_value,
                    "reservation_key_scope": "parent_global_run_uuid_only",
                    "reservation_directory": str(reservation_directory),
                    "output_leaf": leaf,
                    "output_directory": str(output_directory),
                    "staging_directory": str(staging_directory),
                    "failure_directory": str(failure_directory),
                    "emergency_directory": str(emergency_directory),
                    "created_exclusively": True,
                    "writer_policy_immutable": True,
                    "same_token_hostile_admin_protected": False,
                    "released_or_deleted": False,
                    "automatic_retry_count": 0,
                }
            ),
        )
        reservation_stage = "run_uuid_reservation_publication"
        reservation_publication = _publish_one(
            publisher,
            reservation_directory,
            reservation_artifact,
            run_uuid=run_value,
            api_factory=api_factory,
        )
        reservation_stage = "post_reservation_continuation_dispatch"
        return _publish_after_reservation(
            prepared_documents=prepared_documents,
            prepared_success_artifacts=prepared_success_artifacts,
            staging_publications=staging_publications,
            state=post_reservation_state,
            run_value=run_value,
            publisher=publisher,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            aggregate_serializer=aggregate_serializer,
            directory_publisher=directory_publisher,
            final_verifier=final_verifier,
            api_factory=api_factory,
            output_directory=output_directory,
            staging_directory=staging_directory,
            reservation_directory=reservation_directory,
            reservation_publication=reservation_publication,
            failure_directory=failure_directory,
            emergency_directory=emergency_directory,
        )
    except R7S6EvidencePublicationError:
        raise
    except FileExistsError as exc:
        if reservation_stage == "run_uuid_reservation_create":
            raise R7S6EvidenceError("r7s6_parent_global_run_uuid_already_reserved") from exc
        _raise_sealed_failure(
            stage=reservation_stage,
            cause=exc,
            run_uuid=run_value,
            output_directory=output_directory,
            staging_directory=staging_directory,
            reservation_directory=reservation_directory,
            reservation_publication=reservation_publication,
            failure_directory=failure_directory,
            emergency_directory=emergency_directory,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
        )
    except BaseException as exc:
        _raise_sealed_failure(
            stage=reservation_stage,
            cause=exc,
            run_uuid=run_value,
            output_directory=output_directory,
            staging_directory=staging_directory,
            reservation_directory=reservation_directory,
            reservation_publication=reservation_publication,
            failure_directory=failure_directory,
            emergency_directory=emergency_directory,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
        )


def publish_pre_serialized_batch(
    parent_directory: str | os.PathLike[str],
    output_leaf: str,
    documents: Mapping[str, Any],
    *,
    run_uuid: str | uuid.UUID,
) -> PreSerializedBatch:
    return _publish_pre_serialized_batch(
        parent_directory,
        output_leaf,
        documents,
        run_uuid=run_uuid,
        publisher=publish_bound_no_replace_durable,
        failure_publisher=publish_bound_no_replace_durable,
        emergency_publisher=publish_bound_no_replace_durable,
        aggregate_serializer=canonical_json_bytes,
        directory_publisher=_rename_staging_no_replace,
        final_verifier=_verify_final_windows,
        api_factory=None,
    )


def _publish_pre_serialized_batch_for_test(
    parent_directory: str | os.PathLike[str],
    output_leaf: str,
    documents: Mapping[str, Any],
    *,
    run_uuid: str | uuid.UUID,
    publisher: Publisher,
    failure_publisher: Publisher | None = None,
    emergency_publisher: Publisher | None = None,
    aggregate_serializer: Serializer = canonical_json_bytes,
    directory_publisher: DirectoryPublisher = os.rename,
    final_verifier: FinalVerifier = _verify_final_test_only,
    api_factory: ApiFactory | None = None,
) -> PreSerializedBatch:
    return _publish_pre_serialized_batch(
        parent_directory,
        output_leaf,
        documents,
        run_uuid=run_uuid,
        publisher=publisher,
        failure_publisher=failure_publisher or publisher,
        emergency_publisher=emergency_publisher or publisher,
        aggregate_serializer=aggregate_serializer,
        directory_publisher=directory_publisher,
        final_verifier=final_verifier,
        api_factory=api_factory,
    )


def source_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s6.evidence-writer.v1",
        "same_filesystem_staging_directory": True,
        "parent_global_run_uuid_reservation": True,
        "run_reservation_key_scope": "run_uuid_only_not_output_leaf",
        "run_reservation_released_or_deleted": False,
        "success_and_failure_reference_run_reservation": True,
        "all_parent_control_directory_leaves_validated_before_write": True,
        "reservation_directory_in_mutual_preflight_collision_set": True,
        "all_publisher_temporary_leaves_validated_before_write": True,
        "final_temporary_leaf_collisions_rejected_before_write": True,
        "planned_namespace_rejection_publisher_call_count": 0,
        "all_success_json_serialized_before_final_output_directory": True,
        "documents_manifest_index_published_and_flushed_in_staging": True,
        "exclusive_final_directory_publish": True,
        "atomic_final_directory_rename": True,
        "parent_directory_flush_after_final_rename": True,
        "documents_precede_manifest": True,
        "manifest_precedes_index": True,
        "index_published_last": True,
        "publication_identity_scope": "staging_same_handle_before_parent_directory_rename",
        "publication_paths_rebased_to_final": False,
        "embedded_manifest_post_rename_observation": False,
        "returned_batch_post_rename_observation": True,
        "post_rename_content_sha_readback": "windows_open_file_handles_twice",
        "post_rename_handle_identity_verified": True,
        "post_rename_staging_to_final_file_id_crosscheck": True,
        "final_directory_handle_delete_share": False,
        "final_directory_handle_write_share": False,
        "final_child_handle_delete_share": False,
        "final_child_handle_write_share": False,
        "final_handles_held_through_exact_leaf_inventory": True,
        "root_and_child_st_file_attributes_reparse_checked": True,
        "partial_artifacts_preserved_unmodified": True,
        "partial_artifacts_sha_referenced_by_sibling_failure_seal": False,
        "partial_inventory_sha_completeness_recorded": True,
        "unreadable_or_nonregular_partial_downgrades_to_unproven": True,
        "empty_inventory_is_not_proof_of_no_partial_artifacts": True,
        "no_partial_claim_requires_staging_and_final_paths_absent": True,
        "post_rename_verification_failure_final_content_downgraded_to_unproven": True,
        "durable_publication_failure_same_handle_observation_preserved": True,
        "last_known_pre_rename_sha_inventory_in_failure_seal": True,
        "writer_owned_staging_inventory_prioritized": True,
        "unexpected_output_observation": "lstat_only_no_path_follow_or_content_read",
        "failure_seal_attempts": 1,
        "mutation_to_next_protected_boundary_base_exception_sealed": True,
        "terminal_batch_construction_base_exception_sealed": True,
        "failure_context_preparation_failure_uses_independent_emergency_seal": True,
        "failure_seal_partial_inventory_referenced_by_emergency_seal": True,
        "emergency_seal_after_failure_seal_failure": True,
        "automatic_retry_count": 0,
        "cleanup_or_overwrite_on_failure": False,
        "success_or_completion_marker_supported": False,
        "same_token_hostile_admin_protected": False,
        "power_loss_durability_proven": False,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


__all__ = [
    "EMERGENCY_SEAL_LEAF",
    "FAILURE_SEAL_LEAF",
    "IDENTITY_INDEX_LEAF",
    "IDENTITY_MANIFEST_LEAF",
    "RUN_RESERVATION_LEAF",
    "PreSerializedBatch",
    "R7S6EvidenceError",
    "R7S6EvidencePublicationError",
    "canonical_json_bytes",
    "planned_parent_directory_leaves",
    "publish_pre_serialized_batch",
    "source_contract",
]
