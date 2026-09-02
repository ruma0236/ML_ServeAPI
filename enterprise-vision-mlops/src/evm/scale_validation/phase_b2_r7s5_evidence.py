"""Identity-catalogued, append-only r7s5 review evidence.

This module is intentionally review-only.  It improves the r7s4 writer by
persisting the validated handle/flush publication record for every document
inside the aggregate manifest, and the manifest publication record inside the
terminal index.  The index cannot contain its own post-publication identity;
that boundary remains an external read-back requirement and therefore cannot
be used as production GO evidence.

There is no retry, overwrite, cleanup, success marker, or Phase B2 execution
path in this module.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn

from evm.scale_validation import phase_b2_r7s4_evidence as r7s4
from evm.scale_validation.phase_b2_r7s4_handle_io import (
    DurableBoundPublication,
    DurableHandleApi,
    DurablePublicationError,
    publish_bound_no_replace_durable,
)


IDENTITY_MANIFEST_LEAF = "aggregate-publication-identity-manifest.json"
IDENTITY_INDEX_LEAF = "aggregate-publication-identity-index.json"
ATOMIC_FAILURE_SEAL_LEAF = "r7s5-atomic-failure-seal.json"
EMERGENCY_SEAL_LEAF = "r7s5-emergency-seal.json"
FORBIDDEN_SUCCESS_LEAVES = frozenset(
    {
        "completion-marker.json",
        "private-success-index.json",
        "success-index.json",
        "phase-b2-success.json",
    }
)
CONTROL_LEAVES = frozenset(
    {
        IDENTITY_MANIFEST_LEAF,
        IDENTITY_INDEX_LEAF,
        ATOMIC_FAILURE_SEAL_LEAF,
        EMERGENCY_SEAL_LEAF,
    }
)


Publisher = Callable[..., DurableBoundPublication]
ApiFactory = Callable[[str], DurableHandleApi]
Serializer = Callable[[Any], bytes]


class R7S5EvidenceError(RuntimeError):
    """The r7s5 review evidence contract was not satisfied."""


class R7S5EvidencePublicationError(R7S5EvidenceError):
    """A terminal publication failure with immutable prior-publication facts."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        output_directory: Path,
        attempted_leaf: str | None,
        attempted_artifact: Mapping[str, Any] | None,
        publications: tuple[DurableBoundPublication, ...],
        failure_seal_publication: DurableBoundPublication | None,
        failure_seal_error_type: str | None,
        emergency_seal_directory: Path | None,
        emergency_seal_publication: DurableBoundPublication | None,
        emergency_seal_error_type: str | None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.output_directory = output_directory
        self.attempted_leaf = attempted_leaf
        self.attempted_artifact = (
            dict(attempted_artifact) if attempted_artifact is not None else None
        )
        self.publications = publications
        self.failure_seal_publication = failure_seal_publication
        self.failure_seal_error_type = failure_seal_error_type
        self.emergency_seal_directory = emergency_seal_directory
        self.emergency_seal_publication = emergency_seal_publication
        self.emergency_seal_error_type = emergency_seal_error_type
        self.retry_count = 0
        self.automatic_retry_count = 0
        self.downstream_call_count = 0
        self.manual_intervention_required = True
        self.success_marker_created = False
        self.go_evidence_eligible = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.publication-failure.v1",
            "stage": self.stage,
            "output_directory": str(self.output_directory),
            "attempted_leaf": self.attempted_leaf,
            "attempted_artifact": self.attempted_artifact,
            "publications": [item.to_dict() for item in self.publications],
            "failure_seal_publication": (
                self.failure_seal_publication.to_dict()
                if self.failure_seal_publication is not None
                else None
            ),
            "failure_seal_error_type": self.failure_seal_error_type,
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
            "success_marker_created": self.success_marker_created,
            "go_evidence_eligible": self.go_evidence_eligible,
        }


@dataclass(frozen=True)
class IdentityCataloguedBatch:
    output_directory: Path
    run_uuid: str
    document_publications: tuple[DurableBoundPublication, ...]
    manifest_publication: DurableBoundPublication
    index_publication: DurableBoundPublication
    status: str = "review_pending"
    retry_count: int = 0
    success_marker_created: bool = False
    production_go_enabled: bool = False
    go_evidence_eligible: bool = False

    @property
    def publications(self) -> tuple[DurableBoundPublication, ...]:
        return (*self.document_publications, self.manifest_publication, self.index_publication)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.identity-catalogued-batch.v1",
            "output_directory": str(self.output_directory),
            "run_uuid": self.run_uuid,
            "status": self.status,
            "document_publications": [item.to_dict() for item in self.document_publications],
            "manifest_publication": self.manifest_publication.to_dict(),
            "index_publication": self.index_publication.to_dict(),
            "retry_count": self.retry_count,
            "success_marker_created": self.success_marker_created,
            "production_go_enabled": self.production_go_enabled,
            "go_evidence_eligible": self.go_evidence_eligible,
        }


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


def _prepared(
    leaf: str, value: Any, *, role: str, serializer: Serializer
) -> r7s4.PreparedJsonArtifact:
    return r7s4.PreparedJsonArtifact(leaf=leaf, raw=serializer(value), role=role)


def _descriptor(artifact: r7s4.PreparedJsonArtifact) -> dict[str, Any]:
    return {
        "leaf": artifact.leaf,
        "role": artifact.role,
        "sha256": hashlib.sha256(artifact.raw).hexdigest(),
        "bytes": len(artifact.raw),
    }


def _publication_snapshot(publication: DurableBoundPublication) -> dict[str, Any]:
    if type(publication) is not DurableBoundPublication:
        raise R7S5EvidenceError("r7s5_exact_publication_type_required")
    raw = canonical_json_bytes(publication.to_dict())
    parsed = json.loads(raw)
    if type(parsed) is not dict:
        raise R7S5EvidenceError("r7s5_publication_snapshot_mapping_required")
    return parsed


def _attempted_snapshot(
    artifact: r7s4.PreparedJsonArtifact | None,
    *,
    leaf: str | None,
    role: str,
    publication_may_have_committed: bool,
) -> dict[str, Any]:
    if artifact is not None:
        value = _descriptor(artifact)
        value["serialized"] = True
    else:
        value = {
            "leaf": leaf,
            "role": role,
            "sha256": None,
            "bytes": None,
            "serialized": False,
        }
    value["publication_may_have_committed"] = publication_may_have_committed
    return value


def _publication_catalog_entry(
    publication: DurableBoundPublication,
    *,
    leaf: str,
    role: str,
    sequence: int,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "leaf": leaf,
        "role": role,
        "publication": _publication_snapshot(publication),
    }


def _validate_document_leaf_space(documents: tuple[r7s4.PreparedJsonArtifact, ...]) -> None:
    forbidden = {ntpath.normcase(item) for item in (*FORBIDDEN_SUCCESS_LEAVES, *CONTROL_LEAVES)}
    for artifact in documents:
        if ntpath.normcase(artifact.leaf) in forbidden:
            raise R7S5EvidenceError("r7s5_reserved_or_success_leaf_forbidden")


def _validate_planned_leaf_space(
    documents: tuple[r7s4.PreparedJsonArtifact, ...],
    *,
    output_leaf: str,
    run_uuid: str,
) -> None:
    """Reject every deterministic final/temp/parent collision before mkdir."""

    final_leaves = [item.leaf for item in documents]
    final_leaves.extend(sorted(CONTROL_LEAVES, key=ntpath.normcase))
    normalized_finals = [ntpath.normcase(item) for item in final_leaves]
    if len(set(normalized_finals)) != len(normalized_finals):
        raise R7S5EvidenceError("r7s5_planned_final_leaf_collision")

    temporary_leaves: list[str] = []
    for final_leaf in final_leaves:
        try:
            temporary_leaves.append(r7s4._temporary_leaf(final_leaf, run_uuid))
        except r7s4.R7S4EvidenceError as exc:
            raise R7S5EvidenceError("r7s5_planned_temporary_leaf_invalid") from exc
    normalized_temporaries = [ntpath.normcase(item) for item in temporary_leaves]
    if len(set(normalized_temporaries)) != len(normalized_temporaries):
        raise R7S5EvidenceError("r7s5_planned_temporary_leaf_collision")
    if set(normalized_finals).intersection(normalized_temporaries):
        raise R7S5EvidenceError("r7s5_planned_final_temporary_leaf_collision")

    parent_leaves = (
        f".{output_leaf}.{run_uuid}.r7s5-emergency-seal",
        f".{output_leaf}.{run_uuid}.r7s5-create-failure",
    )
    try:
        validated_parent_leaves = [
            r7s4._leaf(item, label="r7s5_parent_control_leaf") for item in parent_leaves
        ]
    except r7s4.R7S4EvidenceError as exc:
        raise R7S5EvidenceError("r7s5_parent_control_leaf_invalid") from exc
    normalized_parent = [ntpath.normcase(item) for item in validated_parent_leaves]
    if (
        len(set(normalized_parent)) != len(normalized_parent)
        or ntpath.normcase(output_leaf) in normalized_parent
    ):
        raise R7S5EvidenceError("r7s5_parent_control_leaf_collision")


def _publish_one(
    publisher: Publisher,
    directory: Path,
    artifact: r7s4.PreparedJsonArtifact,
    *,
    run_uuid: str,
    api_factory: ApiFactory | None,
) -> DurableBoundPublication:
    # Reuse the strict r7s4 path/SHA/bytes/identity/flush/DACL validator.
    publication = r7s4._publish_one(
        publisher,
        directory,
        artifact,
        run_uuid=run_uuid,
        api_factory=api_factory,
    )
    if type(publication) is not DurableBoundPublication:
        raise R7S5EvidenceError("r7s5_exact_publication_type_required")
    # Materialize and canonicalize the complete identity now.  Later aggregate
    # and seal construction therefore cannot invoke a publisher-owned method.
    canonical_json_bytes(publication.to_dict())
    return publication


def _failure_value(
    *,
    run_uuid: str,
    output_directory: Path,
    stage: str,
    attempted_leaf: str | None,
    attempted_artifact: Mapping[str, Any],
    publications: tuple[DurableBoundPublication, ...],
    cause: BaseException,
) -> dict[str, Any]:
    observation: Mapping[str, Any]
    try:
        if isinstance(cause, DurablePublicationError):
            candidate = cause.observation.to_dict()
            canonical_json_bytes(candidate)
            observation = candidate
        else:
            raise TypeError("no_handle_publication_observation")
    except Exception as observation_error:
        observation = {
            "observation_status": "unknown",
            "observation_error_type": _exception_type(observation_error),
            "manual_intervention_required": True,
            "go_evidence_eligible": False,
        }
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.atomic-failure-seal.v1",
        "status": "manual_intervention_required",
        "credit": "zero_credit",
        "run_uuid": run_uuid,
        "output_directory": str(output_directory),
        "failure_stage": stage,
        "attempted_leaf": attempted_leaf,
        "attempted_artifact": dict(attempted_artifact),
        "exception_type": _exception_type(cause),
        "failed_publication_observation": dict(observation),
        "already_published_count": len(publications),
        "already_published": [_publication_snapshot(item) for item in publications],
        "retry_count": 0,
        "automatic_retry_count": 0,
        "cleanup_or_overwrite_attempted": False,
        "success_marker_created": False,
        "process_residue": "not_observed_by_evidence_writer",
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


def _raise_sealed_failure(
    *,
    parent: Path,
    output_leaf: str,
    output_directory: Path,
    run_uuid: str,
    stage: str,
    attempted_leaf: str | None,
    attempted_artifact: Mapping[str, Any],
    publications: tuple[DurableBoundPublication, ...],
    cause: BaseException,
    failure_publisher: Publisher,
    emergency_publisher: Publisher,
    api_factory: ApiFactory | None,
) -> NoReturn:
    failure_publication: DurableBoundPublication | None = None
    failure_error: BaseException | None = None
    emergency_directory: Path | None = None
    emergency_publication: DurableBoundPublication | None = None
    emergency_error: BaseException | None = None
    failure_artifact: r7s4.PreparedJsonArtifact | None = None
    try:
        failure_artifact = _prepared(
            ATOMIC_FAILURE_SEAL_LEAF,
            _failure_value(
                run_uuid=run_uuid,
                output_directory=output_directory,
                stage=stage,
                attempted_leaf=attempted_leaf,
                attempted_artifact=attempted_artifact,
                publications=publications,
                cause=cause,
            ),
            role="atomic_failure_seal",
            serializer=canonical_json_bytes,
        )
        failure_publication = _publish_one(
            failure_publisher,
            output_directory,
            failure_artifact,
            run_uuid=run_uuid,
            api_factory=api_factory,
        )
    except Exception as exc:
        failure_error = exc
        emergency_leaf = f".{output_leaf}.{run_uuid}.r7s5-emergency-seal"
        emergency_directory = parent / emergency_leaf
        try:
            os.mkdir(emergency_directory)
            emergency_artifact = _prepared(
                EMERGENCY_SEAL_LEAF,
                {
                    "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.emergency-seal.v1",
                    "status": "manual_intervention_required",
                    "credit": "zero_credit",
                    "run_uuid": run_uuid,
                    "failed_output_directory": str(output_directory),
                    "failure_stage": stage,
                    "attempted_leaf": attempted_leaf,
                    "attempted_artifact": dict(attempted_artifact),
                    "original_exception_type": _exception_type(cause),
                    "atomic_failure_seal": (
                        _descriptor(failure_artifact)
                        if failure_artifact is not None
                        else {
                            "leaf": ATOMIC_FAILURE_SEAL_LEAF,
                            "role": "atomic_failure_seal",
                            "sha256": None,
                            "bytes": None,
                            "serialized": False,
                        }
                    ),
                    "atomic_failure_seal_exception_type": _exception_type(exc),
                    "already_published_count": len(publications),
                    "already_published": [_publication_snapshot(item) for item in publications],
                    "retry_count": 0,
                    "cleanup_or_overwrite_attempted": False,
                    "success_marker_created": False,
                    "production_go_enabled": False,
                    "go_evidence_eligible": False,
                },
                role="emergency_failure_seal",
                serializer=canonical_json_bytes,
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

    error = R7S5EvidencePublicationError(
        f"r7s5_evidence_publication_failed:{stage}",
        stage=stage,
        output_directory=output_directory,
        attempted_leaf=attempted_leaf,
        attempted_artifact=attempted_artifact,
        publications=publications,
        failure_seal_publication=failure_publication,
        failure_seal_error_type=(
            _exception_type(failure_error) if failure_error is not None else None
        ),
        emergency_seal_directory=emergency_directory,
        emergency_seal_publication=emergency_publication,
        emergency_seal_error_type=(
            _exception_type(emergency_error) if emergency_error is not None else None
        ),
    )
    raise error from cause


def _publish_identity_catalogued_batch(
    parent_directory: str | os.PathLike[str],
    output_leaf: str,
    documents: Mapping[str, Any],
    *,
    run_uuid: str | uuid.UUID,
    publisher: Publisher,
    failure_publisher: Publisher,
    emergency_publisher: Publisher,
    aggregate_serializer: Serializer,
    api_factory: ApiFactory | None,
) -> IdentityCataloguedBatch:
    try:
        prepared_documents = r7s4.prepare_json_batch(documents)
    except r7s4.R7S4EvidenceError as exc:
        raise R7S5EvidenceError(str(exc)) from exc
    _validate_document_leaf_space(prepared_documents)
    try:
        leaf = r7s4._leaf(output_leaf, label="output_leaf")
        run_value = str(uuid.UUID(str(run_uuid)))
    except (r7s4.R7S4EvidenceError, ValueError) as exc:
        raise R7S5EvidenceError("r7s5_output_or_run_identity_invalid") from exc
    _validate_planned_leaf_space(
        prepared_documents,
        output_leaf=leaf,
        run_uuid=run_value,
    )

    parent = Path(parent_directory).resolve()
    output_directory = parent / leaf
    try:
        os.mkdir(output_directory)
    except Exception as exc:
        # No output directory means an in-directory seal is impossible.  Use a
        # unique parent emergency directory once; never replace the occupant.
        emergency_directory = parent / f".{leaf}.{run_value}.r7s5-create-failure"
        emergency_publication: DurableBoundPublication | None = None
        emergency_error: BaseException | None = None
        try:
            os.mkdir(emergency_directory)
            emergency = _prepared(
                EMERGENCY_SEAL_LEAF,
                {
                    "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.create-failure.v1",
                    "status": "manual_intervention_required",
                    "credit": "zero_credit",
                    "run_uuid": run_value,
                    "output_directory": str(output_directory),
                    "exception_type": _exception_type(exc),
                    "downstream_call_count": 0,
                    "retry_count": 0,
                    "cleanup_or_overwrite_attempted": False,
                    "success_marker_created": False,
                    "go_evidence_eligible": False,
                },
                role="create_failure_seal",
                serializer=canonical_json_bytes,
            )
            emergency_publication = _publish_one(
                emergency_publisher,
                emergency_directory,
                emergency,
                run_uuid=run_value,
                api_factory=api_factory,
            )
        except Exception as exc2:
            emergency_error = exc2
        error = R7S5EvidencePublicationError(
            "r7s5_output_directory_create_failed",
            stage="output_directory_create",
            output_directory=output_directory,
            attempted_leaf=None,
            attempted_artifact={
                "leaf": leaf,
                "role": "output_directory",
                "sha256": None,
                "bytes": None,
                "serialized": False,
                "publication_may_have_committed": False,
            },
            publications=(),
            failure_seal_publication=None,
            failure_seal_error_type=None,
            emergency_seal_directory=emergency_directory,
            emergency_seal_publication=emergency_publication,
            emergency_seal_error_type=(
                _exception_type(emergency_error) if emergency_error is not None else None
            ),
        )
        raise error from exc

    published: list[DurableBoundPublication] = []
    for artifact in prepared_documents:
        try:
            published.append(
                _publish_one(
                    publisher,
                    output_directory,
                    artifact,
                    run_uuid=run_value,
                    api_factory=api_factory,
                )
            )
        except Exception as exc:
            _raise_sealed_failure(
                parent=parent,
                output_leaf=leaf,
                output_directory=output_directory,
                run_uuid=run_value,
                stage="document_publication",
                attempted_leaf=artifact.leaf,
                attempted_artifact=_attempted_snapshot(
                    artifact,
                    leaf=artifact.leaf,
                    role=artifact.role,
                    publication_may_have_committed=True,
                ),
                publications=tuple(published),
                cause=exc,
                failure_publisher=failure_publisher,
                emergency_publisher=emergency_publisher,
                api_factory=api_factory,
            )

    try:
        document_entries = [
            _publication_catalog_entry(
                publication,
                leaf=artifact.leaf,
                role=artifact.role,
                sequence=index,
            )
            for index, (artifact, publication) in enumerate(
                zip(prepared_documents, published, strict=True), start=1
            )
        ]
        canonical_json_bytes(document_entries)
    except Exception as exc:
        _raise_sealed_failure(
            parent=parent,
            output_leaf=leaf,
            output_directory=output_directory,
            run_uuid=run_value,
            stage="document_publication_catalog",
            attempted_leaf=IDENTITY_MANIFEST_LEAF,
            attempted_artifact=_attempted_snapshot(
                None,
                leaf=IDENTITY_MANIFEST_LEAF,
                role="aggregate_publication_identity_manifest",
                publication_may_have_committed=False,
            ),
            publications=tuple(published),
            cause=exc,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
        )
    try:
        manifest = _prepared(
            IDENTITY_MANIFEST_LEAF,
            {
                "schema": (
                    "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.aggregate-publication-identity-manifest.v1"
                ),
                "status": "review_pending",
                "run_uuid": run_value,
                "documents": document_entries,
                "document_publication_count": len(document_entries),
                "document_handle_flush_identity_persisted": True,
                "publication_order": [entry["leaf"] for entry in document_entries],
                "retry_count": 0,
                "completion_or_success_marker_created": False,
                "production_go_enabled": False,
                "go_evidence_eligible": False,
            },
            role="aggregate_publication_identity_manifest",
            serializer=aggregate_serializer,
        )
    except Exception as exc:
        _raise_sealed_failure(
            parent=parent,
            output_leaf=leaf,
            output_directory=output_directory,
            run_uuid=run_value,
            stage="aggregate_manifest_serialization",
            attempted_leaf=IDENTITY_MANIFEST_LEAF,
            attempted_artifact=_attempted_snapshot(
                None,
                leaf=IDENTITY_MANIFEST_LEAF,
                role="aggregate_publication_identity_manifest",
                publication_may_have_committed=False,
            ),
            publications=tuple(published),
            cause=exc,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
        )
    try:
        manifest_publication = _publish_one(
            publisher,
            output_directory,
            manifest,
            run_uuid=run_value,
            api_factory=api_factory,
        )
        published.append(manifest_publication)
    except Exception as exc:
        _raise_sealed_failure(
            parent=parent,
            output_leaf=leaf,
            output_directory=output_directory,
            run_uuid=run_value,
            stage="aggregate_manifest_publication",
            attempted_leaf=IDENTITY_MANIFEST_LEAF,
            attempted_artifact=_attempted_snapshot(
                manifest,
                leaf=IDENTITY_MANIFEST_LEAF,
                role=manifest.role,
                publication_may_have_committed=True,
            ),
            publications=tuple(published),
            cause=exc,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
        )

    try:
        index = _prepared(
            IDENTITY_INDEX_LEAF,
            {
                "schema": (
                    "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.aggregate-publication-identity-index.v1"
                ),
                "status": "review_pending",
                "run_uuid": run_value,
                "documents": document_entries,
                "aggregate_manifest": {
                    "artifact": _descriptor(manifest),
                    "publication": _publication_snapshot(manifest_publication),
                },
                "index_published_last": True,
                "terminal_index_identity_requires_external_readback": True,
                "retry_count": 0,
                "completion_or_success_marker_created": False,
                "production_go_enabled": False,
                "go_evidence_eligible": False,
            },
            role="aggregate_publication_identity_index",
            serializer=aggregate_serializer,
        )
    except Exception as exc:
        _raise_sealed_failure(
            parent=parent,
            output_leaf=leaf,
            output_directory=output_directory,
            run_uuid=run_value,
            stage="aggregate_index_serialization",
            attempted_leaf=IDENTITY_INDEX_LEAF,
            attempted_artifact=_attempted_snapshot(
                None,
                leaf=IDENTITY_INDEX_LEAF,
                role="aggregate_publication_identity_index",
                publication_may_have_committed=False,
            ),
            publications=tuple(published),
            cause=exc,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
        )
    try:
        index_publication = _publish_one(
            publisher,
            output_directory,
            index,
            run_uuid=run_value,
            api_factory=api_factory,
        )
        published.append(index_publication)
    except Exception as exc:
        _raise_sealed_failure(
            parent=parent,
            output_leaf=leaf,
            output_directory=output_directory,
            run_uuid=run_value,
            stage="aggregate_index_publication",
            attempted_leaf=IDENTITY_INDEX_LEAF,
            attempted_artifact=_attempted_snapshot(
                index,
                leaf=IDENTITY_INDEX_LEAF,
                role=index.role,
                publication_may_have_committed=True,
            ),
            publications=tuple(published),
            cause=exc,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            api_factory=api_factory,
        )

    return IdentityCataloguedBatch(
        output_directory=output_directory,
        run_uuid=run_value,
        document_publications=tuple(published[:-2]),
        manifest_publication=manifest_publication,
        index_publication=index_publication,
    )


def publish_identity_catalogued_batch(
    parent_directory: str | os.PathLike[str],
    output_leaf: str,
    documents: Mapping[str, Any],
    *,
    run_uuid: str | uuid.UUID,
) -> IdentityCataloguedBatch:
    """Fixed production-facing review writer with no injectable I/O seam."""

    return _publish_identity_catalogued_batch(
        parent_directory,
        output_leaf,
        documents,
        run_uuid=run_uuid,
        publisher=publish_bound_no_replace_durable,
        failure_publisher=publish_bound_no_replace_durable,
        emergency_publisher=publish_bound_no_replace_durable,
        aggregate_serializer=canonical_json_bytes,
        api_factory=None,
    )


def _publish_identity_catalogued_batch_for_test(
    parent_directory: str | os.PathLike[str],
    output_leaf: str,
    documents: Mapping[str, Any],
    *,
    run_uuid: str | uuid.UUID,
    publisher: Publisher,
    failure_publisher: Publisher | None = None,
    emergency_publisher: Publisher | None = None,
    aggregate_serializer: Serializer = canonical_json_bytes,
    api_factory: ApiFactory | None = None,
) -> IdentityCataloguedBatch:
    """Private deterministic fault-injection seam."""

    return _publish_identity_catalogued_batch(
        parent_directory,
        output_leaf,
        documents,
        run_uuid=run_uuid,
        publisher=publisher,
        failure_publisher=failure_publisher or publisher,
        emergency_publisher=emergency_publisher or publisher,
        aggregate_serializer=aggregate_serializer,
        api_factory=api_factory,
    )


def source_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.evidence-writer.v1",
        "legacy_evidence_modified": False,
        "document_publication_identity_persisted_in_manifest": True,
        "document_file_id_volume_dacl_and_flush_persisted": True,
        "manifest_publication_identity_persisted_in_index": True,
        "index_published_last": True,
        "terminal_index_self_identity_embedded": False,
        "terminal_index_external_readback_required": True,
        "aggregate_late_serialization": True,
        "all_json_serialized_before_output_directory": False,
        "all_final_temporary_and_parent_control_leaves_preflighted": True,
        "exact_publication_dataclass_type_required": True,
        "publication_snapshot_canonicalized_immediately": True,
        "exclusive_create_and_no_replace": True,
        "directory_flush_required_per_artifact": True,
        "atomic_failure_seal_attempts": 1,
        "upper_emergency_seal_after_atomic_failure_attempts": 1,
        "failure_seal_persists_all_prior_publication_identities": True,
        "failure_seal_persists_attempted_sha_bytes_or_unserialized_state": True,
        "ambiguous_publication_marked_may_have_committed": True,
        "retry_count": 0,
        "automatic_retry_count": 0,
        "cleanup_or_overwrite_on_failure": False,
        "success_or_completion_marker_supported": False,
        "fixed_global_reservation_integrated": False,
        "multi_host_global_one_shot_proven": False,
        "same_token_hostile_admin_protected": False,
        "power_loss_durability_proven": False,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


__all__ = [
    "ATOMIC_FAILURE_SEAL_LEAF",
    "CONTROL_LEAVES",
    "EMERGENCY_SEAL_LEAF",
    "FORBIDDEN_SUCCESS_LEAVES",
    "IDENTITY_INDEX_LEAF",
    "IDENTITY_MANIFEST_LEAF",
    "IdentityCataloguedBatch",
    "R7S5EvidenceError",
    "R7S5EvidencePublicationError",
    "canonical_json_bytes",
    "publish_identity_catalogued_batch",
    "source_contract",
]
