from __future__ import annotations

import copy
import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.dev import invoke_wsl_process_group_r7s2 as outer
from scripts.dev import qualify_wsl_process_group_r7s2 as qualifier
from scripts.dev import stage_wsl_process_group_r7s2 as stager


QUALIFICATION_ID = "pre-r8-r7s2-wsl-20260901T160000Z-cafebabe"
RUN_UUID = "11223344-5566-4788-899a-bbccddeeff00"
ATTEMPT_ID = "aabbccdd-eeff-4111-8222-334455667788"


class FakeOutcome:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def to_dict(self) -> dict[str, Any]:
        return dict(self.value)


def _contract(root: Path) -> SimpleNamespace:
    outer_directory = root / "outer-aabbccdd"
    run_directory = root / "wsl-aabbccdd"
    qualifier_path = Path(outer.TRUSTED_SOURCE_CONTENT["qualification_script"]["path"])
    qualifier_raw = qualifier_path.read_bytes()
    return SimpleNamespace(
        qualification_id=QUALIFICATION_ID,
        run_uuid=RUN_UUID,
        attempt_id=ATTEMPT_ID,
        run_directory=run_directory,
        emergency_directory=root / "wsl-aabbccdd-emergency-seal",
        launch_authorization={"outer_evidence_directory": str(outer_directory)},
        raw={
            "source_pins": {
                "process_module": {"sha256": "1" * 64},
                "qualification_script": {
                    "path": str(qualifier_path),
                    "sha256": hashlib.sha256(qualifier_raw).hexdigest(),
                    "bytes": len(qualifier_raw),
                    "git_head_blob_oid": outer.TRUSTED_SOURCE_CONTENT["qualification_script"][
                        "git_head_blob_oid"
                    ],
                },
            }
        },
        host_binaries={"python": SimpleNamespace(path=r"C:\trusted\python.exe")},
    )


def _expected_inner_attestation(contract: SimpleNamespace, command: Any) -> dict[str, Any]:
    qualifier_pin = contract.raw["source_pins"]["qualification_script"]
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1",
        "qualifier_path": qualifier_pin["path"],
        "qualifier_sha256": qualifier_pin["sha256"],
        "qualifier_bytes": qualifier_pin["bytes"],
        "qualifier_lf_sha256": outer.TRUSTED_SOURCE_CONTENT["qualification_script"][
            "lf_normalized_sha256"
        ],
        "qualifier_blob_oid": qualifier_pin["git_head_blob_oid"],
        "inner_argv_sha256": hashlib.sha256(
            json.dumps(list(command[11:]), separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "bootstrap_source_sha256": outer.INNER_BOOTSTRAP_SOURCE_SHA256,
    }


def _inner_publication(contract: SimpleNamespace, *, passed: bool, command: Any) -> dict[str, Any]:
    contract.run_directory.mkdir()
    inner_attestation = _expected_inner_attestation(contract, command)
    if passed:
        evidence = qualifier._atomic_exclusive_json(
            contract.run_directory / "qualification-evidence.json",
            {
                "schema": outer.QUALIFICATION_EVIDENCE_SCHEMA,
                "qualification_id": QUALIFICATION_ID,
                "run_uuid": RUN_UUID,
                "attempt_id": ATTEMPT_ID,
                "analysis": {"passed": True},
                "inner_bootstrap_attestation": inner_attestation,
            },
        )
        index = qualifier._atomic_exclusive_json(
            contract.run_directory / "qualification-index.json",
            {
                "schema": outer.QUALIFICATION_INDEX_SCHEMA,
                "qualification_id": QUALIFICATION_ID,
                "run_uuid": RUN_UUID,
                "attempt_id": ATTEMPT_ID,
                "status": "qualified_non_credit",
                "credit": "non_credit_only",
                "report": evidence,
                "failure_seal": None,
                "completion_marker_created": False,
                "private_phase_b2_success_index_created": False,
                "r8_started": False,
            },
        )
        return {"evidence": evidence, "index": index, "passed": True}
    seal = qualifier._atomic_exclusive_json(
        contract.run_directory / "failure-seal.json",
        {
            "schema": outer.QUALIFICATION_FAILURE_SCHEMA,
            "qualification_id": QUALIFICATION_ID,
            "run_uuid": RUN_UUID,
            "attempt_id": ATTEMPT_ID,
            "status": "manual_intervention_required",
            "credit": "zero_credit",
            "partial_evidence": {"inner_bootstrap_attestation": inner_attestation},
            "completion_marker_created": False,
            "r8_started": False,
        },
    )
    index = qualifier._atomic_exclusive_json(
        contract.run_directory / "failure-index.json",
        {
            "schema": outer.QUALIFICATION_INDEX_SCHEMA,
            "qualification_id": QUALIFICATION_ID,
            "run_uuid": RUN_UUID,
            "attempt_id": ATTEMPT_ID,
            "status": "zero_credit_failure",
            "credit": "zero_credit",
            "report": seal,
            "failure_seal": seal,
            "completion_marker_created": False,
            "private_phase_b2_success_index_created": False,
            "r8_started": False,
        },
    )
    return {"failure_seal": seal, "index": index, "passed": False}


def _analysis_failure_publication(
    contract: SimpleNamespace, *, passed: bool, command: Any
) -> dict[str, Any]:
    assert passed is False
    contract.run_directory.mkdir()
    inner_attestation = _expected_inner_attestation(contract, command)
    evidence = qualifier._atomic_exclusive_json(
        contract.run_directory / "failure-evidence.json",
        {
            "schema": outer.QUALIFICATION_EVIDENCE_SCHEMA,
            "qualification_id": QUALIFICATION_ID,
            "run_uuid": RUN_UUID,
            "attempt_id": ATTEMPT_ID,
            "analysis": {"passed": False, "errors": ["synthetic_analysis_no_go"]},
            "inner_bootstrap_attestation": inner_attestation,
        },
    )
    seal = qualifier._atomic_exclusive_json(
        contract.run_directory / "failure-seal.json",
        {
            "schema": outer.QUALIFICATION_FAILURE_SCHEMA,
            "qualification_id": QUALIFICATION_ID,
            "run_uuid": RUN_UUID,
            "attempt_id": ATTEMPT_ID,
            "status": "manual_intervention_required",
            "credit": "zero_credit",
            "failure_evidence": evidence,
            "partial_evidence": None,
            "completion_marker_created": False,
            "r8_started": False,
        },
    )
    index = qualifier._atomic_exclusive_json(
        contract.run_directory / "failure-index.json",
        {
            "schema": outer.QUALIFICATION_INDEX_SCHEMA,
            "qualification_id": QUALIFICATION_ID,
            "run_uuid": RUN_UUID,
            "attempt_id": ATTEMPT_ID,
            "status": "zero_credit_failure",
            "credit": "zero_credit",
            "report": evidence,
            "failure_seal": seal,
            "completion_marker_created": False,
            "private_phase_b2_success_index_created": False,
            "r8_started": False,
        },
    )
    return {"evidence": evidence, "failure_seal": seal, "index": index, "passed": False}


def _success_job_evidence(command: Any) -> dict[str, Any]:
    names = [
        "job_created",
        "root_created_suspended",
        "job_membership_verified",
        "identity_observed",
        "root_resumed",
        "active_process_count_zero",
    ]
    events = []
    for sequence, event in enumerate(names, 1):
        details: dict[str, Any] = {}
        if event == "job_created":
            details = {"run_uuid": RUN_UUID}
        elif event == "job_membership_verified":
            details = {"active_processes": 1, "job_limit_flags": 0}
        events.append(
            {
                "sequence": sequence,
                "event": event,
                "monotonic_ns": sequence,
                "timestamp_utc": "2026-09-01T12:00:01+00:00",
                "pid": 1000 if event in names[1:5] else None,
                "details": details,
            }
        )
    accounting = [
        {
            "sequence": 7,
            "monotonic_ns": 7,
            "timestamp_utc": "2026-09-01T12:00:02+00:00",
            "total_processes": 1,
            "active_processes": 0,
            "total_terminated_processes": 0,
            "active_pids": [],
        },
        {
            "sequence": 8,
            "monotonic_ns": 8,
            "timestamp_utc": "2026-09-01T12:00:02+00:00",
            "total_processes": 1,
            "active_processes": 0,
            "total_terminated_processes": 0,
            "active_pids": [],
        },
    ]
    events.append(
        {
            "sequence": 9,
            "event": "streams_drained",
            "monotonic_ns": 9,
            "timestamp_utc": "2026-09-01T12:00:02+00:00",
            "pid": None,
            "details": {},
        }
    )
    return {
        "events": events,
        "accounting": accounting,
        "identities": [
            {
                "pid": 1000,
                "ppid": 999,
                "creation_time_ns": 1,
                "creation_time_utc": "2026-09-01T12:00:01+00:00",
                "image": str(command[0]),
                "run_uuid": RUN_UUID,
                "observed_sequence": 4,
            }
        ],
    }


def _process(result: dict[str, Any], return_code: int, command: Any) -> dict[str, Any]:
    return {
        "name": "pre-r8-r7s2-qualification-inner-exactly-once",
        "run_uuid": RUN_UUID,
        "command": list(command),
        "started_at_utc": "2026-09-01T12:00:01+00:00",
        "ended_at_utc": "2026-09-01T12:00:02+00:00",
        "duration_seconds": 1.0,
        "safe_for_followup": return_code == 0,
        "forced_termination_attempts": 0,
        "active_process_zero": True,
        "final_active_process_count": 0,
        "job_limit_flags": 0,
        "cancelled": False,
        "manual_intervention_required": False,
        "streams_drained": True,
        "stdout_drained": True,
        "stderr_drained": True,
        "identity_coverage_complete": True,
        "timed_out": False,
        "residual_pids": [],
        "return_code": return_code,
        "stdout": json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        "stderr": "",
        **_success_job_evidence(command),
        "errors": [],
    }


def _install_execution_fakes(
    monkeypatch: pytest.MonkeyPatch,
    contract: SimpleNamespace,
    *,
    passed: bool,
    mutate_process: Any = None,
    publication_factory: Any = None,
) -> tuple[list[tuple[str, ...]], list[dict[str, Any]]]:
    monkeypatch.setattr(outer, "R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED", True)
    commands: list[tuple[str, ...]] = []
    timeout_calls: list[dict[str, Any]] = []
    token = {
        "administrator": True,
        "integrity": "High",
        "token_elevation_type": "Full",
        "token_elevation_type_value": 2,
    }

    class Runner:
        def __init__(self, timeout: Any) -> None:
            assert timeout == "timeout-contract"

        def run(self, command: Any, **_kwargs: Any) -> FakeOutcome:
            commands.append(tuple(command))
            factory = publication_factory or _inner_publication
            result = factory(contract, passed=passed, command=command)
            process = _process(result, 0 if passed else 3, command)
            if mutate_process is not None:
                mutate_process(process)
            return FakeOutcome(process)

    def make_contract(**kwargs: Any) -> str:
        timeout_calls.append(dict(kwargs))
        return "timeout-contract"

    module = SimpleNamespace(
        _process_creation_filetime=lambda _pid: 123456789,
        _make_contract=make_contract,
        _validate_success_job_timeline=qualifier._validate_success_job_timeline,
        WindowsJobProcessRunner=Runner,
    )
    monkeypatch.setattr(
        outer,
        "_load_verified_qualifier",
        lambda *_args, **_kwargs: (
            module,
            contract,
            token,
            {},
            {"schema": outer.BOOTSTRAP_ATTESTATION_SCHEMA, "test": "verified"},
        ),
    )
    return commands, timeout_calls


def _execute(root: Path) -> dict[str, Any]:
    return outer.execute_once(
        contract_path=root / "c-aabbccdd" / "qualification-contract.json",
        expected_contract_sha256="2" * 64,
        launch_index_path=root / "c-aabbccdd" / "staging-index.json",
        expected_launch_index_sha256="3" * 64,
        expected_outer_sha256="4" * 64,
        expected_source_commit="5" * 40,
        expected_source_tree="6" * 40,
    )


def test_direct_outer_execution_without_verified_bootstrap_is_rejected() -> None:
    with pytest.raises(outer.R7S2OuterError, match="verified_bootstrap_attestation_required"):
        outer._require_bootstrap_attestation(
            contract_path=Path("contract.json"),
            expected_contract_sha256="1" * 64,
            launch_index_path=Path("index.json"),
            expected_launch_index_sha256="2" * 64,
            expected_outer_sha256="3" * 64,
            expected_source_commit="4" * 40,
            expected_source_tree="5" * 40,
        )


def test_outer_bootstrap_attestation_rejects_source_argv_replay_and_domain_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outer_path = Path(outer.__file__)
    raw = outer_path.read_bytes()
    normalized = outer._lf_normalized_source(raw)
    contract_path = Path("contract.json")
    launch_path = Path("staging-index.json")
    contract_sha = "1" * 64
    launch_sha = "2" * 64
    source_commit = "3" * 40
    source_tree = "4" * 40
    outer_sha = hashlib.sha256(raw).hexdigest()
    outer_args = [
        "--contract",
        str(contract_path),
        "--expected-contract-sha256",
        contract_sha,
        "--launch-index",
        str(launch_path),
        "--expected-launch-index-sha256",
        launch_sha,
        "--expected-outer-sha256",
        outer_sha,
        "--expected-source-commit",
        source_commit,
        "--expected-source-tree",
        source_tree,
        "--execute-non-credit-once",
    ]
    valid = {
        "schema": outer.BOOTSTRAP_ATTESTATION_SCHEMA,
        "outer_path": str(outer_path),
        "outer_raw_sha256": outer_sha,
        "outer_bytes": len(raw),
        "outer_lf_normalized_sha256": hashlib.sha256(normalized).hexdigest(),
        "contract_path": str(contract_path),
        "contract_sha256": contract_sha,
        "launch_index_path": str(launch_path),
        "launch_index_sha256": launch_sha,
        "expected_source_commit": source_commit,
        "expected_source_tree": source_tree,
        "outer_argv_sha256": hashlib.sha256(
            json.dumps(outer_args, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "python_identity": {
            "path": r"C:\Users\opop0\miniconda3\python.exe",
            "sha256": "ec0ea8d6907787b76dcf8524aaa93e52e167ceee62fa8778e182ea637a3dbc1d",
            "bytes": 104264,
            "version": "3.13.11",
        },
        "bootstrap_source_sha256": outer.TRUSTED_BOOTSTRAP_SOURCE_SHA256,
    }
    monkeypatch.setattr(
        outer.sys,
        "orig_argv",
        [sys.executable, "-I", "-S", "-B", "-c", stager.OUTER_BOOTSTRAP_SOURCE],
    )
    monkeypatch.setattr(outer.sys, "argv", [str(outer_path), *outer_args])
    monkeypatch.setitem(outer.__dict__, "__evm_r7s2_bootstrap_attestation__", valid)
    assert (
        outer._require_bootstrap_attestation(
            contract_path=contract_path,
            expected_contract_sha256=contract_sha,
            launch_index_path=launch_path,
            expected_launch_index_sha256=launch_sha,
            expected_outer_sha256=outer_sha,
            expected_source_commit=source_commit,
            expected_source_tree=source_tree,
        )
        == valid
    )

    for mutated in (
        {**valid, "bootstrap_source_sha256": "f" * 64},
        {**valid, "outer_argv_sha256": "e" * 64},
        {"schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1"},
    ):
        monkeypatch.setitem(outer.__dict__, "__evm_r7s2_bootstrap_attestation__", mutated)
        with pytest.raises(outer.R7S2OuterError):
            outer._require_bootstrap_attestation(
                contract_path=contract_path,
                expected_contract_sha256=contract_sha,
                launch_index_path=launch_path,
                expected_launch_index_sha256=launch_sha,
                expected_outer_sha256=outer_sha,
                expected_source_commit=source_commit,
                expected_source_tree=source_tree,
            )

    monkeypatch.setitem(outer.__dict__, "__evm_r7s2_bootstrap_attestation__", copy.deepcopy(valid))
    monkeypatch.setattr(outer.sys, "argv", [str(outer_path), *outer_args, "--replayed"])
    with pytest.raises(outer.R7S2OuterError):
        outer._require_bootstrap_attestation(
            contract_path=contract_path,
            expected_contract_sha256=contract_sha,
            launch_index_path=launch_path,
            expected_launch_index_sha256=launch_sha,
            expected_outer_sha256=outer_sha,
            expected_source_commit=source_commit,
            expected_source_tree=source_tree,
        )


def test_complete_individually_valid_bootstrap_attestations_cannot_swap_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_path = Path("contract.json")
    launch_path = Path("staging-index.json")
    contract_sha = "1" * 64
    launch_sha = "2" * 64
    source_commit = "3" * 40
    source_tree = "4" * 40

    outer_path = Path(outer.__file__)
    outer_raw = outer_path.read_bytes()
    outer_lf = outer._lf_normalized_source(outer_raw)
    outer_sha = hashlib.sha256(outer_raw).hexdigest()
    outer_args = [
        "--contract",
        str(contract_path),
        "--expected-contract-sha256",
        contract_sha,
        "--launch-index",
        str(launch_path),
        "--expected-launch-index-sha256",
        launch_sha,
        "--expected-outer-sha256",
        outer_sha,
        "--expected-source-commit",
        source_commit,
        "--expected-source-tree",
        source_tree,
        "--execute-non-credit-once",
    ]
    outer_attestation = {
        "schema": outer.BOOTSTRAP_ATTESTATION_SCHEMA,
        "outer_path": str(outer_path),
        "outer_raw_sha256": outer_sha,
        "outer_bytes": len(outer_raw),
        "outer_lf_normalized_sha256": hashlib.sha256(outer_lf).hexdigest(),
        "contract_path": str(contract_path),
        "contract_sha256": contract_sha,
        "launch_index_path": str(launch_path),
        "launch_index_sha256": launch_sha,
        "expected_source_commit": source_commit,
        "expected_source_tree": source_tree,
        "outer_argv_sha256": hashlib.sha256(
            json.dumps(outer_args, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "python_identity": {
            "path": r"C:\Users\opop0\miniconda3\python.exe",
            "sha256": "ec0ea8d6907787b76dcf8524aaa93e52e167ceee62fa8778e182ea637a3dbc1d",
            "bytes": 104264,
            "version": "3.13.11",
        },
        "bootstrap_source_sha256": outer.TRUSTED_BOOTSTRAP_SOURCE_SHA256,
    }

    stager_path = Path(stager.__file__)
    stager_raw = stager_path.read_bytes()
    stager_lf = stager._lf_normalized_source(stager_raw)
    stager_sha = hashlib.sha256(stager_raw).hexdigest()
    stager_args = [
        "--qualification-id",
        QUALIFICATION_ID,
        "--run-uuid",
        RUN_UUID,
        "--attempt-id",
        ATTEMPT_ID,
        "--expected-source-commit",
        source_commit,
        "--expected-source-tree",
        source_tree,
        "--expected-qualification-sha256",
        "5" * 64,
        "--expected-process-module-sha256",
        "6" * 64,
        "--expected-r7s1-runner-sha256",
        "7" * 64,
        "--expected-stager-sha256",
        stager_sha,
        "--expected-outer-sha256",
        outer_sha,
        "--parent-map",
        "parent-map.json",
        "--expected-parent-map-sha256",
        "8" * 64,
        "--execute-stage-non-credit-once",
    ]
    stager_attestation = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.stager-bootstrap-attestation.v1",
        "stager_path": str(stager_path),
        "stager_sha256": stager_sha,
        "stager_bytes": len(stager_raw),
        "stager_lf_sha256": hashlib.sha256(stager_lf).hexdigest(),
        "stager_blob_oid": stager._git_blob_oid(stager_lf),
        "stager_argv_sha256": hashlib.sha256(
            json.dumps(stager_args, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "bootstrap_source_sha256": stager.STAGER_BOOTSTRAP_SOURCE_SHA256,
    }

    qualifier_path = Path(qualifier.__file__)
    qualifier_raw = qualifier_path.read_bytes()
    qualifier_lf = qualifier._lf_normalized_source(qualifier_raw)
    inner_args = [
        "--contract",
        str(contract_path),
        "--expected-contract-sha256",
        contract_sha,
        "--launch-index",
        str(launch_path),
        "--expected-launch-index-sha256",
        launch_sha,
        "--outer-reservation",
        "outer-reservation.json",
        "--expected-outer-reservation-sha256",
        "9" * 64,
        "--execute-non-credit-once",
    ]
    inner_attestation = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1",
        "qualifier_path": str(qualifier_path),
        "qualifier_sha256": hashlib.sha256(qualifier_raw).hexdigest(),
        "qualifier_bytes": len(qualifier_raw),
        "qualifier_lf_sha256": hashlib.sha256(qualifier_lf).hexdigest(),
        "qualifier_blob_oid": qualifier._git_blob_oid(qualifier_lf),
        "inner_argv_sha256": hashlib.sha256(
            json.dumps(inner_args, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "bootstrap_source_sha256": qualifier.INNER_BOOTSTRAP_SOURCE_SHA256,
    }

    def select(module: Any, source: str, argv: list[str], key: str, value: dict[str, Any]) -> None:
        monkeypatch.setattr(
            module.sys, "orig_argv", [sys.executable, "-I", "-S", "-B", "-c", source]
        )
        monkeypatch.setattr(module.sys, "argv", argv)
        monkeypatch.setitem(module.__dict__, key, copy.deepcopy(value))

    select(
        outer,
        stager.OUTER_BOOTSTRAP_SOURCE,
        [str(outer_path), *outer_args],
        "__evm_r7s2_bootstrap_attestation__",
        outer_attestation,
    )
    assert (
        outer._require_bootstrap_attestation(
            contract_path=contract_path,
            expected_contract_sha256=contract_sha,
            launch_index_path=launch_path,
            expected_launch_index_sha256=launch_sha,
            expected_outer_sha256=outer_sha,
            expected_source_commit=source_commit,
            expected_source_tree=source_tree,
        )
        == outer_attestation
    )
    select(
        stager,
        stager.STAGER_BOOTSTRAP_SOURCE,
        [str(stager_path), *stager_args],
        "__evm_r7s2_stager_bootstrap_attestation__",
        stager_attestation,
    )
    assert stager._require_stager_bootstrap_attestation() == stager_attestation
    select(
        qualifier,
        outer.INNER_BOOTSTRAP_SOURCE,
        [str(qualifier_path), *inner_args],
        "__evm_r7s2_inner_bootstrap_attestation__",
        inner_attestation,
    )
    assert qualifier._require_inner_bootstrap_attestation() == inner_attestation

    select(
        outer,
        stager.OUTER_BOOTSTRAP_SOURCE,
        [str(outer_path), *outer_args],
        "__evm_r7s2_bootstrap_attestation__",
        inner_attestation,
    )
    with pytest.raises(outer.R7S2OuterError, match="bootstrap_attestation_fields_mismatch"):
        outer._require_bootstrap_attestation(
            contract_path=contract_path,
            expected_contract_sha256=contract_sha,
            launch_index_path=launch_path,
            expected_launch_index_sha256=launch_sha,
            expected_outer_sha256=outer_sha,
            expected_source_commit=source_commit,
            expected_source_tree=source_tree,
        )
    select(
        stager,
        stager.STAGER_BOOTSTRAP_SOURCE,
        [str(stager_path), *stager_args],
        "__evm_r7s2_stager_bootstrap_attestation__",
        outer_attestation,
    )
    with pytest.raises(stager.R7S2StagerError):
        stager._require_stager_bootstrap_attestation()
    select(
        qualifier,
        outer.INNER_BOOTSTRAP_SOURCE,
        [str(qualifier_path), *inner_args],
        "__evm_r7s2_inner_bootstrap_attestation__",
        stager_attestation,
    )
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier._require_inner_bootstrap_attestation()


def test_inner_bootstrap_executes_only_the_single_verified_buffer_after_source_swap(
    tmp_path: Path,
) -> None:
    fake_qualifier = tmp_path / "verified-qualifier.py"
    good = tmp_path / "verified-inner-buffer-executed.txt"
    unverified = tmp_path / "post-verify-inner-swap-executed.txt"
    good_source = (
        "import json\nfrom pathlib import Path\n"
        f"Path({str(good)!r}).write_text('good', encoding='utf-8')\n"
        "print(json.dumps(__evm_r7s2_inner_bootstrap_attestation__, "
        "sort_keys=True, separators=(',', ':')))\n"
    )
    malicious_source = (
        f"from pathlib import Path\nPath({str(unverified)!r}).write_text('bad', encoding='utf-8')\n"
    )
    fake_qualifier.write_text(malicious_source, encoding="utf-8", newline="\n")
    raw = good_source.encode("utf-8")
    normalized = outer._lf_normalized_source(raw)
    inner_args = [
        "--contract",
        "contract.json",
        "--expected-contract-sha256",
        "1" * 64,
        "--launch-index",
        "staging-index.json",
        "--expected-launch-index-sha256",
        "2" * 64,
        "--outer-reservation",
        "outer-reservation.json",
        "--expected-outer-reservation-sha256",
        "3" * 64,
        "--execute-non-credit-once",
    ]
    qualifier_pin = {
        "path": str(fake_qualifier),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "git_head_blob_oid": outer._git_blob_oid(normalized),
    }
    bootstrap_args = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        outer.INNER_BOOTSTRAP_SOURCE,
        qualifier_pin["path"],
        qualifier_pin["sha256"],
        str(qualifier_pin["bytes"]),
        hashlib.sha256(normalized).hexdigest(),
        qualifier_pin["git_head_blob_oid"],
        *inner_args,
    ]
    trusted = outer.TRUSTED_SOURCE_CONTENT["qualification_script"]
    original_lf = trusted["lf_normalized_sha256"]
    original_blob = trusted["git_head_blob_oid"]
    trusted["lf_normalized_sha256"] = hashlib.sha256(normalized).hexdigest()
    trusted["git_head_blob_oid"] = qualifier_pin["git_head_blob_oid"]
    try:
        expected_attestation = outer._expected_inner_bootstrap_attestation(
            bootstrap_args, qualifier_pin
        )
    finally:
        trusted["lf_normalized_sha256"] = original_lf
        trusted["git_head_blob_oid"] = original_blob
    harness = (
        "import builtins,io,os,sys\n"
        f"bootstrap={outer.INNER_BOOTSTRAP_SOURCE!r}\n"
        f"good={raw!r}\n"
        f"bad={malicious_source.encode('utf-8')!r}\n"
        "target=os.path.normcase(os.path.abspath(sys.argv[1]))\n"
        "sys.orig_argv=[sys.executable,'-I','-S','-B','-c',bootstrap,*sys.argv[1:]]\n"
        "real_open=builtins.open\n"
        "target_reads=0\n"
        "def controlled_open(path,mode='r',*args,**kwargs):\n"
        " global target_reads\n"
        " candidate=os.path.normcase(os.path.abspath(os.fspath(path))) if isinstance(path,(str,bytes,os.PathLike)) else None\n"
        " if candidate==target and mode=='rb':\n"
        "  target_reads+=1\n"
        "  return io.BytesIO(good if target_reads==1 else bad)\n"
        " return real_open(path,mode,*args,**kwargs)\n"
        "builtins.open=controlled_open\n"
        "try:\n"
        " exec(compile(bootstrap,'<trusted-inner-bootstrap>','exec'),globals(),globals())\n"
        "finally:\n"
        " builtins.open=real_open\n"
        "print('TARGET_READS='+str(target_reads))\n"
    )
    command = [*bootstrap_args[:5], harness, *bootstrap_args[6:]]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert lines[-1] == "TARGET_READS=1"
    observed_attestation = json.loads(lines[0])
    assert observed_attestation == expected_attestation
    assert (
        observed_attestation["inner_argv_sha256"]
        == hashlib.sha256(json.dumps(inner_args, separators=(",", ":")).encode("utf-8")).hexdigest()
    )
    assert (
        observed_attestation["inner_argv_sha256"]
        != hashlib.sha256(
            json.dumps(
                [qualifier_pin["git_head_blob_oid"], *inner_args], separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
    )
    assert good.read_text(encoding="utf-8") == "good"
    assert not unverified.exists()


def test_production_outer_is_root_anchor_blocked_before_loader(tmp_path: Path) -> None:
    assert outer.R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED is False
    with pytest.raises(outer.R7S2OuterError, match="r7s2_out_of_band_root_anchor_required"):
        _execute(tmp_path)


def test_outer_timeout_budget_is_exact_and_arithmetically_bound() -> None:
    assert outer._validated_outer_timeout_contract(outer.OUTER_TIMEOUT_CONTRACT) == (
        outer.OUTER_TIMEOUT_CONTRACT
    )
    mutated = dict(outer.OUTER_TIMEOUT_CONTRACT)
    mutated["outer_wrapper_seconds"] = 307
    with pytest.raises(outer.R7S2OuterError, match="exact_mismatch"):
        outer._validated_outer_timeout_contract(mutated)
    assert 298 < 308 < 358


@pytest.mark.parametrize(
    "wire",
    [
        '{"passed":true,"passed":true}\n',
        '{"value":NaN}\n',
        ' {"passed":true}\n',
        '{"passed":true}\n\n',
        '{"passed":"\ufffd"}\n',
    ],
)
def test_inner_result_wire_rejects_noncanonical_json(wire: str) -> None:
    with pytest.raises(outer.R7S2OuterError):
        outer._strict_canonical_json_line(wire, "test_inner")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"same":1,"same":1}\n',
        b'{"value":NaN}\n',
        b'{"value":1}\ntrailing',
        b'{"value":"\xff"}\n',
        b'{ "value":1}\n',
    ],
)
def test_outer_root_json_loader_rejects_ambiguous_or_noncanonical(
    tmp_path: Path, raw: bytes
) -> None:
    path = tmp_path / "owned.json"
    path.write_bytes(raw)
    with pytest.raises(outer.R7S2OuterError):
        outer._read_json_once(
            path,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            label="test_root",
            terminal_newline=True,
        )


def test_partial_inventory_rejects_directory_injection(tmp_path: Path) -> None:
    (tmp_path / "injected-directory").mkdir()
    with pytest.raises(outer.R7S2OuterError, match="outer_partial_non_file_forbidden"):
        outer._partial_inventory(tmp_path)


@pytest.mark.parametrize("passed", [True, False])
def test_outer_binds_reservation_exact_once_and_reads_back_typed_inner_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, passed: bool
) -> None:
    contract = _contract(tmp_path)
    commands, timeout_calls = _install_execution_fakes(monkeypatch, contract, passed=passed)
    result = _execute(tmp_path)
    assert result["passed"] is passed
    assert len(commands) == 1
    command = commands[0]
    assert command.count("--outer-reservation") == 1
    assert command.count("--expected-outer-reservation-sha256") == 1
    assert command.count("--execute-non-credit-once") == 1
    assert timeout_calls == [
        {
            "wrapper": 308,
            "residual": 20,
            "stream": 10,
            "restore_padding": 20,
        }
    ]
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    if passed:
        assert (outer_directory / "outer-report.json").is_file()
        assert (outer_directory / "outer-index.json").is_file()
        assert not (outer_directory / "outer-failure-seal.json").exists()
    else:
        assert (outer_directory / "outer-failure-seal.json").is_file()
        assert (outer_directory / "outer-failure-index.json").is_file()
        assert not (outer_directory / "outer-report.json").exists()


def test_outer_accepts_real_analysis_failure_shape_with_referenced_evidence_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    commands, _timeouts = _install_execution_fakes(
        monkeypatch,
        contract,
        passed=False,
        publication_factory=_analysis_failure_publication,
    )
    result = _execute(tmp_path)
    assert result["passed"] is False
    assert len(commands) == 1
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    seal = json.loads((outer_directory / "outer-failure-seal.json").read_text("utf-8"))
    assert seal["validated_inner_result"]["kind"] == "failure_zero_credit"
    assert seal["validated_inner_result"]["evidence"]["path"].endswith("failure-evidence.json")
    assert (outer_directory / "outer-failure-index.json").is_file()
    assert not (outer_directory / "outer-report.json").exists()


def test_concurrent_loser_is_rejected_without_child_or_emergency_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    commands, _timeouts = _install_execution_fakes(monkeypatch, contract, passed=True)
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    outer_directory.mkdir()
    marker = outer_directory / "winner-marker.bin"
    marker.write_bytes(b"winner")
    with pytest.raises(
        (FileExistsError, outer.R7S2OuterError), match="outer-aabbccdd|rejected_no_write"
    ):
        _execute(tmp_path)
    assert commands == []
    assert marker.read_bytes() == b"winner"
    assert not (tmp_path / "outer-aabbccdd-emergency-seal").exists()


def test_outer_rejects_actual_process_command_mutation_even_with_valid_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    _install_execution_fakes(
        monkeypatch,
        contract,
        passed=True,
        mutate_process=lambda process: process.__setitem__("command", ["forbidden.exe", "reset"]),
    )
    result = _execute(tmp_path)
    assert result["passed"] is False
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    assert (outer_directory / "outer-failure-seal.json").is_file()
    assert (outer_directory / "outer-failure-index.json").is_file()
    assert not (outer_directory / "outer-report.json").exists()


def test_outer_terminal_reread_rejects_artifact_swap_after_first_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    _install_execution_fakes(monkeypatch, contract, passed=True)
    real_validate = outer._validate_inner_result
    calls = 0

    def validate_then_swap(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        validated = real_validate(**kwargs)
        if calls == 1:
            evidence_path = contract.run_directory / "qualification-evidence.json"
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            payload["analysis"]["post_verify_swap"] = True
            evidence_path.write_bytes(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            )
        return validated

    monkeypatch.setattr(outer, "_validate_inner_result", validate_then_swap)
    result = _execute(tmp_path)
    assert result["passed"] is False
    assert calls == 2
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    assert (outer_directory / "outer-failure-seal.json").is_file()
    assert not (outer_directory / "outer-report.json").exists()
    assert not (outer_directory / "outer-index.json").exists()


def test_outer_rc3_terminal_reread_rejects_failure_artifact_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    _install_execution_fakes(
        monkeypatch,
        contract,
        passed=False,
        publication_factory=_analysis_failure_publication,
    )
    real_validate = outer._validate_inner_result
    calls = 0

    def validate_then_swap(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        validated = real_validate(**kwargs)
        if calls == 1:
            evidence_path = contract.run_directory / "failure-evidence.json"
            raw = evidence_path.read_bytes()
            evidence_path.write_bytes(raw + b" ")
        return validated

    monkeypatch.setattr(outer, "_validate_inner_result", validate_then_swap)
    result = _execute(tmp_path)
    assert result["passed"] is False
    assert calls == 2
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    seal = json.loads((outer_directory / "outer-failure-seal.json").read_text("utf-8"))
    assert seal["failed_stage"] == "outer_inner_qualification"
    assert not (outer_directory / "outer-report.json").exists()


@pytest.mark.parametrize("mutation", ["bootstrap_source", "replayed_argv", "domain_swap"])
def test_self_consistent_inner_attestation_publication_mutation_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    contract = _contract(tmp_path)
    real_publication = _inner_publication

    def forged_publication(
        current_contract: SimpleNamespace, *, passed: bool, command: Any
    ) -> dict[str, Any]:
        publication = real_publication(current_contract, passed=passed, command=command)
        evidence_path = current_contract.run_directory / "qualification-evidence.json"
        index_path = current_contract.run_directory / "qualification-index.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        attestation = evidence["inner_bootstrap_attestation"]
        if mutation == "bootstrap_source":
            attestation["bootstrap_source_sha256"] = "f" * 64
        elif mutation == "replayed_argv":
            attestation["inner_argv_sha256"] = hashlib.sha256(
                b'["--contract","replayed.json","--execute-non-credit-once"]'
            ).hexdigest()
        else:
            evidence["inner_bootstrap_attestation"] = {
                "schema": outer.BOOTSTRAP_ATTESTATION_SCHEMA,
                "outer_path": "domain-swapped",
            }
        evidence_raw = json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        evidence_path.write_bytes(evidence_raw)
        evidence_pin = {
            "path": str(evidence_path.resolve()),
            "sha256": hashlib.sha256(evidence_raw).hexdigest(),
            "bytes": len(evidence_raw),
        }
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["report"] = evidence_pin
        index_raw = json.dumps(
            index,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        index_path.write_bytes(index_raw)
        publication["evidence"] = evidence_pin
        publication["index"] = {
            "path": str(index_path.resolve()),
            "sha256": hashlib.sha256(index_raw).hexdigest(),
            "bytes": len(index_raw),
        }
        return publication

    monkeypatch.setattr(sys.modules[__name__], "_inner_publication", forged_publication)
    _install_execution_fakes(monkeypatch, contract, passed=True)
    result = _execute(tmp_path)
    assert result["passed"] is False
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    assert (outer_directory / "outer-failure-seal.json").is_file()
    assert not (outer_directory / "outer-report.json").exists()


@pytest.mark.parametrize(
    "mutation", ["event_reorder", "sequence_duplicate", "final_nonzero", "limit_terminated"]
)
def test_outer_rejects_inner_job_timeline_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    contract = _contract(tmp_path)

    def mutate(process: dict[str, Any]) -> None:
        if mutation == "event_reorder":
            process["events"][1], process["events"][2] = (
                process["events"][2],
                process["events"][1],
            )
        elif mutation == "sequence_duplicate":
            process["accounting"][0]["sequence"] = 6
        elif mutation == "final_nonzero":
            process["accounting"][-1]["active_processes"] = 1
            process["accounting"][-1]["active_pids"] = [1000]
        else:
            process["accounting"][-1]["total_terminated_processes"] = 1

    _install_execution_fakes(monkeypatch, contract, passed=True, mutate_process=mutate)
    result = _execute(tmp_path)
    assert result["passed"] is False
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    assert (outer_directory / "outer-failure-seal.json").is_file()
    assert not (outer_directory / "outer-report.json").exists()


def test_generic_typed_process_failure_preserves_residual_evidence() -> None:
    class TypedFailure(RuntimeError):
        def to_dict(self) -> dict[str, Any]:
            return {
                "residual_pids": [77],
                "forced_termination_attempts": 0,
                "manual_intervention_required": True,
            }

    assert outer._typed_exception_evidence(TypedFailure("timeout")) == {
        "residual_pids": [77],
        "forced_termination_attempts": 0,
        "manual_intervention_required": True,
    }


def test_outer_failure_seal_writer_failure_uses_one_emergency_seal_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    _install_execution_fakes(monkeypatch, contract, passed=True)
    monkeypatch.setattr(
        outer,
        "_validate_inner_result",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic_validation_failure")),
    )
    real_publish = outer._atomic_exclusive_json
    failure_attempts = 0

    def fail_primary_seal(path: Path, value: dict[str, Any]) -> dict[str, Any]:
        nonlocal failure_attempts
        if path.name == "outer-failure-seal.json":
            failure_attempts += 1
            raise PermissionError("synthetic_outer_seal_permission_failure")
        return real_publish(path, value)

    monkeypatch.setattr(outer, "_atomic_exclusive_json", fail_primary_seal)
    result = _execute(tmp_path)
    assert result["passed"] is False
    assert failure_attempts == 1
    emergency = tmp_path / "outer-aabbccdd-emergency-seal" / "emergency-seal.json"
    assert emergency.is_file()
    payload = json.loads(emergency.read_text(encoding="utf-8"))
    assert payload["schema"] == outer.EMERGENCY_SCHEMA
    assert payload["automatic_retry_count"] == 0
    assert payload["forced_termination_attempts"] == 0


@pytest.mark.parametrize("module", [stager, qualifier, outer])
def test_movefile_failure_preserves_fsynced_temp_without_overwrite_or_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    attempts: list[tuple[str, str, int]] = []
    durability_events: list[tuple[str, int]] = []
    real_write = module.os.write
    real_fsync = module.os.fsync

    def tracked_write(descriptor: int, data: Any) -> int:
        written = real_write(descriptor, data)
        durability_events.append(("unbuffered_write_complete", descriptor))
        return written

    def tracked_fsync(descriptor: int) -> None:
        durability_events.append(("fsync", descriptor))
        real_fsync(descriptor)

    class FailingMove:
        argtypes: Any = None
        restype: Any = None

        def __call__(self, source: str, destination: str, flags: int) -> int:
            attempts.append((source, destination, flags))
            durability_events.append(("move_file_ex", -1))
            return 0

    move = FailingMove()
    kernel = SimpleNamespace(MoveFileExW=move)
    monkeypatch.setattr(module.os, "write", tracked_write)
    monkeypatch.setattr(module.os, "fsync", tracked_fsync)
    monkeypatch.setattr(module.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    monkeypatch.setattr(module.ctypes, "get_last_error", lambda: 5)
    final = tmp_path / f"{module.__name__.rsplit('.', 1)[-1]}.json"
    value = {"schema": "test.atomic-move-failure.v1", "value": 1}
    expected = module._canonical_json(value)
    with pytest.raises(OSError, match="atomic_no_replace_move_failed"):
        module._atomic_exclusive_json(final, value)
    partials = list(tmp_path.glob(".t-*.tmp"))
    assert len(attempts) == 1
    assert attempts[0][2] == 0
    names = [name for name, _descriptor in durability_events]
    assert names.count("fsync") == 1
    assert names.count("move_file_ex") == 1
    assert names[-1] == "move_file_ex"
    assert names.index("fsync") > max(
        index for index, name in enumerate(names) if name == "unbuffered_write_complete"
    )
    write_descriptors = {
        descriptor for name, descriptor in durability_events if name == "unbuffered_write_complete"
    }
    fsync_descriptors = {descriptor for name, descriptor in durability_events if name == "fsync"}
    assert len(write_descriptors) == 1
    assert fsync_descriptors == write_descriptors
    assert not final.exists()
    assert len(partials) == 1
    assert partials[0].read_bytes() == expected
    assert (
        hashlib.sha256(partials[0].read_bytes()).hexdigest() == hashlib.sha256(expected).hexdigest()
    )


def test_outer_report_move_failure_is_single_attempt_and_temp_is_in_failure_dag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    _install_execution_fakes(monkeypatch, contract, passed=True)
    report_attempts = 0

    class ConditionalMove:
        argtypes: Any = None
        restype: Any = None

        def __call__(self, source: str, destination: str, flags: int) -> int:
            nonlocal report_attempts
            if Path(destination).name == "outer-report.json":
                report_attempts += 1
                return 0
            os.rename(source, destination)
            return 1

    move = ConditionalMove()
    kernel = SimpleNamespace(MoveFileExW=move)
    monkeypatch.setattr(outer.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    monkeypatch.setattr(outer.ctypes, "get_last_error", lambda: 5)
    result = _execute(tmp_path)
    assert result["passed"] is False
    assert report_attempts == 1
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    seal = json.loads((outer_directory / "outer-failure-seal.json").read_text("utf-8"))
    partials = [item for item in seal["partial_inventory"] if item["name"].startswith(".t-")]
    assert len(partials) == 1
    partial_path = Path(partials[0]["path"])
    raw = partial_path.read_bytes()
    assert partials[0]["bytes"] == len(raw)
    assert partials[0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert seal["automatic_retry_count"] == 0
    assert not (outer_directory / "outer-report.json").exists()
    assert not (outer_directory / "outer-index.json").exists()


def test_primary_failure_seal_move_failure_preserves_temp_in_emergency_dag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    _install_execution_fakes(monkeypatch, contract, passed=True)
    monkeypatch.setattr(
        outer,
        "_validate_inner_result",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic_validation_failure")),
    )
    failure_seal_move_attempts = 0

    class ConditionalMove:
        argtypes: Any = None
        restype: Any = None

        def __call__(self, source: str, destination: str, flags: int) -> int:
            nonlocal failure_seal_move_attempts
            if Path(destination).name == "outer-failure-seal.json":
                failure_seal_move_attempts += 1
                return 0
            os.rename(source, destination)
            return 1

    kernel = SimpleNamespace(MoveFileExW=ConditionalMove())
    monkeypatch.setattr(outer.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    monkeypatch.setattr(outer.ctypes, "get_last_error", lambda: 5)
    result = _execute(tmp_path)
    assert result["passed"] is False
    assert failure_seal_move_attempts == 1
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    assert not (outer_directory / "outer-failure-seal.json").exists()
    assert not (outer_directory / "outer-failure-index.json").exists()
    emergency_path = tmp_path / "outer-aabbccdd-emergency-seal" / "emergency-seal.json"
    emergency = json.loads(emergency_path.read_text(encoding="utf-8"))
    partials = [item for item in emergency["partial_inventory"] if item["name"].startswith(".t-")]
    assert len(partials) == 1
    partial_path = Path(partials[0]["path"])
    raw = partial_path.read_bytes()
    assert partials[0]["bytes"] == len(raw)
    assert partials[0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert emergency["automatic_retry_count"] == 0
    assert emergency["forced_termination_attempts"] == 0
