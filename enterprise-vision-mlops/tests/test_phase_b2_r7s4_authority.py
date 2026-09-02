from __future__ import annotations

import hashlib
import inspect
import ntpath
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any, Callable

import pytest

from evm.scale_validation import phase_b2_r7s4_authority as authority
from evm.scale_validation import phase_b2_r7s4_receipt_store as store
from scripts.dev import launch_pre_r8_r7s4_root as root_gate


NOW = datetime(2026, 9, 2, 1, 2, tzinfo=UTC)
GLOBAL_RUN_ID = "pre-r8-r7s4-20260902T010000Z-cafebabe"
RUN_UUID = "11223344-5566-4788-899a-bbccddeeff00"
ATTEMPT_UUID = "aabbccdd-eeff-4111-8222-334455667788"
EXPECTED = authority.ReceiptExpectation(
    approval_request_id="approval-request-r7s4-001",
    global_run_id=GLOBAL_RUN_ID,
    domain_run_id=f"{GLOBAL_RUN_ID}-wsl",
    domain="wsl",
    run_uuid=RUN_UUID,
    attempt_uuid=ATTEMPT_UUID,
    execution_mode="non_credit_wsl_containment_only",
)


def _pin(name: str, character: str) -> dict[str, Any]:
    return {
        "path": rf"C:\approved\r7s4\{name}",
        "sha256": character * 64,
        "bytes": 101,
    }


def _documents(
    *,
    mechanism: str = "independent_external_verifier",
    approval_id: str = "external-approval-001",
    expected: authority.ReceiptExpectation = EXPECTED,
    bootstrap_pin_character: str = "1",
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
        "bootstrap": _pin("bootstrap.py", bootstrap_pin_character),
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
        "created_at_utc": "2026-09-02T01:00:00Z",
        "expires_at_utc": "2026-09-02T01:30:00Z",
        "subject": subject,
        "production_entry_enabled": False,
    }
    request_raw = authority.canonical_json_bytes(request)
    receipt = {
        "schema": authority.APPROVAL_RECEIPT_SCHEMA,
        "status": "approved",
        "decision": "approve_exact_candidate_once",
        "approval_request_id": expected.approval_request_id,
        "issued_at_utc": "2026-09-02T01:01:00Z",
        "expires_at_utc": "2026-09-02T01:20:00Z",
        "authority": {
            "mechanism": mechanism,
            "reviewer_identity": "independent-reviewer-001",
            "approval_id": approval_id,
            "key_id": "external-key-001",
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
    """Test-only verifier; production modules contain no fake authority."""

    def __init__(self) -> None:
        self.calls = 0

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
        self.calls += 1
        receipt = authority.strict_canonical_json_bytes(receipt_raw, "fake_verifier_receipt")
        return authority.ExternalAuthorityAttestation(
            schema=authority.EXTERNAL_ATTESTATION_SCHEMA,
            status="authenticated",
            receipt_sha256=receipt_sha256,
            approval_request_sha256=approval_request_sha256,
            subject_sha256=subject_sha256,
            approval_id=receipt["authority"]["approval_id"],
            reviewer_identity=receipt["authority"]["reviewer_identity"],
            authority_key_id=receipt["authority"]["key_id"],
            verifier_identity="external-test-verifier",
            independent_authority_verified=True,
            authorize_exact_candidate_once=True,
        )


def _verified(
    *,
    expected: authority.ReceiptExpectation = EXPECTED,
    approval_id: str = "external-approval-001",
    bootstrap_pin_character: str = "1",
) -> tuple[bytes, bytes, authority.VerifiedExternalReceipt]:
    receipt_raw, request_raw = _documents(
        expected=expected,
        approval_id=approval_id,
        bootstrap_pin_character=bootstrap_pin_character,
    )
    result = authority._verify_external_receipt_for_test(
        receipt_raw,
        request_raw,
        expected=expected,
        verifier=FakeExternalVerifier(),
        validation_time=NOW,
    )
    return receipt_raw, request_raw, result


class MemoryPublisher:
    def __init__(
        self,
        *,
        fail_after_commit: bool = False,
        mutation: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.fail_after_commit = fail_after_commit
        self.mutation = mutation
        self.lock = threading.Lock()
        self.markers: dict[tuple[str, str], bytes] = {}
        self.calls: list[dict[str, object]] = []

    def publish(
        self,
        *,
        directory: str,
        final_leaf: str,
        raw: bytes,
        run_uuid: str,
    ) -> dict[str, object]:
        call = {
            "directory": directory,
            "final_leaf": final_leaf,
            "raw": raw,
            "run_uuid": run_uuid,
        }
        with self.lock:
            self.calls.append(call)
            key = (directory, final_leaf)
            if key in self.markers:
                raise FileExistsError(final_leaf)
            self.markers[key] = raw
        if self.fail_after_commit:
            raise OSError("simulated_publication_ack_lost")
        final_path = ntpath.join(directory, final_leaf)
        publication: dict[str, object] = {
            "final_path": final_path,
            "temporary_leaf": f".{final_leaf}.{run_uuid}.partial",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "identity": {
                "final_path": final_path,
                "volume_serial_number": 42,
                "file_id_hex": hashlib.sha256(f"file:{final_path}".encode()).hexdigest()[:32],
                "size": len(raw),
                "link_count": 1,
                "attributes": 0x20,
                "reparse_tag": 0,
                "file_type": 1,
                "owner_sid": "S-1-5-32-544",
                "security_descriptor_sha256": "a" * 64,
                "dacl_present": True,
                "dacl_protected": True,
            },
            "directory_identity": {
                "final_path": directory,
                "volume_serial_number": 42,
                "file_id_hex": hashlib.sha256(f"directory:{directory}".encode()).hexdigest()[:32],
                "size": 0,
                "link_count": 1,
                "attributes": 0x10,
                "reparse_tag": 0,
                "file_type": 1,
                "owner_sid": "S-1-5-32-544",
                "security_descriptor_sha256": "b" * 64,
                "dacl_present": True,
                "dacl_protected": True,
            },
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
        if self.mutation is not None:
            self.mutation(publication)
        return publication


def _publication_mutation(
    path: tuple[str, ...], value: object
) -> Callable[[dict[str, object]], None]:
    def mutate(publication: dict[str, object]) -> None:
        target: Any = publication
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = value

    return mutate


def test_canonical_json_rejects_duplicate_nonfinite_unknown_and_noncanonical() -> None:
    with pytest.raises(authority.R7S4AuthorityError, match="duplicate_key"):
        authority.strict_canonical_json_bytes(b'{"a":1,"a":2}\n', "duplicate")
    with pytest.raises(authority.R7S4AuthorityError, match="nonfinite_number"):
        authority.strict_canonical_json_bytes(b'{"a":NaN}\n', "nan")
    receipt_raw, request_raw = _documents()
    request = authority.strict_canonical_json_bytes(request_raw, "request")
    request["unknown"] = True
    with pytest.raises(authority.R7S4AuthorityError, match="keys_mismatch"):
        authority._verify_external_receipt_for_test(
            receipt_raw,
            authority.canonical_json_bytes(request),
            expected=EXPECTED,
            verifier=FakeExternalVerifier(),
            validation_time=NOW,
        )
    with pytest.raises(authority.R7S4AuthorityError, match="noncanonical_bytes"):
        authority.strict_canonical_json_bytes(b'{"b":2, "a":1}\n', "spaced")


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-02T01:00Z",
        "2026-09-02T01:00:00.000000Z",
        "2026-09-02t01:00:00Z",
        "20260902T010000Z",
    ],
)
def test_utc_requires_exact_canonical_roundtrip(value: str) -> None:
    with pytest.raises(authority.R7S4AuthorityError, match="exact_canonical_utc_required"):
        authority._utc(value, "review_time")
    assert authority._utc("2026-09-02T01:00:00Z", "review_time") == datetime(
        2026, 9, 2, 1, 0, tzinfo=UTC
    )


def test_caller_sha_mapping_and_mutated_typed_result_are_not_authority() -> None:
    public_parameters = inspect.signature(authority.verify_external_receipt).parameters
    assert "expected_receipt_sha256" not in public_parameters
    assert "validation_time" not in public_parameters
    assert (
        "validation_time"
        not in inspect.signature(authority.revalidate_external_receipt_for_consumption).parameters
    )
    receipt_raw, request_raw, verified = _verified()
    with pytest.raises(TypeError):
        authority.verify_external_receipt(
            receipt_raw,
            request_raw,
            expected=EXPECTED,
            verifier=FakeExternalVerifier(),
            expected_receipt_sha256=hashlib.sha256(receipt_raw).hexdigest(),
        )
    with pytest.raises(TypeError):
        authority.verify_external_receipt(
            receipt_raw,
            request_raw,
            expected=EXPECTED,
            verifier=FakeExternalVerifier(),
            validation_time=NOW,
        )
    with pytest.raises(authority.R7S4AuthorityError, match="typed_external"):
        authority.require_verified_external_receipt(asdict(verified))
    forged = replace(verified, approval_id="forged-approval-id")
    with pytest.raises(authority.R7S4AuthorityError, match="integrity_mismatch"):
        authority.require_verified_external_receipt(forged)


@pytest.mark.parametrize("mechanism", ["reviewer_text", "jira", "notion", "local_self_sign"])
def test_local_text_or_self_sign_never_reaches_external_verifier(
    mechanism: str,
) -> None:
    receipt_raw, request_raw = _documents(mechanism=mechanism)
    verifier = FakeExternalVerifier()
    with pytest.raises(authority.R7S4AuthorityError, match="mechanism_not_independent"):
        authority._verify_external_receipt_for_test(
            receipt_raw,
            request_raw,
            expected=EXPECTED,
            verifier=verifier,
            validation_time=NOW,
        )
    assert verifier.calls == 0


def test_self_consistent_mutation_after_validation_is_rejected_before_publication() -> None:
    receipt_raw, request_raw, verified = _verified()
    request = authority.strict_canonical_json_bytes(request_raw, "request")
    receipt = authority.strict_canonical_json_bytes(receipt_raw, "receipt")
    request["subject"]["canonical_revision"]["tree"] = "9" * 40
    mutated_request_raw = authority.canonical_json_bytes(request)
    receipt["subject"] = request["subject"]
    receipt["approval_request"] = {
        "sha256": hashlib.sha256(mutated_request_raw).hexdigest(),
        "bytes": len(mutated_request_raw),
    }
    mutated_receipt_raw = authority.canonical_json_bytes(receipt)
    publisher = MemoryPublisher()
    with pytest.raises(authority.R7S4AuthorityError, match="consumption_rebind_mismatch"):
        store._consume_external_receipt_once_for_test(
            mutated_receipt_raw,
            mutated_request_raw,
            verified,
            expected=EXPECTED,
            validation_time=NOW,
            publisher=publisher,
        )
    assert publisher.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("domain", "windows"),
        ("domain_run_id", f"{GLOBAL_RUN_ID}-windows"),
        ("run_uuid", "00000000-0000-4000-8000-000000000001"),
        ("attempt_uuid", "00000000-0000-4000-8000-000000000002"),
    ],
)
def test_domain_or_run_identity_swap_is_rejected_before_external_verifier(
    field: str, value: str
) -> None:
    receipt_raw, request_raw = _documents()
    request = authority.strict_canonical_json_bytes(request_raw, "request")
    receipt = authority.strict_canonical_json_bytes(receipt_raw, "receipt")
    request["subject"]["run_identity"][field] = value
    mutated_request_raw = authority.canonical_json_bytes(request)
    receipt["subject"] = request["subject"]
    receipt["run_identity"] = request["subject"]["run_identity"]
    receipt["approval_request"] = {
        "sha256": hashlib.sha256(mutated_request_raw).hexdigest(),
        "bytes": len(mutated_request_raw),
    }
    verifier = FakeExternalVerifier()
    with pytest.raises(authority.R7S4AuthorityError, match="exact_binding_mismatch"):
        authority._verify_external_receipt_for_test(
            authority.canonical_json_bytes(receipt),
            mutated_request_raw,
            expected=EXPECTED,
            verifier=verifier,
            validation_time=NOW,
        )
    assert verifier.calls == 0


def test_expiry_at_consumption_rejects_before_publication() -> None:
    receipt_raw, request_raw, verified = _verified()
    publisher = MemoryPublisher()
    with pytest.raises(authority.R7S4AuthorityError, match="expired_or_window_invalid"):
        store._consume_external_receipt_once_for_test(
            receipt_raw,
            request_raw,
            verified,
            expected=EXPECTED,
            validation_time=datetime(2026, 9, 2, 1, 21, tzinfo=UTC),
            publisher=publisher,
        )
    assert publisher.calls == []


def test_marker_binds_every_identity_and_uses_only_canonical_root() -> None:
    receipt_raw, request_raw, verified = _verified()
    publisher = MemoryPublisher()
    result = store._consume_external_receipt_once_for_test(
        receipt_raw,
        request_raw,
        verified,
        expected=EXPECTED,
        validation_time=NOW,
        publisher=publisher,
    )
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["directory"] == store.CANONICAL_RECEIPT_CONSUMPTION_ROOT
    assert set(inspect.signature(store.consume_external_receipt_once).parameters) == {
        "receipt_raw",
        "approval_request_raw",
        "verified",
        "expected",
    }
    with pytest.raises(TypeError):
        store.consume_external_receipt_once(
            receipt_raw,
            request_raw,
            verified,
            expected=EXPECTED,
            directory=r"C:\alternate-root",
        )
    record = authority.strict_canonical_json_bytes(result.raw, "consumption")
    identity = record["execution_identity"]
    assert identity == {
        "global_run_id": EXPECTED.global_run_id,
        "domain_run_id": EXPECTED.domain_run_id,
        "domain": EXPECTED.domain,
        "run_uuid": EXPECTED.run_uuid,
        "attempt_uuid": EXPECTED.attempt_uuid,
        "execution_mode": EXPECTED.execution_mode,
    }
    assert record["request_binding"] == {
        "approval_request_sha256": verified.approval_request_sha256,
        "approval_request_id": verified.approval_request_id,
        "subject_sha256": verified.subject_sha256,
    }
    assert record["approval_instance"] == {
        "receipt_sha256": verified.receipt_sha256,
        "approval_id": verified.approval_id,
        "reviewer_identity": verified.reviewer_identity,
        "authority_key_id": verified.authority_key_id,
        "verifier_identity": verified.verifier_identity,
    }
    assert result.marker_leaf == f"r7s4-{result.execution_identity_sha256}.json"
    assert result.publication["temporary_leaf"] == (
        f".{result.marker_leaf}.{EXPECTED.run_uuid}.partial"
    )
    assert result.publication["file_flush_count"] == 2
    assert result.publication["replace_if_exists"] is False
    assert result.publication["same_handle_readback"] is True
    assert result.publication["file_identity_stable_across_rename"] is True
    assert result.publication["identity"]["link_count"] == 1
    assert result.publication["power_loss_durability_proven"] is False
    assert result.publication["same_token_hostile_admin_protected"] is False
    assert result.publication["go_evidence_eligible"] is False
    assert record["production_entry_enabled"] is False
    assert record["same_token_hostile_admin_protected"] is False
    assert record["multi_host_global_one_shot_provided"] is False


@pytest.mark.parametrize(
    ("path", "value", "error"),
    [
        (("unexpected",), True, "evidence_fields_mismatch"),
        (("sha256",), "0" * 64, "sha256_mismatch"),
        (("bytes",), 0, "bytes_mismatch"),
        (("final_path",), r"C:\wrong\marker.json", "path_mismatch"),
        (("temporary_leaf",), ".wrong.partial", "temporary_leaf_mismatch"),
        (("file_flush_count",), 1, "contract_mismatch:file_flush_count"),
        (("directory_flush_count",), 0, "contract_mismatch:directory_flush_count"),
        (("directory_flush_succeeded",), False, "contract_mismatch:directory_flush_succeeded"),
        (("replace_if_exists",), True, "contract_mismatch:replace_if_exists"),
        (("same_handle_readback",), False, "contract_mismatch:same_handle_readback"),
        (
            ("file_identity_stable_across_rename",),
            False,
            "contract_mismatch:file_identity_stable_across_rename",
        ),
        (
            ("power_loss_durability_proven",),
            True,
            "contract_mismatch:power_loss_durability_proven",
        ),
        (
            ("same_token_hostile_admin_protected",),
            True,
            "contract_mismatch:same_token_hostile_admin_protected",
        ),
        (("go_evidence_eligible",), True, "contract_mismatch:go_evidence_eligible"),
        (("identity", "final_path"), r"C:\wrong\file", "file_identity_path_mismatch"),
        (("identity", "unexpected"), True, "file_identity_fields_mismatch"),
        (
            ("identity", "volume_serial_number"),
            0,
            "file_identity_volume_serial_number_invalid",
        ),
        (("identity", "volume_serial_number"), 43, "file_directory_identity_mismatch"),
        (("identity", "file_id_hex"), "z" * 32, "file_identity_file_id_invalid"),
        (("identity", "size"), 0, "file_identity_invariant_mismatch"),
        (("identity", "link_count"), 2, "file_identity_invariant_mismatch"),
        (("identity", "attributes"), 0x10, "file_identity_invariant_mismatch"),
        (("identity", "reparse_tag"), 1, "file_identity_invariant_mismatch"),
        (("identity", "file_type"), 2, "file_identity_file_type_invalid"),
        (("identity", "owner_sid"), "", "file_identity_owner_sid_invalid"),
        (
            ("identity", "security_descriptor_sha256"),
            "z" * 64,
            "file_identity_security_descriptor_invalid",
        ),
        (("identity", "dacl_present"), False, "file_identity_dacl_not_present_and_protected"),
        (
            ("identity", "dacl_protected"),
            False,
            "file_identity_dacl_not_present_and_protected",
        ),
        (
            ("directory_identity", "final_path"),
            r"C:\wrong\directory",
            "directory_identity_path_mismatch",
        ),
        (
            ("directory_identity", "volume_serial_number"),
            0,
            "directory_identity_volume_serial_number_invalid",
        ),
        (
            ("directory_identity", "volume_serial_number"),
            43,
            "file_directory_identity_mismatch",
        ),
        (
            ("directory_identity", "file_id_hex"),
            "z" * 32,
            "directory_identity_file_id_invalid",
        ),
        (("directory_identity", "link_count"), 0, "directory_identity_invariant_mismatch"),
        (("directory_identity", "attributes"), 0, "directory_identity_invariant_mismatch"),
        (("directory_identity", "reparse_tag"), 1, "directory_identity_invariant_mismatch"),
        (("directory_identity", "file_type"), 2, "directory_identity_file_type_invalid"),
        (("directory_identity", "owner_sid"), "", "directory_identity_owner_sid_invalid"),
        (
            ("directory_identity", "security_descriptor_sha256"),
            "z" * 64,
            "directory_identity_security_descriptor_invalid",
        ),
        (
            ("directory_identity", "dacl_present"),
            False,
            "directory_identity_dacl_not_present_and_protected",
        ),
        (
            ("directory_identity", "dacl_protected"),
            False,
            "directory_identity_dacl_not_present_and_protected",
        ),
    ],
)
def test_publication_contract_mutation_or_overclaim_is_fail_closed(
    path: tuple[str, ...], value: object, error: str
) -> None:
    receipt_raw, request_raw, verified = _verified()
    publisher = MemoryPublisher(mutation=_publication_mutation(path, value))
    with pytest.raises(store.ReceiptPublicationAmbiguousError) as raised:
        store._consume_external_receipt_once_for_test(
            receipt_raw,
            request_raw,
            verified,
            expected=EXPECTED,
            validation_time=NOW,
            publisher=publisher,
        )
    assert isinstance(raised.value.__cause__, store.R7S4ReceiptStoreError)
    assert error in str(raised.value.__cause__)
    assert raised.value.automatic_retry_allowed is False
    assert raised.value.downstream_calls_allowed is False


def test_file_and_directory_zero_volume_serials_are_rejected_together() -> None:
    receipt_raw, request_raw, verified = _verified()

    def zero_both_volume_serials(publication: dict[str, object]) -> None:
        identity = publication["identity"]
        directory_identity = publication["directory_identity"]
        assert isinstance(identity, dict)
        assert isinstance(directory_identity, dict)
        identity["volume_serial_number"] = 0
        directory_identity["volume_serial_number"] = 0

    publisher = MemoryPublisher(mutation=zero_both_volume_serials)
    with pytest.raises(store.ReceiptPublicationAmbiguousError) as raised:
        store._consume_external_receipt_once_for_test(
            receipt_raw,
            request_raw,
            verified,
            expected=EXPECTED,
            validation_time=NOW,
            publisher=publisher,
        )

    assert isinstance(raised.value.__cause__, store.R7S4ReceiptStoreError)
    assert "file_identity_volume_serial_number_invalid" in str(raised.value.__cause__)
    assert raised.value.automatic_retry_allowed is False
    assert raised.value.downstream_calls_allowed is False
    assert raised.value.downstream_call_counts == store.ZERO_DOWNSTREAM_CALLS


def test_partial_or_ambiguous_publication_blocks_retry_and_downstream() -> None:
    receipt_raw, request_raw, verified = _verified()
    publisher = MemoryPublisher(fail_after_commit=True)
    with pytest.raises(store.ReceiptPublicationAmbiguousError) as raised:
        store._consume_external_receipt_once_for_test(
            receipt_raw,
            request_raw,
            verified,
            expected=EXPECTED,
            validation_time=NOW,
            publisher=publisher,
        )
    assert raised.value.manual_intervention_required is True
    assert raised.value.automatic_retry_allowed is False
    assert raised.value.downstream_calls_allowed is False
    assert raised.value.downstream_call_counts == store.ZERO_DOWNSTREAM_CALLS
    assert len(publisher.calls) == 1
    assert len(publisher.markers) == 1


def test_reissued_receipt_for_same_exact_candidate_collides_with_stable_marker() -> None:
    receipt_one, request_one, verified_one = _verified(approval_id="external-approval-001")
    receipt_two, request_two, verified_two = _verified(approval_id="external-approval-002")
    assert request_one == request_two
    assert verified_one.approval_request_sha256 == verified_two.approval_request_sha256
    assert verified_one.subject_sha256 == verified_two.subject_sha256
    assert verified_one.receipt_sha256 != verified_two.receipt_sha256
    assert verified_one.approval_id != verified_two.approval_id
    publisher = MemoryPublisher()
    first = store._consume_external_receipt_once_for_test(
        receipt_one,
        request_one,
        verified_one,
        expected=EXPECTED,
        validation_time=NOW,
        publisher=publisher,
    )
    with pytest.raises(store.ReceiptPublicationAmbiguousError):
        store._consume_external_receipt_once_for_test(
            receipt_two,
            request_two,
            verified_two,
            expected=EXPECTED,
            validation_time=NOW,
            publisher=publisher,
        )
    assert len(publisher.calls) == 2
    assert publisher.calls[0]["final_leaf"] == publisher.calls[1]["final_leaf"]
    assert publisher.calls[0]["final_leaf"] == first.marker_leaf
    assert len(publisher.markers) == 1


def test_changed_request_and_subject_for_same_execution_identity_still_collide() -> None:
    changed_request = replace(
        EXPECTED,
        approval_request_id="approval-request-r7s4-reissued",
    )
    receipt_one, request_one, verified_one = _verified(approval_id="external-approval-001")
    receipt_two, request_two, verified_two = _verified(
        expected=changed_request,
        approval_id="external-approval-002",
        bootstrap_pin_character="9",
    )
    assert request_one != request_two
    assert verified_one.approval_request_sha256 != verified_two.approval_request_sha256
    assert verified_one.approval_request_id != verified_two.approval_request_id
    assert verified_one.subject_sha256 != verified_two.subject_sha256
    assert verified_one.receipt_sha256 != verified_two.receipt_sha256
    publisher = MemoryPublisher()
    first = store._consume_external_receipt_once_for_test(
        receipt_one,
        request_one,
        verified_one,
        expected=EXPECTED,
        validation_time=NOW,
        publisher=publisher,
    )

    with pytest.raises(store.ReceiptPublicationAmbiguousError):
        store._consume_external_receipt_once_for_test(
            receipt_two,
            request_two,
            verified_two,
            expected=changed_request,
            validation_time=NOW,
            publisher=publisher,
        )

    assert publisher.calls[0]["final_leaf"] == publisher.calls[1]["final_leaf"]
    assert publisher.calls[0]["final_leaf"] == first.marker_leaf
    assert len(publisher.markers) == 1


def test_different_exact_candidate_has_a_distinct_stable_marker() -> None:
    other_global_run_id = "pre-r8-r7s4-20260902T010000Z-deadbeef"
    other = authority.ReceiptExpectation(
        approval_request_id="approval-request-r7s4-002",
        global_run_id=other_global_run_id,
        domain_run_id=f"{other_global_run_id}-wsl",
        domain="wsl",
        run_uuid="00000000-0000-4000-8000-000000000001",
        attempt_uuid="00000000-0000-4000-8000-000000000002",
        execution_mode=EXPECTED.execution_mode,
    )
    receipt_one, request_one, verified_one = _verified()
    receipt_two, request_two, verified_two = _verified(
        expected=other,
        approval_id="external-approval-002",
    )
    publisher = MemoryPublisher()
    first = store._consume_external_receipt_once_for_test(
        receipt_one,
        request_one,
        verified_one,
        expected=EXPECTED,
        validation_time=NOW,
        publisher=publisher,
    )
    second = store._consume_external_receipt_once_for_test(
        receipt_two,
        request_two,
        verified_two,
        expected=other,
        validation_time=NOW,
        publisher=publisher,
    )
    assert first.execution_identity_sha256 != second.execution_identity_sha256
    assert first.marker_leaf != second.marker_leaf
    assert len(publisher.markers) == 2


def test_concurrent_or_reentrant_consumption_has_one_winner_and_no_retry() -> None:
    receipt_raw, request_raw, verified = _verified()
    publisher = MemoryPublisher()

    def consume() -> str:
        try:
            store._consume_external_receipt_once_for_test(
                receipt_raw,
                request_raw,
                verified,
                expected=EXPECTED,
                validation_time=NOW,
                publisher=publisher,
            )
            return "consumed"
        except store.ReceiptPublicationAmbiguousError:
            return "manual_intervention_required"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: consume(), range(2)))
    assert sorted(outcomes) == ["consumed", "manual_intervention_required"]
    assert consume() == "manual_intervention_required"
    assert len(publisher.markers) == 1
    assert store.receipt_store_contract()["caller_selectable_root"] is False
    assert store.receipt_store_contract()["automatic_retry_allowed"] is False
    assert (
        store.receipt_store_contract()["file_and_directory_volume_serial_positive_required"] is True
    )
    assert store.receipt_store_contract()["public_caller_validation_time_allowed"] is False
    assert (
        store.receipt_store_contract()[
            "marker_collision_key_excludes_request_subject_receipt_approval"
        ]
        is True
    )
    assert store.receipt_store_contract()["marker_collision_key_scope"] == [
        "global_run_id",
        "domain_run_id",
        "domain",
        "run_uuid",
        "attempt_uuid",
        "execution_mode",
    ]
    assert (
        store.receipt_store_contract()[
            "same_execution_identity_reissued_request_or_receipt_collides"
        ]
        is True
    )


def test_root_gate_rejects_missing_mapping_and_verified_test_capability_before_all_calls() -> None:
    counters = {name: 0 for name in ("repo", "spawn", "service", "wsl", "r8")}

    def hit(name: str) -> None:
        counters[name] += 1

    hooks = root_gate.RootObservationHooks(
        repo_read=lambda: hit("repo"),
        process_spawn=lambda: hit("spawn"),
        service_call=lambda: hit("service"),
        live_wsl=lambda: hit("wsl"),
        r8=lambda: hit("r8"),
    )
    with pytest.raises(root_gate.R7S4RootGateError, match="capability_required"):
        root_gate.enter_production_once(None, observation_hooks=hooks)
    with pytest.raises(root_gate.R7S4RootGateError, match="capability_unanchored"):
        root_gate.enter_production_once({}, observation_hooks=hooks)
    _receipt_raw, _request_raw, verified = _verified()
    with pytest.raises(root_gate.R7S4RootGateError, match="authority_unavailable"):
        root_gate.enter_production_once(verified, observation_hooks=hooks)
    assert counters == {name: 0 for name in counters}
    assert root_gate.root_gate_contract()["call_counts"] == root_gate.ZERO_CALL_COUNTS
    assert root_gate.PRODUCTION_ENTRY_ENABLED is False
    assert root_gate.PRODUCTION_WIRING_IMPLEMENTED is False
    assert authority.authority_contract()["production_external_authority_configured"] is False
    authority_boundary = authority.authority_contract()
    assert authority_boundary["caller_validation_time_allowed"] is False
    assert authority_boundary["verifier_identity_allowlist_implemented"] is False
    assert authority_boundary["authority_key_allowlist_implemented"] is False
    assert authority_boundary["independent_external_trust_root_available"] is False
    assert authority_boundary["production_authority_blockers"]
    root_boundary = root_gate.root_gate_contract()
    assert root_boundary["required_future_production_admission_sequence"] == [
        "consumption_time_revalidate_raw_receipt_a0_and_expectation",
        "fixed_canonical_receipt_store_one_shot",
        "process_admission_only_after_one_shot_readback",
    ]
    assert root_boundary["required_production_sequence_enforced"] is False
    assert root_boundary["local_flag_flip_can_enable_execution"] is False
    assert root_boundary["production_blockers"]


def test_even_local_flag_drift_has_no_production_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counters = {name: 0 for name in ("repo", "spawn", "service", "wsl", "r8")}

    def hit(name: str) -> None:
        counters[name] += 1

    hooks = root_gate.RootObservationHooks(
        repo_read=lambda: hit("repo"),
        process_spawn=lambda: hit("spawn"),
        service_call=lambda: hit("service"),
        live_wsl=lambda: hit("wsl"),
        r8=lambda: hit("r8"),
    )
    for flag in (
        "PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED",
        "PRODUCTION_WIRING_IMPLEMENTED",
        "PRODUCTION_ENTRY_ENABLED",
    ):
        monkeypatch.setattr(root_gate, flag, True)
    _receipt_raw, _request_raw, verified = _verified()
    with pytest.raises(root_gate.R7S4RootGateError, match="execution_intentionally_absent"):
        root_gate.enter_production_once(verified, observation_hooks=hooks)
    assert counters == {name: 0 for name in counters}
