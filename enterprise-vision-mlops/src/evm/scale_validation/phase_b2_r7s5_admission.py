"""Review-only r7s5 admission state machine.

Production has no external authority adapter, fixed-root reservation backend,
process wiring, or evidence publisher.  Its public entry therefore fails before
reading candidate bytes or invoking any dependency.  The private ``_for_test``
seam proves ordering, exact call counts, and reservation-handle lifetime with
typed fakes only.
"""

from __future__ import annotations

import ntpath
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from evm.scale_validation import phase_b2_r7s4_authority as authority
from evm.scale_validation import phase_b2_r7s5_reservation as reservation


ADMISSION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.admission.v1"
CANONICAL_LOCAL_ONE_SHOT_ROOT = (
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s5-local-one-shot"
)
PROCESS_PROFILE = "r7s5_review_only_typed_fake_process"
PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED = False
PRODUCTION_RESERVATION_BACKEND_CONFIGURED = False
PRODUCTION_LOCAL_ONE_SHOT_BACKEND_CONFIGURED = False
PRODUCTION_PROCESS_WIRING_IMPLEMENTED = False
PRODUCTION_EVIDENCE_WIRING_IMPLEMENTED = False
PRODUCTION_ENTRY_ENABLED = False
MULTI_HOST_GLOBAL_ADMISSION_PROVIDED = False
SAME_TOKEN_HOSTILE_ADMIN_PROTECTED = False
HEX64_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class DependencyCallCounts:
    receipt_verification: int = 0
    receipt_revalidation: int = 0
    reservation_acquire: int = 0
    reservation_readback: int = 0
    local_one_shot: int = 0
    process_admission: int = 0
    evidence_publication: int = 0
    reservation_close: int = 0
    automatic_retry: int = 0
    live: int = 0
    service: int = 0
    wsl: int = 0
    docker: int = 0
    r8: int = 0


class _MutableCounts:
    def __init__(self) -> None:
        self.receipt_verification = 0
        self.receipt_revalidation = 0
        self.reservation_acquire = 0
        self.reservation_readback = 0
        self.local_one_shot = 0
        self.process_admission = 0
        self.evidence_publication = 0
        self.reservation_close = 0
        self.automatic_retry = 0
        self.live = 0
        self.service = 0
        self.wsl = 0
        self.docker = 0
        self.r8 = 0

    def snapshot(self) -> DependencyCallCounts:
        return DependencyCallCounts(
            **{name: getattr(self, name) for name in DependencyCallCounts.__dataclass_fields__}
        )


class R7S5AdmissionError(RuntimeError):
    """Fail-closed terminal result with immutable dependency call counts."""

    manual_intervention_required = True
    automatic_retry_allowed = False
    production_go = False

    def __init__(self, code: str, *, stage: str, counts: DependencyCallCounts) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.counts = counts


@dataclass(frozen=True, slots=True)
class LocalOneShotProof:
    execution_identity_sha256: str
    receipt_sha256: str
    approval_request_sha256: str
    marker_leaf: str
    marker_path: str
    marker_sha256: str
    local_one_shot_consumed: bool
    same_handle_readback: bool
    automatic_retry_allowed: bool = False
    multi_host_global_one_shot_provided: bool = False
    same_token_hostile_admin_protected: bool = False
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class ProcessAdmissionContext:
    schema: str
    process_profile: str
    execution_identity: reservation.ExecutionIdentity
    execution_identity_sha256: str
    receipt_sha256: str
    approval_request_sha256: str
    reservation_evidence: reservation.ReservationEvidence
    local_one_shot_proof: LocalOneShotProof
    reservation_handle_active_at_dispatch: bool
    caller_command_accepted: bool = False
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class ProcessAdmissionResult:
    execution_identity_sha256: str
    status: str
    create_suspended: bool
    containment_assigned_before_resume: bool
    active_process_count: int
    stream_drained: bool
    residual_process_count: int
    force_termination_count: int
    live_call_count: int
    service_call_count: int
    wsl_call_count: int
    docker_call_count: int
    r8_call_count: int
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class EvidencePublicationResult:
    execution_identity_sha256: str
    status: str
    evidence_sha256: str
    evidence_bytes: int
    same_handle_readback: bool
    success_marker_created: bool
    completion_marker_created: bool
    live_call_count: int
    service_call_count: int
    wsl_call_count: int
    docker_call_count: int
    r8_call_count: int
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class ReviewOnlyAdmissionOutcome:
    schema: str
    status: str
    execution_identity_sha256: str
    reservation: reservation.ReservationEvidence
    local_one_shot: LocalOneShotProof
    process: ProcessAdmissionResult
    evidence: EvidencePublicationResult
    counts: DependencyCallCounts
    reservation_handle_closed_after_terminal_evidence: bool
    automatic_retry_allowed: bool = False
    multi_host_global_admission_provided: bool = False
    same_token_hostile_admin_protected: bool = False
    production_go: bool = False


class _LocalOneShotConsumerForTest(Protocol):
    def consume_for_test(
        self,
        *,
        receipt_raw: bytes,
        approval_request_raw: bytes,
        capability: authority.VerifiedExternalReceipt,
        execution_identity: reservation.ExecutionIdentity,
        execution_identity_sha256: str,
    ) -> LocalOneShotProof: ...


class _ProcessAdmitterForTest(Protocol):
    def admit_process_for_test(
        self, context: ProcessAdmissionContext
    ) -> ProcessAdmissionResult: ...


class _EvidencePublisherForTest(Protocol):
    def publish_evidence_for_test(
        self,
        context: ProcessAdmissionContext,
        process: ProcessAdmissionResult,
    ) -> EvidencePublicationResult: ...


class _StageFailure(RuntimeError):
    def __init__(self, code: str, stage: str) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage


def _execution_identity(expected: authority.ReceiptExpectation) -> reservation.ExecutionIdentity:
    return reservation.ExecutionIdentity(
        global_run_id=expected.global_run_id,
        domain_run_id=expected.domain_run_id,
        domain=expected.domain,
        run_uuid=expected.run_uuid,
        attempt_uuid=expected.attempt_uuid,
        execution_mode=expected.execution_mode,
    )


def _normal_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value))


def _validated_local_one_shot(
    value: object,
    *,
    capability: authority.VerifiedExternalReceipt,
    identity_sha: str,
) -> LocalOneShotProof:
    if type(value) is not LocalOneShotProof:
        raise _StageFailure("typed_local_one_shot_proof_required", "local_one_shot")
    assert isinstance(value, LocalOneShotProof)
    expected_leaf = f"r7s5-local-one-shot-{identity_sha}.json"
    expected_path = ntpath.join(CANONICAL_LOCAL_ONE_SHOT_ROOT, expected_leaf)
    if (
        value.execution_identity_sha256 != identity_sha
        or value.receipt_sha256 != capability.receipt_sha256
        or value.approval_request_sha256 != capability.approval_request_sha256
        or value.marker_leaf != expected_leaf
        or _normal_path(value.marker_path) != _normal_path(expected_path)
        or HEX64_RE.fullmatch(value.marker_sha256) is None
        or value.local_one_shot_consumed is not True
        or value.same_handle_readback is not True
        or value.automatic_retry_allowed is not False
        or value.multi_host_global_one_shot_provided is not False
        or value.same_token_hostile_admin_protected is not False
        or value.production_go is not False
    ):
        raise _StageFailure("local_one_shot_proof_mismatch", "local_one_shot")
    return value


def _validated_process_result(value: object, *, identity_sha: str) -> ProcessAdmissionResult:
    if type(value) is not ProcessAdmissionResult:
        raise _StageFailure("typed_process_admission_result_required", "process")
    assert isinstance(value, ProcessAdmissionResult)
    if (
        value.execution_identity_sha256 != identity_sha
        or value.status != "test_fake_completed"
        or value.create_suspended is not True
        or value.containment_assigned_before_resume is not True
        or type(value.active_process_count) is not int
        or value.active_process_count != 0
        or value.stream_drained is not True
        or type(value.residual_process_count) is not int
        or value.residual_process_count != 0
        or type(value.force_termination_count) is not int
        or value.force_termination_count != 0
        or any(
            type(item) is not int or item != 0
            for item in (
                value.live_call_count,
                value.service_call_count,
                value.wsl_call_count,
                value.docker_call_count,
                value.r8_call_count,
            )
        )
        or value.production_go is not False
    ):
        raise _StageFailure("process_admission_result_mismatch", "process")
    return value


def _validated_evidence_result(value: object, *, identity_sha: str) -> EvidencePublicationResult:
    if type(value) is not EvidencePublicationResult:
        raise _StageFailure("typed_evidence_publication_result_required", "evidence")
    assert isinstance(value, EvidencePublicationResult)
    if (
        value.execution_identity_sha256 != identity_sha
        or value.status != "test_fake_evidence_published"
        or HEX64_RE.fullmatch(value.evidence_sha256) is None
        or type(value.evidence_bytes) is not int
        or value.evidence_bytes <= 0
        or value.same_handle_readback is not True
        or value.success_marker_created is not False
        or value.completion_marker_created is not False
        or any(
            type(item) is not int or item != 0
            for item in (
                value.live_call_count,
                value.service_call_count,
                value.wsl_call_count,
                value.docker_call_count,
                value.r8_call_count,
            )
        )
        or value.production_go is not False
    ):
        raise _StageFailure("evidence_publication_result_mismatch", "evidence")
    return value


def _failure_code(exc: BaseException, fallback: str) -> str:
    value = getattr(exc, "code", None)
    return value if type(value) is str and value else fallback


def _close_lease(
    lease: reservation.ReservationLease | None,
    counts: _MutableCounts,
) -> _StageFailure | None:
    if lease is None or not lease.active:
        return None
    counts.reservation_close += 1
    try:
        lease.close()
    except Exception:
        return _StageFailure("reservation_close_ambiguous", "reservation_close")
    return None


def _admit_production_for_test(
    receipt_raw: bytes,
    approval_request_raw: bytes,
    *,
    expected: authority.ReceiptExpectation,
    validation_time: datetime,
    external_verifier: authority.ExternalReceiptVerifier,
    root_expectation: reservation.ReservationRootExpectation,
    reservation_backend: reservation.ReservationBackend,
    local_one_shot_consumer: _LocalOneShotConsumerForTest,
    process_admitter: _ProcessAdmitterForTest,
    evidence_publisher: _EvidencePublisherForTest,
) -> ReviewOnlyAdmissionOutcome:
    """Private deterministic dependency seam.  It can never return production GO."""

    counts = _MutableCounts()
    lease: reservation.ReservationLease | None = None
    pending_failure: _StageFailure | None = None
    capability: authority.VerifiedExternalReceipt
    identity: reservation.ExecutionIdentity | None = None
    identity_sha: str | None = None
    local_proof: LocalOneShotProof | None = None
    process_result: ProcessAdmissionResult | None = None
    evidence_result: EvidencePublicationResult | None = None
    try:
        counts.receipt_verification += 1
        try:
            capability = authority._verify_external_receipt_for_test(
                receipt_raw,
                approval_request_raw,
                expected=expected,
                verifier=external_verifier,
                validation_time=validation_time,
            )
        except Exception as exc:
            raise _StageFailure(
                _failure_code(exc, "external_receipt_verification_failed"), "receipt"
            ) from exc

        counts.receipt_revalidation += 1
        try:
            capability = authority._revalidate_external_receipt_for_consumption_for_test(
                capability,
                receipt_raw,
                approval_request_raw,
                expected=expected,
                validation_time=validation_time,
            )
        except Exception as exc:
            raise _StageFailure(
                _failure_code(exc, "external_receipt_revalidation_failed"), "receipt"
            ) from exc

        identity = _execution_identity(expected)
        identity_sha = reservation.execution_identity_sha256(identity)

        counts.reservation_acquire += 1
        try:
            lease = reservation._acquire_reservation_for_test(
                capability,
                execution_identity=identity,
                root_expectation=root_expectation,
                backend=reservation_backend,
            )
            counts.reservation_readback = lease.readback_count
        except Exception as exc:
            raise _StageFailure(
                _failure_code(exc, "reservation_acquisition_failed"), "reservation"
            ) from exc

        counts.local_one_shot += 1
        try:
            local_proof = _validated_local_one_shot(
                local_one_shot_consumer.consume_for_test(
                    receipt_raw=receipt_raw,
                    approval_request_raw=approval_request_raw,
                    capability=capability,
                    execution_identity=identity,
                    execution_identity_sha256=identity_sha,
                ),
                capability=capability,
                identity_sha=identity_sha,
            )
        except _StageFailure:
            raise
        except Exception as exc:
            raise _StageFailure("local_one_shot_ambiguous", "local_one_shot") from exc

        try:
            lease.assert_active()
            counts.reservation_readback = lease.readback_count
        except Exception as exc:
            raise _StageFailure(
                _failure_code(exc, "reservation_not_active_before_process"), "reservation"
            ) from exc

        context = ProcessAdmissionContext(
            schema=ADMISSION_SCHEMA,
            process_profile=PROCESS_PROFILE,
            execution_identity=identity,
            execution_identity_sha256=identity_sha,
            receipt_sha256=capability.receipt_sha256,
            approval_request_sha256=capability.approval_request_sha256,
            reservation_evidence=lease.evidence,
            local_one_shot_proof=local_proof,
            reservation_handle_active_at_dispatch=lease.active,
        )

        counts.process_admission += 1
        try:
            process_result = _validated_process_result(
                process_admitter.admit_process_for_test(context), identity_sha=identity_sha
            )
        except _StageFailure:
            raise
        except Exception as exc:
            raise _StageFailure("process_admission_ambiguous", "process") from exc

        try:
            lease.assert_active()
            counts.reservation_readback = lease.readback_count
        except Exception as exc:
            raise _StageFailure(
                _failure_code(exc, "reservation_not_active_before_evidence"), "reservation"
            ) from exc

        counts.evidence_publication += 1
        try:
            evidence_result = _validated_evidence_result(
                evidence_publisher.publish_evidence_for_test(context, process_result),
                identity_sha=identity_sha,
            )
        except _StageFailure:
            raise
        except Exception as exc:
            raise _StageFailure("evidence_publication_ambiguous", "evidence") from exc

        try:
            lease.assert_active()
            counts.reservation_readback = lease.readback_count
        except Exception as exc:
            raise _StageFailure(
                _failure_code(exc, "reservation_not_active_after_evidence"), "reservation"
            ) from exc
    except _StageFailure as exc:
        pending_failure = exc

    close_failure = _close_lease(lease, counts)
    if close_failure is not None:
        pending_failure = close_failure
    if pending_failure is not None:
        raise R7S5AdmissionError(
            pending_failure.code,
            stage=pending_failure.stage,
            counts=counts.snapshot(),
        )

    assert lease is not None
    assert identity_sha is not None
    assert local_proof is not None
    assert process_result is not None
    assert evidence_result is not None
    final_counts = counts.snapshot()
    return ReviewOnlyAdmissionOutcome(
        schema=ADMISSION_SCHEMA,
        status="review_only_test_sequence_complete",
        execution_identity_sha256=identity_sha,
        reservation=lease.evidence,
        local_one_shot=local_proof,
        process=process_result,
        evidence=evidence_result,
        counts=final_counts,
        reservation_handle_closed_after_terminal_evidence=(
            not lease.active and lease.close_count == 1
        ),
    )


def admit_production_once(
    receipt_raw: bytes,
    approval_request_raw: bytes,
    *,
    expected: authority.ReceiptExpectation,
) -> ReviewOnlyAdmissionOutcome:
    """Non-injectable public gate; no configured dependency means zero calls."""

    del receipt_raw, approval_request_raw, expected
    counts = DependencyCallCounts()
    if not PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED:
        raise R7S5AdmissionError(
            "production_external_authority_unconfigured",
            stage="root_gate",
            counts=counts,
        )
    if not PRODUCTION_RESERVATION_BACKEND_CONFIGURED:
        raise R7S5AdmissionError(
            "production_reservation_backend_unconfigured",
            stage="root_gate",
            counts=counts,
        )
    if not PRODUCTION_LOCAL_ONE_SHOT_BACKEND_CONFIGURED:
        raise R7S5AdmissionError(
            "production_local_one_shot_backend_unconfigured",
            stage="root_gate",
            counts=counts,
        )
    raise R7S5AdmissionError(
        "production_admission_wiring_not_implemented",
        stage="root_gate",
        counts=counts,
    )


def admission_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.admission-contract.v1",
        "fixed_order": [
            "external_receipt_verify_and_consumption_revalidate",
            "fixed_root_handle_bound_reservation",
            "fixed_root_local_one_shot",
            "typed_process_admission",
            "typed_terminal_evidence",
            "reservation_handle_close",
        ],
        "production_api_accepts_backend_or_root": False,
        "production_api_accepts_clock": False,
        "production_api_accepts_command": False,
        "test_dependency_injection_private_for_test_only": True,
        "production_external_authority_configured": PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED,
        "production_reservation_backend_configured": PRODUCTION_RESERVATION_BACKEND_CONFIGURED,
        "production_local_one_shot_backend_configured": (
            PRODUCTION_LOCAL_ONE_SHOT_BACKEND_CONFIGURED
        ),
        "production_process_wiring_implemented": PRODUCTION_PROCESS_WIRING_IMPLEMENTED,
        "production_evidence_wiring_implemented": PRODUCTION_EVIDENCE_WIRING_IMPLEMENTED,
        "production_entry_enabled": PRODUCTION_ENTRY_ENABLED,
        "missing_external_dependency_downstream_calls": asdict(DependencyCallCounts()),
        "automatic_retry_allowed": False,
        "reservation_handle_retained_through_terminal_evidence": True,
        "same_token_hostile_admin_protected": SAME_TOKEN_HOSTILE_ADMIN_PROTECTED,
        "multi_host_global_admission_provided": MULTI_HOST_GLOBAL_ADMISSION_PROVIDED,
        "review_only_test_completion_is_production_go": False,
        "production_go": False,
    }


__all__ = [
    "ADMISSION_SCHEMA",
    "CANONICAL_LOCAL_ONE_SHOT_ROOT",
    "DependencyCallCounts",
    "EvidencePublicationResult",
    "LocalOneShotProof",
    "PROCESS_PROFILE",
    "PRODUCTION_ENTRY_ENABLED",
    "ProcessAdmissionContext",
    "ProcessAdmissionResult",
    "R7S5AdmissionError",
    "ReviewOnlyAdmissionOutcome",
    "admission_contract",
    "admit_production_once",
]
