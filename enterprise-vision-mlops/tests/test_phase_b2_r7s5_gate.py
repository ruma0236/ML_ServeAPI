from __future__ import annotations

import copy
import hashlib
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r7s5_gate as gate


RUN_UUID = "11111111-1111-4111-8111-111111111111"
ATTEMPT_UUID = "22222222-2222-4222-8222-222222222222"
CANDIDATE_SHA256 = "a" * 64


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


EXPECTED_PRE_IMPORT_TCB_BINDING = {
    "commit": "1" * 40,
    "tree": "2" * 40,
    "outer_launcher_sha256": _hash("outer-launcher"),
    "runner_sha256": _hash("runner"),
    "publisher_sha256": _hash("publisher"),
    "git_sha256": _hash("git"),
    "powershell_sha256": _hash("powershell"),
    "python_sha256": _hash("python"),
}
EXPECTED_PRE_IMPORT_TCB_BINDING_SHA256 = gate.pre_import_tcb_binding_sha256(
    EXPECTED_PRE_IMPORT_TCB_BINDING
)


def _r6() -> dict[str, Any]:
    return {
        "schema": gate.R6_SCHEMA,
        "decision": "manual_intervention_required",
        "credit": "zero_credit",
        "go": False,
        "completion_marker_created": False,
        "acceptance_credit": False,
        "success_marker_created": False,
        "phase_b2_executed": False,
        "r6_restore_only": dict(gate.R6_RESTORE_ONLY),
        "historical_metadata": {"preserved": True},
    }


def _proof(name: str) -> dict[str, Any]:
    identity = gate.execution_identity_sha256(
        run_uuid=RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        candidate_sha256=CANDIDATE_SHA256,
    )
    value: dict[str, Any] = {
        "schema": gate.DEPENDENCY_SCHEMAS[name],
        "status": gate._EXPECTED_STATUS[name],
        "run_uuid": RUN_UUID,
        "attempt_uuid": ATTEMPT_UUID,
        "candidate_sha256": CANDIDATE_SHA256,
        "execution_identity_sha256": identity,
        "proof_sha256": _hash(name),
        "acceptance_credit": False,
        "go": False,
        "completion_marker_created": False,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "synthetic": False,
        "replayed": False,
    }
    if name == "pre_import_tcb_bootstrap":
        value.update(
            **EXPECTED_PRE_IMPORT_TCB_BINDING,
            tracked_clean=True,
            ordinary_untracked_import_active_count=0,
            ignored_import_active_count=0,
            scan_precedes_project_imports=True,
            outer_self_hash_verified=True,
        )
    elif name == "r6_restore_only_approval":
        historical_projection = gate.validate_historical_r6_no_go(_r6())
        historical_projection_sha256 = hashlib.sha256(
            gate._canonical_json_bytes(historical_projection)
        ).hexdigest()
        value.update(
            historical_r6_no_go_projection_sha256=historical_projection_sha256,
            r6_restore_run_uuid="33333333-3333-4333-8333-333333333333",
            r6_restore_attempt_uuid="44444444-4444-4444-8444-444444444444",
            r6_restore_manifest_sha256=_hash("r6-restore-manifest"),
            r6_restore_success_index_sha256=_hash("r6-restore-success-index"),
            restore_only_result={
                "decision": "PASS",
                "credit": "environment_recovery_only",
                "executed": True,
                "outer_calls": 1,
                "bridge_calls": 1,
                "runner_calls": 1,
                "automatic_retry_count": 0,
                "docker_off_probe_calls": 0,
                "service_lifecycle_calls": 0,
                "windows_collector_calls": 0,
                "wsl_collector_calls": 0,
                "fresh_phase_b2_executed": False,
                "completion_marker_created": False,
            },
            independent_approval={
                "independent_authority_verified": True,
                "decision": "approve_exact_r6_restore_only_pass_once",
                "approval_scope": "pre_r8_prerequisite_only",
                "receipt_sha256": _hash("r6-independent-approval-receipt"),
            },
        )
        value["proof_sha256"] = gate.r6_restore_only_approval_proof_sha256(value)
    elif name == "process_containment_architecture":
        value.update(
            job_capability_consumed_before_workload=True,
            ambient_ancestor_job_effective_limits_audited=True,
            residual_job_observer_lease_until_active_zero=True,
            pre_kernel_cancel_create_race_kernel_bound=True,
            pre_kernel_filesystem_setup_hard_deadline_bounded=True,
            wsl_kernel_lineage_containment=True,
            wsl_launcher_interpreter_sha256_pinned=True,
            wsl_residual_scan_resource_caps_enforced=True,
            wsl_scan_nonce_unique_per_poll=True,
        )
    elif name == "external_receipt":
        value.update(
            independent_authority_verified=True,
            decision="approve_exact_candidate_once",
        )
    elif name == "global_reservation":
        value.update(global_one_shot_reserved=True, replace_if_exists=False)
    elif name in {"windows_qualification", "wsl_qualification"}:
        value.update(
            completion_credit="non_credit_only",
            residual_state="zero",
            residual_pids=[],
        )
    elif name == "dual_collector":
        value.update(
            completion_credit="non_credit_only",
            cross_domain_raw_comparison=False,
            domain_sample_counts={"windows_host": 1800, "wsl_ubuntu": 1800},
        )
    elif name == "runtime_approval":
        value.update(
            independent_authority_verified=True,
            decision="approve_runtime_admission_once",
            approval_scope="single_process_admission_only",
        )
    return value


def _dependencies() -> dict[str, dict[str, Any]]:
    return {name: _proof(name) for name in gate.DEPENDENCY_SCHEMAS}


def _evaluate(**changes: object) -> gate.GateDecision:
    dependencies = _dependencies()
    inputs: dict[str, object] = {
        "historical_r6": _r6(),
        "run_uuid": RUN_UUID,
        "attempt_uuid": ATTEMPT_UUID,
        "candidate_sha256": CANDIDATE_SHA256,
        **dependencies,
        "expected_r6_restore_only_approval_sha256": dependencies["r6_restore_only_approval"][
            "proof_sha256"
        ],
        "expected_pre_import_tcb_binding": copy.deepcopy(EXPECTED_PRE_IMPORT_TCB_BINDING),
        "expected_pre_import_tcb_binding_sha256": EXPECTED_PRE_IMPORT_TCB_BINDING_SHA256,
    }
    inputs.update(changes)
    return gate.evaluate_r7s5_gate(**inputs)  # type: ignore[arg-type]


def test_historical_r6_projection_preserves_exact_no_go_semantics() -> None:
    projection = gate.validate_historical_r6_no_go(_r6())

    assert projection == {
        "schema": gate.R6_SCHEMA,
        "decision": "manual_intervention_required",
        "credit": "zero_credit",
        "go": False,
        "completion_marker_created": False,
        "r6_restore_only": gate.R6_RESTORE_ONLY,
    }


@pytest.mark.parametrize("missing", list(gate.DEPENDENCY_SCHEMAS))
def test_every_missing_prerequisite_is_no_go_with_zero_downstream(missing: str) -> None:
    decision = _evaluate(**{missing: None}).to_dict()

    assert decision["decision"] == "NO-GO"
    assert decision["ready_for_separate_runtime_admission"] is False
    assert f"missing:{missing}" in decision["blockers"]
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS
    assert decision["automatic_retry_allowed"] is False
    assert decision["completion_marker_created"] is False
    assert decision["success_marker_created"] is False


def test_exact_complete_proof_set_is_only_ready_for_a_separate_boundary() -> None:
    decision = _evaluate().to_dict()

    assert decision["schema"] == gate.GATE_SCHEMA
    assert decision["decision"] == "ready_for_separate_runtime_admission"
    assert decision["ready_for_separate_runtime_admission"] is True
    assert decision["blockers"] == []
    assert decision["acceptance_credit"] is False
    assert decision["go"] is False
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS
    assert decision["completion_marker_created"] is False
    assert decision["success_marker_created"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tracked_clean", False),
        ("ordinary_untracked_import_active_count", 1),
        ("ignored_import_active_count", 1),
        ("scan_precedes_project_imports", False),
        ("outer_self_hash_verified", False),
        ("runner_sha256", "0" * 63),
    ],
)
def test_pre_import_bootstrap_false_or_incomplete_claim_is_no_go(
    field: str,
    value: object,
) -> None:
    dependencies = _dependencies()
    dependencies["pre_import_tcb_bootstrap"][field] = value

    decision = _evaluate(**dependencies).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(
        item.startswith("invalid:pre_import_tcb_bootstrap:") for item in decision["blockers"]
    )
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


@pytest.mark.parametrize("field", gate.PRE_IMPORT_TCB_BINDING_FIELDS)
def test_each_pre_import_tcb_binding_field_mutation_is_no_go(field: str) -> None:
    dependencies = _dependencies()
    proof = dependencies["pre_import_tcb_bootstrap"]
    proof[field] = ("3" * 40) if field in {"commit", "tree"} else _hash(f"changed-{field}")
    proof["proof_sha256"] = hashlib.sha256(
        gate._canonical_json_bytes(
            {key: value for key, value in proof.items() if key != "proof_sha256"}
        )
    ).hexdigest()

    decision = _evaluate(**dependencies).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(f"pre_import_tcb_binding_{field}_mismatch" in item for item in decision["blockers"])
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


def test_pre_import_tcb_binding_sha_mismatch_is_no_go() -> None:
    decision = _evaluate(expected_pre_import_tcb_binding_sha256=_hash("wrong-binding")).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(
        "pre_import_tcb_binding_external_sha256_mismatch" in item for item in decision["blockers"]
    )
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_pre_import_tcb_binding", None),
        ("expected_pre_import_tcb_binding_sha256", None),
    ],
)
def test_pre_import_tcb_binding_requires_both_independent_inputs(
    field: str,
    value: object,
) -> None:
    decision = _evaluate(**{field: value}).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(
        item.startswith("invalid:pre_import_tcb_bootstrap:") for item in decision["blockers"]
    )
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


def test_self_consistent_pre_import_tcb_proof_and_expected_binding_tamper_is_rejected() -> None:
    dependencies = _dependencies()
    proof = dependencies["pre_import_tcb_bootstrap"]
    proof["runner_sha256"] = _hash("mutated-runner")
    proof["proof_sha256"] = hashlib.sha256(
        gate._canonical_json_bytes(
            {key: value for key, value in proof.items() if key != "proof_sha256"}
        )
    ).hexdigest()
    expected = copy.deepcopy(EXPECTED_PRE_IMPORT_TCB_BINDING)
    expected["runner_sha256"] = proof["runner_sha256"]

    decision = _evaluate(
        **dependencies,
        expected_pre_import_tcb_binding=expected,
    ).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(
        "pre_import_tcb_binding_external_sha256_mismatch" in item for item in decision["blockers"]
    )
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


@pytest.mark.parametrize(
    "field",
    sorted(gate._EXTRA_FIELDS["process_containment_architecture"]),
)
def test_each_process_containment_architecture_gap_is_no_go(field: str) -> None:
    dependencies = _dependencies()
    dependencies["process_containment_architecture"][field] = False

    decision = _evaluate(**dependencies).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(
        item.startswith("invalid:process_containment_architecture:")
        for item in decision["blockers"]
    )
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


def test_historical_r6_no_go_never_substitutes_for_new_restore_only_pass_proof() -> None:
    decision = gate.evaluate_r7s5_gate(
        historical_r6=_r6(),
        run_uuid=RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        candidate_sha256=CANDIDATE_SHA256,
    ).to_dict()

    assert decision["decision"] == "NO-GO"
    assert "missing:r6_restore_only_approval" in decision["blockers"]
    assert decision["historical_r6_projection_sha256"] is not None
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


def test_r6_restore_only_proof_requires_out_of_band_sha256_expectation() -> None:
    decision = _evaluate(expected_r6_restore_only_approval_sha256=None).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(
        "expected_r6_restore_only_approval_sha256_invalid" in item for item in decision["blockers"]
    )


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("restore_only_result", "decision", "manual_intervention_required"),
        ("restore_only_result", "executed", False),
        ("restore_only_result", "outer_calls", 0),
        ("restore_only_result", "automatic_retry_count", 1),
        ("restore_only_result", "fresh_phase_b2_executed", True),
        ("independent_approval", "independent_authority_verified", False),
        ("independent_approval", "decision", "approve_any_restore"),
    ],
)
def test_r6_restore_only_false_or_wrong_claim_is_no_go(
    section: str,
    field: str,
    value: object,
) -> None:
    dependencies = _dependencies()
    proof = dependencies["r6_restore_only_approval"]
    proof[section][field] = value
    proof["proof_sha256"] = gate.r6_restore_only_approval_proof_sha256(proof)

    decision = _evaluate(
        **dependencies,
        expected_r6_restore_only_approval_sha256=proof["proof_sha256"],
    ).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(
        item.startswith("invalid:r6_restore_only_approval:") for item in decision["blockers"]
    )


def test_stale_r6_restore_only_approval_binding_is_no_go_even_when_rehashed() -> None:
    dependencies = _dependencies()
    proof = dependencies["r6_restore_only_approval"]
    proof["candidate_sha256"] = "b" * 64
    proof["proof_sha256"] = gate.r6_restore_only_approval_proof_sha256(proof)

    decision = _evaluate(
        **dependencies,
        expected_r6_restore_only_approval_sha256=proof["proof_sha256"],
    ).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(
        "r6_restore_only_approval_candidate_binding_mismatch" in item
        for item in decision["blockers"]
    )


def test_self_consistent_r6_proof_tamper_rejected_by_external_digest_pin() -> None:
    dependencies = _dependencies()
    proof = dependencies["r6_restore_only_approval"]
    expected_sha256 = proof["proof_sha256"]
    proof["r6_restore_manifest_sha256"] = _hash("tampered-r6-restore-manifest")
    proof["proof_sha256"] = gate.r6_restore_only_approval_proof_sha256(proof)

    decision = _evaluate(
        **dependencies,
        expected_r6_restore_only_approval_sha256=expected_sha256,
    ).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(
        "r6_restore_only_approval_external_sha256_mismatch" in item for item in decision["blockers"]
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("go",), True),
        (("completion_marker_created",), True),
        (("acceptance_credit",), True),
        (("r6_restore_only", "executed"), True),
        (("r6_restore_only", "outer_calls"), True),
        (("r6_restore_only", "runner_calls"), 1),
    ],
)
def test_historical_r6_credit_execution_or_numeric_bool_mutation_is_no_go(
    path: tuple[str, ...], value: object
) -> None:
    historical = _r6()
    target: dict[str, Any] = historical
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    decision = _evaluate(historical_r6=historical).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(item.startswith("historical_r6_invalid:") for item in decision["blockers"])
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("external_receipt", "synthetic", True),
        ("external_receipt", "independent_authority_verified", False),
        ("global_reservation", "global_one_shot_reserved", False),
        ("global_reservation", "replace_if_exists", True),
        ("windows_qualification", "residual_pids", [4321]),
        ("wsl_qualification", "residual_state", "unknown"),
        ("dual_collector", "cross_domain_raw_comparison", True),
        (
            "dual_collector",
            "domain_sample_counts",
            {"windows_host": 1800, "wsl_ubuntu": 1799},
        ),
        ("runtime_approval", "approval_scope", "service_and_r8"),
        ("runtime_approval", "automatic_retry_count", True),
    ],
)
def test_invalid_prerequisite_is_no_go(name: str, field: str, value: object) -> None:
    dependencies = _dependencies()
    dependencies[name][field] = value

    decision = _evaluate(**dependencies).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any(item.startswith(f"invalid:{name}:") for item in decision["blockers"])
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


def test_proof_swap_and_duplicate_proof_hash_are_rejected() -> None:
    swapped = _dependencies()
    swapped["wsl_qualification"]["run_uuid"] = "33333333-3333-4333-8333-333333333333"
    decision = _evaluate(**swapped).to_dict()
    assert decision["decision"] == "NO-GO"
    assert any("wsl_qualification_run_binding_mismatch" in item for item in decision["blockers"])

    duplicate = _dependencies()
    duplicate["runtime_approval"]["proof_sha256"] = duplicate["external_receipt"]["proof_sha256"]
    decision = _evaluate(**duplicate).to_dict()
    assert "dependency_proof_sha256_reused" in decision["blockers"]


def test_execution_identity_replay_is_no_go() -> None:
    identity = gate.execution_identity_sha256(
        run_uuid=RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        candidate_sha256=CANDIDATE_SHA256,
    )

    decision = _evaluate(seen_execution_identities=(identity,)).to_dict()

    assert decision["decision"] == "NO-GO"
    assert "execution_identity_replay" in decision["blockers"]
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS


def test_unknown_dependency_field_cannot_be_used_as_a_local_flag_flip() -> None:
    dependencies = _dependencies()
    dependencies["runtime_approval"]["production_entry_enabled"] = True

    decision = _evaluate(**copy.deepcopy(dependencies)).to_dict()

    assert decision["decision"] == "NO-GO"
    assert any("runtime_approval_fields_mismatch" in item for item in decision["blockers"])
    contract = gate.gate_contract()
    assert contract["ready_decision_is_production_go"] is False
    assert contract["dependency_proof_authenticity_verified_by_this_module"] is False
    assert contract["pre_import_tcb_binding_sha256_supplied_independently_from_proof"] is True
    assert contract["pre_import_tcb_expected_binding_authority_verified_by_this_module"] is False
    assert contract["caller_must_authenticate_expected_pre_import_tcb_binding_sha256"] is True
    assert contract["separate_trusted_boundary_revalidation_required"] is True
