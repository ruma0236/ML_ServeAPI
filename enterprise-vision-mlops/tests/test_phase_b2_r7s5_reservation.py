from __future__ import annotations

import hashlib
import inspect
import ntpath
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from typing import Callable

import pytest

from evm.scale_validation import phase_b2_r7s4_authority as authority
from evm.scale_validation import phase_b2_r7s5_reservation as reservation


NOW = datetime(2026, 9, 2, 5, 10, tzinfo=UTC)
GLOBAL_RUN_ID = "pre-r8-r7s5-20260902T050413Z-c70d11ab"
RUN_UUID = "11223344-5566-4788-899a-bbccddeeff00"
ATTEMPT_UUID = "aabbccdd-eeff-4111-8222-334455667788"


def _expected(
    *,
    run_uuid: str = RUN_UUID,
    attempt_uuid: str = ATTEMPT_UUID,
) -> authority.ReceiptExpectation:
    return authority.ReceiptExpectation(
        approval_request_id=f"approval-request-{run_uuid}",
        global_run_id=GLOBAL_RUN_ID,
        domain_run_id=f"{GLOBAL_RUN_ID}-wsl",
        domain="wsl",
        run_uuid=run_uuid,
        attempt_uuid=attempt_uuid,
        execution_mode="non_credit_r7s5_admission_review",
    )


def _pin(name: str, character: str) -> dict[str, object]:
    return {"path": rf"C:\approved\r7s5\{name}", "sha256": character * 64, "bytes": 17}


def _documents(
    expected: authority.ReceiptExpectation,
    *,
    approval_id: str = "external-approval-r7s5-001",
) -> tuple[bytes, bytes]:
    run_identity = {
        "global_run_id": expected.global_run_id,
        "domain_run_id": expected.domain_run_id,
        "domain": expected.domain,
        "run_uuid": expected.run_uuid,
        "attempt_uuid": expected.attempt_uuid,
        "execution_mode": expected.execution_mode,
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
        "approval_request_id": expected.approval_request_id,
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
        "approval_request_id": expected.approval_request_id,
        "issued_at_utc": "2026-09-02T05:01:00Z",
        "expires_at_utc": "2026-09-02T05:20:00Z",
        "authority": {
            "mechanism": "independent_external_verifier",
            "reviewer_identity": "independent-reviewer-r7s5",
            "approval_id": approval_id,
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


def _verified(
    expected: authority.ReceiptExpectation,
    *,
    approval_id: str = "external-approval-r7s5-001",
) -> authority.VerifiedExternalReceipt:
    receipt_raw, request_raw = _documents(expected, approval_id=approval_id)
    return authority._verify_external_receipt_for_test(
        receipt_raw,
        request_raw,
        expected=expected,
        verifier=FakeExternalVerifier(),
        validation_time=NOW,
    )


ROOT = reservation.ReservationRootExpectation(
    final_path=reservation.CANONICAL_RESERVATION_ROOT,
    volume_serial_number=42,
    file_id_hex="1" * 32,
    owner_sid="S-1-5-32-544",
    security_descriptor_sha256="a" * 64,
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
    def __init__(
        self,
        *,
        acquisition_mutation: Callable[
            [reservation.ReservationBackendAcquisition], reservation.ReservationBackendAcquisition
        ]
        | None = None,
        readback_mutation: Callable[
            [reservation.ReservationBackendReadback], reservation.ReservationBackendReadback
        ]
        | None = None,
    ) -> None:
        self.lock = threading.Lock()
        self.markers: set[tuple[str, str]] = set()
        self.acquisitions: dict[int, reservation.ReservationBackendAcquisition] = {}
        self.open_handles: set[int] = set()
        self.next_handle = 100
        self.acquire_calls = 0
        self.read_calls = 0
        self.close_calls = 0
        self.events: list[str] = []
        self.acquisition_mutation = acquisition_mutation
        self.readback_mutation = readback_mutation

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
            key = (ntpath.normcase(root_path), final_leaf.casefold())
            if key in self.markers:
                raise FileExistsError(final_leaf)
            self.markers.add(key)
            self.next_handle += 1
            handle = self.next_handle
            final_path = ntpath.join(root_path, final_leaf)
            file_id = hashlib.sha256(final_path.encode("utf-8")).hexdigest()[:32]
            acquisition = reservation.ReservationBackendAcquisition(
                handle=handle,
                final_path=final_path,
                temporary_leaf=f".{final_leaf}.{run_uuid}.partial",
                raw=raw,
                sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw),
                identity=reservation.HandleIdentitySnapshot(
                    final_path=final_path,
                    volume_serial_number=42,
                    file_id_hex=file_id,
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
            if self.acquisition_mutation is not None:
                acquisition = self.acquisition_mutation(acquisition)
            self.acquisitions[handle] = acquisition
            self.open_handles.add(handle)
            return acquisition

    def read_same_handle(
        self, handle: int, *, expected_size: int
    ) -> reservation.ReservationBackendReadback:
        with self.lock:
            self.read_calls += 1
            self.events.append("readback")
            if handle not in self.open_handles:
                raise OSError("closed handle")
            acquisition = self.acquisitions[handle]
            assert expected_size == acquisition.bytes
            result = reservation.ReservationBackendReadback(
                handle=handle,
                raw=acquisition.raw,
                sha256=acquisition.sha256,
                identity=acquisition.identity,
                root_identity=acquisition.root_identity,
            )
            if self.readback_mutation is not None:
                result = self.readback_mutation(result)
            return result

    def close(self, handle: int) -> None:
        with self.lock:
            self.close_calls += 1
            self.events.append("close")
            if handle not in self.open_handles:
                raise OSError("double close")
            self.open_handles.remove(handle)


def _identity(expected: authority.ReceiptExpectation) -> reservation.ExecutionIdentity:
    return reservation.ExecutionIdentity(
        global_run_id=expected.global_run_id,
        domain_run_id=expected.domain_run_id,
        domain=expected.domain,
        run_uuid=expected.run_uuid,
        attempt_uuid=expected.attempt_uuid,
        execution_mode=expected.execution_mode,
    )


def _acquire(
    backend: MemoryReservationBackend,
    *,
    expected: authority.ReceiptExpectation | None = None,
    approval_id: str = "external-approval-r7s5-001",
    root: reservation.ReservationRootExpectation = ROOT,
) -> reservation.ReservationLease:
    expectation = expected or _expected()
    return reservation._acquire_reservation_for_test(
        _verified(expectation, approval_id=approval_id),
        execution_identity=_identity(expectation),
        root_expectation=root,
        backend=backend,
    )


def test_production_api_is_fixed_root_and_fails_before_backend_io() -> None:
    signature = inspect.signature(reservation.acquire_production_reservation)
    assert "backend" not in signature.parameters
    assert "root" not in signature.parameters
    with pytest.raises(
        reservation.R7S5ReservationError,
        match="production_reservation_backend_unconfigured",
    ):
        reservation.acquire_production_reservation(
            object(), execution_identity=_identity(_expected())
        )
    contract = reservation.reservation_contract()
    assert contract["canonical_root"] == reservation.CANONICAL_RESERVATION_ROOT
    assert contract["caller_selectable_production_root"] is False
    assert contract["production_entry_enabled"] is False
    assert contract["multi_host_global_one_shot_provided"] is False
    assert contract["same_token_hostile_admin_protected"] is False
    assert contract["production_go"] is False


def test_handle_is_retained_read_back_and_closed_only_at_terminal_boundary() -> None:
    backend = MemoryReservationBackend()
    lease = _acquire(backend)
    assert lease.active is True
    assert lease.readback_count == 1
    assert backend.open_handles
    assert lease.evidence.root_identity.security_descriptor_sha256 == "a" * 64
    lease.assert_active()
    assert lease.readback_count == 2
    lease.close()
    assert lease.active is False
    assert lease.close_count == 1
    assert backend.open_handles == set()
    with pytest.raises(reservation.R7S5ReservationError, match="lease_not_active"):
        lease.assert_active()
    with pytest.raises(reservation.R7S5ReservationError, match="close_reentry"):
        lease.close()


def test_reissued_receipt_same_execution_identity_collides_on_stable_key() -> None:
    backend = MemoryReservationBackend()
    first = _acquire(backend, approval_id="external-approval-r7s5-first")
    first_leaf = first.evidence.marker_leaf
    first.close()
    with pytest.raises(reservation.ReservationCollisionError, match="identity_collision"):
        _acquire(backend, approval_id="external-approval-r7s5-second")
    assert backend.acquire_calls == 2
    assert len(backend.markers) == 1
    assert first_leaf.startswith("r7s5-execution-")


def test_concurrent_same_execution_identity_has_one_local_winner() -> None:
    backend = MemoryReservationBackend()
    barrier = threading.Barrier(8)

    def compete() -> reservation.ReservationLease | str:
        barrier.wait()
        try:
            return _acquire(backend)
        except reservation.ReservationCollisionError:
            return "collision"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: compete(), range(8)))
    winners = [item for item in results if isinstance(item, reservation.ReservationLease)]
    assert len(winners) == 1
    assert results.count("collision") == 7
    winners[0].close()
    assert backend.open_handles == set()


def test_different_execution_identity_uses_a_distinct_collision_key() -> None:
    backend = MemoryReservationBackend()
    first_expected = _expected()
    second_expected = _expected(
        run_uuid="22334455-6677-4889-9aab-ccddeeff0011",
        attempt_uuid="bbccddee-ff00-4222-8333-445566778899",
    )
    first = _acquire(backend, expected=first_expected)
    second = _acquire(backend, expected=second_expected)
    assert first.evidence.execution_identity_sha256 != second.evidence.execution_identity_sha256
    assert len(backend.markers) == 2
    first.close()
    second.close()


@pytest.mark.parametrize(
    "root,match",
    [
        (replace(ROOT, final_path=r"F:\alternate"), "alternate_reservation_root"),
        (replace(ROOT, volume_serial_number=0), "positive_int_required"),
        (replace(ROOT, volume_serial_number=True), "positive_int_required"),
        (replace(ROOT, file_id_hex="z" * 32), "root_file_id_invalid"),
        (replace(ROOT, owner_sid="owner"), "root_owner_sid_invalid"),
        (replace(ROOT, security_descriptor_sha256="a" * 63), "security_descriptor"),
        (replace(ROOT, dacl_present=False), "root_dacl_contract_invalid"),
        (replace(ROOT, dacl_protected=False), "root_dacl_contract_invalid"),
    ],
)
def test_root_expectation_mutation_fails_before_backend(
    root: reservation.ReservationRootExpectation, match: str
) -> None:
    backend = MemoryReservationBackend()
    with pytest.raises(reservation.R7S5ReservationError, match=match):
        _acquire(backend, root=root)
    assert backend.acquire_calls == 0


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: replace(value, handle=True), "reservation_handle_invalid"),
        (lambda value: replace(value, handle_retained=False), "publication_contract"),
        (lambda value: replace(value, create_no_replace=False), "publication_contract"),
        (lambda value: replace(value, directory_flush_count=True), "publication_contract"),
        (lambda value: replace(value, bytes=True), "raw_sha_bytes"),
        (lambda value: replace(value, production_go=True), "publication_contract"),
        (
            lambda value: replace(
                value,
                root_identity=replace(value.root_identity, volume_serial_number=0),
            ),
            "positive_int_required",
        ),
        (
            lambda value: replace(
                value,
                root_identity=replace(value.root_identity, security_descriptor_sha256="c" * 64),
            ),
            "root_identity_mismatch",
        ),
        (
            lambda value: replace(
                value,
                identity=replace(value.identity, volume_serial_number=43),
            ),
            "cross_volume",
        ),
        (
            lambda value: replace(
                value,
                identity=replace(value.identity, file_id_hex=value.root_identity.file_id_hex),
            ),
            "file_root_identity_collision",
        ),
    ],
)
def test_publication_identity_or_overclaim_mutation_is_rejected_and_closed(
    mutation: Callable[
        [reservation.ReservationBackendAcquisition], reservation.ReservationBackendAcquisition
    ],
    match: str,
) -> None:
    backend = MemoryReservationBackend(acquisition_mutation=mutation)
    with pytest.raises(reservation.R7S5ReservationError, match=match):
        _acquire(backend)
    if match == "reservation_handle_invalid":
        # An untrusted returned handle is never used as a cleanup target.
        assert backend.close_calls == 0
    else:
        assert backend.close_calls == 1
        assert backend.open_handles == set()


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: replace(value, sha256="0" * 64), "readback_raw_sha"),
        (
            lambda value: replace(
                value,
                root_identity=replace(value.root_identity, owner_sid="S-1-5-18"),
            ),
            "root_identity_mismatch",
        ),
        (
            lambda value: replace(value, identity=replace(value.identity, dacl_protected=False)),
            "dacl_contract",
        ),
    ],
)
def test_same_handle_readback_mutation_is_fail_closed(
    mutation: Callable[
        [reservation.ReservationBackendReadback], reservation.ReservationBackendReadback
    ],
    match: str,
) -> None:
    backend = MemoryReservationBackend(readback_mutation=mutation)
    with pytest.raises(reservation.R7S5ReservationError, match=match):
        _acquire(backend)
    assert backend.close_calls == 1
    assert backend.open_handles == set()


def test_receipt_domain_swap_is_rejected_before_backend() -> None:
    expected = _expected()
    other = replace(expected, domain="windows", domain_run_id=f"{GLOBAL_RUN_ID}-windows")
    backend = MemoryReservationBackend()
    with pytest.raises(
        reservation.R7S5ReservationError, match="receipt_execution_identity_mismatch"
    ):
        reservation._acquire_reservation_for_test(
            _verified(expected),
            execution_identity=_identity(other),
            root_expectation=ROOT,
            backend=backend,
        )
    assert backend.acquire_calls == 0
