from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from datetime import UTC, datetime, timedelta

import pytest

from scripts.dev import qualify_wsl_process_group_r7s2 as qualifier
from scripts.dev import invoke_wsl_process_group_r7s2 as outer_launcher
from scripts.dev import stage_wsl_process_group_r7s2 as contract_stager


PROJECT = Path(__file__).parents[1]
RUN_ID = "pre-r8-r7s2-wsl-20260901T140000Z-deadbeef"
RUN_UUID = "11111111-2222-4333-8444-555555555555"
ATTEMPT_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa"
EVIDENCE_LEAF = "wsl-66666666"
BOOT_ID = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
ADMIN_TOKEN = {
    "captured_at_utc": "2026-09-01T14:00:00+00:00",
    "pid": 100,
    "ppid": 50,
    "session_id": 1,
    "path": str(Path(sys.executable).resolve()),
    "path_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
    "administrator": True,
    "integrity": "High",
    "integrity_rid": 0x3000,
    "token_elevation_type": "Full",
    "token_elevation_type_value": 2,
}
GIT_IDENTITY = subprocess.check_output(
    [
        r"C:\Program Files\Git\mingw64\bin\git.exe",
        "-C",
        str(PROJECT.parent),
        "rev-parse",
        "HEAD",
        "HEAD^{tree}",
    ],
    text=True,
).splitlines()
SOURCE_COMMIT, SOURCE_TREE = GIT_IDENTITY


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_pin(path: Path, *, version: str = "test") -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "version": version,
    }


def _artifact_pin(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _write_owned_json(path: Path, value: Any) -> None:
    path.write_bytes(
        (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    )


def _json_line(value: Any) -> str:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def _success_job_evidence(command: list[str]) -> dict[str, Any]:
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
                "image": command[0],
                "run_uuid": RUN_UUID,
                "observed_sequence": 4,
            }
        ],
    }


def _stager_process_evidence(
    source_pins: dict[str, dict[str, Any]], stager_pin: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, int]]:
    all_sources = {**source_pins, "stager": stager_pin}
    roles = sorted(all_sources)
    git = qualifier.GIT_EXPECTATION["path"]
    wsl = qualifier.HOST_EXPECTATIONS["system32_wsl"]["path"]
    relative_paths = [all_sources[role]["relative_path"] for role in roles]
    common = [git, "-c", "core.fsmonitor=false", "-c", "core.autocrlf=true"]
    commands: dict[str, list[str]] = {
        "git_identity": [
            git,
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(qualifier.GIT_ROOT),
            "rev-parse",
            "HEAD",
            "HEAD^{tree}",
        ],
        "git_status": [
            git,
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(qualifier.GIT_ROOT),
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
        ],
        "git_source_ls_tree": [
            *common,
            "-C",
            str(qualifier.GIT_ROOT),
            "ls-tree",
            "-rz",
            "--full-tree",
            SOURCE_COMMIT,
            "--",
            *relative_paths,
        ],
        "git_source_ls_files": [
            *common,
            "-C",
            str(qualifier.GIT_ROOT),
            "ls-files",
            "-vz",
            "--stage",
            "--",
            *relative_paths,
        ],
        "ubuntu_verbose_pre": [wsl, "--list", "--verbose"],
        "ubuntu_running_pre": [wsl, "--list", "--running", "--quiet"],
        "ubuntu_verbose_post": [wsl, "--list", "--verbose"],
        "ubuntu_running_post": [wsl, "--list", "--running", "--quiet"],
        "linux_identity_readback": [
            wsl,
            "--distribution",
            "Ubuntu",
            "--cd",
            "/",
            "--exec",
            "/usr/bin/env",
            "-i",
            "LANG=C.UTF-8",
            "LC_ALL=C.UTF-8",
            f"EVM_PHASE_B2_RUN_UUID={RUN_UUID}",
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            "-c",
            contract_stager.LINUX_DISCOVERY_SOURCE,
            "Ubuntu",
            "/usr/bin/env",
            "/usr/bin/setsid",
        ],
    }
    for role in roles:
        commands[f"git_source_hash_object_{role}"] = [
            *common,
            "-C",
            str(qualifier.GIT_ROOT),
            "hash-object",
            f"--path={all_sources[role]['relative_path']}",
            all_sources[role]["path"],
        ]
    git_keys = sorted(
        {
            "SystemRoot",
            "WINDIR",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_TERMINAL_PROMPT",
            "GCM_INTERACTIVE",
            "GIT_OPTIONAL_LOCKS",
            "GIT_NO_REPLACE_OBJECTS",
            "GIT_ATTR_NOSYSTEM",
            "LC_ALL",
        }
    )
    wsl_keys = ["SystemRoot", "WINDIR", "WSL_UTF8"]
    linux_raw = {"python3": b"python3", "env": b"env", "setsid": b"setsid"}
    linux_stdout = (
        json.dumps(
            {
                "schema": qualifier.LINUX_DISCOVERY_SCHEMA,
                "status": "observed",
                "distro": "Ubuntu",
                "kernel_release": "test-kernel",
                "distro_version": "Ubuntu test",
                "boot_id": BOOT_ID,
                "rootfs_identity": "4" * 64,
                "os_release_sha256": "1" * 64,
                "machine_id_sha256": "2" * 64,
                "binaries": {
                    role: {
                        "candidate_path": f"/usr/bin/{role}",
                        "realpath": f"/usr/bin/{role}",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw),
                        "version": "3.13.7" if role == "python3" else "test-version",
                    }
                    for role, raw in linux_raw.items()
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )

    def semantic_stdout(stage: str) -> str:
        if stage == "git_identity":
            return f"{SOURCE_COMMIT}\n{SOURCE_TREE}\n"
        if stage == "git_status":
            return ""
        if stage == "git_source_ls_tree":
            return "".join(
                f"{all_sources[role]['git_mode']} blob "
                f"{all_sources[role]['git_head_blob_oid']}\t"
                f"{all_sources[role]['relative_path']}\0"
                for role in roles
            )
        if stage == "git_source_ls_files":
            return "".join(
                f"H {all_sources[role]['git_mode']} "
                f"{all_sources[role]['git_head_blob_oid']} 0\t"
                f"{all_sources[role]['relative_path']}\0"
                for role in roles
            )
        if stage.startswith("git_source_hash_object_"):
            role = stage.removeprefix("git_source_hash_object_")
            return f"{all_sources[role]['git_normalized_worktree_blob_oid']}\n"
        if stage in {"ubuntu_verbose_pre", "ubuntu_verbose_post"}:
            return "  NAME STATE VERSION\n* Ubuntu Running 2\n"
        if stage in {"ubuntu_running_pre", "ubuntu_running_post"}:
            return "Ubuntu\n"
        if stage == "linux_identity_readback":
            return linux_stdout
        raise AssertionError(stage)

    processes: dict[str, Any] = {}
    for stage, command in commands.items():
        name = f"pre-r8-r7s2-stager-{stage.replace('_', '-')}"
        if stage == "linux_identity_readback":
            name += "-exactly-once"
        processes[stage] = {
            "name": name,
            "run_uuid": RUN_UUID,
            "command": command,
            "started_at_utc": "2026-09-01T12:00:01+00:00",
            "ended_at_utc": "2026-09-01T12:00:02+00:00",
            "duration_seconds": 1.0,
            "safe_for_followup": True,
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
            "return_code": 0,
            "residual_pids": [],
            "stdout": semantic_stdout(stage),
            "stderr": "",
            **_success_job_evidence(command),
            "errors": [],
            "invocation": {
                "name": name,
                "command": command,
                "argv_sha256": hashlib.sha256(
                    json.dumps(command, separators=(",", ":")).encode()
                ).hexdigest(),
                "cwd": r"C:\Windows",
                "environment_keys": git_keys if stage.startswith("git_") else wsl_keys,
                "run_uuid": RUN_UUID,
            },
        }
    zero = {
        "automatic_retries",
        "forced_termination_attempts",
        "wsl_shutdown_calls",
        "docker_kubernetes_service_mutations",
        "qualification_calls",
        "r8_calls",
    }
    counts = {**{stage: 1 for stage in processes}, **{stage: 0 for stage in zero}}
    return processes, counts


def _write_contract(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    staging = evidence_root / f"c-{ATTEMPT_ID.replace('-', '')[:8]}"
    staging.mkdir()

    script = Path(qualifier.__file__).resolve()
    process_module = PROJECT / "src" / "evm" / "scale_validation" / "phase_b2_r7_process.py"
    runner = PROJECT / "scripts" / "dev" / "run_x1_phase_b2_r7s1.py"
    stager = PROJECT / "scripts" / "dev" / "stage_wsl_process_group_r7s2.py"
    outer = PROJECT / "scripts" / "dev" / "invoke_wsl_process_group_r7s2.py"
    git = Path(r"C:\Program Files\Git\mingw64\bin\git.exe")
    source_pins = {
        "qualification_script": _source_pin(script),
        "process_module": _source_pin(process_module),
        "r7s1_runner": _source_pin(runner),
        "outer_launcher": _source_pin(outer),
    }
    stager_pin = _source_pin(stager)
    stager_normalized = qualifier._lf_normalized_source(stager.read_bytes())
    stager_bootstrap_attestation = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.stager-bootstrap-attestation.v1",
        "stager_path": str(stager),
        "stager_sha256": stager_pin["sha256"],
        "stager_bytes": stager_pin["bytes"],
        "stager_lf_sha256": hashlib.sha256(stager_normalized).hexdigest(),
        "stager_blob_oid": stager_pin["git_head_blob_oid"],
        "stager_argv_sha256": "0" * 64,
        "bootstrap_source_sha256": qualifier.STAGER_BOOTSTRAP_SOURCE_SHA256,
    }
    host_binaries = {
        role: copy.deepcopy(qualifier.HOST_EXPECTATIONS[role])
        for role in sorted(qualifier.HOST_BINARY_ROLES)
    }
    parent_evidence = copy.deepcopy(qualifier.CANONICAL_PARENT_PINS)
    linux_raw = {
        "python3": b"python3",
        "env": b"env",
        "setsid": b"setsid",
    }
    linux_binaries = {
        role: {
            "path": f"/usr/bin/{role}",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "version": "3.13.7" if role == "python3" else "test-version",
        }
        for role, raw in linux_raw.items()
    }
    platform_identity = {
        "windows_build": qualifier.host_platform.version(),
        "wsl_package_version": "2.7.12.0",
        "kernel_release": "test-kernel",
        "distro_version": "Ubuntu test",
        "rootfs_identity": "4" * 64,
        "os_release_sha256": "1" * 64,
        "machine_id_sha256": "2" * 64,
        "boot_id": BOOT_ID,
    }
    source_identity = {
        "commit": SOURCE_COMMIT,
        "tree": SOURCE_TREE,
        "stager": stager_pin,
        "git": _file_pin(git, version="2.54.0.windows.1"),
        "git_config": qualifier._git_config_semantic_readback(),
    }
    contract_path = staging / "qualification-contract.json"
    issued = datetime.now(UTC)
    expires = issued + timedelta(minutes=10)
    parent_map = tmp_path / "parent-map.json"
    parent_map.write_text(
        json.dumps(
            {
                "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.parent-map.v1",
                "parents": parent_evidence,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    parent_map_pin = _artifact_pin(parent_map)
    expected_stager_argv = [
        "--qualification-id",
        RUN_ID,
        "--run-uuid",
        RUN_UUID,
        "--attempt-id",
        ATTEMPT_ID,
        "--expected-source-commit",
        SOURCE_COMMIT,
        "--expected-source-tree",
        SOURCE_TREE,
        "--expected-qualification-sha256",
        source_pins["qualification_script"]["sha256"],
        "--expected-process-module-sha256",
        source_pins["process_module"]["sha256"],
        "--expected-r7s1-runner-sha256",
        source_pins["r7s1_runner"]["sha256"],
        "--expected-stager-sha256",
        stager_pin["sha256"],
        "--expected-outer-sha256",
        source_pins["outer_launcher"]["sha256"],
        "--parent-map",
        parent_map_pin["path"],
        "--expected-parent-map-sha256",
        parent_map_pin["sha256"],
        "--execute-stage-non-credit-once",
    ]
    stager_bootstrap_attestation["stager_argv_sha256"] = hashlib.sha256(
        json.dumps(expected_stager_argv, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    reservation = staging / "staging-reservation.json"
    _write_owned_json(
        reservation,
        {
            "schema": qualifier.STAGING_RESERVATION_SCHEMA,
            "qualification_id": RUN_ID,
            "run_uuid": RUN_UUID,
            "attempt_id": ATTEMPT_ID,
            "reserved_at_utc": (issued - timedelta(seconds=1)).isoformat(),
            "expected_source_commit": SOURCE_COMMIT,
            "expected_source_tree": SOURCE_TREE,
            "expected_source_sha256": {
                role: pin["sha256"]
                for role, pin in sorted({**source_pins, "stager": stager_pin}.items())
            },
            "parent_map": parent_map_pin,
            "stager_bootstrap_attestation": stager_bootstrap_attestation,
            "automatic_retry_budget": 0,
            "forced_termination_budget": 0,
            "service_mutation_budget": 0,
            "linux_identity_readback_budget": 1,
            "qualification_budget": 0,
        },
    )
    process_evidence, call_counts = _stager_process_evidence(source_pins, stager_pin)
    staging_index = {
        "schema": qualifier.STAGING_INDEX_SCHEMA,
        "stager_schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.contract-stager.v1",
        "status": "staging_authorized_contract_pending",
        "qualification_id": RUN_ID,
        "run_uuid": RUN_UUID,
        "attempt_id": ATTEMPT_ID,
        "indexed_at_utc": issued.isoformat(),
        "expires_at_utc": expires.isoformat(),
        "contract_expected_path": str(contract_path),
        "source_identity": {
            "commit": SOURCE_COMMIT,
            "tree": SOURCE_TREE,
            "source_pins": {**source_pins, "stager": stager_pin},
            "git_config": source_identity["git_config"],
        },
        "token_evidence": dict(ADMIN_TOKEN),
        "host_binaries": host_binaries,
        "linux_identity": {
            "schema": qualifier.LINUX_DISCOVERY_SCHEMA,
            "status": "observed",
            "distro": "Ubuntu",
            "kernel_release": platform_identity["kernel_release"],
            "distro_version": platform_identity["distro_version"],
            "boot_id": BOOT_ID,
            "rootfs_identity": platform_identity["rootfs_identity"],
            "os_release_sha256": platform_identity["os_release_sha256"],
            "machine_id_sha256": platform_identity["machine_id_sha256"],
            "binaries": linux_binaries,
        },
        "ubuntu_state_pre": {"distribution": "Ubuntu", "state": "Running", "version": 2},
        "ubuntu_state_post": {"distribution": "Ubuntu", "state": "Running", "version": 2},
        "parent_map": parent_map_pin,
        "parent_evidence": parent_evidence,
        "reservation": _artifact_pin(reservation),
        "stager_bootstrap_attestation": stager_bootstrap_attestation,
        "process_evidence": process_evidence,
        "call_counts": call_counts,
        "qualification_invocation_policy": {
            "execution_route": "outer_launcher_only",
            "outer_launcher": source_pins["outer_launcher"]["path"],
            "qualification_script_sha256": source_pins["qualification_script"]["sha256"],
            "contract_and_final_index_sha256": "out_of_band_required",
        },
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "wsl_shutdown_calls": 0,
        "docker_kubernetes_service_mutations": 0,
        "qualification_started": False,
        "r8_started": False,
    }
    staging_index_path = staging / "preauthorization-index.json"
    _write_owned_json(staging_index_path, staging_index)
    contract: dict[str, Any] = {
        "schema": qualifier.CONTRACT_SCHEMA,
        "qualification_id": RUN_ID,
        "evidence_leaf": EVIDENCE_LEAF,
        "run_uuid": RUN_UUID,
        "attempt_id": ATTEMPT_ID,
        "evidence_root": str(evidence_root.resolve()),
        "distribution": "Ubuntu",
        "host_binaries": host_binaries,
        "linux_binaries": linux_binaries,
        "platform_identity": platform_identity,
        "source_identity": source_identity,
        "staging_attestation": {
            "schema": qualifier.STAGING_ATTESTATION_SCHEMA,
            "issued_at_utc": issued.isoformat(),
            "expires_at_utc": expires.isoformat(),
            "preauthorization_index": _artifact_pin(staging_index_path),
        },
        "parent_evidence": parent_evidence,
        "source_pins": source_pins,
        "timeouts": dict(qualifier.CONTRACT_TIMEOUTS),
        "outer_timeout_contract": dict(qualifier.OUTER_TIMEOUT_CONTRACT),
        "fixture": {
            "source_sha256": hashlib.sha256(
                qualifier.DETACHED_DESCENDANT_SOURCE.encode()
            ).hexdigest(),
            "lifetime_seconds": qualifier.FIXTURE_LIFETIME_SECONDS,
        },
        "invocation_policy": dict(qualifier.INVOCATION_POLICY),
    }
    _write_owned_json(contract_path, contract)
    contract_pin = _artifact_pin(contract_path)
    launch_index = {
        "schema": qualifier.LAUNCH_INDEX_SCHEMA,
        "status": "ready_non_credit_not_executed",
        "qualification_id": RUN_ID,
        "run_uuid": RUN_UUID,
        "attempt_id": ATTEMPT_ID,
        "published_at_utc": datetime.now(UTC).isoformat(),
        "expires_at_utc": expires.isoformat(),
        "contract": contract_pin,
        "preauthorization_index": _artifact_pin(staging_index_path),
        "reservation": _artifact_pin(reservation),
        "stager_bootstrap_attestation": stager_bootstrap_attestation,
        "source_identity": {
            "commit": SOURCE_COMMIT,
            "tree": SOURCE_TREE,
            "source_pins": {**source_pins, "stager": stager_pin},
            "git_config": source_identity["git_config"],
        },
        "bootstrap": {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-bootstrap.v1",
            "source_sha256": qualifier.OUTER_BOOTSTRAP_SOURCE_SHA256,
            "line_ending_policy": ("uniform_lf_or_uniform_crlf_normalized_to_lf_bare_cr_forbidden"),
            "outer_lf_normalized_sha256": hashlib.sha256(
                qualifier._lf_normalized_source(
                    Path(source_pins["outer_launcher"]["path"]).read_bytes()
                )
            ).hexdigest(),
            "expected_source_commit": SOURCE_COMMIT,
            "expected_source_tree": SOURCE_TREE,
        },
        "outer_timeout_contract": dict(qualifier.OUTER_TIMEOUT_CONTRACT),
        "outer_evidence_leaf": f"outer-{ATTEMPT_ID.replace('-', '')[:8]}",
        "outer_evidence_directory": str(evidence_root / f"outer-{ATTEMPT_ID.replace('-', '')[:8]}"),
        "qualification_invocation_policy": {
            "python": host_binaries["python"]["path"],
            "isolated_flags": ["-I", "-S", "-B"],
            "execution_route": "python_c_sha_pinned_bootstrap_then_outer",
            "bootstrap_source_sha256": qualifier.OUTER_BOOTSTRAP_SOURCE_SHA256,
            "outer_launcher": source_pins["outer_launcher"]["path"],
            "contract_path": str(contract_path),
            "launch_index_path": str(staging / "staging-index.json"),
            "contract_sha256_source": "out_of_band_required",
            "launch_index_sha256_source": "out_of_band_required",
            "outer_sha256_source": "out_of_band_required",
            "source_commit_tree_source": "out_of_band_required",
            "execute_flag": "--execute-non-credit-once",
        },
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "wsl_shutdown_calls": 0,
        "docker_kubernetes_service_mutations": 0,
        "qualification_started": False,
        "r8_started": False,
    }
    launch_index_path = staging / "staging-index.json"
    _write_owned_json(launch_index_path, launch_index)
    return contract_path, evidence_root, contract


def _load(tmp_path: Path) -> tuple[qualifier.QualificationContract, Path, dict[str, Any]]:
    path, evidence_root, raw = _write_contract(tmp_path)
    contract = qualifier.load_contract(
        path,
        expected_sha256=_sha256(path),
        expected_evidence_root=evidence_root,
        launch_index_path=path.parent / "staging-index.json",
        expected_launch_index_sha256=_sha256(path.parent / "staging-index.json"),
    )
    return contract, path, raw


def _launch_kwargs(path: Path) -> dict[str, Any]:
    launch = path.parent / "staging-index.json"
    return {
        "launch_index_path": launch,
        "expected_launch_index_sha256": _sha256(launch),
    }


def _repin_staging_dag(
    contract_path: Path,
    contract: dict[str, Any],
    preauthorization: dict[str, Any],
) -> Path:
    preauthorization_path = contract_path.parent / "preauthorization-index.json"
    launch_path = contract_path.parent / "staging-index.json"
    _write_owned_json(preauthorization_path, preauthorization)
    contract["staging_attestation"]["preauthorization_index"] = _artifact_pin(preauthorization_path)
    _write_owned_json(contract_path, contract)
    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    launch["preauthorization_index"] = _artifact_pin(preauthorization_path)
    launch["contract"] = _artifact_pin(contract_path)
    _write_owned_json(launch_path, launch)
    return launch_path


def _repin_complete_staging_dag_attestation(
    contract_path: Path,
    contract: dict[str, Any],
    foreign_attestation: dict[str, Any],
) -> Path:
    staging = contract_path.parent
    reservation_path = staging / "staging-reservation.json"
    preauthorization_path = staging / "preauthorization-index.json"
    launch_path = staging / "staging-index.json"

    reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
    reservation["stager_bootstrap_attestation"] = copy.deepcopy(foreign_attestation)
    _write_owned_json(reservation_path, reservation)

    preauthorization = json.loads(preauthorization_path.read_text(encoding="utf-8"))
    preauthorization["stager_bootstrap_attestation"] = copy.deepcopy(foreign_attestation)
    preauthorization["reservation"] = _artifact_pin(reservation_path)
    _write_owned_json(preauthorization_path, preauthorization)

    contract["staging_attestation"]["preauthorization_index"] = _artifact_pin(preauthorization_path)
    _write_owned_json(contract_path, contract)

    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    launch["stager_bootstrap_attestation"] = copy.deepcopy(foreign_attestation)
    launch["reservation"] = _artifact_pin(reservation_path)
    launch["preauthorization_index"] = _artifact_pin(preauthorization_path)
    launch["contract"] = _artifact_pin(contract_path)
    _write_owned_json(launch_path, launch)
    return launch_path


def _process_outcome(stdout: str, *, forced: int = 0) -> dict[str, Any]:
    if not stdout.endswith("\n"):
        stdout += "\n"
    return {
        "safe_for_followup": forced == 0,
        "forced_termination_attempts": forced,
        "stdout": stdout,
    }


def _git_blob_oid(raw: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(raw)}\0".encode("ascii") + raw, usedforsecurity=False
    ).hexdigest()


def _source_pin(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    worktree_oid = _git_blob_oid(raw)
    normalized_oid = _git_blob_oid(qualifier._lf_normalized_source(raw))
    return {
        **_artifact_pin(path),
        "relative_path": path.resolve().relative_to(PROJECT.parent.resolve()).as_posix(),
        "worktree_blob_oid": worktree_oid,
        "git_mode": "100644",
        "git_head_blob_oid": normalized_oid,
        "git_normalized_worktree_blob_oid": normalized_oid,
    }


def _source_readback(_contract: qualifier.QualificationContract) -> dict[str, Any]:
    return {
        "schema": "test-source-readback",
        "commit": SOURCE_COMMIT,
        "tree": SOURCE_TREE,
        "processes": {},
        "call_counts": {"git_identity": 1, "git_status": 1},
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
    }


def _ubuntu_readback(_contract: qualifier.QualificationContract) -> dict[str, Any]:
    return {
        "schema": "test-ubuntu-running-readback",
        "state": {"distribution": "Ubuntu", "state": "Running", "version": 2},
        "processes": {},
        "call_counts": {"ubuntu_verbose_gate": 1, "ubuntu_running_gate": 1},
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "wsl_shutdown_calls": 0,
    }


def _authorize_for_test(
    contract: qualifier.QualificationContract, monkeypatch: pytest.MonkeyPatch
) -> qualifier.QualificationContract:
    monkeypatch.setattr(qualifier, "R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED", True)
    monkeypatch.setattr(qualifier, "_require_inner_bootstrap_attestation", lambda: {})
    monkeypatch.setattr(qualifier, "CANONICAL_EVIDENCE_ROOT", contract.evidence_root)
    monkeypatch.setattr(qualifier, "_runtime_admin_token_readback", lambda _contract: ADMIN_TOKEN)
    monkeypatch.setattr(
        qualifier,
        "_runtime_job_containment_readback",
        lambda: {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-job-readback.v1",
            "query_scope": "immediate_job_of_calling_process_null_handle",
            "pid": os.getpid(),
            "is_process_in_job": True,
            "limit_flags": 0,
            "kill_on_job_close": False,
            "breakaway_ok": False,
            "silent_breakaway_ok": False,
            "active_processes": 1,
            "total_processes": 1,
            "terminated_processes": 0,
            "assigned_processes": 1,
            "process_ids": [os.getpid()],
        },
    )
    monkeypatch.setattr(qualifier, "_runtime_source_identity_readback", _source_readback)
    monkeypatch.setattr(qualifier, "_runtime_ubuntu_running_readback", _ubuntu_readback)
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    outer_directory.mkdir()
    parent_pid = os.getppid()
    outer_pin = contract.raw["source_pins"]["outer_launcher"]
    outer_raw = Path(outer_pin["path"]).read_bytes()
    outer_bootstrap_attestation = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.bootstrap-attestation.v1",
        "outer_path": outer_pin["path"],
        "outer_raw_sha256": outer_pin["sha256"],
        "outer_bytes": outer_pin["bytes"],
        "outer_lf_normalized_sha256": hashlib.sha256(
            qualifier._lf_normalized_source(outer_raw)
        ).hexdigest(),
        "contract_path": str(contract.contract_path),
        "contract_sha256": contract.contract_sha256,
        "launch_index_path": str(contract.launch_index_path),
        "launch_index_sha256": contract.launch_index_sha256,
        "expected_source_commit": contract.source_identity["commit"],
        "expected_source_tree": contract.source_identity["tree"],
        "outer_argv_sha256": "0" * 64,
        "python_identity": copy.deepcopy(qualifier.HOST_EXPECTATIONS["python"]),
        "bootstrap_source_sha256": qualifier.OUTER_BOOTSTRAP_SOURCE_SHA256,
    }
    expected_outer_argv = [
        "--contract",
        str(contract.contract_path),
        "--expected-contract-sha256",
        contract.contract_sha256,
        "--launch-index",
        str(contract.launch_index_path),
        "--expected-launch-index-sha256",
        contract.launch_index_sha256,
        "--expected-outer-sha256",
        contract.raw["source_pins"]["outer_launcher"]["sha256"],
        "--expected-source-commit",
        contract.source_identity["commit"],
        "--expected-source-tree",
        contract.source_identity["tree"],
        "--execute-non-credit-once",
    ]
    outer_bootstrap_attestation["outer_argv_sha256"] = hashlib.sha256(
        json.dumps(expected_outer_argv, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    reservation_path = outer_directory / "outer-reservation.json"
    _write_owned_json(
        reservation_path,
        {
            "schema": qualifier.OUTER_RESERVATION_SCHEMA,
            "qualification_id": contract.qualification_id,
            "run_uuid": contract.run_uuid,
            "attempt_id": contract.attempt_id,
            "reserved_at_utc": datetime.now(UTC).isoformat(),
            "pid": parent_pid,
            "process_creation_filetime": qualifier._process_creation_filetime(parent_pid),
            "administrator_token_evidence": ADMIN_TOKEN,
            "contract_sha256": contract.contract_sha256,
            "launch_index_sha256": contract.launch_index_sha256,
            "outer_sha256": contract.raw["source_pins"]["outer_launcher"]["sha256"],
            "outer_bootstrap_attestation": outer_bootstrap_attestation,
            "qualification_child_budget": 1,
            "automatic_retry_budget": 0,
            "forced_termination_budget": 0,
        },
    )
    reservation_pin = _artifact_pin(reservation_path)
    return replace(
        contract,
        outer_reservation_path=Path(reservation_pin["path"]),
        outer_reservation_sha256=reservation_pin["sha256"],
        outer_parent_pid=parent_pid,
        execution_authorized=True,
    )


class _FakeOutcome:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.value)


def _record(*, run_uuid: str = RUN_UUID) -> dict[str, Any]:
    return {
        "pid": 900,
        "ppid": 1,
        "pgrp": 900,
        "session": 900,
        "start_time_ticks": 9001,
        "boot_id": BOOT_ID,
        "run_uuid_match": run_uuid == RUN_UUID,
        "process_group_match": True,
        "auxiliary_read_status": "complete",
        "unreadable_fields": [],
        "cmdline_sha256": "3" * 64,
        "open_fd_count": 0,
        "stdio_fds_present": [],
    }


def _ack() -> dict[str, Any]:
    return {
        "schema": qualifier.FIXTURE_ACK_SCHEMA,
        "run_uuid": RUN_UUID,
        "pid": 900,
        "ppid": 1,
        "pgrp": 900,
        "session": 900,
        "start_time_ticks": 9001,
        "boot_id": BOOT_ID,
        "stdio_detach": "close_all_inherited_fds_after_ack",
    }


def _analysis_inputs(
    contract: qualifier.QualificationContract,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    host = contract.host_binaries
    linux_readback = {"boot_id": BOOT_ID}
    initial = _process_outcome("[]")
    launch = {
        "safe_for_followup": True,
        "forced_termination_attempts": 0,
        "active_process_zero": True,
        "streams_drained": True,
        "identity_coverage_complete": True,
        "stdout": json.dumps(_ack(), sort_keys=True, separators=(",", ":")) + "\n",
        "identities": [
            {
                "pid": 10,
                "ppid": 9,
                "creation_time_ns": 10,
                "creation_time_utc": "2026-09-01T00:00:00+00:00",
                "image": host["system32_wsl"].path,
                "run_uuid": "job-run",
                "observed_sequence": 1,
            },
            {
                "pid": 11,
                "ppid": 10,
                "creation_time_ns": 11,
                "creation_time_utc": "2026-09-01T00:00:00+00:00",
                "image": host["store_wsl"].path,
                "run_uuid": "job-run",
                "observed_sequence": 2,
            },
            {
                "pid": 12,
                "ppid": 11,
                "creation_time_ns": 12,
                "creation_time_utc": "2026-09-01T00:00:00+00:00",
                "image": host["wslhost"].path,
                "run_uuid": "job-run",
                "observed_sequence": 3,
            },
        ],
        "events": [
            {
                "sequence": 1,
                "event": "job_exit_process",
                "monotonic_ns": 100,
                "timestamp_utc": "2026-09-01T00:00:01+00:00",
                "pid": 10,
                "details": {},
            },
            {
                "sequence": 2,
                "event": "job_exit_process",
                "monotonic_ns": 110,
                "timestamp_utc": "2026-09-01T00:00:01+00:00",
                "pid": 11,
                "details": {},
            },
            {
                "sequence": 3,
                "event": "job_active_process_zero",
                "monotonic_ns": 300,
                "timestamp_utc": "2026-09-01T00:00:03+00:00",
                "pid": None,
                "details": {},
            },
        ],
        "accounting": [
            {
                "sequence": 1,
                "monotonic_ns": 200,
                "timestamp_utc": "2026-09-01T00:00:02+00:00",
                "total_processes": 3,
                "active_processes": 1,
                "total_terminated_processes": 0,
                "active_pids": [12],
            },
            {
                "sequence": 2,
                "monotonic_ns": 300,
                "timestamp_utc": "2026-09-01T00:00:03+00:00",
                "total_processes": 3,
                "active_processes": 0,
                "total_terminated_processes": 0,
                "active_pids": [],
            },
        ],
    }
    scans = [
        {
            "sequence": 1,
            "started_monotonic_ns": 150,
            "ended_monotonic_ns": 250,
            "outcome": _process_outcome(
                json.dumps([_record()], sort_keys=True, separators=(",", ":"))
            ),
        },
        {
            "sequence": 2,
            "started_monotonic_ns": 310,
            "ended_monotonic_ns": 320,
            "query": {
                "run_uuid": contract.run_uuid,
                "expected_pgrp": _ack()["pgrp"],
                "expected_start_time_ticks": _ack()["start_time_ticks"],
                "expected_boot_id": BOOT_ID,
                "match_policy": "run_uuid_or_ack_process_group",
            },
            "outcome": _process_outcome("[]"),
        },
    ]
    return linux_readback, initial, launch, scans


def test_production_qualification_is_root_anchor_blocked_before_contract_access() -> None:
    assert qualifier.R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED is False
    with pytest.raises(
        qualifier.R7S2QualificationError,
        match="r7s2_out_of_band_root_anchor_required",
    ):
        qualifier.execute_once(None, expected_contract_sha256="0" * 64)  # type: ignore[arg-type]


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
def test_qualification_root_json_loader_rejects_ambiguous_or_noncanonical(
    raw: bytes,
) -> None:
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier._strict_json_bytes(raw, "test_root", canonical_owned=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("is_process_in_job", False),
        ("listed_pids", [7332]),
        ("listed_pids", [7331, 7332]),
        ("listed_pids", [7331, 7331]),
        ("active_processes", 0),
        ("active_processes", 2),
        ("total_processes", 2),
        ("terminated_processes", 1),
        ("assigned_processes", 2),
        ("limit_flags", 1),
        ("limit_flags", 0x800),
        ("limit_flags", 0x1000),
        ("limit_flags", 0x2000),
        ("active_processes", True),
    ],
)
def test_runtime_job_containment_rejects_no_job_foreign_and_accounting_mutations(
    field: str, value: Any
) -> None:
    values: dict[str, Any] = {
        "current_pid": 7331,
        "is_process_in_job": True,
        "limit_flags": 0,
        "active_processes": 1,
        "total_processes": 1,
        "terminated_processes": 0,
        "assigned_processes": 1,
        "listed_pids": [7331],
    }
    baseline = qualifier._validated_runtime_job_containment_observation(**values)
    assert baseline["process_ids"] == [7331]
    assert baseline["terminated_processes"] == 0
    values[field] = value
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier._validated_runtime_job_containment_observation(**values)


def test_runtime_job_native_query_requires_exact_returned_lengths() -> None:
    source = inspect.getsource(qualifier._runtime_job_containment_readback)
    assert "returned.value <= 0" in source
    assert "returned.value > ctypes.sizeof(value)" in source
    assert "limits_bytes != ctypes.sizeof(limits)" in source
    assert "accounting_bytes != ctypes.sizeof(accounting)" in source
    assert "process_ids_bytes != required_pid_bytes" in source
    assert "total_terminated_processes" in source


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("in_job", False),
        ("pids", [7332]),
        ("pids", [7331, 7332]),
        ("limit_returned", "short"),
        ("accounting_returned", "short"),
        ("pid_returned", "short"),
        ("pid_returned", "required_minus_one"),
        ("pid_returned", "required_plus_one"),
        ("pid_returned", "allocated_buffer"),
        ("active", 2),
        ("total", 2),
        ("terminated", 1),
        ("assigned", 2),
        ("flags", 1),
        ("flags", 0x800),
        ("flags", 0x1000),
        ("flags", 0x2000),
    ],
)
def test_runtime_job_native_wiring_rejects_kernel_snapshot_mutations_end_to_end(
    monkeypatch: pytest.MonkeyPatch, mutation: str, value: Any
) -> None:
    snapshot: dict[str, Any] = {
        "in_job": True,
        "pids": [7331],
        "active": 1,
        "total": 1,
        "terminated": 0,
        "assigned": 1,
        "flags": 0,
        "limit_returned": "exact",
        "accounting_returned": "exact",
        "pid_returned": "exact",
    }
    snapshot[mutation] = value
    calls: list[int] = []

    class NativeFunction:
        argtypes: Any = None
        restype: Any = None

        def __init__(self, implementation: Any) -> None:
            self.implementation = implementation

        def __call__(self, *args: Any) -> Any:
            return self.implementation(*args)

    def get_current_process() -> int:
        return -1

    def is_process_in_job(_process: Any, _job: Any, result: Any) -> int:
        result._obj.value = bool(snapshot["in_job"])
        return 1

    def query_job(
        _job: Any,
        information_class: int,
        value_pointer: Any,
        buffer_size: int,
        returned_pointer: Any,
    ) -> int:
        calls.append(information_class)
        target = value_pointer._obj
        returned = int(buffer_size)
        if information_class == 9:
            target.basic_limit_information.limit_flags = snapshot["flags"]
            if snapshot["limit_returned"] == "short":
                returned -= 1
        elif information_class == 1:
            target.active_processes = snapshot["active"]
            target.total_processes = snapshot["total"]
            target.total_terminated_processes = snapshot["terminated"]
            if snapshot["accounting_returned"] == "short":
                returned -= 1
        elif information_class == 3:
            target.number_of_assigned_processes = snapshot["assigned"]
            target.number_of_process_ids_in_list = len(snapshot["pids"])
            for index, pid in enumerate(snapshot["pids"]):
                target.process_id_list[index] = pid
            required = target.__class__.process_id_list.offset + len(
                snapshot["pids"]
            ) * qualifier.ctypes.sizeof(qualifier.ctypes.c_size_t)
            returned = required
            if snapshot["pid_returned"] == "short":
                returned = 1
            elif snapshot["pid_returned"] == "required_minus_one":
                returned = required - 1
            elif snapshot["pid_returned"] == "required_plus_one":
                returned = required + 1
            elif snapshot["pid_returned"] == "allocated_buffer":
                returned = int(buffer_size)
        else:
            raise AssertionError(information_class)
        returned_pointer._obj.value = returned
        return 1

    kernel = SimpleNamespace(
        GetCurrentProcess=NativeFunction(get_current_process),
        IsProcessInJob=NativeFunction(is_process_in_job),
        QueryInformationJobObject=NativeFunction(query_job),
    )
    monkeypatch.setattr(qualifier.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    monkeypatch.setattr(qualifier.os, "name", "nt")
    monkeypatch.setattr(qualifier.os, "getpid", lambda: 7331)
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier._runtime_job_containment_readback()
    assert calls == [9, 1, 3]


def test_runtime_job_native_wiring_accepts_exact_solo_nonterminating_job_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    pid_query_boundaries: list[tuple[int, int, int]] = []

    class NativeFunction:
        argtypes: Any = None
        restype: Any = None

        def __init__(self, implementation: Any) -> None:
            self.implementation = implementation

        def __call__(self, *args: Any) -> Any:
            return self.implementation(*args)

    def is_process_in_job(_process: Any, _job: Any, result: Any) -> int:
        result._obj.value = True
        return 1

    def query_job(
        _job: Any,
        information_class: int,
        value_pointer: Any,
        buffer_size: int,
        returned_pointer: Any,
    ) -> int:
        calls.append(information_class)
        target = value_pointer._obj
        returned = int(buffer_size)
        if information_class == 9:
            target.basic_limit_information.limit_flags = 0
        elif information_class == 1:
            target.active_processes = 1
            target.total_processes = 1
            target.total_terminated_processes = 0
        elif information_class == 3:
            target.number_of_assigned_processes = 1
            target.number_of_process_ids_in_list = 1
            target.process_id_list[0] = 7331
            required = target.__class__.process_id_list.offset + qualifier.ctypes.sizeof(
                qualifier.ctypes.c_size_t
            )
            returned = required
            pid_query_boundaries.append((required, returned, int(buffer_size)))
        returned_pointer._obj.value = returned
        return 1

    kernel = SimpleNamespace(
        GetCurrentProcess=NativeFunction(lambda: -1),
        IsProcessInJob=NativeFunction(is_process_in_job),
        QueryInformationJobObject=NativeFunction(query_job),
    )
    monkeypatch.setattr(qualifier.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    monkeypatch.setattr(qualifier.os, "name", "nt")
    monkeypatch.setattr(qualifier.os, "getpid", lambda: 7331)
    evidence = qualifier._runtime_job_containment_readback()
    assert evidence["process_ids"] == [7331]
    assert evidence["terminated_processes"] == 0
    assert calls == [9, 1, 3]
    assert len(pid_query_boundaries) == 1
    required, returned, allocated = pid_query_boundaries[0]
    assert returned == required
    assert required < allocated


def test_inner_bootstrap_attestation_rejects_source_argv_replay_and_domain_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qualifier_path = Path(qualifier.__file__)
    raw = qualifier_path.read_bytes()
    normalized = qualifier._lf_normalized_source(raw)
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
    valid = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1",
        "qualifier_path": str(qualifier_path),
        "qualifier_sha256": hashlib.sha256(raw).hexdigest(),
        "qualifier_bytes": len(raw),
        "qualifier_lf_sha256": hashlib.sha256(normalized).hexdigest(),
        "qualifier_blob_oid": _git_blob_oid(normalized),
        "inner_argv_sha256": hashlib.sha256(
            json.dumps(inner_args, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "bootstrap_source_sha256": qualifier.INNER_BOOTSTRAP_SOURCE_SHA256,
    }
    monkeypatch.setattr(
        qualifier.sys,
        "orig_argv",
        [sys.executable, "-I", "-S", "-B", "-c", outer_launcher.INNER_BOOTSTRAP_SOURCE],
    )
    monkeypatch.setattr(qualifier.sys, "argv", [str(qualifier_path), *inner_args])
    monkeypatch.setitem(
        qualifier.__dict__, "__evm_r7s2_inner_bootstrap_attestation__", copy.deepcopy(valid)
    )
    assert qualifier._require_inner_bootstrap_attestation() == valid

    for mutated in (
        {**valid, "bootstrap_source_sha256": "f" * 64},
        {**valid, "inner_argv_sha256": "e" * 64},
        {"schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.bootstrap-attestation.v1"},
    ):
        monkeypatch.setitem(qualifier.__dict__, "__evm_r7s2_inner_bootstrap_attestation__", mutated)
        with pytest.raises(qualifier.R7S2QualificationError):
            qualifier._require_inner_bootstrap_attestation()

    monkeypatch.setitem(
        qualifier.__dict__, "__evm_r7s2_inner_bootstrap_attestation__", copy.deepcopy(valid)
    )
    monkeypatch.setattr(qualifier.sys, "argv", [str(qualifier_path), *inner_args, "--replayed"])
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier._require_inner_bootstrap_attestation()


def test_contract_is_out_of_band_sha_bound_and_paths_are_exact(tmp_path: Path) -> None:
    contract, path, raw = _load(tmp_path)
    assert contract.qualification_id == RUN_ID
    assert contract.run_uuid == RUN_UUID
    assert contract.run_directory.name == EVIDENCE_LEAF
    assert not contract.run_directory.exists()

    with pytest.raises(qualifier.R7S2QualificationError, match="contract_sha256_mismatch"):
        qualifier.load_contract(
            path,
            expected_sha256="f" * 64,
            expected_evidence_root=contract.evidence_root,
            **_launch_kwargs(path),
        )

    mutated = copy.deepcopy(raw)
    mutated["invocation_policy"]["automatic_retries"] = 1
    _write_owned_json(path, mutated)
    with pytest.raises(qualifier.R7S2QualificationError, match="invocation_policy_mismatch"):
        qualifier.load_contract(
            path,
            expected_sha256=_sha256(path),
            expected_evidence_root=contract.evidence_root,
            **_launch_kwargs(path),
        )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("distribution", "docker-desktop", "distribution_must_equal_Ubuntu"),
        ("run_uuid", ATTEMPT_ID, "run_uuid_attempt_id_must_differ"),
        ("evidence_leaf", "wsl-deadbeef", "evidence_leaf_mismatch"),
    ],
)
def test_contract_identity_mutations_are_rejected(
    tmp_path: Path, field: str, value: str, error: str
) -> None:
    path, evidence_root, raw = _write_contract(tmp_path)
    raw[field] = value
    _write_owned_json(path, raw)
    with pytest.raises(qualifier.R7S2QualificationError, match=error):
        qualifier.load_contract(
            path,
            expected_sha256=_sha256(path),
            expected_evidence_root=evidence_root,
            **_launch_kwargs(path),
        )


def test_process_runtime_is_canonical_sha_verified_before_deferred_execution(
    tmp_path: Path,
) -> None:
    contract, _path, raw = _load(tmp_path)
    expected = raw["source_pins"]["process_module"]
    assert qualifier._PROCESS_RUNTIME_SHA256 == expected["sha256"]
    assert qualifier.TimeoutContract.__module__.startswith("_evm_pre_r8_r7s2_process_")
    assert qualifier.WindowsJobProcessRunner.__module__ == qualifier.TimeoutContract.__module__
    assert contract.raw["source_pins"]["process_module"] == expected


def test_self_consistent_alternate_process_module_pin_is_rejected_before_load(
    tmp_path: Path,
) -> None:
    path, evidence_root, raw = _write_contract(tmp_path)
    alternate = tmp_path / "alternate_process.py"
    alternate.write_text("raise RuntimeError('must_not_execute')\n", encoding="utf-8")
    raw["source_pins"]["process_module"] = _artifact_pin(alternate)
    _write_owned_json(path, raw)
    with pytest.raises(
        qualifier.R7S2QualificationError, match="source_process_module_fields_mismatch"
    ):
        qualifier.load_contract(
            path,
            expected_sha256=_sha256(path),
            expected_evidence_root=evidence_root,
            **_launch_kwargs(path),
        )


def test_self_consistent_preauthorization_outcome_command_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    contract_path, evidence_root, contract = _write_contract(tmp_path)
    staging = contract_path.parent
    preauth_path = staging / "preauthorization-index.json"
    preauth = json.loads(preauth_path.read_text(encoding="utf-8"))
    preauth["process_evidence"]["git_status"]["command"] = ["forbidden.exe", "reset"]
    _write_owned_json(preauth_path, preauth)
    contract["staging_attestation"]["preauthorization_index"] = _artifact_pin(preauth_path)
    _write_owned_json(contract_path, contract)
    launch_path = staging / "staging-index.json"
    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    launch["preauthorization_index"] = _artifact_pin(preauth_path)
    launch["contract"] = _artifact_pin(contract_path)
    _write_owned_json(launch_path, launch)
    with pytest.raises(
        qualifier.R7S2QualificationError,
        match="staging_process_git_status_outcome_invocation_mismatch",
    ):
        qualifier.load_contract(
            contract_path,
            expected_sha256=_sha256(contract_path),
            expected_evidence_root=evidence_root,
            launch_index_path=launch_path,
            expected_launch_index_sha256=_sha256(launch_path),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "git_status_space",
        "git_status_stderr",
        "ls_files_missing_nul",
        "ls_files_double_nul",
        "ls_files_uppercase_oid",
    ],
)
def test_self_consistent_preauthorization_git_stream_grammar_mutation_is_rejected(
    tmp_path: Path, mutation: str
) -> None:
    contract_path, evidence_root, contract = _write_contract(tmp_path)
    preauthorization_path = contract_path.parent / "preauthorization-index.json"
    preauthorization = json.loads(preauthorization_path.read_text(encoding="utf-8"))
    if mutation == "git_status_space":
        preauthorization["process_evidence"]["git_status"]["stdout"] = " "
    elif mutation == "git_status_stderr":
        preauthorization["process_evidence"]["git_status"]["stderr"] = "warning\n"
    else:
        outcome = preauthorization["process_evidence"]["git_source_ls_files"]
        if mutation == "ls_files_missing_nul":
            outcome["stdout"] = outcome["stdout"][:-1]
        elif mutation == "ls_files_double_nul":
            outcome["stdout"] += "\0"
        else:
            oid = next(iter(contract["source_pins"].values()))["git_head_blob_oid"]
            outcome["stdout"] = outcome["stdout"].replace(oid, oid.upper(), 1)
    launch_path = _repin_staging_dag(contract_path, contract, preauthorization)
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier.load_contract(
            contract_path,
            expected_sha256=_sha256(contract_path),
            expected_evidence_root=evidence_root,
            launch_index_path=launch_path,
            expected_launch_index_sha256=_sha256(launch_path),
        )


@pytest.mark.parametrize(
    ("role", "mutation"),
    [
        (role, mutation)
        for role in (
            "qualification_script",
            "process_module",
            "r7s1_runner",
            "outer_launcher",
            "stager",
        )
        for mutation in ("uppercase", "missing_lf", "extra_lf")
    ],
)
def test_each_hash_object_role_requires_exact_lowerhex40_and_one_lf(
    tmp_path: Path, role: str, mutation: str
) -> None:
    contract_path, evidence_root, contract = _write_contract(tmp_path)
    preauthorization_path = contract_path.parent / "preauthorization-index.json"
    preauthorization = json.loads(preauthorization_path.read_text(encoding="utf-8"))
    outcome = preauthorization["process_evidence"][f"git_source_hash_object_{role}"]
    if mutation == "uppercase":
        outcome["stdout"] = outcome["stdout"].upper()
    elif mutation == "missing_lf":
        outcome["stdout"] = outcome["stdout"].removesuffix("\n")
    else:
        outcome["stdout"] += "\n"
    launch_path = _repin_staging_dag(contract_path, contract, preauthorization)
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier.load_contract(
            contract_path,
            expected_sha256=_sha256(contract_path),
            expected_evidence_root=evidence_root,
            launch_index_path=launch_path,
            expected_launch_index_sha256=_sha256(launch_path),
        )


@pytest.mark.parametrize(
    "mutation",
    ["bootstrap_source", "replayed_argv", "domain_swap"],
)
def test_self_consistent_stager_bootstrap_mutation_or_replay_is_rejected(
    tmp_path: Path, mutation: str
) -> None:
    contract_path, evidence_root, contract = _write_contract(tmp_path)
    preauthorization_path = contract_path.parent / "preauthorization-index.json"
    preauthorization = json.loads(preauthorization_path.read_text(encoding="utf-8"))
    attestation = preauthorization["stager_bootstrap_attestation"]
    if mutation == "bootstrap_source":
        attestation["bootstrap_source_sha256"] = "f" * 64
    elif mutation == "replayed_argv":
        attestation["stager_argv_sha256"] = hashlib.sha256(
            json.dumps(
                ["--qualification-id", "replayed-run", "--execute-stage-non-credit-once"],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    else:
        preauthorization["stager_bootstrap_attestation"] = {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.bootstrap-attestation.v1",
            "outer_path": "domain-swapped",
        }
    launch_path = _repin_staging_dag(contract_path, contract, preauthorization)
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier.load_contract(
            contract_path,
            expected_sha256=_sha256(contract_path),
            expected_evidence_root=evidence_root,
            launch_index_path=launch_path,
            expected_launch_index_sha256=_sha256(launch_path),
        )


@pytest.mark.parametrize("foreign_domain", ["outer", "inner"])
def test_individually_valid_full_domain_attestation_swap_is_rejected_after_complete_dag_repin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    foreign_domain: str,
) -> None:
    contract_path, evidence_root, contract = _write_contract(tmp_path)
    launch_path = contract_path.parent / "staging-index.json"
    baseline = qualifier.load_contract(
        contract_path,
        expected_sha256=_sha256(contract_path),
        expected_evidence_root=evidence_root,
        launch_index_path=launch_path,
        expected_launch_index_sha256=_sha256(launch_path),
    )
    assert baseline.raw["staging_attestation"]["schema"] == qualifier.STAGING_ATTESTATION_SCHEMA

    if foreign_domain == "outer":
        outer_pin = contract["source_pins"]["outer_launcher"]
        outer_path = Path(outer_pin["path"])
        outer_raw = outer_path.read_bytes()
        outer_lf = outer_launcher._lf_normalized_source(outer_raw)
        outer_args = [
            "--contract",
            str(contract_path),
            "--expected-contract-sha256",
            _sha256(contract_path),
            "--launch-index",
            str(launch_path),
            "--expected-launch-index-sha256",
            _sha256(launch_path),
            "--expected-outer-sha256",
            outer_pin["sha256"],
            "--expected-source-commit",
            SOURCE_COMMIT,
            "--expected-source-tree",
            SOURCE_TREE,
            "--execute-non-credit-once",
        ]
        foreign_attestation = {
            "schema": outer_launcher.BOOTSTRAP_ATTESTATION_SCHEMA,
            "outer_path": str(outer_path),
            "outer_raw_sha256": outer_pin["sha256"],
            "outer_bytes": outer_pin["bytes"],
            "outer_lf_normalized_sha256": hashlib.sha256(outer_lf).hexdigest(),
            "contract_path": str(contract_path),
            "contract_sha256": _sha256(contract_path),
            "launch_index_path": str(launch_path),
            "launch_index_sha256": _sha256(launch_path),
            "expected_source_commit": SOURCE_COMMIT,
            "expected_source_tree": SOURCE_TREE,
            "outer_argv_sha256": hashlib.sha256(
                json.dumps(outer_args, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "python_identity": copy.deepcopy(qualifier.HOST_EXPECTATIONS["python"]),
            "bootstrap_source_sha256": outer_launcher.TRUSTED_BOOTSTRAP_SOURCE_SHA256,
        }
        monkeypatch.setattr(
            outer_launcher.sys,
            "orig_argv",
            [sys.executable, "-I", "-S", "-B", "-c", contract_stager.OUTER_BOOTSTRAP_SOURCE],
        )
        monkeypatch.setattr(outer_launcher.sys, "argv", [str(outer_path), *outer_args])
        monkeypatch.setitem(
            outer_launcher.__dict__,
            "__evm_r7s2_bootstrap_attestation__",
            copy.deepcopy(foreign_attestation),
        )
        assert (
            outer_launcher._require_bootstrap_attestation(
                contract_path=contract_path,
                expected_contract_sha256=_sha256(contract_path),
                launch_index_path=launch_path,
                expected_launch_index_sha256=_sha256(launch_path),
                expected_outer_sha256=outer_pin["sha256"],
                expected_source_commit=SOURCE_COMMIT,
                expected_source_tree=SOURCE_TREE,
            )
            == foreign_attestation
        )
    else:
        qualifier_pin = contract["source_pins"]["qualification_script"]
        qualifier_path = Path(qualifier_pin["path"])
        qualifier_raw = qualifier_path.read_bytes()
        qualifier_lf = qualifier._lf_normalized_source(qualifier_raw)
        inner_args = [
            "--contract",
            str(contract_path),
            "--expected-contract-sha256",
            _sha256(contract_path),
            "--launch-index",
            str(launch_path),
            "--expected-launch-index-sha256",
            _sha256(launch_path),
            "--outer-reservation",
            str(contract_path.parent / "outer-reservation.json"),
            "--expected-outer-reservation-sha256",
            "9" * 64,
            "--execute-non-credit-once",
        ]
        foreign_attestation = {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1",
            "qualifier_path": str(qualifier_path),
            "qualifier_sha256": qualifier_pin["sha256"],
            "qualifier_bytes": qualifier_pin["bytes"],
            "qualifier_lf_sha256": hashlib.sha256(qualifier_lf).hexdigest(),
            "qualifier_blob_oid": qualifier._git_blob_oid(qualifier_lf),
            "inner_argv_sha256": hashlib.sha256(
                json.dumps(inner_args, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "bootstrap_source_sha256": qualifier.INNER_BOOTSTRAP_SOURCE_SHA256,
        }
        monkeypatch.setattr(
            qualifier.sys,
            "orig_argv",
            [sys.executable, "-I", "-S", "-B", "-c", outer_launcher.INNER_BOOTSTRAP_SOURCE],
        )
        monkeypatch.setattr(qualifier.sys, "argv", [str(qualifier_path), *inner_args])
        monkeypatch.setitem(
            qualifier.__dict__,
            "__evm_r7s2_inner_bootstrap_attestation__",
            copy.deepcopy(foreign_attestation),
        )
        assert qualifier._require_inner_bootstrap_attestation() == foreign_attestation

    launch_path = _repin_complete_staging_dag_attestation(
        contract_path, contract, foreign_attestation
    )
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier.load_contract(
            contract_path,
            expected_sha256=_sha256(contract_path),
            expected_evidence_root=evidence_root,
            launch_index_path=launch_path,
            expected_launch_index_sha256=_sha256(launch_path),
        )


@pytest.mark.parametrize("mutation", ["bootstrap_source", "replayed_revision", "domain_swap"])
def test_self_consistent_outer_bootstrap_launch_index_mutation_is_rejected(
    tmp_path: Path, mutation: str
) -> None:
    contract_path, evidence_root, _contract = _write_contract(tmp_path)
    launch_path = contract_path.parent / "staging-index.json"
    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    if mutation == "bootstrap_source":
        launch["bootstrap"]["source_sha256"] = "f" * 64
    elif mutation == "replayed_revision":
        launch["bootstrap"]["expected_source_commit"] = "e" * 40
    else:
        launch["bootstrap"] = {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1"
        }
    _write_owned_json(launch_path, launch)
    with pytest.raises(qualifier.R7S2QualificationError):
        qualifier.load_contract(
            contract_path,
            expected_sha256=_sha256(contract_path),
            expected_evidence_root=evidence_root,
            launch_index_path=launch_path,
            expected_launch_index_sha256=_sha256(launch_path),
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("launch_wrapper_seconds", 11),
        ("launch_residual_seconds", 7),
        ("stream_drain_seconds", 4),
        ("scan_wrapper_seconds", 4),
        ("scan_residual_seconds", 2),
        ("observer_deadline_seconds", 29),
        ("observer_interval_seconds", 0.06),
        ("observer_max_scans", 63),
    ],
)
def test_self_consistent_timeout_contract_mutation_is_rejected(
    tmp_path: Path, key: str, value: float
) -> None:
    path, evidence_root, raw = _write_contract(tmp_path)
    raw["timeouts"][key] = value
    _write_owned_json(path, raw)
    with pytest.raises(
        qualifier.R7S2QualificationError,
        match="timeout_contract_exact_mismatch",
    ):
        qualifier.load_contract(
            path,
            expected_sha256=_sha256(path),
            expected_evidence_root=evidence_root,
            **_launch_kwargs(path),
        )


def test_windows_path_budget_is_fail_closed() -> None:
    with pytest.raises(qualifier.R7S2QualificationError, match="path_budget_exceeded"):
        qualifier._assert_path_budget(Path("C:/") / ("x" * 241))


def test_commands_are_exact_pinned_and_only_read_only_observers_repeat(tmp_path: Path) -> None:
    contract, _path, _raw = _load(tmp_path)
    protocol = qualifier.WslResidualProtocol(contract.run_uuid)
    launch = qualifier._launch_command(contract, protocol)
    scan = qualifier._scan_command(contract, protocol)
    toolchain = qualifier._linux_readback_command(contract)

    assert launch[0] == contract.host_binaries["system32_wsl"].path
    assert launch[3:7] == ("--cd", "/", "--exec", contract.linux_binaries["env"].path)
    assert launch[7:10] == ("-i", "LANG=C.UTF-8", "LC_ALL=C.UTF-8")
    assert launch[10] == f"EVM_PHASE_B2_RUN_UUID={contract.run_uuid}"
    assert launch[11:14] == (
        contract.linux_binaries["setsid"].path,
        "--fork",
        "--wait",
    )
    assert launch.count(qualifier.DETACHED_DESCENDANT_SOURCE) == 1
    assert scan[0] == contract.host_binaries["system32_wsl"].path
    assert scan[3:10] == (
        "--cd",
        "/",
        "--exec",
        contract.linux_binaries["env"].path,
        "-i",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
    )
    assert scan[10:14] == (
        contract.linux_binaries["python3"].path,
        "-I",
        "-S",
        "-B",
    )
    assert contract.run_uuid in scan
    assert toolchain[3:10] == (
        "--cd",
        "/",
        "--exec",
        contract.linux_binaries["env"].path,
        "-i",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
    )
    assert toolchain[10:14] == (
        contract.linux_binaries["python3"].path,
        "-I",
        "-S",
        "-B",
    )
    assert qualifier.INVOCATION_POLICY["adversary_launches"] == 1
    assert qualifier.INVOCATION_POLICY["automatic_retries"] == 0


def test_hostile_windows_environment_is_not_inherited_by_wsl_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)
    monkeypatch.setenv("WSLENV", "LD_PRELOAD/u:PYTHONPATH/u")
    monkeypatch.setenv("LD_PRELOAD", "hostile")
    monkeypatch.setenv("PYTHONPATH", "hostile")
    environment = qualifier._wsl_windows_environment(contract)
    assert set(environment) == {"SystemRoot", "WINDIR", "WSL_UTF8"}
    assert "WSLENV" not in environment
    assert "LD_PRELOAD" not in environment
    assert "PYTHONPATH" not in environment


def test_linux_readback_binds_distro_rootfs_and_all_binary_identities(
    tmp_path: Path,
) -> None:
    contract, _path, _raw = _load(tmp_path)
    payload = {
        "schema": qualifier.LINUX_READBACK_SCHEMA,
        "status": "observed",
        "distro": contract.distribution,
        "kernel_release": contract.platform_identity["kernel_release"],
        "python_version": contract.linux_binaries["python3"].version,
        "boot_id": BOOT_ID,
        "distro_version": contract.platform_identity["distro_version"],
        "rootfs_identity": contract.platform_identity["rootfs_identity"],
        "os_release_sha256": contract.platform_identity["os_release_sha256"],
        "machine_id_sha256": contract.platform_identity["machine_id_sha256"],
        "binaries": {
            role: {
                "path": pin.path,
                "realpath": pin.path,
                "sha256": pin.sha256,
                "bytes": pin.bytes,
            }
            for role, pin in contract.linux_binaries.items()
        },
    }
    outcome = {
        "safe_for_followup": True,
        "forced_termination_attempts": 0,
        "stdout": json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
    }
    assert qualifier._validate_linux_readback(contract, outcome) == payload

    for key in ("distro_version", "rootfs_identity"):
        mutated = copy.deepcopy(payload)
        mutated[key] = "mismatch" if key == "distro_version" else "f" * 64
        with pytest.raises(
            qualifier.R7S2QualificationError,
            match="linux_platform_identity_mismatch",
        ):
            qualifier._validate_linux_readback(
                contract,
                {
                    **outcome,
                    "stdout": json.dumps(mutated, sort_keys=True, separators=(",", ":")) + "\n",
                },
            )


def test_analysis_accepts_exact_launcher_exit_live_residual_wslhost_overlap(
    tmp_path: Path,
) -> None:
    contract, _path, _raw = _load(tmp_path)
    linux, initial, launch, scans = _analysis_inputs(contract)
    result = qualifier.analyse_observation(
        contract,
        linux_readback=linux,
        initial_scan=initial,
        launch=launch,
        observer_scans=scans,
    )
    assert result["passed"] is True
    assert result["classification"] == "qualified_non_credit"
    assert result["launcher_pids"] == [10, 11]
    assert result["launcher_pids_by_role"] == {
        "store_wsl": [11],
        "system32_wsl": [10],
    }
    assert result["wslhost_pids"] == [12]
    assert result["live_overlap_scan_sequence"] == 1
    assert result["post_job_zero_scan_sequence"] == 2
    assert result["automatic_retry_count"] == 0
    assert result["forced_termination_attempts"] == 0
    assert result["completion_credit"] == "non_credit_only"


def test_ack_bound_process_group_residual_cannot_hide_after_uuid_tag_loss(
    tmp_path: Path,
) -> None:
    contract, _path, _raw = _load(tmp_path)
    linux, initial, launch, scans = _analysis_inputs(contract)
    detached = _record(run_uuid="uuid-tag-removed")
    assert detached["run_uuid_match"] is False
    assert detached["process_group_match"] is True
    scans[-1]["outcome"] = _process_outcome(
        json.dumps([detached], sort_keys=True, separators=(",", ":"))
    )
    result = qualifier.analyse_observation(
        contract,
        linux_readback=linux,
        initial_scan=initial,
        launch=launch,
        observer_scans=scans,
    )
    assert result["passed"] is False
    assert "post_job_uuid_zero_scan_missing" in result["errors"]
    assert result["post_job_zero_scan_sequence"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_launcher_exit",
        "missing_store_launcher",
        "duplicate_pid_identity",
        "scan_before_launcher_exit",
        "wslhost_not_active",
        "active_zero_before_live_scan",
        "missing_final_zero",
        "uuid_swap",
        "pgrp_swap",
        "session_swap",
        "stdio_still_open",
        "non_stdio_fd_still_open",
        "forced_termination",
        "missing_full_accounting",
    ],
)
def test_analysis_fails_closed_for_temporal_and_identity_mutations(
    tmp_path: Path, mutation: str
) -> None:
    contract, _path, _raw = _load(tmp_path)
    linux, initial, launch, scans = _analysis_inputs(contract)
    if mutation == "missing_launcher_exit":
        launch["events"] = launch["events"][1:]
    elif mutation == "missing_store_launcher":
        launch["identities"][1]["image"] = launch["identities"][0]["image"]
    elif mutation == "duplicate_pid_identity":
        launch["identities"][1]["pid"] = launch["identities"][0]["pid"]
    elif mutation == "scan_before_launcher_exit":
        scans[0]["started_monotonic_ns"] = 105
    elif mutation == "wslhost_not_active":
        launch["accounting"][0]["active_pids"] = []
    elif mutation == "active_zero_before_live_scan":
        launch["events"][-1]["monotonic_ns"] = 225
    elif mutation == "missing_final_zero":
        scans.pop()
    elif mutation == "uuid_swap":
        scans[0]["outcome"]["stdout"] = _json_line([_record(run_uuid="wrong")])
    elif mutation == "pgrp_swap":
        record = _record()
        record["pgrp"] += 1
        scans[0]["outcome"]["stdout"] = _json_line([record])
    elif mutation == "session_swap":
        record = _record()
        record["session"] += 1
        scans[0]["outcome"]["stdout"] = _json_line([record])
    elif mutation == "stdio_still_open":
        record = _record()
        record["open_fd_count"] = 1
        record["stdio_fds_present"] = [1]
        scans[0]["outcome"]["stdout"] = _json_line([record])
    elif mutation == "non_stdio_fd_still_open":
        record = _record()
        record["open_fd_count"] = 1
        scans[0]["outcome"]["stdout"] = _json_line([record])
    elif mutation == "forced_termination":
        scans[0]["outcome"]["forced_termination_attempts"] = 1
        scans[0]["outcome"]["safe_for_followup"] = False
    elif mutation == "missing_full_accounting":
        launch.pop("accounting")

    result = qualifier.analyse_observation(
        contract,
        linux_readback=linux,
        initial_scan=initial,
        launch=launch,
        observer_scans=scans,
    )
    assert result["passed"] is False
    assert result["classification"] == "zero_credit_failure"
    assert result["manual_intervention_required"] is True
    assert result["completion_credit"] == "non_credit_only"


def test_initial_residual_is_fail_closed(tmp_path: Path) -> None:
    contract, _path, _raw = _load(tmp_path)
    linux, initial, launch, scans = _analysis_inputs(contract)
    initial["stdout"] = _json_line([_record()])
    result = qualifier.analyse_observation(
        contract,
        linux_readback=linux,
        initial_scan=initial,
        launch=launch,
        observer_scans=scans,
    )
    assert result["passed"] is False
    assert "initial_uuid_residual_nonzero" in result["errors"]


def test_atomic_publication_never_overwrites_and_uses_short_temp(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    first = qualifier._atomic_exclusive_json(path, {"value": 1})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        qualifier._atomic_exclusive_json(path, {"value": 2})
    assert path.read_bytes() == before
    assert first["sha256"] == hashlib.sha256(before).hexdigest()
    assert not list(tmp_path.glob(".t-*.tmp"))


def test_atomic_publication_reopens_and_rejects_corrupt_final_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.json"

    class CorruptMove:
        argtypes: Any = None
        restype: Any = None

        def __call__(self, source: str, destination: str, _flags: int) -> int:
            Path(source).replace(destination)
            Path(destination).write_bytes(b"corrupt-after-move")
            return 1

    class Kernel32:
        MoveFileExW = CorruptMove()

    monkeypatch.setattr(qualifier.ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel32())
    with pytest.raises(
        qualifier.R7S2QualificationError,
        match="atomic_publication_readback_mismatch",
    ):
        qualifier._atomic_exclusive_json(path, {"value": 1})
    assert path.read_bytes() == b"corrupt-after-move"
    assert not list(tmp_path.glob(".t-*.tmp"))


def test_canonical_evidence_paths_fit_budget_with_short_leaf_and_temp() -> None:
    run_directory = qualifier.CANONICAL_EVIDENCE_ROOT / "wsl-12345678"
    emergency_directory = qualifier.CANONICAL_EVIDENCE_ROOT / "wsl-12345678-emergency-seal"
    paths = [
        run_directory,
        emergency_directory,
        emergency_directory / "emergency-seal.json",
        emergency_directory / ".t-12345678.tmp",
        *(
            run_directory / leaf
            for leaf in (
                "invocation-reservation.json",
                "qualification-evidence.json",
                "failure-evidence.json",
                "failure-seal.json",
                "qualification-index.json",
                "failure-index.json",
                ".t-12345678.tmp",
            )
        ),
    ]
    for path in paths:
        qualifier._assert_path_budget(path)
        assert len(str(path.resolve())) <= qualifier.WINDOWS_PATH_BUDGET


def test_reparse_parent_is_rejected_before_atomic_publication(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    try:
        junction.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    output = junction / "evidence.json"
    with pytest.raises(qualifier.R7S2QualificationError, match="reparse_component_forbidden"):
        qualifier._atomic_exclusive_json(output, {"value": 1})
    assert not (target / "evidence.json").exists()


def test_execute_requires_explicit_flag_before_contract_read_or_write(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(
        qualifier.R7S2QualificationError,
        match="production_main_argv_override_forbidden",
    ):
        qualifier.main(
            [
                "--contract",
                str(missing),
                "--expected-contract-sha256",
                "0" * 64,
                "--launch-index",
                str(tmp_path / "missing-index.json"),
                "--expected-launch-index-sha256",
                "0" * 64,
                "--outer-reservation",
                str(tmp_path / "missing-reservation.json"),
                "--expected-outer-reservation-sha256",
                "0" * 64,
            ]
        )
    assert not list(tmp_path.iterdir())


def test_entrypoint_rejects_ambient_stdlib_shadow_before_import(
    tmp_path: Path,
) -> None:
    script = Path(qualifier.__file__).resolve()
    marker = tmp_path / "shadow-imported.txt"
    (tmp_path / "argparse.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tmp_path)
    rejected = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert rejected.returncode != 0
    assert "isolated_interpreter_flags_required:-I -S -B" in (rejected.stdout + rejected.stderr)
    assert not marker.exists()

    isolated = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(script), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert isolated.returncode != 0
    assert "inner_bootstrap_attestation_mapping_required" in (isolated.stdout + isolated.stderr)
    assert not marker.exists()


def test_manual_latch_stops_observer_and_performs_no_post_launch_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)
    observer_started = threading.Event()
    launch_returned = threading.Event()
    scan_calls: list[tuple[str, bool]] = []

    class ScanRunner:
        def run(self, _command: Any, *, name: str, **_kwargs: Any) -> _FakeOutcome:
            scan_calls.append((name, launch_returned.is_set()))
            if "concurrent-proc-observer" in name:
                observer_started.set()
            return _FakeOutcome(
                {
                    "safe_for_followup": True,
                    "forced_termination_attempts": 0,
                    "stdout": "[]\n" if "linux-toolchain" not in name else "{}\n",
                    "residual_pids": [],
                }
            )

    class UnsafeLaunchRunner:
        def run(self, _command: Any, **_kwargs: Any) -> _FakeOutcome:
            assert observer_started.wait(timeout=1)
            launch_returned.set()
            return _FakeOutcome(
                {
                    "safe_for_followup": False,
                    "forced_termination_attempts": 0,
                    "manual_intervention_required": True,
                    "residual_pids": [707],
                    "active_process_zero": False,
                    "streams_drained": True,
                    "identity_coverage_complete": True,
                    "stdout": _json_line(_ack()),
                    "identities": [],
                    "events": [],
                    "accounting": [],
                }
            )

    monkeypatch.setattr(
        qualifier,
        "_validate_linux_readback",
        lambda _contract, _outcome: {"boot_id": BOOT_ID},
    )
    runner = qualifier.ConcurrentQualification(
        contract, launch_runner=UnsafeLaunchRunner(), scan_runner=ScanRunner()
    )
    evidence = runner.run()
    assert evidence["analysis"]["passed"] is False
    assert runner.call_counts["adversary_launches"] == 1
    assert runner.call_counts["post_launch_zero_scans"] == 0
    assert not any(
        after_return for name, after_return in scan_calls if "concurrent-proc-observer" in name
    )
    assert not any("post-launch-uuid-zero-scan" in name for name, _ in scan_calls)


def test_launch_exception_stops_and_joins_observer_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)
    observer_started = threading.Event()

    class ScanRunner:
        def run(self, _command: Any, *, name: str, **_kwargs: Any) -> _FakeOutcome:
            if "concurrent-proc-observer" in name:
                observer_started.set()
            return _FakeOutcome(
                {
                    "safe_for_followup": True,
                    "forced_termination_attempts": 0,
                    "stdout": "[]\n" if "linux-toolchain" not in name else "{}\n",
                    "residual_pids": [],
                }
            )

    class TypedLaunchFailure(RuntimeError):
        def to_dict(self) -> dict[str, Any]:
            return {
                "manual_intervention_required": True,
                "safe_for_followup": False,
                "residual_pids": [808],
                "forced_termination_attempts": 0,
                "events": [{"event": "containment_failure"}],
                "accounting": [{"active_processes": 1, "active_pids": [808]}],
            }

    class RaisingLaunchRunner:
        calls = 0

        def run(self, _command: Any, **_kwargs: Any) -> _FakeOutcome:
            self.calls += 1
            assert observer_started.wait(timeout=1)
            raise TypedLaunchFailure("synthetic_launch_failure")

    monkeypatch.setattr(
        qualifier,
        "_validate_linux_readback",
        lambda _contract, _outcome: {"boot_id": BOOT_ID},
    )
    launch_runner = RaisingLaunchRunner()
    runner = qualifier.ConcurrentQualification(
        contract, launch_runner=launch_runner, scan_runner=ScanRunner()
    )
    with pytest.raises(TypedLaunchFailure, match="synthetic_launch_failure"):
        runner.run()
    assert launch_runner.calls == 1
    assert runner.call_counts["post_launch_zero_scans"] == 0
    assert runner.partial_evidence["process_failures"][0]["typed_evidence"]["residual_pids"] == [
        808
    ]
    assert not any(
        thread.name == "pre-r8-r7s2-proc-observer" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_safe_launch_performs_exactly_one_post_launch_zero_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)
    observer_started = threading.Event()
    names: list[str] = []
    commands: list[tuple[str, ...]] = []

    class ScanRunner:
        def run(self, _command: Any, *, name: str, **_kwargs: Any) -> _FakeOutcome:
            names.append(name)
            commands.append(tuple(_command))
            if "concurrent-proc-observer" in name:
                observer_started.set()
            return _FakeOutcome(
                {
                    "safe_for_followup": True,
                    "forced_termination_attempts": 0,
                    "stdout": "[]\n" if "linux-toolchain" not in name else "{}\n",
                    "residual_pids": [],
                }
            )

    class SafeLaunchRunner:
        def run(self, _command: Any, **_kwargs: Any) -> _FakeOutcome:
            assert observer_started.wait(timeout=1)
            return _FakeOutcome(
                {
                    "safe_for_followup": True,
                    "forced_termination_attempts": 0,
                    "manual_intervention_required": False,
                    "residual_pids": [],
                    "active_process_zero": True,
                    "streams_drained": True,
                    "identity_coverage_complete": True,
                    "stdout": _json_line(_ack()),
                    "identities": [],
                    "events": [],
                    "accounting": [],
                }
            )

    monkeypatch.setattr(
        qualifier,
        "_validate_linux_readback",
        lambda _contract, _outcome: {"boot_id": BOOT_ID},
    )
    monkeypatch.setattr(
        qualifier,
        "analyse_observation",
        lambda *_args, **_kwargs: {
            "passed": True,
            "forced_termination_attempts": 0,
        },
    )
    runner = qualifier.ConcurrentQualification(
        contract, launch_runner=SafeLaunchRunner(), scan_runner=ScanRunner()
    )
    evidence = runner.run()
    assert evidence["analysis"]["passed"] is True
    assert runner.call_counts["adversary_launches"] == 1
    assert runner.call_counts["post_launch_zero_scans"] == 1
    assert sum("post-launch-uuid-pgrp-zero-scan" in name for name in names) == 1
    final_command = commands[names.index("pre-r8-r7s2-post-launch-uuid-pgrp-zero-scan")]
    assert final_command[-4:] == (RUN_UUID, "900", "9001", BOOT_ID)


def test_failure_is_sealed_once_without_retry_or_success_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)
    calls = 0

    class Failure:
        def __init__(self, _contract: Any) -> None:
            self.call_counts = {
                "linux_toolchain_readback": 0,
                "initial_scan": 0,
                "observer_scans": 0,
                "adversary_launches": 1,
                "post_launch_zero_scans": 0,
            }
            self.partial_evidence = {
                "launch": {
                    "manual_intervention_required": True,
                    "safe_for_followup": False,
                    "residual_pids": [4242],
                    "forced_termination_attempts": 0,
                    "events": [{"event": "residual_processes_observed"}],
                    "accounting": [{"active_processes": 1, "active_pids": [4242]}],
                }
            }

        def run(self) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            raise qualifier.R7S2QualificationError("synthetic_observer_failure")

    monkeypatch.setattr(qualifier, "ConcurrentQualification", Failure)
    contract = _authorize_for_test(contract, monkeypatch)
    result = qualifier.execute_once(
        contract,
        expected_contract_sha256=contract.contract_sha256,
    )
    assert result["passed"] is False
    assert calls == 1
    failure_path = contract.run_directory / "failure-seal.json"
    failure_index = contract.run_directory / "failure-index.json"
    payload = json.loads(failure_path.read_text(encoding="utf-8"))
    assert payload["automatic_retry_count"] == 0
    assert payload["forced_termination_attempts"] == 0
    assert payload["runtime_probe_calls"] == 0
    assert payload["lifecycle_calls"] == 0
    assert payload["completion_marker_created"] is False
    assert payload["partial_process_summary"]["residual_pids"] == [4242]
    assert payload["partial_evidence"]["launch"]["events"]
    assert failure_index.is_file()
    index_payload = json.loads(failure_index.read_text(encoding="utf-8"))
    assert index_payload["failure_seal"]["sha256"] == _sha256(failure_path)
    assert not (contract.run_directory / "qualification-evidence.json").exists()
    with pytest.raises(FileExistsError):
        qualifier.execute_once(
            contract,
            expected_contract_sha256=contract.contract_sha256,
        )
    assert calls == 1


def test_analysis_no_go_creates_sha_bound_failure_evidence_and_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)

    class AnalysisFailure:
        def __init__(self, _contract: Any) -> None:
            self.call_counts = {
                "linux_toolchain_readback": 1,
                "initial_scan": 1,
                "observer_scans": 2,
                "adversary_launches": 1,
                "post_launch_zero_scans": 1,
            }
            self.partial_evidence = {
                "launch": {
                    "manual_intervention_required": False,
                    "safe_for_followup": True,
                    "residual_pids": [],
                    "forced_termination_attempts": 0,
                }
            }

        def run(self) -> dict[str, Any]:
            return {
                "schema": qualifier.EVIDENCE_SCHEMA,
                "analysis": {
                    "passed": False,
                    "forced_termination_attempts": 0,
                    "errors": ["launcher_exit_linux_residual_overlap_unproven"],
                },
            }

    monkeypatch.setattr(qualifier, "ConcurrentQualification", AnalysisFailure)
    contract = _authorize_for_test(contract, monkeypatch)
    result = qualifier.execute_once(
        contract,
        expected_contract_sha256=contract.contract_sha256,
    )
    assert result["passed"] is False
    failure_evidence_path = contract.run_directory / "failure-evidence.json"
    failure_seal_path = contract.run_directory / "failure-seal.json"
    failure_index_path = contract.run_directory / "failure-index.json"
    assert failure_evidence_path.is_file()
    assert failure_seal_path.is_file()
    assert failure_index_path.is_file()
    seal = json.loads(failure_seal_path.read_text(encoding="utf-8"))
    assert seal["failure_evidence"]["sha256"] == _sha256(failure_evidence_path)
    assert seal["failed_stage"] == "concurrent_wsl_qualification_analysis"
    assert seal["automatic_retry_count"] == 0
    index = json.loads(failure_index_path.read_text(encoding="utf-8"))
    assert index["report"]["sha256"] == _sha256(failure_evidence_path)
    assert index["failure_seal"]["sha256"] == _sha256(failure_seal_path)
    assert not (contract.run_directory / "qualification-evidence.json").exists()


def test_qualified_non_credit_report_gets_atomic_non_phase_b2_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)

    class Qualified:
        def __init__(self, _contract: Any) -> None:
            self.call_counts = {
                "linux_toolchain_readback": 1,
                "initial_scan": 1,
                "observer_scans": 2,
                "adversary_launches": 1,
                "post_launch_zero_scans": 1,
            }
            self.partial_evidence: dict[str, Any] = {}

        def run(self) -> dict[str, Any]:
            return {
                "schema": qualifier.EVIDENCE_SCHEMA,
                "analysis": {"passed": True, "forced_termination_attempts": 0},
            }

    monkeypatch.setattr(qualifier, "ConcurrentQualification", Qualified)
    contract = _authorize_for_test(contract, monkeypatch)
    result = qualifier.execute_once(
        contract,
        expected_contract_sha256=contract.contract_sha256,
    )
    assert result["passed"] is True
    report_path = contract.run_directory / "qualification-evidence.json"
    index_path = contract.run_directory / "qualification-index.json"
    assert report_path.is_file()
    assert index_path.is_file()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["status"] == "qualified_non_credit"
    assert index["credit"] == "non_credit_only"
    assert index["report"]["sha256"] == _sha256(report_path)
    assert index["failure_seal"] is None
    assert index["completion_marker_created"] is False
    assert index["private_phase_b2_success_index_created"] is False


def test_reservation_publication_failure_uses_parent_emergency_seal_before_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)
    real_publish = qualifier._atomic_exclusive_json
    reservation_attempts = 0
    constructor_calls = 0

    class MustNotConstruct:
        def __init__(self, _contract: Any) -> None:
            nonlocal constructor_calls
            constructor_calls += 1
            raise AssertionError("child-capable qualification constructed before reservation")

    def fail_reservation(path: Path, value: dict[str, Any]) -> dict[str, Any]:
        nonlocal reservation_attempts
        if path.name == "invocation-reservation.json":
            reservation_attempts += 1
            raise PermissionError("synthetic_reservation_denied")
        return real_publish(path, value)

    monkeypatch.setattr(qualifier, "ConcurrentQualification", MustNotConstruct)
    monkeypatch.setattr(qualifier, "_atomic_exclusive_json", fail_reservation)
    contract = _authorize_for_test(contract, monkeypatch)
    result = qualifier.execute_once(
        contract,
        expected_contract_sha256=contract.contract_sha256,
    )
    assert result["passed"] is False
    assert result["irrecoverable_primary_publication_failure"] is True
    assert result["failed_stage"] == "invocation_reservation_publication"
    assert reservation_attempts == 1
    assert constructor_calls == 0
    emergency_path = contract.emergency_directory / "emergency-seal.json"
    payload = json.loads(emergency_path.read_text(encoding="utf-8"))
    assert payload["failed_stage"] == "invocation_reservation_publication"
    assert payload["child_launch_attempted"] is False
    assert payload["partial_inventory"] == []
    assert payload["automatic_retry_count"] == 0
    assert payload["forced_termination_attempts"] == 0
    assert payload["lifecycle_calls"] == 0
    assert result["emergency_seal"]["sha256"] == _sha256(emergency_path)


def test_qualified_report_publication_failure_uses_parent_emergency_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)

    class Qualified:
        def __init__(self, _contract: Any) -> None:
            self.call_counts = {"adversary_launches": 1}
            self.partial_evidence = {
                "launch": {
                    "forced_termination_attempts": 0,
                    "residual_pids": [],
                    "events": [],
                    "accounting": [],
                }
            }

        def run(self) -> dict[str, Any]:
            return {
                "schema": qualifier.EVIDENCE_SCHEMA,
                "analysis": {"passed": True, "forced_termination_attempts": 0},
            }

    real_publish = qualifier._atomic_exclusive_json
    report_attempts = 0

    def fail_report(path: Path, value: dict[str, Any]) -> dict[str, Any]:
        nonlocal report_attempts
        if path.name == "qualification-evidence.json":
            report_attempts += 1
            raise OSError("synthetic_report_write_failure")
        return real_publish(path, value)

    monkeypatch.setattr(qualifier, "ConcurrentQualification", Qualified)
    monkeypatch.setattr(qualifier, "_atomic_exclusive_json", fail_report)
    contract = _authorize_for_test(contract, monkeypatch)
    result = qualifier.execute_once(
        contract,
        expected_contract_sha256=contract.contract_sha256,
    )
    assert result["passed"] is False
    assert result["failed_stage"] == "qualification_report_publication"
    assert report_attempts == 1
    payload = json.loads(
        (contract.emergency_directory / "emergency-seal.json").read_text(encoding="utf-8")
    )
    assert payload["child_launch_attempted"] is True
    assert payload["automatic_retry_count"] == 0
    assert payload["completion_marker_created"] is False


def test_failure_seal_writer_failure_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)

    class AnalysisFailure:
        def __init__(self, _contract: Any) -> None:
            self.call_counts = {
                "linux_toolchain_readback": 0,
                "initial_scan": 0,
                "observer_scans": 0,
                "adversary_launches": 0,
                "post_launch_zero_scans": 0,
            }
            self.partial_evidence: dict[str, Any] = {}

        def run(self) -> dict[str, Any]:
            return {
                "schema": qualifier.EVIDENCE_SCHEMA,
                "analysis": {
                    "passed": False,
                    "forced_termination_attempts": 0,
                    "errors": ["synthetic_no_go"],
                },
            }

    real_publish = qualifier._atomic_exclusive_json
    seal_attempts = 0

    def fail_seal(path: Path, value: dict[str, Any]) -> dict[str, Any]:
        nonlocal seal_attempts
        if path.name == "failure-seal.json":
            seal_attempts += 1
            raise PermissionError("synthetic_seal_denied")
        return real_publish(path, value)

    monkeypatch.setattr(qualifier, "ConcurrentQualification", AnalysisFailure)
    monkeypatch.setattr(qualifier, "_atomic_exclusive_json", fail_seal)
    contract = _authorize_for_test(contract, monkeypatch)
    result = qualifier.execute_once(
        contract,
        expected_contract_sha256=contract.contract_sha256,
    )
    assert result["passed"] is False
    assert result["failed_stage"] == "primary_failure_seal_publication"
    assert seal_attempts == 1
    emergency_path = contract.emergency_directory / "emergency-seal.json"
    payload = json.loads(emergency_path.read_text(encoding="utf-8"))
    assert payload["failed_stage"] == "primary_failure_seal_publication"
    assert payload["automatic_retry_count"] == 0
    assert {item["name"] for item in payload["partial_inventory"]} == {
        "failure-evidence.json",
        "invocation-reservation.json",
    }


def test_emergency_seal_failure_is_distinct_and_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, _path, _raw = _load(tmp_path)
    real_publish = qualifier._atomic_exclusive_json
    attempts = {"reservation": 0, "emergency": 0}

    def fail_primary_and_emergency(path: Path, value: dict[str, Any]) -> dict[str, Any]:
        if path.name == "invocation-reservation.json":
            attempts["reservation"] += 1
            raise PermissionError("synthetic_reservation_denied")
        if path.name == "emergency-seal.json":
            attempts["emergency"] += 1
            raise OSError("synthetic_emergency_denied")
        return real_publish(path, value)

    monkeypatch.setattr(qualifier, "_atomic_exclusive_json", fail_primary_and_emergency)
    with pytest.raises(
        qualifier.R7S2QualificationError,
        match="emergency_seal_publication_failed_no_retry",
    ):
        contract = _authorize_for_test(contract, monkeypatch)
        qualifier.execute_once(
            contract,
            expected_contract_sha256=contract.contract_sha256,
        )
    assert attempts == {"reservation": 1, "emergency": 1}
    assert not (contract.emergency_directory / "emergency-seal.json").exists()


def test_emergency_seal_is_exclusive_and_never_overwritten(tmp_path: Path) -> None:
    contract, _path, _raw = _load(tmp_path)
    contract.run_directory.mkdir()
    inner_attestation = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1",
        "test_fixture": "verified_before_emergency_publication",
    }
    first = qualifier._publish_emergency_seal(
        contract,
        expected_contract_sha256="8" * 64,
        failed_stage="synthetic_primary_failure",
        exception=RuntimeError("first"),
        qualification=None,
        child_launch_attempted=False,
        inner_bootstrap_attestation=inner_attestation,
    )
    emergency_path = contract.emergency_directory / "emergency-seal.json"
    before = emergency_path.read_bytes()
    with pytest.raises(
        qualifier.R7S2QualificationError,
        match="emergency_seal_publication_failed_no_retry",
    ):
        qualifier._publish_emergency_seal(
            contract,
            expected_contract_sha256="8" * 64,
            failed_stage="synthetic_second_failure",
            exception=RuntimeError("second"),
            qualification=None,
            child_launch_attempted=False,
            inner_bootstrap_attestation=inner_attestation,
        )
    assert emergency_path.read_bytes() == before
    assert first["sha256"] == hashlib.sha256(before).hexdigest()
    payload = json.loads(before)
    assert payload["bootstrap_provenance"] == {
        "stage": "inner_bootstrap_verified_before_qualification",
        "inner_bootstrap_attestation": inner_attestation,
    }


def test_source_contains_no_service_or_process_termination_path() -> None:
    source = inspect.getsource(qualifier)
    forbidden = (
        "Terminate" + "JobObject",
        "task" + "kill",
        "wsl --" + "shutdown",
        "docker compose",
        "kubectl.exe",
        "'kubectl'",
        '"kubectl"',
        "subprocess.",
        ".ki" + "ll(",
        ".termi" + "nate(",
    )
    for token in forbidden:
        assert token not in source
    assert "observer.join()" in source
    assert "from evm.scale_validation.phase_b2_r7_process import" not in source
    assert "_bind_verified_process_runtime" in source
    assert "sys.path.insert" not in source
    assert "with scan_start_gate:" in source
    assert "launch_done.set()" in source
    assert 'if launch_dict.get("safe_for_followup"):' in source
    assert '"launch": launch_dict' in source
    assert '"observer_scans": observer_rows' in source


def test_fixture_is_double_fork_new_session_then_closes_every_inherited_fd() -> None:
    source = qualifier.DETACHED_DESCENDANT_SOURCE
    assert source.count("os.fork()") == 2
    assert source.count("os._exit(0)") == 3
    assert "os.setsid()" in source
    assert "os.listdir('/proc/self/fd')" in source
    assert "os.close(descriptor)" in source
    assert "time.sleep(duration)" in source
    assert "os.system" not in source
    scanner = qualifier.QUALIFICATION_SCANNER_SOURCE
    assert "(proc/'fd').iterdir()" in scanner
    assert "'open_fd_count':open_fd_count" in scanner
    assert "'auxiliary_read_status':'unreadable_residual' if unreadable else 'complete'" in scanner
    assert "'stdio_fds_present'" in scanner
    assert ".write_" not in scanner
    assert "unlink" not in scanner
