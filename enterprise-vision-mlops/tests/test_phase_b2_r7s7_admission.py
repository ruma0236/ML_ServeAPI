from __future__ import annotations

import hashlib
import inspect
import ntpath
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace

import pytest

from evm.scale_validation import phase_b2_r7s7_admission as admission


GLOBAL_RUN_ID = "11111111-1111-4111-8111-111111111111"
RUN_UUID = "22222222-2222-4222-8222-222222222222"
ATTEMPT_UUID = "33333333-3333-4333-8333-333333333333"
PLAN_ID = "44444444-4444-4444-8444-444444444444"
COMMIT = "a" * 40
TREE = "b" * 40
CANDIDATE_SHA256 = "c" * 64
APPROVAL_RAW = b"internal-non-authoritative-approval-test-double\n"


def _directory(
    role: str,
    path: str,
    digit: str,
    *,
    volume_serial_number: int,
) -> admission.DirectoryIdentity:
    return admission.DirectoryIdentity(
        role=role,
        final_path=path,
        volume_serial_number=volume_serial_number,
        file_id_hex=digit * 32,
        owner_sid="S-1-5-32-544",
        security_descriptor_sha256=hex((int(digit, 16) + 5) % 16)[2:] * 64,
        dacl_present=True,
        dacl_protected=True,
        link_count=1,
        reparse_tag=0,
        file_type=admission.FILE_TYPE_DISK,
        is_directory=True,
    )


def _identity(role: str, filename: str, digit: str) -> admission.HandleIdentity:
    final_path = rf"C:\approved\r7s7\{filename}"
    volume_serial_number = int(digit, 16) + 1
    parent_digit = hex((int(digit, 16) + 8) % 16)[2:]
    return admission.HandleIdentity(
        role=role,
        final_path=final_path,
        volume_serial_number=volume_serial_number,
        file_id_hex=digit * 32,
        sha256=digit * 64,
        bytes=101,
        owner_sid="S-1-5-32-544",
        security_descriptor_sha256=(hex((int(digit, 16) + 5) % 16)[2:]) * 64,
        dacl_present=True,
        dacl_protected=True,
        link_count=1,
        reparse_tag=0,
        file_type=admission.FILE_TYPE_DISK,
        creation_time_ns=1_700_000_000_000_000_000 + int(digit, 16),
        parent_directory_identity=_directory(
            f"{role}:parent",
            ntpath.dirname(final_path),
            parent_digit,
            volume_serial_number=volume_serial_number,
        ),
    )


SOURCE_IDENTITY = _identity("source", "candidate.json", "1")
TOOL_IDENTITIES = (
    _identity("tool:0", "outer.ps1", "2"),
    _identity("tool:1", "bridge.py", "3"),
)
INVOCATION_PAYLOAD = {
    "schema": admission.INVOCATION_SCHEMA,
    "working_directory": r"C:\approved\r7s7",
    "argv": [
        TOOL_IDENTITIES[0].final_path,
        "-NoProfile",
        "-File",
        TOOL_IDENTITIES[1].final_path,
        "--candidate",
        SOURCE_IDENTITY.final_path,
    ],
    "absolute_path_argument_indexes": [0, 3, 5],
}
NORMALIZED_INVOCATION = admission.NormalizedInvocation(
    schema=admission.INVOCATION_SCHEMA,
    working_directory=str(INVOCATION_PAYLOAD["working_directory"]),
    argv=tuple(INVOCATION_PAYLOAD["argv"]),
    absolute_path_argument_indexes=tuple(INVOCATION_PAYLOAD["absolute_path_argument_indexes"]),
    canonical_sha256=hashlib.sha256(admission.canonical_json_bytes(INVOCATION_PAYLOAD)).hexdigest(),
)
RESERVATION_DIRECTORY_IDENTITY = _directory(
    "reservation_marker:parent",
    admission.CANONICAL_RESERVATION_ROOT,
    "4",
    volume_serial_number=41,
)
EVIDENCE_DIRECTORY_IDENTITY = _directory(
    "evidence_record:parent",
    admission.CANONICAL_EVIDENCE_ROOT,
    "5",
    volume_serial_number=42,
)


def _rename_identity(
    *,
    role: str,
    directory: admission.DirectoryIdentity,
    leaf: str,
    raw: bytes,
    file_handle: int,
    digit: str,
) -> admission.RenameIdentityEvidence:
    temporary_leaf = f".{leaf}.{RUN_UUID}.partial"
    before = admission.HandleIdentity(
        role=role,
        final_path=ntpath.join(directory.final_path, temporary_leaf),
        volume_serial_number=directory.volume_serial_number,
        file_id_hex=digit * 32,
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
        owner_sid="S-1-5-32-544",
        security_descriptor_sha256=hex((int(digit, 16) + 5) % 16)[2:] * 64,
        dacl_present=True,
        dacl_protected=True,
        link_count=1,
        reparse_tag=0,
        file_type=admission.FILE_TYPE_DISK,
        creation_time_ns=1_800_000_000_000_000_000 + int(digit, 16),
        parent_directory_identity=directory,
    )
    return admission.RenameIdentityEvidence(
        temporary_leaf=temporary_leaf,
        file_handle=file_handle,
        before_identity=before,
        after_identity=replace(before, final_path=ntpath.join(directory.final_path, leaf)),
        same_file_handle_across_rename=True,
        rename_no_replace=True,
    )


def _work_order_value() -> dict[str, object]:
    return {
        "schema": admission.WORK_ORDER_SCHEMA,
        "global_run_id": GLOBAL_RUN_ID,
        "run_uuid": RUN_UUID,
        "attempt_uuid": ATTEMPT_UUID,
        "commit": COMMIT,
        "tree": TREE,
        "candidate_sha256": CANDIDATE_SHA256,
        "execution_mode": "offline_reviewer_candidate",
        "source_identity": asdict(SOURCE_IDENTITY),
        "tool_identities": [asdict(item) for item in TOOL_IDENTITIES],
        "normalized_invocation": asdict(NORMALIZED_INVOCATION),
        "reservation_directory_identity": asdict(RESERVATION_DIRECTORY_IDENTITY),
        "evidence_directory_identity": asdict(EVIDENCE_DIRECTORY_IDENTITY),
    }


def _work_order_raw(value: dict[str, object] | None = None) -> bytes:
    return admission.canonical_json_bytes(value or _work_order_value())


def _expectation(raw: bytes | None = None) -> admission.ReviewerExpectation:
    selected = _work_order_raw() if raw is None else raw
    return admission.ReviewerExpectation(
        work_order_sha256=hashlib.sha256(selected).hexdigest(),
        global_run_id=GLOBAL_RUN_ID,
        run_uuid=RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        commit=COMMIT,
        tree=TREE,
        candidate_sha256=CANDIDATE_SHA256,
    )


class FakeVerifier:
    def __init__(self, events: list[str], **overrides: object) -> None:
        self.events = events
        self.overrides = overrides
        self.calls = 0

    def verify_for_test(
        self, receipt_raw: bytes, *, work_order_sha256: str
    ) -> admission.VerifiedApprovalReceipt:
        self.calls += 1
        self.events.append("approval")
        values: dict[str, object] = {
            "schema": admission.INTERNAL_APPROVAL_SCHEMA,
            "authority_scope": "internal_non_authoritative_test_double",
            "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
            "approval_id": "internal-approval-r7s7-001",
            "work_order_sha256": work_order_sha256,
            "global_run_id": GLOBAL_RUN_ID,
            "run_uuid": RUN_UUID,
            "attempt_uuid": ATTEMPT_UUID,
            "commit": COMMIT,
            "tree": TREE,
            "candidate_sha256": CANDIDATE_SHA256,
            "externally_verified": True,
            "approve_exact_reviewer_candidate_once": True,
            "production_go": False,
        }
        values.update(self.overrides)
        return admission.VerifiedApprovalReceipt(**values)


class FakeReservation:
    def __init__(
        self,
        events: list[str],
        *,
        collide: bool = False,
        wrong_root: bool = False,
        close_fails: bool = False,
        directory_drift: str | None = None,
        rename_identity_drift: str | None = None,
        replace_on_read: int | None = None,
    ) -> None:
        self.events = events
        self.collide = collide
        self.wrong_root = wrong_root
        self.close_fails = close_fails
        self.directory_drift = directory_drift
        self.rename_identity_drift = rename_identity_drift
        self.replace_on_read = replace_on_read
        self.acquire_calls = 0
        self.read_calls = 0
        self.close_calls = 0
        self.last_acquisition: admission.ReservationAcquisition | None = None

    def acquire_once_for_test(
        self, *, collision_key_sha256: str, record_raw: bytes
    ) -> admission.ReservationAcquisition:
        self.acquire_calls += 1
        self.events.append("reservation")
        if self.collide:
            raise FileExistsError(collision_key_sha256)
        leaf = f"r7s7-review-{collision_key_sha256}.reservation.json"
        root = (
            r"C:\unapproved\fallback" if self.wrong_root else admission.CANONICAL_RESERVATION_ROOT
        )
        rename_identity = _rename_identity(
            role="reservation_marker",
            directory=RESERVATION_DIRECTORY_IDENTITY,
            leaf=leaf,
            raw=record_raw,
            file_handle=701,
            digit="6",
        )
        if self.rename_identity_drift:
            changed: object
            if self.rename_identity_drift == "file_id_hex":
                changed = "9" * 32
            elif self.rename_identity_drift == "volume_serial_number":
                changed = rename_identity.after_identity.volume_serial_number + 1
            elif self.rename_identity_drift == "security_descriptor_sha256":
                changed = "a" * 64
            elif self.rename_identity_drift == "dacl_protected":
                changed = False
            else:
                changed = rename_identity.after_identity.creation_time_ns + 1
            rename_identity = replace(
                rename_identity,
                after_identity=replace(
                    rename_identity.after_identity,
                    **{self.rename_identity_drift: changed},
                ),
            )
        directory_after = RESERVATION_DIRECTORY_IDENTITY
        if self.directory_drift:
            if self.directory_drift == "file_id_hex":
                changed = "9" * 32
            elif self.directory_drift == "volume_serial_number":
                changed = directory_after.volume_serial_number + 1
            elif self.directory_drift == "dacl_protected":
                changed = False
            elif self.directory_drift == "is_directory":
                changed = False
            else:
                changed = "a" * 64
            directory_after = replace(directory_after, **{self.directory_drift: changed})
        self.last_acquisition = admission.ReservationAcquisition(
            schema=admission.INTERNAL_RESERVATION_SCHEMA,
            collision_key_sha256=collision_key_sha256,
            root_path=root,
            final_path=ntpath.join(root, leaf),
            leaf=leaf,
            handle=701,
            record_sha256=hashlib.sha256(record_raw).hexdigest(),
            create_no_replace=True,
            replace_if_exists=False,
            cross_process_visible=True,
            same_handle_readback=True,
            handle_retained=True,
            directory_handle=702,
            directory_identity_before=RESERVATION_DIRECTORY_IDENTITY,
            directory_identity_after=directory_after,
            directory_handle_retained=True,
            same_directory_handle_across_rename=True,
            rename_identity=rename_identity,
            path_fallback_count=0,
            production_go=False,
        )
        return self.last_acquisition

    def read_same_handles_for_test(
        self, handle: int, directory_handle: int
    ) -> admission.ReservationAcquisition:
        assert handle == 701
        assert directory_handle == 702
        assert self.last_acquisition is not None
        self.read_calls += 1
        self.events.append("reservation_readback")
        if self.read_calls != self.replace_on_read:
            return self.last_acquisition
        rename_identity = self.last_acquisition.rename_identity
        before = replace(
            rename_identity.before_identity,
            file_id_hex="9" * 32,
            creation_time_ns=rename_identity.before_identity.creation_time_ns + 1,
        )
        after = replace(
            rename_identity.after_identity,
            file_id_hex="9" * 32,
            creation_time_ns=rename_identity.after_identity.creation_time_ns + 1,
        )
        return replace(
            self.last_acquisition,
            rename_identity=replace(
                rename_identity,
                before_identity=before,
                after_identity=after,
            ),
        )

    def close_for_test(self, handle: int, directory_handle: int) -> None:
        assert handle == 701
        assert directory_handle == 702
        self.close_calls += 1
        self.events.append("reservation_close")
        if self.close_fails:
            raise OSError("ambiguous reservation close")


class AtomicRacingReservation(FakeReservation):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self._lock = threading.Lock()
        self._claimed = False

    def acquire_once_for_test(
        self, *, collision_key_sha256: str, record_raw: bytes
    ) -> admission.ReservationAcquisition:
        with self._lock:
            if self._claimed:
                self.acquire_calls += 1
                self.events.append("reservation")
                raise FileExistsError(collision_key_sha256)
            self._claimed = True
        return super().acquire_once_for_test(
            collision_key_sha256=collision_key_sha256,
            record_raw=record_raw,
        )


class FakeIdentityBinder:
    def __init__(
        self,
        events: list[str],
        *,
        bind_drift: bool = False,
        readback_drift: bool = False,
        readback_file_drift: str | None = None,
        readback_directory_drift: bool = False,
        readback_handle_change: bool = False,
        close_fails: bool = False,
    ) -> None:
        self.events = events
        self.bind_drift = bind_drift
        self.readback_drift = readback_drift
        self.readback_file_drift = readback_file_drift
        self.readback_directory_drift = readback_directory_drift
        self.readback_handle_change = readback_handle_change
        self.close_fails = close_fails
        self.bind_calls = 0
        self.readback_calls = 0
        self.close_calls = 0
        self.acquisition: admission.BoundIdentityAcquisition | None = None

    @staticmethod
    def _with_sha(
        *,
        handle_ids: tuple[int, ...],
        directory_handle_ids: tuple[int, ...],
        source: admission.HandleIdentity,
        tools: tuple[admission.HandleIdentity, ...],
    ) -> admission.BoundIdentityAcquisition:
        unsealed = admission.BoundIdentityAcquisition(
            handle_ids=handle_ids,
            directory_handle_ids=directory_handle_ids,
            source_identity=source,
            tool_identities=tools,
            same_handle_readback=True,
            same_directory_handle_readback=True,
            handles_retained=True,
            directory_handles_retained=True,
            snapshot_sha256="0" * 64,
        )
        return replace(unsealed, snapshot_sha256=admission._identity_snapshot_sha(unsealed))

    def bind_for_test(self, order: admission.WorkOrder) -> admission.BoundIdentityAcquisition:
        self.bind_calls += 1
        self.events.append("identity_bind")
        source = order.source_identity
        if self.bind_drift:
            source = replace(source, sha256="d" * 64)
        self.acquisition = self._with_sha(
            handle_ids=(801, 802, 803),
            directory_handle_ids=(811, 812, 813),
            source=source,
            tools=order.tool_identities,
        )
        return self.acquisition

    def read_same_handles_for_test(
        self,
        handle_ids: tuple[int, ...],
        directory_handle_ids: tuple[int, ...],
    ) -> admission.BoundIdentityAcquisition:
        self.readback_calls += 1
        self.events.append("identity_readback")
        assert self.acquisition is not None
        assert handle_ids == self.acquisition.handle_ids
        assert directory_handle_ids == self.acquisition.directory_handle_ids
        source = self.acquisition.source_identity
        if self.readback_drift:
            source = replace(source, sha256="e" * 64)
        if self.readback_file_drift:
            if self.readback_file_drift == "file_id_hex":
                changed: object = "f" * 32
            elif self.readback_file_drift == "volume_serial_number":
                changed = source.volume_serial_number + 1
            elif self.readback_file_drift == "security_descriptor_sha256":
                changed = "0" * 64
            elif self.readback_file_drift == "dacl_protected":
                changed = False
            else:
                changed = source.creation_time_ns + 1
            source = replace(source, **{self.readback_file_drift: changed})
        if self.readback_directory_drift:
            source = replace(
                source,
                parent_directory_identity=replace(
                    source.parent_directory_identity,
                    security_descriptor_sha256="f" * 64,
                ),
            )
        returned_handles = (901, 902, 903) if self.readback_handle_change else handle_ids
        return self._with_sha(
            handle_ids=returned_handles,
            directory_handle_ids=directory_handle_ids,
            source=source,
            tools=self.acquisition.tool_identities,
        )

    def close_for_test(
        self,
        handle_ids: tuple[int, ...],
        directory_handle_ids: tuple[int, ...],
    ) -> None:
        assert handle_ids == (801, 802, 803)
        assert directory_handle_ids == (811, 812, 813)
        self.close_calls += 1
        self.events.append("identity_close")
        if self.close_fails:
            raise OSError("ambiguous identity close")


class FakePlanBuilder:
    def __init__(
        self,
        events: list[str],
        *,
        wrong_binding: bool = False,
        raise_after_nonce: bool = False,
        invocation_override: admission.NormalizedInvocation | None = None,
        command_sha_override: str | None = None,
    ) -> None:
        self.events = events
        self.wrong_binding = wrong_binding
        self.raise_after_nonce = raise_after_nonce
        self.invocation_override = invocation_override
        self.command_sha_override = command_sha_override
        self.calls = 0
        self.plan: admission.SuspendedAdminRootPlan | None = None
        self.supplied_nonce: bytearray | None = None
        self.nonce_before: bytes | None = None

    def build_for_test(
        self,
        *,
        order: admission.WorkOrder,
        receipt: admission.VerifiedApprovalReceipt,
        reservation: admission.ReservationAcquisition,
        identities: admission.BoundIdentityAcquisition,
        launch_nonce: bytearray,
    ) -> admission.SuspendedAdminRootPlan:
        self.calls += 1
        self.events.append("suspended_plan")
        self.supplied_nonce = launch_nonce
        self.nonce_before = bytes(launch_nonce)
        if self.raise_after_nonce:
            raise OSError("simulated plan builder exception")
        invocation = self.invocation_override or order.normalized_invocation
        self.plan = admission.SuspendedAdminRootPlan(
            schema=admission.INTERNAL_PLAN_SCHEMA,
            plan_id=PLAN_ID,
            work_order_sha256=("f" * 64 if self.wrong_binding else order.raw_sha256),
            receipt_sha256=receipt.receipt_sha256,
            reservation_key_sha256=reservation.collision_key_sha256,
            identity_snapshot_sha256=identities.snapshot_sha256,
            job_identity="internal-query-only-job-r7s7",
            normalized_invocation=invocation,
            command_sha256=self.command_sha_override or invocation.canonical_sha256,
            create_suspended=True,
            administrator_required=True,
            integrity_required="High",
            elevation_type_required="Full",
            process_created=False,
            root_resumed=False,
            launch_nonce=launch_nonce,
            production_go=False,
        )
        return self.plan


class FakeJobAdapter:
    def __init__(
        self,
        events: list[str],
        *,
        snapshot_mismatch: bool = False,
        can_terminate: bool = False,
        mutate_plan: bool = False,
    ) -> None:
        self.events = events
        self.snapshot_mismatch = snapshot_mismatch
        self.can_terminate = can_terminate
        self.mutate_plan = mutate_plan
        self.calls = 0

    def query_for_test(
        self, plan: admission.SuspendedAdminRootPlan
    ) -> admission.QueryOnlyJobEvidence:
        self.calls += 1
        self.events.append("job_query")
        if self.mutate_plan:
            object.__setattr__(plan, "command_sha256", "7" * 64)
        explicit = admission.JobSnapshot(
            job_identity=plan.job_identity,
            active_process_count=0,
            total_process_count=0,
            assigned_process_id_list=(),
            accounting_sequence=1,
        )
        implicit = replace(explicit, accounting_sequence=2) if self.snapshot_mismatch else explicit
        return admission.QueryOnlyJobEvidence(
            schema=admission.INTERNAL_JOB_SCHEMA,
            plan_id=plan.plan_id,
            job_identity=plan.job_identity,
            access_rights="JOB_OBJECT_QUERY",
            query_only=True,
            can_assign=False,
            can_set_limits=False,
            can_terminate=self.can_terminate,
            explicit_snapshot=explicit,
            implicit_snapshot=implicit,
            explicit_query_count=1,
            implicit_query_count=1,
            production_go=False,
        )


class FakeEvidenceWriter:
    def __init__(
        self,
        events: list[str],
        *,
        fail: bool = False,
        wrong_root: bool = False,
        directory_drift: bool = False,
        rename_identity_drift: bool = False,
    ) -> None:
        self.events = events
        self.fail = fail
        self.wrong_root = wrong_root
        self.directory_drift = directory_drift
        self.rename_identity_drift = rename_identity_drift
        self.calls = 0
        self.raw: bytes | None = None

    def publish_no_replace_for_test(
        self, *, root_path: str, final_leaf: str, raw: bytes
    ) -> admission.AtomicEvidencePublication:
        self.calls += 1
        self.events.append("evidence")
        self.raw = raw
        if self.fail:
            raise OSError("simulated atomic publication failure")
        root = r"C:\unapproved\fallback" if self.wrong_root else root_path
        rename_identity = _rename_identity(
            role="evidence_record",
            directory=EVIDENCE_DIRECTORY_IDENTITY,
            leaf=final_leaf,
            raw=raw,
            file_handle=901,
            digit="7",
        )
        if self.rename_identity_drift:
            rename_identity = replace(
                rename_identity,
                after_identity=replace(
                    rename_identity.after_identity,
                    creation_time_ns=rename_identity.after_identity.creation_time_ns + 1,
                ),
            )
        directory_after = EVIDENCE_DIRECTORY_IDENTITY
        if self.directory_drift:
            directory_after = replace(directory_after, file_id_hex="8" * 32)
        return admission.AtomicEvidencePublication(
            schema=admission.INTERNAL_PUBLICATION_SCHEMA,
            root_path=root,
            final_path=ntpath.join(root, final_leaf),
            leaf=final_leaf,
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            create_attempt_count=1,
            atomic_rename=True,
            create_no_replace=True,
            replace_if_exists=False,
            same_handle_readback=True,
            directory_handle=902,
            directory_identity_before=EVIDENCE_DIRECTORY_IDENTITY,
            directory_identity_after=directory_after,
            directory_handle_retained=True,
            same_directory_handle_across_rename=True,
            rename_identity=rename_identity,
            file_flush_count=2,
            directory_flush_count=1,
            directory_flush_succeeded=True,
            worm_append_only=True,
            path_fallback_count=0,
            success_marker_count=0,
            completion_marker_count=0,
            production_go=False,
        )


def _internal_call(
    *,
    work_order_raw: bytes | None = None,
    expected: admission.ReviewerExpectation | None = None,
    approval_raw: bytes | None = APPROVAL_RAW,
    seen_receipts: tuple[str, ...] = (),
    verifier: FakeVerifier | None = None,
    reservation: FakeReservation | None = None,
    binder: FakeIdentityBinder | None = None,
    planner: FakePlanBuilder | None = None,
    job: FakeJobAdapter | None = None,
    writer: FakeEvidenceWriter | None = None,
) -> tuple[
    admission.InternalReviewerCandidateResult,
    list[str],
    FakeVerifier,
    FakeReservation,
    FakeIdentityBinder,
    FakePlanBuilder,
    FakeJobAdapter,
    FakeEvidenceWriter,
]:
    raw = _work_order_raw() if work_order_raw is None else work_order_raw
    events: list[str] = []
    selected_verifier = verifier or FakeVerifier(events)
    selected_reservation = reservation or FakeReservation(events)
    selected_binder = binder or FakeIdentityBinder(events)
    selected_planner = planner or FakePlanBuilder(events)
    selected_job = job or FakeJobAdapter(events)
    selected_writer = writer or FakeEvidenceWriter(events)
    result = admission._admit_reviewer_candidate_for_test(
        raw,
        approval_raw,
        expected=_expectation(raw) if expected is None else expected,
        verifier=selected_verifier,
        reservation_adapter=selected_reservation,
        identity_binder=selected_binder,
        plan_builder=selected_planner,
        job_adapter=selected_job,
        evidence_writer=selected_writer,
        seen_receipt_sha256s=seen_receipts,
    )
    return (
        result,
        events,
        selected_verifier,
        selected_reservation,
        selected_binder,
        selected_planner,
        selected_job,
        selected_writer,
    )


def _assert_forbidden_counts_zero(counts: admission.AdmissionCallCounts) -> None:
    for field in (
        "process_creation",
        "process_resume",
        "automatic_retry",
        "force_termination",
        "success_marker",
        "completion_marker",
        "path_fallback",
        "docker",
        "wsl",
        "etw",
        "r8",
    ):
        assert getattr(counts, field) == 0


def test_public_entry_is_noninjectable_and_fails_closed_before_process_creation() -> None:
    assert list(inspect.signature(admission.admit_reviewer_candidate).parameters) == [
        "work_order_raw",
        "approval_receipt_raw",
    ]

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        admission.admit_reviewer_candidate(b"ignored", b"ignored")

    failure = raised.value
    assert failure.code == "production_external_authority_unconfigured"
    assert failure.stage == "root_gate"
    assert failure.status == "reviewer_pending"
    assert failure.decision == "NO-GO"
    assert failure.credit == "zero_credit"
    assert failure.completed_stages == ()
    assert all(value == 0 for value in asdict(failure.counts).values())
    assert failure.counts.process_creation == 0
    assert failure.to_dict()["production_go"] is False


def test_public_contract_discloses_unprovisioned_authority_worm_and_no_live_entry() -> None:
    contract = admission.admission_contract()

    assert contract["status"] == "reviewer_pending"
    assert contract["decision"] == "NO-GO"
    assert contract["production_external_authority_configured"] is False
    assert contract["production_cross_process_reservation_configured"] is False
    assert contract["production_worm_evidence_adapter_configured"] is False
    assert contract["production_process_creation_enabled"] is False
    assert contract["production_entry_enabled"] is False
    assert contract["public_dependency_injection_allowed"] is False
    assert contract["live_process_calls_implemented"] is False
    assert contract["docker_wsl_etw_r8_calls_implemented"] is False


def test_public_flags_cannot_turn_unimplemented_wiring_into_process_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admission, "PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED", True)
    monkeypatch.setattr(admission, "PRODUCTION_CROSS_PROCESS_RESERVATION_CONFIGURED", True)
    monkeypatch.setattr(admission, "PRODUCTION_WORM_EVIDENCE_ADAPTER_CONFIGURED", True)
    monkeypatch.setattr(admission, "PRODUCTION_PROCESS_CREATION_ENABLED", True)
    monkeypatch.setattr(admission, "PRODUCTION_ENTRY_ENABLED", True)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        admission.admit_reviewer_candidate(_work_order_raw(), APPROVAL_RAW)

    assert raised.value.code == "production_reviewer_candidate_wiring_not_implemented"
    assert raised.value.decision == "NO-GO"
    assert raised.value.counts.process_creation == 0
    assert all(value == 0 for value in asdict(raised.value.counts).values())


def test_internal_candidate_obeys_order_and_is_never_production_closure() -> None:
    result, events, _, reservation, binder, planner, _, writer = _internal_call()

    assert events == [
        "approval",
        "reservation",
        "identity_bind",
        "suspended_plan",
        "job_query",
        "identity_readback",
        "reservation_readback",
        "evidence",
        "reservation_readback",
        "identity_close",
        "reservation_close",
    ]
    assert result.completed_stages == admission.ORDERED_STAGES
    assert result.reservation_closed is True
    assert result.identity_handles_closed is True
    assert reservation.acquire_calls == reservation.close_calls == 1
    assert reservation.read_calls == result.counts.reservation_readback == 2
    assert binder.bind_calls == binder.readback_calls == binder.close_calls == 1
    assert planner.plan is not None
    assert planner.supplied_nonce is planner.plan.launch_nonce
    assert planner.nonce_before is not None and any(planner.nonce_before)
    assert bytes(planner.plan.launch_nonce) == b"\x00" * 32
    assert writer.raw is not None
    assert planner.nonce_before.hex().encode("ascii") not in writer.raw
    assert b'"launch_nonce_present":false' in writer.raw
    assert reservation.last_acquisition is not None
    assert (
        reservation.last_acquisition.directory_identity_before
        == reservation.last_acquisition.directory_identity_after
        == RESERVATION_DIRECTORY_IDENTITY
    )
    assert (
        reservation.last_acquisition.rename_identity.before_identity.file_id_hex
        == reservation.last_acquisition.rename_identity.after_identity.file_id_hex
    )
    assert (
        result.publication.directory_identity_before
        == result.publication.directory_identity_after
        == EVIDENCE_DIRECTORY_IDENTITY
    )
    assert (
        result.publication.rename_identity.before_identity.file_id_hex
        == result.publication.rename_identity.after_identity.file_id_hex
    )
    assert result.publication.success_marker_count == 0
    assert result.publication.completion_marker_count == 0
    assert result.normalized_invocation_sha256 == NORMALIZED_INVOCATION.canonical_sha256
    _assert_forbidden_counts_zero(result.counts)

    rendered = result.to_dict()
    assert rendered["schema"] == admission.INTERNAL_RESULT_SCHEMA
    assert rendered["status"] == "internal_non_authoritative"
    assert rendered["decision"] == "reviewer_pending"
    assert rendered["credit"] == "zero_credit"
    assert rendered["ready_for_production_closure"] is False
    assert rendered["production_go"] is False


def test_absent_approval_stops_before_verification_or_reservation() -> None:
    events: list[str] = []
    verifier = FakeVerifier(events)
    reservation = FakeReservation(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(approval_raw=None, verifier=verifier, reservation=reservation)

    assert raised.value.code == "approval_receipt_absent"
    assert verifier.calls == 0
    assert reservation.acquire_calls == 0
    assert raised.value.counts.process_creation == 0


def test_replayed_receipt_stops_before_reservation() -> None:
    events: list[str] = []
    reservation = FakeReservation(events)
    receipt_sha = hashlib.sha256(APPROVAL_RAW).hexdigest()

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(seen_receipts=(receipt_sha,), reservation=reservation)

    assert raised.value.code == "approval_receipt_replay"
    assert raised.value.counts.receipt_replay_check == 1
    assert reservation.acquire_calls == 0
    _assert_forbidden_counts_zero(raised.value.counts)


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("global_run_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("run_uuid", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("attempt_uuid", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        ("commit", "d" * 40),
        ("tree", "e" * 40),
    ],
)
def test_oob_expectation_rejects_uuid_commit_or_tree_mismatch(field: str, wrong: str) -> None:
    raw = _work_order_raw()
    expected = replace(_expectation(raw), **{field: wrong})

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(work_order_raw=raw, expected=expected)

    assert raised.value.code == f"work_order_{field}_mismatch"
    assert raised.value.completed_stages == ()
    assert raised.value.counts.receipt_verification == 0
    _assert_forbidden_counts_zero(raised.value.counts)


def test_oob_digest_mismatch_precedes_json_or_authority_processing() -> None:
    raw = _work_order_raw()
    expected = replace(_expectation(raw), work_order_sha256="f" * 64)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(work_order_raw=raw, expected=expected)

    assert raised.value.code == "work_order_oob_digest_mismatch"
    assert raised.value.counts.work_order_validation == 1
    assert raised.value.counts.receipt_verification == 0


def test_self_consistent_work_order_and_receipt_tamper_cannot_replace_oob_digest() -> None:
    original_raw = _work_order_raw()
    value = _work_order_value()
    value["commit"] = "d" * 40
    value["tree"] = "e" * 40
    mutated_raw = _work_order_raw(value)
    events: list[str] = []
    verifier = FakeVerifier(events, commit="d" * 40, tree="e" * 40)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(
            work_order_raw=mutated_raw,
            expected=_expectation(original_raw),
            approval_raw=b"self-consistently-mutated-receipt\n",
            verifier=verifier,
        )

    assert raised.value.code == "work_order_oob_digest_mismatch"
    assert verifier.calls == 0
    assert raised.value.counts.reservation_acquire == 0
    assert raised.value.counts.process_creation == 0


def test_work_order_rejects_self_repin_of_normalized_invocation_hash() -> None:
    value = _work_order_value()
    invocation = dict(value["normalized_invocation"])
    invocation["canonical_sha256"] = "f" * 64
    value["normalized_invocation"] = invocation
    raw = _work_order_raw(value)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(work_order_raw=raw, expected=_expectation(raw))

    assert raised.value.code == "normalized_invocation_sha256_mismatch"
    assert raised.value.counts.receipt_verification == 0
    assert raised.value.counts.process_creation == 0


def test_work_order_rejects_self_consistent_invocation_not_bound_to_source_handles() -> None:
    value = _work_order_value()
    invocation = dict(value["normalized_invocation"])
    argv = list(invocation["argv"])
    argv[-1] = r"C:\approved\r7s7\unbound-candidate.json"
    invocation["argv"] = argv
    invocation["canonical_sha256"] = hashlib.sha256(
        admission.canonical_json_bytes(
            {
                "schema": invocation["schema"],
                "working_directory": invocation["working_directory"],
                "argv": argv,
                "absolute_path_argument_indexes": invocation["absolute_path_argument_indexes"],
            }
        )
    ).hexdigest()
    value["normalized_invocation"] = invocation
    raw = _work_order_raw(value)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(work_order_raw=raw, expected=_expectation(raw))

    assert raised.value.code == "invocation_bound_path_count_mismatch"
    assert raised.value.counts.receipt_verification == 0
    assert raised.value.counts.process_creation == 0


def test_work_order_rejects_unbound_invocation_working_directory() -> None:
    value = _work_order_value()
    invocation = dict(value["normalized_invocation"])
    invocation["working_directory"] = r"C:\unbound\r7s7"
    invocation["canonical_sha256"] = hashlib.sha256(
        admission.canonical_json_bytes(
            {
                "schema": invocation["schema"],
                "working_directory": invocation["working_directory"],
                "argv": list(invocation["argv"]),
                "absolute_path_argument_indexes": invocation["absolute_path_argument_indexes"],
            }
        )
    ).hexdigest()
    value["normalized_invocation"] = invocation
    raw = _work_order_raw(value)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(work_order_raw=raw, expected=_expectation(raw))

    assert raised.value.code == "invocation_working_directory_not_handle_bound"
    assert raised.value.counts.receipt_verification == 0
    assert raised.value.counts.process_creation == 0


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("global_run_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("run_uuid", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("attempt_uuid", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        ("commit", "d" * 40),
        ("tree", "e" * 40),
    ],
)
def test_receipt_binding_rejects_uuid_commit_or_tree_mismatch(field: str, wrong: str) -> None:
    events: list[str] = []
    verifier = FakeVerifier(events, **{field: wrong})
    reservation = FakeReservation(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(verifier=verifier, reservation=reservation)

    assert raised.value.code == f"approval_receipt_binding_mismatch:{field}"
    assert verifier.calls == 1
    assert reservation.acquire_calls == 0
    _assert_forbidden_counts_zero(raised.value.counts)


def test_cross_process_reservation_collision_is_one_shot_and_fail_closed() -> None:
    events: list[str] = []
    reservation = FakeReservation(events, collide=True)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(reservation=reservation)

    assert raised.value.code == "cross_process_reservation_collision"
    assert reservation.acquire_calls == 1
    assert reservation.close_calls == 0
    assert raised.value.counts.reservation_acquire == 1
    assert raised.value.counts.automatic_retry == 0
    assert raised.value.counts.process_creation == 0


def test_cross_thread_reservation_race_has_exactly_one_internal_candidate() -> None:
    events: list[str] = []
    reservation = AtomicRacingReservation(events)

    def attempt() -> tuple[str, admission.AdmissionCallCounts]:
        try:
            result, *_ = _internal_call(reservation=reservation)
        except admission.R7S7AdmissionError as exc:
            return exc.code, exc.counts
        return "internal_candidate", result.counts

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))

    assert sorted(item[0] for item in outcomes) == [
        "cross_process_reservation_collision",
        "internal_candidate",
    ]
    assert reservation.acquire_calls == 2
    assert reservation.close_calls == 1
    assert all(item[1].process_creation == 0 for item in outcomes)
    assert all(item[1].automatic_retry == 0 for item in outcomes)


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("directory", "reservation_contract_mismatch"),
        ("rename", "reservation_marker_file_identity_changed_across_rename"),
    ],
)
def test_reservation_directory_or_rename_identity_drift_is_rejected(
    failure: str, expected_code: str
) -> None:
    events: list[str] = []
    reservation = FakeReservation(
        events,
        directory_drift="security_descriptor_sha256" if failure == "directory" else None,
        rename_identity_drift="file_id_hex" if failure == "rename" else None,
    )

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(reservation=reservation)

    assert raised.value.code == expected_code
    assert raised.value.counts.process_creation == 0
    assert raised.value.counts.automatic_retry == 0


@pytest.mark.parametrize(
    ("scope", "field"),
    [
        ("directory", "file_id_hex"),
        ("directory", "volume_serial_number"),
        ("directory", "security_descriptor_sha256"),
        ("directory", "dacl_protected"),
        ("directory", "is_directory"),
        ("rename", "file_id_hex"),
        ("rename", "volume_serial_number"),
        ("rename", "security_descriptor_sha256"),
        ("rename", "dacl_protected"),
        ("rename", "creation_time_ns"),
    ],
)
def test_reservation_identity_fields_are_not_replaceable(scope: str, field: str) -> None:
    events: list[str] = []
    reservation = FakeReservation(
        events,
        directory_drift=field if scope == "directory" else None,
        rename_identity_drift=field if scope == "rename" else None,
    )

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(reservation=reservation)

    assert raised.value.stage == "reservation"
    assert raised.value.counts.process_creation == 0
    assert raised.value.counts.evidence_publication == 0


@pytest.mark.parametrize("read_number", [1, 2])
def test_replay_marker_replacement_is_detected_on_same_handle_readback(
    read_number: int,
) -> None:
    events: list[str] = []
    reservation = FakeReservation(events, replace_on_read=read_number)
    writer = FakeEvidenceWriter(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(reservation=reservation, writer=writer)

    assert raised.value.code == "cross_process_reservation_identity_changed"
    assert reservation.read_calls == read_number
    assert writer.calls == read_number - 1
    assert raised.value.counts.process_creation == 0
    assert raised.value.counts.automatic_retry == 0
    assert raised.value.counts.success_marker == 0
    assert raised.value.counts.completion_marker == 0


def test_noncanonical_reservation_path_is_rejected_without_fallback() -> None:
    events: list[str] = []
    reservation = FakeReservation(events, wrong_root=True)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(reservation=reservation)

    assert raised.value.code == "reservation_contract_mismatch"
    assert reservation.acquire_calls == 1
    assert reservation.close_calls == 0
    assert raised.value.counts.path_fallback == 0
    assert raised.value.counts.process_creation == 0


def test_initial_source_identity_drift_stops_before_plan_creation() -> None:
    events: list[str] = []
    binder = FakeIdentityBinder(events, bind_drift=True)
    planner = FakePlanBuilder(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(binder=binder, planner=planner)

    assert raised.value.code == "source_or_tool_identity_drift"
    assert binder.bind_calls == 1
    assert planner.calls == 0
    assert raised.value.counts.process_creation == 0


@pytest.mark.parametrize("mode", ["content", "handle"])
def test_same_handle_revalidation_rejects_identity_or_handle_drift(mode: str) -> None:
    events: list[str] = []
    binder = FakeIdentityBinder(
        events,
        readback_drift=mode == "content",
        readback_handle_change=mode == "handle",
    )
    planner = FakePlanBuilder(events)
    writer = FakeEvidenceWriter(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(binder=binder, planner=planner, writer=writer)

    expected_code = (
        "source_or_tool_identity_drift"
        if mode == "content"
        else "source_tool_handle_snapshot_changed"
    )
    assert raised.value.code == expected_code
    assert writer.calls == 0
    assert planner.plan is not None
    assert bytes(planner.plan.launch_nonce) == b"\x00" * 32
    assert raised.value.counts.nonce_clear == 1
    assert binder.close_calls == 1
    _assert_forbidden_counts_zero(raised.value.counts)


def test_parent_directory_toctou_is_rejected_before_evidence_or_process() -> None:
    events: list[str] = []
    binder = FakeIdentityBinder(events, readback_directory_drift=True)
    writer = FakeEvidenceWriter(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(binder=binder, writer=writer)

    assert raised.value.code == "source_or_tool_identity_drift"
    assert writer.calls == 0
    assert raised.value.counts.process_creation == 0
    assert raised.value.counts.force_termination == 0


@pytest.mark.parametrize(
    "field",
    [
        "file_id_hex",
        "volume_serial_number",
        "security_descriptor_sha256",
        "dacl_protected",
        "creation_time_ns",
    ],
)
def test_source_handle_identity_toctou_fields_are_rejected(field: str) -> None:
    events: list[str] = []
    binder = FakeIdentityBinder(events, readback_file_drift=field)
    writer = FakeEvidenceWriter(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(binder=binder, writer=writer)

    assert raised.value.stage == "identity_readback"
    assert writer.calls == 0
    assert raised.value.counts.process_creation == 0
    assert raised.value.counts.force_termination == 0


def test_suspended_admin_plan_binding_failure_clears_nonce_and_creates_no_process() -> None:
    events: list[str] = []
    planner = FakePlanBuilder(events, wrong_binding=True)
    writer = FakeEvidenceWriter(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(planner=planner, writer=writer)

    assert raised.value.code == "suspended_admin_root_plan_mismatch"
    assert planner.plan is not None
    assert bytes(planner.plan.launch_nonce) == b"\x00" * 32
    assert raised.value.counts.nonce_clear == 1
    assert raised.value.counts.process_creation == 0
    assert writer.calls == 0


@pytest.mark.parametrize("mutation", ["invocation_object", "command_sha256"])
def test_suspended_plan_requires_exact_oob_invocation_object_and_hash(mutation: str) -> None:
    events: list[str] = []
    alternate_argv = list(NORMALIZED_INVOCATION.argv)
    alternate_argv[1], alternate_argv[2] = alternate_argv[2], alternate_argv[1]
    invocation_payload = {
        "schema": NORMALIZED_INVOCATION.schema,
        "working_directory": NORMALIZED_INVOCATION.working_directory,
        "argv": alternate_argv,
        "absolute_path_argument_indexes": list(
            NORMALIZED_INVOCATION.absolute_path_argument_indexes
        ),
    }
    alternate = admission.NormalizedInvocation(
        schema=admission.INVOCATION_SCHEMA,
        working_directory=str(invocation_payload["working_directory"]),
        argv=tuple(invocation_payload["argv"]),
        absolute_path_argument_indexes=tuple(invocation_payload["absolute_path_argument_indexes"]),
        canonical_sha256=hashlib.sha256(
            admission.canonical_json_bytes(invocation_payload)
        ).hexdigest(),
    )
    planner = FakePlanBuilder(
        events,
        invocation_override=alternate if mutation == "invocation_object" else None,
        command_sha_override="f" * 64 if mutation == "command_sha256" else None,
    )
    writer = FakeEvidenceWriter(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(planner=planner, writer=writer)

    assert raised.value.code == "suspended_admin_root_plan_mismatch"
    assert planner.plan is not None
    assert bytes(planner.plan.launch_nonce) == b"\x00" * 32
    assert writer.calls == 0
    assert raised.value.counts.process_creation == 0


def test_plan_builder_exception_clears_orchestrator_owned_nonce() -> None:
    events: list[str] = []
    planner = FakePlanBuilder(events, raise_after_nonce=True)
    writer = FakeEvidenceWriter(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(planner=planner, writer=writer)

    assert raised.value.code == "suspended_admin_root_plan_failed"
    assert planner.plan is None
    assert planner.supplied_nonce is not None
    assert planner.nonce_before is not None and any(planner.nonce_before)
    assert bytes(planner.supplied_nonce) == b"\x00" * 32
    assert raised.value.counts.nonce_clear == 1
    assert raised.value.counts.process_creation == 0
    assert writer.calls == 0


@pytest.mark.parametrize("failure", ["snapshot", "terminate_capability"])
def test_query_only_job_requires_equal_explicit_implicit_snapshots(failure: str) -> None:
    events: list[str] = []
    job = FakeJobAdapter(
        events,
        snapshot_mismatch=failure == "snapshot",
        can_terminate=failure == "terminate_capability",
    )
    planner = FakePlanBuilder(events)
    writer = FakeEvidenceWriter(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(job=job, planner=planner, writer=writer)

    expected = (
        "explicit_implicit_job_snapshot_mismatch"
        if failure == "snapshot"
        else "query_only_job_capability_mismatch"
    )
    assert raised.value.code == expected
    assert job.calls == 1
    assert writer.calls == 0
    assert planner.plan is not None
    assert bytes(planner.plan.launch_nonce) == b"\x00" * 32
    assert raised.value.counts.force_termination == 0
    assert raised.value.counts.process_creation == 0


def test_job_query_cannot_mutate_the_bound_suspended_plan() -> None:
    events: list[str] = []
    job = FakeJobAdapter(events, mutate_plan=True)
    planner = FakePlanBuilder(events)
    writer = FakeEvidenceWriter(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(job=job, planner=planner, writer=writer)

    assert raised.value.code == "suspended_admin_root_plan_mismatch"
    assert writer.calls == 0
    assert planner.plan is not None
    assert bytes(planner.plan.launch_nonce) == b"\x00" * 32
    assert raised.value.counts.process_creation == 0
    _assert_forbidden_counts_zero(raised.value.counts)


def test_atomic_evidence_failure_is_terminal_without_retry_marker_or_fallback() -> None:
    events: list[str] = []
    writer = FakeEvidenceWriter(events, fail=True)
    planner = FakePlanBuilder(events)
    binder = FakeIdentityBinder(events)
    reservation = FakeReservation(events)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(
            writer=writer,
            planner=planner,
            binder=binder,
            reservation=reservation,
        )

    failure = raised.value
    assert failure.code == "atomic_no_replace_evidence_failed"
    assert writer.calls == 1
    assert planner.plan is not None
    assert bytes(planner.plan.launch_nonce) == b"\x00" * 32
    assert binder.close_calls == 1
    assert reservation.close_calls == 1
    assert failure.counts.evidence_publication == 1
    _assert_forbidden_counts_zero(failure.counts)


def test_noncanonical_evidence_path_fails_without_second_path_or_marker() -> None:
    events: list[str] = []
    writer = FakeEvidenceWriter(events, wrong_root=True)

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(writer=writer)

    assert raised.value.code == "atomic_no_replace_evidence_contract_mismatch"
    assert writer.calls == 1
    assert raised.value.counts.evidence_publication == 1
    assert raised.value.counts.path_fallback == 0
    assert raised.value.counts.success_marker == 0
    assert raised.value.counts.completion_marker == 0


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("directory", "atomic_no_replace_evidence_contract_mismatch"),
        ("rename", "evidence_record_file_identity_changed_across_rename"),
    ],
)
def test_evidence_directory_or_rename_identity_drift_is_rejected(
    failure: str, expected_code: str
) -> None:
    events: list[str] = []
    writer = FakeEvidenceWriter(
        events,
        directory_drift=failure == "directory",
        rename_identity_drift=failure == "rename",
    )

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(writer=writer)

    assert raised.value.code == expected_code
    assert writer.calls == 1
    assert raised.value.counts.evidence_publication == 1
    assert raised.value.counts.process_creation == 0
    assert raised.value.counts.automatic_retry == 0


@pytest.mark.parametrize("resource", ["identity", "reservation"])
def test_ambiguous_handle_close_is_terminal(resource: str) -> None:
    events: list[str] = []
    binder = FakeIdentityBinder(events, close_fails=resource == "identity")
    reservation = FakeReservation(events, close_fails=resource == "reservation")

    with pytest.raises(admission.R7S7AdmissionError) as raised:
        _internal_call(binder=binder, reservation=reservation)

    assert raised.value.code == f"{resource}_handle_close_ambiguous"
    assert raised.value.counts.process_creation == 0
    _assert_forbidden_counts_zero(raised.value.counts)


def test_module_contains_no_live_process_or_environment_adapter() -> None:
    source = inspect.getsource(admission)

    assert "import subprocess" not in source
    assert "from subprocess" not in source
    assert "CreateProcess" not in source
    assert "ResumeThread" not in source
    assert "TerminateProcess" not in source
    assert "TerminateJobObject" not in source
    assert "docker.exe" not in source
    assert "wsl.exe" not in source
    assert "kubectl.exe" not in source
    assert "_admit_reviewer_candidate_for_test" not in admission.__all__
