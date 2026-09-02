"""Pure fail-closed admission gate for pre-r8 r7s5.

This module performs no filesystem, process, WSL, ETW, Docker, service, or
runtime operation.  A positive result only means that the exact prerequisite
proof set is ready for a separate admission boundary; it is never Phase B2
credit or a production success marker.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


R6_SCHEMA = "r6-compose-recovery-rca/v1"
GATE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.offline-gate-decision.v1"
HEX64_RE = re.compile(r"[0-9a-f]{64}")

DEPENDENCY_SCHEMAS = {
    "external_receipt": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.external-receipt-proof.v1",
    "global_reservation": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.global-reservation-proof.v1",
    "windows_qualification": ("evm.s8-v4.x1.phase-b2.pre-r8-r7s5.windows-qualification-proof.v1"),
    "wsl_qualification": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.wsl-qualification-proof.v1",
    "dual_collector": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.dual-clock-proof.v1",
    "runtime_approval": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.runtime-approval-proof.v1",
}

ZERO_DOWNSTREAM_CALLS = {
    "repo_reads": 0,
    "process_spawn": 0,
    "service_calls": 0,
    "live_wsl": 0,
    "etw_sessions": 0,
    "dual_collector": 0,
    "runtime_admission": 0,
    "r8": 0,
    "automatic_retry": 0,
    "force_kill": 0,
    "completion_markers": 0,
}

R6_RESTORE_ONLY = {
    "bundle_created": False,
    "executed": False,
    "outer_calls": 0,
    "bridge_calls": 0,
    "runner_calls": 0,
    "retries": 0,
}


class R7S5GateError(ValueError):
    """Raised when an offline gate input is structurally unsafe."""


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
        raise R7S5GateError("canonical_json_value_rejected") from exc
    return (encoded + "\n").encode("ascii")


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R7S5GateError(f"{label}_object_required")
    return dict(value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise R7S5GateError(f"{label}_fields_mismatch")


def _strict_bool(value: object, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise R7S5GateError(f"{label}_must_be_{str(expected).lower()}")


def _strict_int(value: object, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise R7S5GateError(f"{label}_must_equal_{expected}")


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        raise R7S5GateError(f"{label}_sha256_invalid")
    return value


def _canonical_uuid4(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise R7S5GateError(f"{label}_uuid4_invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise R7S5GateError(f"{label}_uuid4_invalid") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise R7S5GateError(f"{label}_uuid4_not_canonical")
    return value


def execution_identity_sha256(*, run_uuid: str, attempt_uuid: str, candidate_sha256: str) -> str:
    identity = {
        "attempt_uuid": _canonical_uuid4(attempt_uuid, "attempt"),
        "candidate_sha256": _sha256(candidate_sha256, "candidate"),
        "run_uuid": _canonical_uuid4(run_uuid, "run"),
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.execution-identity.v1",
    }
    return hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()


def validate_historical_r6_no_go(value: object) -> dict[str, Any]:
    """Return the frozen security projection of the immutable r6 NO-GO RCA."""

    raw = _mapping(value, "historical_r6")
    required = {
        "schema",
        "decision",
        "credit",
        "go",
        "completion_marker_created",
        "r6_restore_only",
    }
    if not required.issubset(raw):
        raise R7S5GateError("historical_r6_required_fields_missing")
    if raw["schema"] != R6_SCHEMA:
        raise R7S5GateError("historical_r6_schema_mismatch")
    if raw["decision"] != "manual_intervention_required":
        raise R7S5GateError("historical_r6_decision_mismatch")
    if raw["credit"] != "zero_credit":
        raise R7S5GateError("historical_r6_credit_mismatch")
    _strict_bool(raw["go"], False, "historical_r6_go")
    _strict_bool(
        raw["completion_marker_created"],
        False,
        "historical_r6_completion_marker_created",
    )
    restore = _mapping(raw["r6_restore_only"], "historical_r6_restore_only")
    _exact_keys(restore, set(R6_RESTORE_ONLY), "historical_r6_restore_only")
    for name, expected in R6_RESTORE_ONLY.items():
        if type(expected) is bool:
            _strict_bool(restore[name], expected, f"historical_r6_{name}")
        else:
            _strict_int(restore[name], expected, f"historical_r6_{name}")
    for name in ("acceptance_credit", "success_marker_created", "phase_b2_executed"):
        if name in raw:
            _strict_bool(raw[name], False, f"historical_r6_{name}")
    return {
        "schema": R6_SCHEMA,
        "decision": "manual_intervention_required",
        "credit": "zero_credit",
        "go": False,
        "completion_marker_created": False,
        "r6_restore_only": dict(R6_RESTORE_ONLY),
    }


_COMMON_FIELDS = {
    "schema",
    "status",
    "run_uuid",
    "attempt_uuid",
    "candidate_sha256",
    "execution_identity_sha256",
    "proof_sha256",
    "acceptance_credit",
    "go",
    "completion_marker_created",
    "automatic_retry_count",
    "forced_termination_attempts",
    "synthetic",
    "replayed",
}

_EXTRA_FIELDS = {
    "external_receipt": {"independent_authority_verified", "decision"},
    "global_reservation": {"global_one_shot_reserved", "replace_if_exists"},
    "windows_qualification": {"completion_credit", "residual_state", "residual_pids"},
    "wsl_qualification": {"completion_credit", "residual_state", "residual_pids"},
    "dual_collector": {
        "completion_credit",
        "cross_domain_raw_comparison",
        "domain_sample_counts",
    },
    "runtime_approval": {"independent_authority_verified", "decision", "approval_scope"},
}

_EXPECTED_STATUS = {
    "external_receipt": "verified",
    "global_reservation": "reserved_once_global",
    "windows_qualification": "qualified_non_credit",
    "wsl_qualification": "qualified_non_credit",
    "dual_collector": "qualified_non_credit",
    "runtime_approval": "approved_exactly_once",
}


def _validate_dependency(
    name: str,
    value: object,
    *,
    run_uuid: str,
    attempt_uuid: str,
    candidate_sha256: str,
    execution_sha256: str,
) -> str:
    proof = _mapping(value, name)
    _exact_keys(proof, _COMMON_FIELDS | _EXTRA_FIELDS[name], name)
    if proof["schema"] != DEPENDENCY_SCHEMAS[name]:
        raise R7S5GateError(f"{name}_schema_mismatch")
    if proof["status"] != _EXPECTED_STATUS[name]:
        raise R7S5GateError(f"{name}_status_mismatch")
    if proof["run_uuid"] != run_uuid or proof["attempt_uuid"] != attempt_uuid:
        raise R7S5GateError(f"{name}_run_binding_mismatch")
    if proof["candidate_sha256"] != candidate_sha256:
        raise R7S5GateError(f"{name}_candidate_binding_mismatch")
    if proof["execution_identity_sha256"] != execution_sha256:
        raise R7S5GateError(f"{name}_execution_identity_mismatch")
    proof_sha = _sha256(proof["proof_sha256"], f"{name}_proof")
    _strict_bool(proof["acceptance_credit"], False, f"{name}_acceptance_credit")
    _strict_bool(proof["go"], False, f"{name}_go")
    _strict_bool(proof["completion_marker_created"], False, f"{name}_completion_marker_created")
    _strict_int(proof["automatic_retry_count"], 0, f"{name}_automatic_retry_count")
    _strict_int(proof["forced_termination_attempts"], 0, f"{name}_forced_termination_attempts")
    _strict_bool(proof["synthetic"], False, f"{name}_synthetic")
    _strict_bool(proof["replayed"], False, f"{name}_replayed")

    if name in {"external_receipt", "runtime_approval"}:
        _strict_bool(
            proof["independent_authority_verified"],
            True,
            f"{name}_independent_authority_verified",
        )
    if name == "external_receipt" and proof["decision"] != "approve_exact_candidate_once":
        raise R7S5GateError("external_receipt_decision_mismatch")
    if name == "global_reservation":
        _strict_bool(proof["global_one_shot_reserved"], True, "global_one_shot_reserved")
        _strict_bool(proof["replace_if_exists"], False, "global_reservation_replace_if_exists")
    if name in {"windows_qualification", "wsl_qualification"}:
        if proof["completion_credit"] != "non_credit_only":
            raise R7S5GateError(f"{name}_credit_mismatch")
        if proof["residual_state"] != "zero" or proof["residual_pids"] != []:
            raise R7S5GateError(f"{name}_residual_not_zero")
    if name == "dual_collector":
        if proof["completion_credit"] != "non_credit_only":
            raise R7S5GateError("dual_collector_credit_mismatch")
        _strict_bool(
            proof["cross_domain_raw_comparison"],
            False,
            "dual_collector_cross_domain_raw_comparison",
        )
        if proof["domain_sample_counts"] != {"windows_host": 1800, "wsl_ubuntu": 1800}:
            raise R7S5GateError("dual_collector_sample_counts_mismatch")
    if name == "runtime_approval":
        if proof["decision"] != "approve_runtime_admission_once":
            raise R7S5GateError("runtime_approval_decision_mismatch")
        if proof["approval_scope"] != "single_process_admission_only":
            raise R7S5GateError("runtime_approval_scope_mismatch")
    return proof_sha


@dataclass(frozen=True, slots=True)
class GateDecision:
    decision: str
    blockers: tuple[str, ...]
    ready_for_separate_runtime_admission: bool
    execution_identity_sha256: str
    historical_r6_projection_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": GATE_SCHEMA,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "ready_for_separate_runtime_admission": self.ready_for_separate_runtime_admission,
            "execution_identity_sha256": self.execution_identity_sha256,
            "historical_r6_projection_sha256": self.historical_r6_projection_sha256,
            "acceptance_credit": False,
            "go": False,
            "completion_marker_created": False,
            "success_marker_created": False,
            "automatic_retry_allowed": False,
            "downstream_calls": dict(ZERO_DOWNSTREAM_CALLS),
        }


def evaluate_r7s5_gate(
    *,
    historical_r6: object,
    run_uuid: str,
    attempt_uuid: str,
    candidate_sha256: str,
    external_receipt: object | None = None,
    global_reservation: object | None = None,
    windows_qualification: object | None = None,
    wsl_qualification: object | None = None,
    dual_collector: object | None = None,
    runtime_approval: object | None = None,
    seen_execution_identities: Sequence[str] = (),
) -> GateDecision:
    """Evaluate proof readiness without performing or authorizing a downstream call."""

    run = _canonical_uuid4(run_uuid, "run")
    attempt = _canonical_uuid4(attempt_uuid, "attempt")
    candidate = _sha256(candidate_sha256, "candidate")
    identity = execution_identity_sha256(
        run_uuid=run,
        attempt_uuid=attempt,
        candidate_sha256=candidate,
    )
    blockers: list[str] = []
    r6_projection_sha: str | None = None
    try:
        projection = validate_historical_r6_no_go(historical_r6)
        r6_projection_sha = hashlib.sha256(_canonical_json_bytes(projection)).hexdigest()
    except R7S5GateError as exc:
        blockers.append(f"historical_r6_invalid:{exc}")

    if identity in seen_execution_identities:
        blockers.append("execution_identity_replay")

    dependencies = {
        "external_receipt": external_receipt,
        "global_reservation": global_reservation,
        "windows_qualification": windows_qualification,
        "wsl_qualification": wsl_qualification,
        "dual_collector": dual_collector,
        "runtime_approval": runtime_approval,
    }
    proof_hashes: list[str] = []
    for name, value in dependencies.items():
        if value is None:
            blockers.append(f"missing:{name}")
            continue
        try:
            proof_hashes.append(
                _validate_dependency(
                    name,
                    value,
                    run_uuid=run,
                    attempt_uuid=attempt,
                    candidate_sha256=candidate,
                    execution_sha256=identity,
                )
            )
        except R7S5GateError as exc:
            blockers.append(f"invalid:{name}:{exc}")
    if len(proof_hashes) != len(set(proof_hashes)):
        blockers.append("dependency_proof_sha256_reused")

    ready = not blockers
    return GateDecision(
        decision="ready_for_separate_runtime_admission" if ready else "NO-GO",
        blockers=tuple(blockers),
        ready_for_separate_runtime_admission=ready,
        execution_identity_sha256=identity,
        historical_r6_projection_sha256=r6_projection_sha,
    )


def gate_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.offline-gate-contract.v1",
        "historical_r6_no_go_must_be_preserved": True,
        "required_dependencies": list(DEPENDENCY_SCHEMAS),
        "missing_or_invalid_dependency_downstream_calls": dict(ZERO_DOWNSTREAM_CALLS),
        "automatic_retry_allowed": False,
        "success_or_completion_marker_allowed": False,
        "offline_module_performs_downstream_calls": False,
        "ready_decision_is_production_go": False,
        "dependency_proof_authenticity_verified_by_this_module": False,
        "historical_r6_raw_bytes_or_parent_chain_verified_by_this_module": False,
        "separate_trusted_boundary_revalidation_required": True,
    }


__all__ = (
    "DEPENDENCY_SCHEMAS",
    "GateDecision",
    "R7S5GateError",
    "ZERO_DOWNSTREAM_CALLS",
    "evaluate_r7s5_gate",
    "execution_identity_sha256",
    "gate_contract",
    "validate_historical_r6_no_go",
)
