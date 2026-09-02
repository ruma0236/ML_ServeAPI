"""Strict offline ETW contract validation for pre-r8 r7s5.

Provider GUIDs and event identifiers are authority supplied.  This module has
no built-in provider catalogue and never starts an ETW session.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


NOT_RUN_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.etw-not-run.v1"
MANIFEST_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.etw-approved-manifest.v1"
CAPTURE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.etw-capture.v1"
HEX64_RE = re.compile(r"[0-9a-f]{64}")
# ETW provider identifiers are Windows GUIDs, not necessarily RFC 4122 UUIDs.
# Only their canonical text shape is fixed here; no provider values or UUID
# version/variant bits are guessed by this offline validator.
GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
KEYWORDS_RE = re.compile(r"0x[0-9a-f]{16}")

ZERO_ETW_CALLS = {
    "etw_sessions": 0,
    "process_spawn": 0,
    "service_mutations": 0,
    "automatic_retry": 0,
    "force_kill": 0,
    "completion_markers": 0,
}


class R7S5EtwError(ValueError):
    """Raised when an ETW manifest or capture is not exact and replay-safe."""


def _canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise R7S5EtwError("canonical_json_value_rejected") from exc
    return (encoded + "\n").encode("ascii")


def approved_manifest_sha256(value: object) -> str:
    manifest = validate_approved_manifest(value)
    return hashlib.sha256(_canonical_json_bytes(manifest)).hexdigest()


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R7S5EtwError(f"{label}_object_required")
    return dict(value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise R7S5EtwError(f"{label}_fields_mismatch")


def _strict_int(value: object, label: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise R7S5EtwError(f"{label}_integer_invalid")
    return value


def _strict_bool(value: object, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise R7S5EtwError(f"{label}_must_be_{str(expected).lower()}")


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise R7S5EtwError(f"{label}_nonempty_string_required")
    return value


def _uuid4(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise R7S5EtwError(f"{label}_uuid4_invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise R7S5EtwError(f"{label}_uuid4_invalid") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise R7S5EtwError(f"{label}_uuid4_not_canonical")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        raise R7S5EtwError(f"{label}_sha256_invalid")
    return value


def _guid(value: object, label: str) -> str:
    if not isinstance(value, str) or GUID_RE.fullmatch(value) is None:
        raise R7S5EtwError(f"{label}_guid_invalid")
    return value


def _event_spec(value: object, label: str) -> dict[str, int]:
    event = _mapping(value, label)
    _exact_keys(event, {"event_id", "version"}, label)
    return {
        "event_id": _strict_int(event["event_id"], f"{label}_id", maximum=65_535),
        "version": _strict_int(event["version"], f"{label}_version", maximum=255),
    }


def _provider(value: object, label: str) -> dict[str, Any]:
    provider = _mapping(value, label)
    _exact_keys(provider, {"name", "guid", "level", "keywords", "events"}, label)
    name = _nonempty(provider["name"], f"{label}_name")
    guid = _guid(provider["guid"], label)
    keywords = provider["keywords"]
    if not isinstance(keywords, str) or KEYWORDS_RE.fullmatch(keywords) is None:
        raise R7S5EtwError(f"{label}_keywords_invalid")
    events_raw = provider["events"]
    if not isinstance(events_raw, list) or not events_raw:
        raise R7S5EtwError(f"{label}_events_required")
    events = [_event_spec(item, f"{label}_event_{index}") for index, item in enumerate(events_raw)]
    identities = [(item["event_id"], item["version"]) for item in events]
    if len(identities) != len(set(identities)):
        raise R7S5EtwError(f"{label}_event_duplicate")
    return {
        "name": name,
        "guid": guid,
        "level": _strict_int(provider["level"], f"{label}_level", maximum=255),
        "keywords": keywords,
        "events": events,
    }


def validate_approved_manifest(value: object) -> dict[str, Any]:
    manifest = _mapping(value, "approved_manifest")
    _exact_keys(
        manifest,
        {
            "schema",
            "manifest_id",
            "status",
            "approval_id",
            "reviewer_identity",
            "collector",
            "providers",
            "service_configuration_changes_authorized",
        },
        "approved_manifest",
    )
    if manifest["schema"] != MANIFEST_SCHEMA or manifest["status"] != "approved":
        raise R7S5EtwError("approved_manifest_schema_or_status_mismatch")
    manifest_id = _uuid4(manifest["manifest_id"], "manifest")
    approval_id = _nonempty(manifest["approval_id"], "approval_id")
    reviewer = _nonempty(manifest["reviewer_identity"], "reviewer_identity")
    collector = _mapping(manifest["collector"], "collector")
    _exact_keys(
        collector,
        {"sha256", "bytes", "requires_run_as_administrator"},
        "collector",
    )
    normalized_collector = {
        "sha256": _sha256(collector["sha256"], "collector"),
        "bytes": _strict_int(collector["bytes"], "collector_bytes", minimum=1),
        "requires_run_as_administrator": True,
    }
    _strict_bool(collector["requires_run_as_administrator"], True, "requires_run_as_administrator")
    providers_raw = manifest["providers"]
    if not isinstance(providers_raw, list) or not providers_raw:
        raise R7S5EtwError("approved_manifest_providers_required")
    providers = [_provider(item, f"provider_{index}") for index, item in enumerate(providers_raw)]
    guids = [item["guid"] for item in providers]
    if len(guids) != len(set(guids)):
        raise R7S5EtwError("approved_manifest_provider_guid_duplicate")
    _strict_bool(
        manifest["service_configuration_changes_authorized"],
        False,
        "service_configuration_changes_authorized",
    )
    return {
        "schema": MANIFEST_SCHEMA,
        "manifest_id": manifest_id,
        "status": "approved",
        "approval_id": approval_id,
        "reviewer_identity": reviewer,
        "collector": normalized_collector,
        "providers": providers,
        "service_configuration_changes_authorized": False,
    }


@dataclass(frozen=True, slots=True)
class EtwDecision:
    status: str
    decision: str
    qualified_non_credit: bool
    capture_id: str | None
    approved_manifest_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "decision": self.decision,
            "qualified_non_credit": self.qualified_non_credit,
            "capture_id": self.capture_id,
            "approved_manifest_sha256": self.approved_manifest_sha256,
            "acceptance_credit": False,
            "completion_credit": "non_credit_only",
            "go": False,
            "completion_marker_created": False,
            "automatic_retry_allowed": False,
            "downstream_calls": dict(ZERO_ETW_CALLS),
        }


_NOT_RUN_FIELDS = {
    "schema",
    "status",
    "decision",
    "reason",
    "approved_external_manifest_present",
    "collector_started",
    "administrator_session",
    "archive_created",
    "service_configuration_changed",
    "acceptance_credit",
    "completion_credit",
    "go",
    "completion_marker_created",
    "automatic_retry_count",
    "forced_termination_attempts",
    "call_counts",
}


def _validate_not_run(value: object) -> EtwDecision:
    raw = _mapping(value, "etw_not_run")
    _exact_keys(raw, _NOT_RUN_FIELDS, "etw_not_run")
    exact_values = {
        "schema": NOT_RUN_SCHEMA,
        "status": "not_run",
        "decision": "NO-GO",
        "reason": "approved_external_manifest_required",
        "completion_credit": "non_credit_only",
    }
    if any(raw[name] != expected for name, expected in exact_values.items()):
        raise R7S5EtwError("etw_not_run_semantics_mismatch")
    for name in (
        "approved_external_manifest_present",
        "collector_started",
        "administrator_session",
        "archive_created",
        "service_configuration_changed",
        "acceptance_credit",
        "go",
        "completion_marker_created",
    ):
        _strict_bool(raw[name], False, f"etw_not_run_{name}")
    _strict_int(raw["automatic_retry_count"], "automatic_retry_count")
    _strict_int(raw["forced_termination_attempts"], "forced_termination_attempts")
    if raw["automatic_retry_count"] != 0 or raw["forced_termination_attempts"] != 0:
        raise R7S5EtwError("etw_not_run_retry_or_termination_forbidden")
    if raw["call_counts"] != ZERO_ETW_CALLS:
        raise R7S5EtwError("etw_not_run_call_counts_nonzero")
    return EtwDecision(
        status="not_run",
        decision="NO-GO",
        qualified_non_credit=False,
        capture_id=None,
        approved_manifest_sha256=None,
    )


def process_identity_id(*, pid: int, creation_time_ns: int, image_sha256: str) -> str:
    identity = {
        "creation_time_ns": _strict_int(creation_time_ns, "creation_time_ns", minimum=1),
        "image_sha256": _sha256(image_sha256, "image"),
        "pid": _strict_int(pid, "pid", minimum=1),
    }
    return hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()


_CAPTURE_FIELDS = {
    "schema",
    "run_uuid",
    "attempt_uuid",
    "capture_id",
    "status",
    "decision",
    "source_kind",
    "synthetic",
    "replayed",
    "acceptance_credit",
    "completion_credit",
    "go",
    "completion_marker_created",
    "approved_manifest_sha256",
    "collector_sha256",
    "administrator_session",
    "service_configuration_changed",
    "service_mutation_calls",
    "session_count",
    "session_name",
    "session_started",
    "session_stopped",
    "lost_events",
    "lost_buffers",
    "providers",
    "processes",
    "events",
    "archive",
    "automatic_retry_count",
    "forced_termination_attempts",
}


def _validate_capture(
    value: object,
    *,
    manifest: dict[str, Any],
    manifest_sha: str,
    seen_capture_ids: Sequence[str],
) -> EtwDecision:
    raw = _mapping(value, "etw_capture")
    _exact_keys(raw, _CAPTURE_FIELDS, "etw_capture")
    if (
        raw["schema"] != CAPTURE_SCHEMA
        or raw["status"] != "captured_non_credit"
        or raw["decision"] != "qualified_non_credit"
        or raw["source_kind"] != "live_etw"
    ):
        raise R7S5EtwError("etw_capture_schema_status_or_source_mismatch")
    _uuid4(raw["run_uuid"], "run")
    _uuid4(raw["attempt_uuid"], "attempt")
    capture_id = _uuid4(raw["capture_id"], "capture")
    if capture_id in seen_capture_ids:
        raise R7S5EtwError("etw_capture_replay")
    for name, expected in (
        ("synthetic", False),
        ("replayed", False),
        ("acceptance_credit", False),
        ("go", False),
        ("completion_marker_created", False),
        ("administrator_session", True),
        ("service_configuration_changed", False),
        ("session_started", True),
        ("session_stopped", True),
    ):
        _strict_bool(raw[name], expected, name)
    if raw["completion_credit"] != "non_credit_only":
        raise R7S5EtwError("etw_capture_credit_mismatch")
    if raw["approved_manifest_sha256"] != manifest_sha:
        raise R7S5EtwError("etw_capture_manifest_binding_mismatch")
    if raw["collector_sha256"] != manifest["collector"]["sha256"]:
        raise R7S5EtwError("etw_capture_collector_binding_mismatch")
    for name, expected in (
        ("service_mutation_calls", 0),
        ("session_count", 1),
        ("lost_events", 0),
        ("lost_buffers", 0),
        ("automatic_retry_count", 0),
        ("forced_termination_attempts", 0),
    ):
        observed = _strict_int(raw[name], name)
        if observed != expected:
            raise R7S5EtwError(f"etw_capture_{name}_mismatch")
    _nonempty(raw["session_name"], "session_name")

    providers_raw = raw["providers"]
    if not isinstance(providers_raw, list):
        raise R7S5EtwError("etw_capture_providers_list_required")
    providers = [
        _provider(item, f"capture_provider_{index}") for index, item in enumerate(providers_raw)
    ]
    if providers != manifest["providers"]:
        raise R7S5EtwError("etw_capture_provider_contract_mismatch")
    allowed_events = {
        (provider["guid"], event["event_id"], event["version"])
        for provider in providers
        for event in provider["events"]
    }

    processes_raw = raw["processes"]
    if not isinstance(processes_raw, list) or not processes_raw:
        raise R7S5EtwError("etw_capture_processes_required")
    processes: dict[str, tuple[int, int, str]] = {}
    process_keys: set[tuple[int, int]] = set()
    for index, value_process in enumerate(processes_raw):
        process = _mapping(value_process, f"process_{index}")
        _exact_keys(
            process,
            {"identity_id", "pid", "creation_time_ns", "image_sha256"},
            f"process_{index}",
        )
        pid = _strict_int(process["pid"], f"process_{index}_pid", minimum=1)
        created = _strict_int(
            process["creation_time_ns"], f"process_{index}_creation_time_ns", minimum=1
        )
        image_sha = _sha256(process["image_sha256"], f"process_{index}_image")
        expected_identity = process_identity_id(
            pid=pid,
            creation_time_ns=created,
            image_sha256=image_sha,
        )
        if process["identity_id"] != expected_identity:
            raise R7S5EtwError("etw_process_identity_digest_mismatch")
        if expected_identity in processes or (pid, created) in process_keys:
            raise R7S5EtwError("etw_process_identity_duplicate")
        processes[expected_identity] = (pid, created, image_sha)
        process_keys.add((pid, created))

    events_raw = raw["events"]
    if not isinstance(events_raw, list) or not events_raw:
        raise R7S5EtwError("etw_capture_events_required")
    observed_specs: set[tuple[str, int, int]] = set()
    observed_processes: set[str] = set()
    event_fingerprints: set[tuple[object, ...]] = set()
    previous_timestamp: int | None = None
    for sequence, value_event in enumerate(events_raw):
        event = _mapping(value_event, f"event_{sequence}")
        _exact_keys(
            event,
            {
                "sequence",
                "provider_guid",
                "event_id",
                "version",
                "timestamp_qpc",
                "process_identity_id",
                "pid",
                "process_creation_time_ns",
            },
            f"event_{sequence}",
        )
        if _strict_int(event["sequence"], "event_sequence") != sequence:
            raise R7S5EtwError("etw_event_sequence_not_contiguous")
        spec = (
            _guid(event["provider_guid"], "event_provider"),
            _strict_int(event["event_id"], "event_id", maximum=65_535),
            _strict_int(event["version"], "event_version", maximum=255),
        )
        if spec not in allowed_events:
            raise R7S5EtwError("etw_event_not_in_approved_manifest")
        observed_specs.add(spec)
        timestamp = _strict_int(event["timestamp_qpc"], "event_timestamp_qpc", minimum=1)
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise R7S5EtwError("etw_event_clock_regressed")
        previous_timestamp = timestamp
        identity_id = _sha256(event["process_identity_id"], "event_process_identity")
        if identity_id not in processes:
            raise R7S5EtwError("etw_event_process_identity_unknown")
        pid, creation_time_ns, _image_sha = processes[identity_id]
        event_pid = _strict_int(event["pid"], "event_pid", minimum=1)
        event_created = _strict_int(
            event["process_creation_time_ns"], "event_process_creation_time_ns", minimum=1
        )
        if event_pid != pid or event_created != creation_time_ns:
            raise R7S5EtwError("etw_event_process_identity_binding_mismatch")
        fingerprint = (*spec, timestamp, identity_id)
        if fingerprint in event_fingerprints:
            raise R7S5EtwError("etw_event_replay_duplicate")
        event_fingerprints.add(fingerprint)
        observed_processes.add(identity_id)
    if observed_specs != allowed_events:
        raise R7S5EtwError("etw_required_event_coverage_incomplete")
    if observed_processes != set(processes):
        raise R7S5EtwError("etw_process_identity_coverage_incomplete")

    archive = _mapping(raw["archive"], "archive")
    _exact_keys(archive, {"created", "sha256", "bytes"}, "archive")
    _strict_bool(archive["created"], True, "archive_created")
    _sha256(archive["sha256"], "archive")
    _strict_int(archive["bytes"], "archive_bytes", minimum=1)
    return EtwDecision(
        status="captured_non_credit",
        decision="qualified_non_credit",
        qualified_non_credit=True,
        capture_id=capture_id,
        approved_manifest_sha256=manifest_sha,
    )


def validate_etw_qualification(
    value: object,
    *,
    approved_external_manifest: object | None = None,
    seen_capture_ids: Sequence[str] = (),
) -> EtwDecision:
    """Validate a strict not-run record or an externally approved capture."""

    if approved_external_manifest is None:
        return _validate_not_run(value)
    manifest = validate_approved_manifest(approved_external_manifest)
    manifest_sha = hashlib.sha256(_canonical_json_bytes(manifest)).hexdigest()
    return _validate_capture(
        value,
        manifest=manifest,
        manifest_sha=manifest_sha,
        seen_capture_ids=seen_capture_ids,
    )


def etw_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.etw-offline-contract.v1",
        "built_in_provider_guids": [],
        "built_in_event_ids": [],
        "approved_external_manifest_required": True,
        "missing_manifest_status": "not_run",
        "missing_manifest_decision": "NO-GO",
        "administrator_session_required_for_capture": True,
        "lost_events_required": 0,
        "lost_buffers_required": 0,
        "pid_and_creation_time_identity_required": True,
        "service_configuration_change_allowed": False,
        "synthetic_or_replay_allowed": False,
        "completion_credit": "non_credit_only",
        "success_or_completion_marker_allowed": False,
        "live_calls_implemented": False,
        "external_manifest_authenticity_verified_by_this_module": False,
        "capture_liveness_verified_by_this_module": False,
        "capture_replay_scope": "caller_supplied_seen_capture_ids",
    }


__all__ = (
    "CAPTURE_SCHEMA",
    "EtwDecision",
    "MANIFEST_SCHEMA",
    "NOT_RUN_SCHEMA",
    "R7S5EtwError",
    "ZERO_ETW_CALLS",
    "approved_manifest_sha256",
    "etw_contract",
    "process_identity_id",
    "validate_approved_manifest",
    "validate_etw_qualification",
)
