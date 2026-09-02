from __future__ import annotations

import hashlib
import inspect
import ntpath
import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import Callable

import pytest

from evm.scale_validation import phase_b2_r7s4_authority as authority
from evm.scale_validation import phase_b2_r7s5_admission as admission
from evm.scale_validation import phase_b2_r7s5_reservation as reservation


NOW = datetime(2026, 9, 2, 5, 10, tzinfo=UTC)
GLOBAL_RUN_ID = "pre-r8-r7s5-20260902T050413Z-c70d11ab"
RUN_UUID = "11223344-5566-4788-899a-bbccddeeff00"
ATTEMPT_UUID = "aabbccdd-eeff-4111-8222-334455667788"
EXPECTED = authority.ReceiptExpectation(
    approval_request_id="approval-request-r7s5-admission-001",
    global_run_id=GLOBAL_RUN_ID,
    domain_run_id=f"{GLOBAL_RUN_ID}-windows",
    domain="windows",
    run_uuid=RUN_UUID,
    attempt_uuid=ATTEMPT_UUID,
    execution_mode="non_credit_r7s5_admission_review",
)
ROOT = reservation.ReservationRootExpectation(
    final_path=reservation.CANONICAL_RESERVATION_ROOT,
    volume_serial_number=42,
    file_id_hex="1" * 32,
    owner_sid="S-1-5-32-544",
    security_descriptor_sha256="a" * 64,
)


def _pin(name: str, character: str) -> dict[str, object]:
    return {"path": rf"C:\approved\r7s5\{name}", "sha256": character * 64, "bytes": 19}


def _documents() -> tuple[bytes, bytes]:
    run_identity = {
        "global_run_id": EXPECTED.global_run_id,
        "domain_run_id": EXPECTED.domain_run_id,
        "domain": EXPECTED.domain,
        "run_uuid": EXPECTED.run_uuid,
        "attempt_uuid": EXPECTED.attempt_uuid,
        "execution_mode": EXPECTED.execution_mode,
    }
    subject = {
        "bootstrap": _pin("bootstrap.py", "1"),
        "bootstrap_argv": _pin("bootstrap-argv.json", "2"),
        "work_order": _pin("work-order.json", "3"),
        "root_orchestrator": _pin("root.py", "4"),
        "canonical_revision": {"commit": "5" * 40, "tree": "6" * 40},
        "run_identity": run_identity,
    }
    request = {
        "schema": authority.APPROVAL_REQUEST_SCHEMA,
        "status": "review_pending",
        "decision": "not_approved",
        "approval_request_id": EXPECTED.approval_request_id,
        "created_at_utc": "2026-09-02T05:00:00Z",
        "expires_at_utc": "2026-09-02T05:30:00Z",
        "subject": subject,
        "production_entry_enabled": False,
    }
    request_raw = authority.canonical_json_bytes(request)
    receipt = {
        "schema": authority.APPROVAL_RECEIPT_SCHEMA,
        "status": "approved",
        "decision": "approve_exact_candidate_once",
        "approval_request_id": EXPECTED.approval_request_id,
        "issued_at_utc": "2026-09-02T05:01:00Z",
        "expires_at_utc": "2026-09-02T05:20:00Z",
        "authority": {
            "mechanism": "independent_external_verifier",
            "reviewer_identity": "independent-reviewer-r7s5",
            "approval_id": "external-approval-r7s5-admission-001",
            "key_id": "external-key-r7s5",
        },
        "approval_request": {
            "sha256": hashlib.sha256(request_raw).hexdigest(),
            "bytes": len(request_raw),
        },
        "subject": subject,
        "run_identity": run_identity,
    }
    return authority.canonical_json_bytes(receipt), request_raw


class FakeExternalVerifier:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def verify(
        self,
        *,
        receipt_raw: bytes,
        approval_request_raw: bytes,
        receipt_sha256: str,
        approval_request_sha256: str,
        subject_sha256: str,
    ) -> authority.ExternalAuthorityAttestation:
        del approval_request_raw
        self.events.append("receipt")
        parsed = authority.strict_canonical_json_bytes(receipt_raw, "receipt")
        return authority.ExternalAuthorityAttestation(
            schema=authority.EXTERNAL_ATTESTATION_SCHEMA,
            status="authenticated",
            receipt_sha256=receipt_sha256,
            approval_request_sha256=approval_request_sha256,
            subject_sha256=subject_sha256,
            approval_id=parsed["authority"]["approval_id"],
            reviewer_identity=parsed["authority"]["reviewer_identity"],
            authority_key_id=parsed["authority"]["key_id"],
            verifier_identity="external-test-verifier-r7s5",
            independent_authority_verified=True,
            authorize_exact_candidate_once=True,
        )


def _root_identity() -> reservation.HandleIdentitySnapshot:
    return reservation.HandleIdentitySnapshot(
        final_path=reservation.CANONICAL_RESERVATION_ROOT,
        volume_serial_number=42,
        file_id_hex="1" * 32,
        size=0,
        link_count=1,
        attributes=0x10,
        reparse_tag=0,
        file_type=1,
        owner_sid="S-1-5-32-544",
        security_descriptor_sha256="a" * 64,
        dacl_present=True,
        dacl_protected=True,
    )


class MemoryReservationBackend:
    def __init__(self, events: list[str], *, mutate_read_number: int | None = None) -> None:
        self.events = events
        self.mutate_read_number = mutate_read_number
        self.lock = threading.Lock()
        self.markers: set[str] = set()
        self.open_handles: set[int] = set()
        self.acquisitions: dict[int, reservation.ReservationBackendAcquisition] = {}
        self.acquire_calls = 0
        self.read_calls = 0
        self.close_calls = 0

    def acquire_no_replace(
        self,
        *,
        root_path: str,
        final_leaf: str,
        raw: bytes,
        run_uuid: str,
    ) -> reservation.ReservationBackendAcquisition:
        with self.lock:
            self.acquire_calls += 1
            self.events.append("reservation")
            if final_leaf in self.markers:
                raise FileExistsError(final_leaf)
            self.markers.add(final_leaf)
            handle = 100 + self.acquire_calls
            final_path = ntpath.join(root_path, final_leaf)
            acquired = reservation.ReservationBackendAcquisition(
                handle=handle,
                final_path=final_path,
                temporary_leaf=f".{final_leaf}.{run_uuid}.partial",
                raw=raw,
                sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw),
                identity=reservation.HandleIdentitySnapshot(
                    final_path=final_path,
                    volume_serial_number=42,
                    file_id_hex=hashlib.sha256(final_path.encode()).hexdigest()[:32],
                    size=len(raw),
                    link_count=1,
                    attributes=0x20,
                    reparse_tag=0,
                    file_type=1,
                    owner_sid="S-1-5-32-544",
                    security_descriptor_sha256="b" * 64,
                    dacl_present=True,
                    dacl_protected=True,
                ),
                root_identity=_root_identity(),
                file_flush_count=2,
                directory_flush_count=1,
                directory_flush_succeeded=True,
                create_no_replace=True,
                replace_if_exists=False,
                same_handle_readback=True,
                file_identity_stable_across_rename=True,
                handle_retained=True,
            )
            self.acquisitions[handle] = acquired
            self.open_handles.add(handle)
            return acquired

    def read_same_handle(
        self, handle: int, *, expected_size: int
    ) -> reservation.ReservationBackendReadback:
        with self.lock:
            self.read_calls += 1
            self.events.append("readback")
            if handle not in self.open_handles:
                raise OSError("closed")
            acquired = self.acquisitions[handle]
            assert expected_size == acquired.bytes
            result = reservation.ReservationBackendReadback(
                handle=handle,
                raw=acquired.raw,
                sha256=acquired.sha256,
                identity=acquired.identity,
                root_identity=acquired.root_identity,
            )
            if self.read_calls == self.mutate_read_number:
                result = replace(result, sha256="0" * 64)
            return result

    def close(self, handle: int) -> None:
        with self.lock:
            self.close_calls += 1
            self.events.append("close")
            if handle not in self.open_handles:
                raise OSError("double close")
            self.open_handles.remove(handle)


class MemoryLocalOneShot:
    def __init__(
        self,
        events: list[str],
        *,
        mutation: Callable[[admission.LocalOneShotProof], admission.LocalOneShotProof | object]
        | None = None,
        fail: bool = False,
    ) -> None:
        self.events = events
        self.mutation = mutation
        self.fail = fail
        self.keys: set[str] = set()
        self.calls = 0

    def consume_for_test(
        self,
        *,
        receipt_raw: bytes,
        approval_request_raw: bytes,
        capability: authority.VerifiedExternalReceipt,
        execution_identity: reservation.ExecutionIdentity,
        execution_identity_sha256: str,
    ) -> admission.LocalOneShotProof:
        del receipt_raw, approval_request_raw, execution_identity
        self.calls += 1
        self.events.append("local_one_shot")
        if self.fail:
            raise OSError("one-shot acknowledgement lost")
        if execution_identity_sha256 in self.keys:
            raise FileExistsError(execution_identity_sha256)
        self.keys.add(execution_identity_sha256)
        leaf = f"r7s5-local-one-shot-{execution_identity_sha256}.json"
        result: admission.LocalOneShotProof | object = admission.LocalOneShotProof(
            execution_identity_sha256=execution_identity_sha256,
            receipt_sha256=capability.receipt_sha256,
            approval_request_sha256=capability.approval_request_sha256,
            marker_leaf=leaf,
            marker_path=ntpath.join(admission.CANONICAL_LOCAL_ONE_SHOT_ROOT, leaf),
            marker_sha256=hashlib.sha256(leaf.encode()).hexdigest(),
            local_one_shot_consumed=True,
            same_handle_readback=True,
        )
        if self.mutation is not None:
            result = self.mutation(result)
        return result  # type: ignore[return-value]


class FakeProcessAdmitter:
    def __init__(
        self,
        events: list[str],
        backend: MemoryReservationBackend,
        *,
        mutation: Callable[
            [admission.ProcessAdmissionResult], admission.ProcessAdmissionResult | object
        ]
        | None = None,
        fail: bool = False,
    ) -> None:
        self.events = events
        self.backend = backend
        self.mutation = mutation
        self.fail = fail
        self.calls = 0
        self.observed_open_handle = False

    def admit_process_for_test(
        self, context: admission.ProcessAdmissionContext
    ) -> admission.ProcessAdmissionResult:
        self.calls += 1
        self.events.append("process")
        self.observed_open_handle = bool(self.backend.open_handles)
        assert context.caller_command_accepted is False
        assert context.process_profile == admission.PROCESS_PROFILE
        if self.fail:
            raise OSError("fake process admission ambiguity")
        result: admission.ProcessAdmissionResult | object = admission.ProcessAdmissionResult(
            execution_identity_sha256=context.execution_identity_sha256,
            status="test_fake_completed",
            create_suspended=True,
            containment_assigned_before_resume=True,
            active_process_count=0,
            stream_drained=True,
            residual_process_count=0,
            force_termination_count=0,
            live_call_count=0,
            service_call_count=0,
            wsl_call_count=0,
            docker_call_count=0,
            r8_call_count=0,
        )
        if self.mutation is not None:
            result = self.mutation(result)
        return result  # type: ignore[return-value]


class FakeEvidencePublisher:
    def __init__(
        self,
        events: list[str],
        backend: MemoryReservationBackend,
        *,
        mutation: Callable[
            [admission.EvidencePublicationResult], admission.EvidencePublicationResult | object
        ]
        | None = None,
        fail: bool = False,
    ) -> None:
        self.events = events
        self.backend = backend
        self.mutation = mutation
        self.fail = fail
        self.calls = 0
        self.observed_open_handle = False

    def publish_evidence_for_test(
        self,
        context: admission.ProcessAdmissionContext,
        process: admission.ProcessAdmissionResult,
    ) -> admission.EvidencePublicationResult:
        del process
        self.calls += 1
        self.events.append("evidence")
        self.observed_open_handle = bool(self.backend.open_handles)
        if self.fail:
            raise OSError("fake evidence acknowledgement lost")
        result: admission.EvidencePublicationResult | object = admission.EvidencePublicationResult(
            execution_identity_sha256=context.execution_identity_sha256,
            status="test_fake_evidence_published",
            evidence_sha256="e" * 64,
            evidence_bytes=211,
            same_handle_readback=True,
            success_marker_created=False,
            completion_marker_created=False,
            live_call_count=0,
            service_call_count=0,
            wsl_call_count=0,
            docker_call_count=0,
            r8_call_count=0,
        )
        if self.mutation is not None:
            result = self.mutation(result)
        return result  # type: ignore[return-value]


def _dependencies(
    *,
    backend: MemoryReservationBackend | None = None,
    local: MemoryLocalOneShot | None = None,
    process: FakeProcessAdmitter | None = None,
    evidence: FakeEvidencePublisher | None = None,
) -> tuple[
    list[str],
    MemoryReservationBackend,
    MemoryLocalOneShot,
    FakeProcessAdmitter,
    FakeEvidencePublisher,
]:
    events: list[str]
    if backend is not None:
        events = backend.events
    elif local is not None:
        events = local.events
    elif process is not None:
        events = process.events
    elif evidence is not None:
        events = evidence.events
    else:
        events = []
    actual_backend = backend or MemoryReservationBackend(events)
    actual_local = local or MemoryLocalOneShot(events)
    actual_process = process or FakeProcessAdmitter(events, actual_backend)
    actual_evidence = evidence or FakeEvidencePublisher(events, actual_backend)
    return events, actual_backend, actual_local, actual_process, actual_evidence


def _run(
    *,
    receipt_raw: bytes | None = None,
    validation_time: datetime = NOW,
    backend: MemoryReservationBackend | None = None,
    local: MemoryLocalOneShot | None = None,
    process: FakeProcessAdmitter | None = None,
    evidence: FakeEvidencePublisher | None = None,
) -> admission.ReviewOnlyAdmissionOutcome:
    actual_receipt, request_raw = _documents()
    events, actual_backend, actual_local, actual_process, actual_evidence = _dependencies(
        backend=backend, local=local, process=process, evidence=evidence
    )
    return admission._admit_production_for_test(
        receipt_raw if receipt_raw is not None else actual_receipt,
        request_raw,
        expected=EXPECTED,
        validation_time=validation_time,
        external_verifier=FakeExternalVerifier(events),
        root_expectation=ROOT,
        reservation_backend=actual_backend,
        local_one_shot_consumer=actual_local,
        process_admitter=actual_process,
        evidence_publisher=actual_evidence,
    )


def test_public_gate_has_no_injection_surface_and_missing_authority_is_zero_call() -> None:
    signature = inspect.signature(admission.admit_production_once)
    assert set(signature.parameters) == {"receipt_raw", "approval_request_raw", "expected"}
    receipt_raw, request_raw = _documents()
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        admission.admit_production_once(receipt_raw, request_raw, expected=EXPECTED)
    assert caught.value.code == "production_external_authority_unconfigured"
    assert caught.value.counts == admission.DependencyCallCounts()
    contract = admission.admission_contract()
    assert contract["production_entry_enabled"] is False
    assert contract["production_api_accepts_backend_or_root"] is False
    assert contract["same_token_hostile_admin_protected"] is False
    assert contract["multi_host_global_admission_provided"] is False
    assert contract["production_go"] is False


def test_flag_flip_still_has_no_production_wiring_or_downstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED",
        "PRODUCTION_RESERVATION_BACKEND_CONFIGURED",
        "PRODUCTION_LOCAL_ONE_SHOT_BACKEND_CONFIGURED",
        "PRODUCTION_PROCESS_WIRING_IMPLEMENTED",
        "PRODUCTION_EVIDENCE_WIRING_IMPLEMENTED",
        "PRODUCTION_ENTRY_ENABLED",
    ):
        monkeypatch.setattr(admission, name, True)
    receipt_raw, request_raw = _documents()
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        admission.admit_production_once(receipt_raw, request_raw, expected=EXPECTED)
    assert caught.value.code == "production_admission_wiring_not_implemented"
    assert caught.value.counts == admission.DependencyCallCounts()


def test_fixed_state_machine_exact_counts_and_handle_lifetime() -> None:
    events: list[str] = []
    backend = MemoryReservationBackend(events)
    local = MemoryLocalOneShot(events)
    process = FakeProcessAdmitter(events, backend)
    evidence = FakeEvidencePublisher(events, backend)
    result = _run(backend=backend, local=local, process=process, evidence=evidence)
    assert events == [
        "receipt",
        "reservation",
        "readback",
        "local_one_shot",
        "readback",
        "process",
        "readback",
        "evidence",
        "readback",
        "close",
    ]
    assert result.counts == admission.DependencyCallCounts(
        receipt_verification=1,
        receipt_revalidation=1,
        reservation_acquire=1,
        reservation_readback=4,
        local_one_shot=1,
        process_admission=1,
        evidence_publication=1,
        reservation_close=1,
    )
    assert process.observed_open_handle is True
    assert evidence.observed_open_handle is True
    assert backend.open_handles == set()
    assert result.reservation_handle_closed_after_terminal_evidence is True
    assert result.production_go is False
    assert result.evidence.success_marker_created is False


def test_receipt_failure_has_zero_reservation_process_and_evidence() -> None:
    events, backend, local, process, evidence = _dependencies()
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(
            receipt_raw=b"{}\n",
            backend=backend,
            local=local,
            process=process,
            evidence=evidence,
        )
    assert caught.value.stage == "receipt"
    assert caught.value.counts.receipt_verification == 1
    assert caught.value.counts.receipt_revalidation == 0
    assert caught.value.counts.reservation_acquire == 0
    assert caught.value.counts.local_one_shot == 0
    assert caught.value.counts.process_admission == 0
    assert caught.value.counts.evidence_publication == 0
    assert events == []


def test_expired_receipt_stops_before_reservation() -> None:
    events, backend, local, process, evidence = _dependencies()
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(
            validation_time=datetime(2026, 9, 2, 5, 21, tzinfo=UTC),
            backend=backend,
            local=local,
            process=process,
            evidence=evidence,
        )
    assert caught.value.stage == "receipt"
    assert backend.acquire_calls == 0
    assert process.calls == 0
    assert evidence.calls == 0


def test_reservation_collision_stops_before_local_process_and_evidence() -> None:
    events: list[str] = []
    backend = MemoryReservationBackend(events)
    first_local = MemoryLocalOneShot(events)
    first_process = FakeProcessAdmitter(events, backend)
    first_evidence = FakeEvidencePublisher(events, backend)
    _run(
        backend=backend,
        local=first_local,
        process=first_process,
        evidence=first_evidence,
    )
    events.clear()
    second_local = MemoryLocalOneShot(events)
    second_process = FakeProcessAdmitter(events, backend)
    second_evidence = FakeEvidencePublisher(events, backend)
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(
            backend=backend,
            local=second_local,
            process=second_process,
            evidence=second_evidence,
        )
    assert caught.value.code == "reservation_execution_identity_collision"
    assert caught.value.counts.local_one_shot == 0
    assert caught.value.counts.process_admission == 0
    assert caught.value.counts.evidence_publication == 0
    assert events == ["receipt", "reservation"]


def test_local_one_shot_failure_closes_reservation_and_has_zero_process_evidence() -> None:
    events: list[str] = []
    backend = MemoryReservationBackend(events)
    local = MemoryLocalOneShot(events, fail=True)
    process = FakeProcessAdmitter(events, backend)
    evidence = FakeEvidencePublisher(events, backend)
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(backend=backend, local=local, process=process, evidence=evidence)
    assert caught.value.code == "local_one_shot_ambiguous"
    assert caught.value.counts.process_admission == 0
    assert caught.value.counts.evidence_publication == 0
    assert caught.value.counts.reservation_close == 1
    assert backend.open_handles == set()
    assert events[-1] == "close"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, marker_path=r"F:\alternate\marker.json"),
        lambda value: replace(value, receipt_sha256="0" * 64),
        lambda value: replace(value, multi_host_global_one_shot_provided=True),
        lambda value: replace(value, same_token_hostile_admin_protected=True),
        lambda value: replace(value, production_go=True),
        lambda _value: {"forged": True},
    ],
)
def test_local_one_shot_forgery_or_overclaim_blocks_process_and_evidence(
    mutation: Callable[[admission.LocalOneShotProof], admission.LocalOneShotProof | object],
) -> None:
    events: list[str] = []
    backend = MemoryReservationBackend(events)
    local = MemoryLocalOneShot(events, mutation=mutation)
    process = FakeProcessAdmitter(events, backend)
    evidence = FakeEvidencePublisher(events, backend)
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(backend=backend, local=local, process=process, evidence=evidence)
    assert caught.value.stage == "local_one_shot"
    assert process.calls == 0
    assert evidence.calls == 0
    assert backend.close_calls == 1


def test_reservation_readback_mutation_after_local_one_shot_blocks_process() -> None:
    events: list[str] = []
    backend = MemoryReservationBackend(events, mutate_read_number=2)
    local = MemoryLocalOneShot(events)
    process = FakeProcessAdmitter(events, backend)
    evidence = FakeEvidencePublisher(events, backend)
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(backend=backend, local=local, process=process, evidence=evidence)
    assert caught.value.stage == "reservation"
    assert local.calls == 1
    assert process.calls == 0
    assert evidence.calls == 0
    assert backend.close_calls == 1


def test_process_ambiguity_has_zero_evidence_retry_and_closes_reservation() -> None:
    events: list[str] = []
    backend = MemoryReservationBackend(events)
    local = MemoryLocalOneShot(events)
    process = FakeProcessAdmitter(events, backend, fail=True)
    evidence = FakeEvidencePublisher(events, backend)
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(backend=backend, local=local, process=process, evidence=evidence)
    assert caught.value.code == "process_admission_ambiguous"
    assert caught.value.counts.process_admission == 1
    assert caught.value.counts.evidence_publication == 0
    assert caught.value.counts.automatic_retry == 0
    assert evidence.calls == 0
    assert backend.open_handles == set()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, residual_process_count=1),
        lambda value: replace(value, service_call_count=1),
        lambda value: replace(value, active_process_count=True),
        lambda value: replace(value, production_go=True),
        lambda _value: {"status": "forged"},
    ],
)
def test_process_result_mutation_blocks_evidence(
    mutation: Callable[
        [admission.ProcessAdmissionResult], admission.ProcessAdmissionResult | object
    ],
) -> None:
    events: list[str] = []
    backend = MemoryReservationBackend(events)
    local = MemoryLocalOneShot(events)
    process = FakeProcessAdmitter(events, backend, mutation=mutation)
    evidence = FakeEvidencePublisher(events, backend)
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(backend=backend, local=local, process=process, evidence=evidence)
    assert caught.value.stage == "process"
    assert evidence.calls == 0
    assert backend.close_calls == 1


def test_evidence_ambiguity_is_terminal_no_retry_and_handle_closes() -> None:
    events: list[str] = []
    backend = MemoryReservationBackend(events)
    local = MemoryLocalOneShot(events)
    process = FakeProcessAdmitter(events, backend)
    evidence = FakeEvidencePublisher(events, backend, fail=True)
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(backend=backend, local=local, process=process, evidence=evidence)
    assert caught.value.code == "evidence_publication_ambiguous"
    assert caught.value.counts.process_admission == 1
    assert caught.value.counts.evidence_publication == 1
    assert caught.value.counts.automatic_retry == 0
    assert backend.open_handles == set()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, success_marker_created=True),
        lambda value: replace(value, completion_marker_created=True),
        lambda value: replace(value, docker_call_count=1),
        lambda value: replace(value, evidence_bytes=True),
        lambda value: replace(value, production_go=True),
        lambda _value: {"status": "forged"},
    ],
)
def test_evidence_result_overclaim_is_fail_closed(
    mutation: Callable[
        [admission.EvidencePublicationResult], admission.EvidencePublicationResult | object
    ],
) -> None:
    events: list[str] = []
    backend = MemoryReservationBackend(events)
    local = MemoryLocalOneShot(events)
    process = FakeProcessAdmitter(events, backend)
    evidence = FakeEvidencePublisher(events, backend, mutation=mutation)
    with pytest.raises(admission.R7S5AdmissionError) as caught:
        _run(backend=backend, local=local, process=process, evidence=evidence)
    assert caught.value.stage == "evidence"
    assert caught.value.counts.automatic_retry == 0
    assert backend.close_calls == 1


def test_module_has_no_subprocess_or_service_execution_surface() -> None:
    source = inspect.getsource(admission)
    assert "import subprocess" not in source
    assert "CreateProcess" not in source
    assert "docker compose" not in source
    assert "wsl.exe" not in source
    assert "r8: int = 0" in source
    private_signature = inspect.signature(admission._admit_production_for_test)
    assert "validation_time" in private_signature.parameters
    assert "external_verifier" in private_signature.parameters
    assert "validation_time" not in inspect.signature(admission.admit_production_once).parameters
