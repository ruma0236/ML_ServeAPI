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
    if name == "external_receipt":
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
    inputs: dict[str, object] = {
        "historical_r6": _r6(),
        "run_uuid": RUN_UUID,
        "attempt_uuid": ATTEMPT_UUID,
        "candidate_sha256": CANDIDATE_SHA256,
        **_dependencies(),
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

    assert decision["decision"] == "ready_for_separate_runtime_admission"
    assert decision["ready_for_separate_runtime_admission"] is True
    assert decision["blockers"] == []
    assert decision["acceptance_credit"] is False
    assert decision["go"] is False
    assert decision["downstream_calls"] == gate.ZERO_DOWNSTREAM_CALLS
    assert decision["completion_marker_created"] is False
    assert decision["success_marker_created"] is False


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
    assert contract["separate_trusted_boundary_revalidation_required"] is True
