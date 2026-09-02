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
GATE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.offline-gate-decision.v2"
HEX64_RE = re.compile(r"[0-9a-f]{64}")

PRE_IMPORT_TCB_BINDING_FIELDS = (
    "commit",
    "tree",
    "outer_launcher_sha256",
    "runner_sha256",
    "publisher_sha256",
    "git_sha256",
    "powershell_sha256",
    "python_sha256",
)

DEPENDENCY_SCHEMAS = {
    "pre_import_tcb_bootstrap": (
        "evm.s8-v4.x1.phase-b2.pre-r8-r7s6.pre-import-tcb-bootstrap-proof.v1"
    ),
    "r6_restore_only_approval": (
        "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.r6-restore-only-pass-approval-proof.v1"
    ),
    "process_containment_architecture": (
        "evm.s8-v4.x1.phase-b2.pre-r8-r7s6.process-containment-architecture-proof.v1"
    ),
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
    "pre_import_tcb_bootstrap": {
        "outer_launcher_sha256",
        "runner_sha256",
        "publisher_sha256",
        "git_sha256",
        "powershell_sha256",
        "python_sha256",
        "commit",
        "tree",
        "tracked_clean",
        "ordinary_untracked_import_active_count",
        "ignored_import_active_count",
        "scan_precedes_project_imports",
        "outer_self_hash_verified",
    },
    "r6_restore_only_approval": {
        "historical_r6_no_go_projection_sha256",
        "r6_restore_run_uuid",
        "r6_restore_attempt_uuid",
        "r6_restore_manifest_sha256",
        "r6_restore_success_index_sha256",
        "restore_only_result",
        "independent_approval",
    },
    "process_containment_architecture": {
        "job_capability_consumed_before_workload",
        "ambient_ancestor_job_effective_limits_audited",
        "residual_job_observer_lease_until_active_zero",
        "pre_kernel_cancel_create_race_kernel_bound",
        "pre_kernel_filesystem_setup_hard_deadline_bounded",
        "wsl_kernel_lineage_containment",
        "wsl_launcher_interpreter_sha256_pinned",
        "wsl_residual_scan_resource_caps_enforced",
        "wsl_scan_nonce_unique_per_poll",
    },
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
    "pre_import_tcb_bootstrap": "verified_pre_import",
    "r6_restore_only_approval": "approved_restore_only_pass",
    "process_containment_architecture": "qualified_non_credit",
    "external_receipt": "verified",
    "global_reservation": "reserved_once_global",
    "windows_qualification": "qualified_non_credit",
    "wsl_qualification": "qualified_non_credit",
    "dual_collector": "qualified_non_credit",
    "runtime_approval": "approved_exactly_once",
}

_R6_RESTORE_ONLY_RESULT_FIELDS = {
    "decision",
    "credit",
    "executed",
    "outer_calls",
    "bridge_calls",
    "runner_calls",
    "automatic_retry_count",
    "docker_off_probe_calls",
    "service_lifecycle_calls",
    "windows_collector_calls",
    "wsl_collector_calls",
    "fresh_phase_b2_executed",
    "completion_marker_created",
}

_R6_INDEPENDENT_APPROVAL_FIELDS = {
    "independent_authority_verified",
    "decision",
    "approval_scope",
    "receipt_sha256",
}


def r6_restore_only_approval_proof_sha256(value: object) -> str:
    """Hash the exact approval proof payload without its declared self-hash."""

    proof = _mapping(value, "r6_restore_only_approval")
    if "proof_sha256" not in proof:
        raise R7S5GateError("r6_restore_only_approval_proof_sha256_missing")
    payload = dict(proof)
    payload.pop("proof_sha256")
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def pre_import_tcb_binding_sha256(value: object) -> str:
    """Hash an exact, independently supplied pre-import TCB binding."""

    binding = _mapping(value, "expected_pre_import_tcb_binding")
    _exact_keys(
        binding,
        set(PRE_IMPORT_TCB_BINDING_FIELDS),
        "expected_pre_import_tcb_binding",
    )
    for field in ("commit", "tree"):
        item = binding[field]
        if not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{40}", item) is None:
            raise R7S5GateError(f"expected_pre_import_tcb_binding_{field}_invalid")
    for field in PRE_IMPORT_TCB_BINDING_FIELDS[2:]:
        _sha256(binding[field], f"expected_pre_import_tcb_binding_{field}")
    return hashlib.sha256(_canonical_json_bytes(binding)).hexdigest()


def _validate_dependency(
    name: str,
    value: object,
    *,
    run_uuid: str,
    attempt_uuid: str,
    candidate_sha256: str,
    execution_sha256: str,
    historical_r6_projection_sha256: str | None = None,
    expected_r6_restore_only_approval_sha256: str | None = None,
    expected_pre_import_tcb_binding: object | None = None,
    expected_pre_import_tcb_binding_sha256: str | None = None,
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

    if name == "pre_import_tcb_bootstrap":
        for field in (
            "outer_launcher_sha256",
            "runner_sha256",
            "publisher_sha256",
            "git_sha256",
            "powershell_sha256",
            "python_sha256",
        ):
            _sha256(proof[field], f"pre_import_tcb_bootstrap_{field}")
        for field in ("commit", "tree"):
            value = proof[field]
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
                raise R7S5GateError(f"pre_import_tcb_bootstrap_{field}_invalid")
        _strict_bool(proof["tracked_clean"], True, "pre_import_tcb_bootstrap_tracked_clean")
        _strict_int(
            proof["ordinary_untracked_import_active_count"],
            0,
            "pre_import_tcb_bootstrap_ordinary_untracked_import_active_count",
        )
        _strict_int(
            proof["ignored_import_active_count"],
            0,
            "pre_import_tcb_bootstrap_ignored_import_active_count",
        )
        _strict_bool(
            proof["scan_precedes_project_imports"],
            True,
            "pre_import_tcb_bootstrap_scan_precedes_project_imports",
        )
        _strict_bool(
            proof["outer_self_hash_verified"],
            True,
            "pre_import_tcb_bootstrap_outer_self_hash_verified",
        )
        expected_binding = _mapping(
            expected_pre_import_tcb_binding,
            "expected_pre_import_tcb_binding",
        )
        computed_binding_sha256 = pre_import_tcb_binding_sha256(expected_binding)
        declared_binding_sha256 = _sha256(
            expected_pre_import_tcb_binding_sha256,
            "expected_pre_import_tcb_binding",
        )
        if computed_binding_sha256 != declared_binding_sha256:
            raise R7S5GateError("pre_import_tcb_binding_external_sha256_mismatch")
        for field in PRE_IMPORT_TCB_BINDING_FIELDS:
            if proof[field] != expected_binding[field]:
                raise R7S5GateError(f"pre_import_tcb_binding_{field}_mismatch")
    elif name == "process_containment_architecture":
        for field in _EXTRA_FIELDS["process_containment_architecture"]:
            _strict_bool(
                proof[field],
                True,
                f"process_containment_architecture_{field}",
            )
    elif name == "r6_restore_only_approval":
        if historical_r6_projection_sha256 is None:
            raise R7S5GateError("r6_restore_only_approval_historical_projection_unavailable")
        if proof["historical_r6_no_go_projection_sha256"] != historical_r6_projection_sha256:
            raise R7S5GateError("r6_restore_only_approval_historical_projection_mismatch")
        _canonical_uuid4(proof["r6_restore_run_uuid"], "r6_restore_run")
        _canonical_uuid4(proof["r6_restore_attempt_uuid"], "r6_restore_attempt")
        manifest_sha = _sha256(
            proof["r6_restore_manifest_sha256"],
            "r6_restore_manifest",
        )
        index_sha = _sha256(
            proof["r6_restore_success_index_sha256"],
            "r6_restore_success_index",
        )

        result = _mapping(proof["restore_only_result"], "r6_restore_only_result")
        _exact_keys(result, _R6_RESTORE_ONLY_RESULT_FIELDS, "r6_restore_only_result")
        if result["decision"] != "PASS":
            raise R7S5GateError("r6_restore_only_decision_mismatch")
        if result["credit"] != "environment_recovery_only":
            raise R7S5GateError("r6_restore_only_credit_mismatch")
        _strict_bool(result["executed"], True, "r6_restore_only_executed")
        for field in ("outer_calls", "bridge_calls", "runner_calls"):
            _strict_int(result[field], 1, f"r6_restore_only_{field}")
        for field in (
            "automatic_retry_count",
            "docker_off_probe_calls",
            "service_lifecycle_calls",
            "windows_collector_calls",
            "wsl_collector_calls",
        ):
            _strict_int(result[field], 0, f"r6_restore_only_{field}")
        _strict_bool(
            result["fresh_phase_b2_executed"],
            False,
            "r6_restore_only_fresh_phase_b2_executed",
        )
        _strict_bool(
            result["completion_marker_created"],
            False,
            "r6_restore_only_completion_marker_created",
        )

        approval = _mapping(proof["independent_approval"], "r6_independent_approval")
        _exact_keys(approval, _R6_INDEPENDENT_APPROVAL_FIELDS, "r6_independent_approval")
        _strict_bool(
            approval["independent_authority_verified"],
            True,
            "r6_independent_authority_verified",
        )
        if approval["decision"] != "approve_exact_r6_restore_only_pass_once":
            raise R7S5GateError("r6_independent_approval_decision_mismatch")
        if approval["approval_scope"] != "pre_r8_prerequisite_only":
            raise R7S5GateError("r6_independent_approval_scope_mismatch")
        receipt_sha = _sha256(approval["receipt_sha256"], "r6_independent_approval_receipt")
        if len({manifest_sha, index_sha, receipt_sha, proof_sha}) != 4:
            raise R7S5GateError("r6_restore_only_evidence_sha256_reused")

        computed_sha = r6_restore_only_approval_proof_sha256(proof)
        if proof_sha != computed_sha:
            raise R7S5GateError("r6_restore_only_approval_declared_sha256_mismatch")
        expected_sha = _sha256(
            expected_r6_restore_only_approval_sha256,
            "expected_r6_restore_only_approval",
        )
        if computed_sha != expected_sha:
            raise R7S5GateError("r6_restore_only_approval_external_sha256_mismatch")

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
    process_containment_architecture: object | None = None,
    external_receipt: object | None = None,
    global_reservation: object | None = None,
    windows_qualification: object | None = None,
    wsl_qualification: object | None = None,
    dual_collector: object | None = None,
    runtime_approval: object | None = None,
    pre_import_tcb_bootstrap: object | None = None,
    r6_restore_only_approval: object | None = None,
    expected_r6_restore_only_approval_sha256: str | None = None,
    expected_pre_import_tcb_binding: object | None = None,
    expected_pre_import_tcb_binding_sha256: str | None = None,
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
        "pre_import_tcb_bootstrap": pre_import_tcb_bootstrap,
        "r6_restore_only_approval": r6_restore_only_approval,
        "process_containment_architecture": process_containment_architecture,
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
                    historical_r6_projection_sha256=r6_projection_sha,
                    expected_r6_restore_only_approval_sha256=(
                        expected_r6_restore_only_approval_sha256
                    ),
                    expected_pre_import_tcb_binding=expected_pre_import_tcb_binding,
                    expected_pre_import_tcb_binding_sha256=(expected_pre_import_tcb_binding_sha256),
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
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.offline-gate-contract.v2",
        "historical_r6_no_go_must_be_preserved": True,
        "historical_r6_no_go_is_not_r6_restore_only_pass": True,
        "required_dependencies": list(DEPENDENCY_SCHEMAS),
        "missing_or_invalid_dependency_downstream_calls": dict(ZERO_DOWNSTREAM_CALLS),
        "automatic_retry_allowed": False,
        "success_or_completion_marker_allowed": False,
        "offline_module_performs_downstream_calls": False,
        "ready_decision_is_production_go": False,
        "dependency_proof_authenticity_verified_by_this_module": False,
        "r6_restore_only_approval_external_sha256_expectation_required": True,
        "r6_restore_only_approval_self_hash_verified_by_this_module": True,
        "r6_restore_only_approval_authority_signature_verified_by_this_module": False,
        "historical_r6_raw_bytes_or_parent_chain_verified_by_this_module": False,
        "pre_import_tcb_bootstrap_proof_required": True,
        "pre_import_tcb_bootstrap_implemented_by_this_module": False,
        "pre_import_tcb_exact_external_binding_required": True,
        "pre_import_tcb_external_binding_sha256_required": True,
        "pre_import_tcb_binding_sha256_supplied_independently_from_proof": True,
        "pre_import_tcb_expected_binding_authority_verified_by_this_module": False,
        "caller_must_authenticate_expected_pre_import_tcb_binding_sha256": True,
        "internal_post_import_inventory_is_not_bootstrap_proof": True,
        "process_containment_architecture_proof_required": True,
        "primitive_contract_with_false_safety_claims_is_not_qualification": True,
        "separate_trusted_boundary_revalidation_required": True,
    }


__all__ = (
    "DEPENDENCY_SCHEMAS",
    "GateDecision",
    "PRE_IMPORT_TCB_BINDING_FIELDS",
    "R7S5GateError",
    "ZERO_DOWNSTREAM_CALLS",
    "evaluate_r7s5_gate",
    "execution_identity_sha256",
    "gate_contract",
    "pre_import_tcb_binding_sha256",
    "r6_restore_only_approval_proof_sha256",
    "validate_historical_r6_no_go",
)
