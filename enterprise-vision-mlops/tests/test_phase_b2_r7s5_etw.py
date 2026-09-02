from __future__ import annotations

import copy
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r7s5_etw as etw


RUN_UUID = "11111111-1111-4111-8111-111111111111"
ATTEMPT_UUID = "22222222-2222-4222-8222-222222222222"
CAPTURE_ID = "33333333-3333-4333-8333-333333333333"
MANIFEST_ID = "44444444-4444-4444-8444-444444444444"
PROVIDER_A = "12345678-1234-4234-9234-1234567890ab"
# A Windows GUID need not encode RFC 4122 version/variant bits.
PROVIDER_B = "abcdefab-cdef-0abc-0def-abcdefabcdef"


def _manifest() -> dict[str, Any]:
    return {
        "schema": etw.MANIFEST_SCHEMA,
        "manifest_id": MANIFEST_ID,
        "status": "approved",
        "approval_id": "external-etw-approval-001",
        "reviewer_identity": "independent-reviewer-key-01",
        "collector": {
            "sha256": "a" * 64,
            "bytes": 8192,
            "requires_run_as_administrator": True,
        },
        "providers": [
            {
                "name": "authority-supplied-provider-a",
                "guid": PROVIDER_A,
                "level": 5,
                "keywords": "0x0000000000000001",
                "events": [{"event_id": 1, "version": 0}, {"event_id": 2, "version": 1}],
            },
            {
                "name": "authority-supplied-provider-b",
                "guid": PROVIDER_B,
                "level": 4,
                "keywords": "0x0000000000000002",
                "events": [{"event_id": 7, "version": 3}],
            },
        ],
        "service_configuration_changes_authorized": False,
    }


def _not_run() -> dict[str, Any]:
    return {
        "schema": etw.NOT_RUN_SCHEMA,
        "status": "not_run",
        "decision": "NO-GO",
        "reason": "approved_external_manifest_required",
        "approved_external_manifest_present": False,
        "collector_started": False,
        "administrator_session": False,
        "archive_created": False,
        "service_configuration_changed": False,
        "acceptance_credit": False,
        "completion_credit": "non_credit_only",
        "go": False,
        "completion_marker_created": False,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "call_counts": dict(etw.ZERO_ETW_CALLS),
    }


def _process(pid: int, created: int, image_sha: str) -> dict[str, Any]:
    return {
        "identity_id": etw.process_identity_id(
            pid=pid,
            creation_time_ns=created,
            image_sha256=image_sha,
        ),
        "pid": pid,
        "creation_time_ns": created,
        "image_sha256": image_sha,
    }


def _capture() -> dict[str, Any]:
    manifest = _manifest()
    first = _process(4040, 10_000, "b" * 64)
    reused = _process(4040, 20_000, "c" * 64)
    return {
        "schema": etw.CAPTURE_SCHEMA,
        "run_uuid": RUN_UUID,
        "attempt_uuid": ATTEMPT_UUID,
        "capture_id": CAPTURE_ID,
        "status": "captured_non_credit",
        "decision": "qualified_non_credit",
        "source_kind": "live_etw",
        "synthetic": False,
        "replayed": False,
        "acceptance_credit": False,
        "completion_credit": "non_credit_only",
        "go": False,
        "completion_marker_created": False,
        "approved_manifest_sha256": etw.approved_manifest_sha256(manifest),
        "collector_sha256": manifest["collector"]["sha256"],
        "administrator_session": True,
        "service_configuration_changed": False,
        "service_mutation_calls": 0,
        "session_count": 1,
        "session_name": "approved-r7s5-etw-session",
        "session_started": True,
        "session_stopped": True,
        "lost_events": 0,
        "lost_buffers": 0,
        "providers": copy.deepcopy(manifest["providers"]),
        "processes": [first, reused],
        "events": [
            {
                "sequence": 0,
                "provider_guid": PROVIDER_A,
                "event_id": 1,
                "version": 0,
                "timestamp_qpc": 100,
                "process_identity_id": first["identity_id"],
                "pid": first["pid"],
                "process_creation_time_ns": first["creation_time_ns"],
            },
            {
                "sequence": 1,
                "provider_guid": PROVIDER_A,
                "event_id": 2,
                "version": 1,
                "timestamp_qpc": 110,
                "process_identity_id": first["identity_id"],
                "pid": first["pid"],
                "process_creation_time_ns": first["creation_time_ns"],
            },
            {
                "sequence": 2,
                "provider_guid": PROVIDER_B,
                "event_id": 7,
                "version": 3,
                "timestamp_qpc": 120,
                "process_identity_id": reused["identity_id"],
                "pid": reused["pid"],
                "process_creation_time_ns": reused["creation_time_ns"],
            },
        ],
        "archive": {"created": True, "sha256": "d" * 64, "bytes": 65_536},
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
    }


def test_missing_external_manifest_requires_exact_not_run_no_go() -> None:
    decision = etw.validate_etw_qualification(_not_run()).to_dict()

    assert decision["status"] == "not_run"
    assert decision["decision"] == "NO-GO"
    assert decision["qualified_non_credit"] is False
    assert decision["downstream_calls"] == etw.ZERO_ETW_CALLS
    assert decision["go"] is False
    assert decision["completion_marker_created"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("collector_started", True),
        ("administrator_session", True),
        ("archive_created", True),
        ("go", True),
        ("automatic_retry_count", True),
    ],
)
def test_not_run_record_rejects_local_execution_or_numeric_bool(field: str, value: object) -> None:
    payload = _not_run()
    payload[field] = value

    with pytest.raises(etw.R7S5EtwError):
        etw.validate_etw_qualification(payload)


def test_capture_cannot_substitute_for_missing_approved_manifest() -> None:
    with pytest.raises(etw.R7S5EtwError, match="etw_not_run_fields_mismatch"):
        etw.validate_etw_qualification(_capture())


def test_approved_capture_handles_pid_reuse_by_creation_identity() -> None:
    capture = _capture()
    decision = etw.validate_etw_qualification(
        capture,
        approved_external_manifest=_manifest(),
    ).to_dict()

    assert capture["processes"][0]["pid"] == capture["processes"][1]["pid"]
    assert capture["processes"][0]["identity_id"] != capture["processes"][1]["identity_id"]
    assert decision["status"] == "captured_non_credit"
    assert decision["qualified_non_credit"] is True
    assert decision["acceptance_credit"] is False
    assert decision["go"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "lost_event",
        "lost_buffer",
        "synthetic",
        "service_change",
        "session_count_numeric_bool",
        "provider_guid_swap",
        "event_id_swap",
        "event_version_swap",
        "pid_reuse_creation_swap",
        "identity_digest_swap",
        "missing_event_coverage",
        "event_clock_regression",
        "collector_swap",
        "manifest_hash_swap",
        "event_pid_numeric_bool",
        "event_provider_unhashable",
        "event_identity_unhashable",
    ],
)
def test_capture_mutations_fail_closed(mutation: str) -> None:
    capture = _capture()
    if mutation == "lost_event":
        capture["lost_events"] = 1
    elif mutation == "lost_buffer":
        capture["lost_buffers"] = 1
    elif mutation == "synthetic":
        capture["synthetic"] = True
    elif mutation == "service_change":
        capture["service_configuration_changed"] = True
    elif mutation == "session_count_numeric_bool":
        capture["session_count"] = True
    elif mutation == "provider_guid_swap":
        capture["providers"][0]["guid"] = PROVIDER_B
    elif mutation == "event_id_swap":
        capture["events"][0]["event_id"] = 99
    elif mutation == "event_version_swap":
        capture["events"][0]["version"] = 99
    elif mutation == "pid_reuse_creation_swap":
        capture["events"][2]["process_creation_time_ns"] = capture["processes"][0][
            "creation_time_ns"
        ]
    elif mutation == "identity_digest_swap":
        capture["processes"][1]["identity_id"] = capture["processes"][0]["identity_id"]
    elif mutation == "missing_event_coverage":
        capture["events"].pop()
    elif mutation == "event_clock_regression":
        capture["events"][2]["timestamp_qpc"] = 99
    elif mutation == "collector_swap":
        capture["collector_sha256"] = "e" * 64
    elif mutation == "manifest_hash_swap":
        capture["approved_manifest_sha256"] = "f" * 64
    elif mutation == "event_pid_numeric_bool":
        replacement = _process(1, 10_000, "b" * 64)
        capture["processes"][0] = replacement
        for event in capture["events"][:2]:
            event["process_identity_id"] = replacement["identity_id"]
            event["pid"] = True
    elif mutation == "event_provider_unhashable":
        capture["events"][0]["provider_guid"] = [PROVIDER_A]
    elif mutation == "event_identity_unhashable":
        capture["events"][0]["process_identity_id"] = [capture["processes"][0]["identity_id"]]

    with pytest.raises(etw.R7S5EtwError):
        etw.validate_etw_qualification(
            capture,
            approved_external_manifest=_manifest(),
        )


def test_event_duplicate_replay_and_capture_id_replay_are_rejected() -> None:
    duplicate = _capture()
    replayed_event = copy.deepcopy(duplicate["events"][-1])
    replayed_event["sequence"] = len(duplicate["events"])
    duplicate["events"].append(replayed_event)
    with pytest.raises(etw.R7S5EtwError, match="etw_event_replay_duplicate"):
        etw.validate_etw_qualification(
            duplicate,
            approved_external_manifest=_manifest(),
        )

    with pytest.raises(etw.R7S5EtwError, match="etw_capture_replay"):
        etw.validate_etw_qualification(
            _capture(),
            approved_external_manifest=_manifest(),
            seen_capture_ids=(CAPTURE_ID,),
        )


def test_manifest_is_external_strict_and_does_not_use_guessed_provider_ids() -> None:
    manifest = etw.validate_approved_manifest(_manifest())
    contract = etw.etw_contract()

    assert manifest["providers"][0]["guid"] == PROVIDER_A
    assert contract["built_in_provider_guids"] == []
    assert contract["built_in_event_ids"] == []
    assert contract["approved_external_manifest_required"] is True
    assert contract["live_calls_implemented"] is False
    assert contract["external_manifest_authenticity_verified_by_this_module"] is False
    assert not hasattr(etw, "subprocess")


@pytest.mark.parametrize("mutation", ["duplicate_event", "duplicate_provider", "admin_false"])
def test_approved_manifest_mutations_are_rejected(mutation: str) -> None:
    manifest = _manifest()
    if mutation == "duplicate_event":
        manifest["providers"][0]["events"].append(
            copy.deepcopy(manifest["providers"][0]["events"][0])
        )
    elif mutation == "duplicate_provider":
        manifest["providers"][1]["guid"] = manifest["providers"][0]["guid"]
    elif mutation == "admin_false":
        manifest["collector"]["requires_run_as_administrator"] = False

    with pytest.raises(etw.R7S5EtwError):
        etw.validate_approved_manifest(manifest)
