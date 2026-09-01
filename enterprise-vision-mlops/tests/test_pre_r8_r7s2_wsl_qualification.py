from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from scripts.dev import qualify_wsl_process_group_r7s2 as qualifier


PROJECT = Path(__file__).parents[1]
RUN_ID = "pre-r8-r7s2-wsl-20260901T140000Z-deadbeef"
RUN_UUID = "11111111-2222-4333-8444-555555555555"
ATTEMPT_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa"
EVIDENCE_LEAF = "wsl-66666666"
BOOT_ID = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"


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


def _write_contract(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    system32 = tmp_path / "System32"
    store = tmp_path / "Program Files" / "WSL"
    system32.mkdir()
    store.mkdir(parents=True)
    system_wsl = system32 / "wsl.exe"
    store_wsl = store / "wsl.exe"
    wslhost = store / "wslhost.exe"
    system_wsl.write_bytes(b"system32-wsl")
    store_wsl.write_bytes(b"store-wsl")
    wslhost.write_bytes(b"wslhost")

    parent_paths: dict[str, Path] = {}
    for role in qualifier.PARENT_ROLES:
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps({"role": role}), encoding="utf-8")
        parent_paths[role] = path

    script = Path(qualifier.__file__).resolve()
    process_module = PROJECT / "src" / "evm" / "scale_validation" / "phase_b2_r7_process.py"
    runner = PROJECT / "scripts" / "dev" / "run_x1_phase_b2_r7s1.py"
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
    contract: dict[str, Any] = {
        "schema": qualifier.CONTRACT_SCHEMA,
        "qualification_id": RUN_ID,
        "evidence_leaf": EVIDENCE_LEAF,
        "run_uuid": RUN_UUID,
        "attempt_id": ATTEMPT_ID,
        "evidence_root": str(evidence_root.resolve()),
        "distribution": "Ubuntu",
        "host_binaries": {
            "python": _file_pin(
                Path(sys.executable), version=qualifier.host_platform.python_version()
            ),
            "system32_wsl": _file_pin(system_wsl, version="2.7.11.0"),
            "store_wsl": _file_pin(store_wsl, version="2.7.11.0"),
            "wslhost": _file_pin(wslhost, version="2.7.11.0"),
        },
        "linux_binaries": linux_binaries,
        "platform_identity": {
            "windows_build": qualifier.host_platform.version(),
            "wsl_package_version": "2.7.11.0",
            "kernel_release": "test-kernel",
            "distro_version": "Ubuntu test",
            "rootfs_identity": "4" * 64,
            "os_release_sha256": "1" * 64,
            "machine_id_sha256": "2" * 64,
        },
        "parent_evidence": {role: _artifact_pin(path) for role, path in parent_paths.items()},
        "source_pins": {
            "qualification_script": _artifact_pin(script),
            "process_module": _artifact_pin(process_module),
            "r7s1_runner": _artifact_pin(runner),
        },
        "timeouts": {
            "launch_wrapper_seconds": 3,
            "launch_residual_seconds": 1,
            "stream_drain_seconds": 0.5,
            "scan_wrapper_seconds": 1,
            "scan_residual_seconds": 0.5,
            "observer_deadline_seconds": 8,
            "observer_interval_seconds": 0.01,
            "observer_max_scans": 40,
        },
        "fixture": {
            "source_sha256": hashlib.sha256(
                qualifier.DETACHED_DESCENDANT_SOURCE.encode()
            ).hexdigest(),
            "lifetime_seconds": 0.2,
        },
        "invocation_policy": dict(qualifier.INVOCATION_POLICY),
    }
    path = tmp_path / "qualification-contract.json"
    path.write_text(
        json.dumps(contract, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return path, evidence_root, contract


def _load(tmp_path: Path) -> tuple[qualifier.QualificationContract, Path, dict[str, Any]]:
    path, evidence_root, raw = _write_contract(tmp_path)
    contract = qualifier.load_contract(
        path,
        expected_sha256=_sha256(path),
        expected_evidence_root=evidence_root,
    )
    return contract, path, raw


def _process_outcome(stdout: str, *, forced: int = 0) -> dict[str, Any]:
    return {
        "safe_for_followup": forced == 0,
        "forced_termination_attempts": forced,
        "stdout": stdout,
    }


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
        "stdout": json.dumps(_ack(), sort_keys=True, separators=(",", ":")),
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
                "total_terminated_processes": 2,
                "active_pids": [12],
            },
            {
                "sequence": 2,
                "monotonic_ns": 300,
                "timestamp_utc": "2026-09-01T00:00:03+00:00",
                "total_processes": 3,
                "active_processes": 0,
                "total_terminated_processes": 3,
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
            "outcome": _process_outcome("[]"),
        },
    ]
    return linux_readback, initial, launch, scans


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
        )

    mutated = copy.deepcopy(raw)
    mutated["invocation_policy"]["automatic_retries"] = 1
    path.write_text(
        json.dumps(mutated, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(qualifier.R7S2QualificationError, match="invocation_policy_mismatch"):
        qualifier.load_contract(
            path,
            expected_sha256=_sha256(path),
            expected_evidence_root=contract.evidence_root,
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
    path.write_text(
        json.dumps(raw, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(qualifier.R7S2QualificationError, match=error):
        qualifier.load_contract(
            path,
            expected_sha256=_sha256(path),
            expected_evidence_root=evidence_root,
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
    path.write_text(
        json.dumps(raw, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(qualifier.R7S2QualificationError, match="process_module_path_mismatch"):
        qualifier.load_contract(
            path,
            expected_sha256=_sha256(path),
            expected_evidence_root=evidence_root,
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("launch_wrapper_seconds", 31),
        ("launch_residual_seconds", 16),
        ("stream_drain_seconds", 11),
        ("scan_wrapper_seconds", 9),
        ("scan_residual_seconds", 6),
        ("observer_deadline_seconds", 61),
        ("observer_interval_seconds", 0.001),
        ("observer_max_scans", 129),
    ],
)
def test_self_consistent_unbounded_timeout_contract_is_rejected(
    tmp_path: Path, key: str, value: float
) -> None:
    path, evidence_root, raw = _write_contract(tmp_path)
    raw["timeouts"][key] = value
    path.write_text(
        json.dumps(raw, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(
        qualifier.R7S2QualificationError,
        match="timeout_order_or_bound_invalid",
    ):
        qualifier.load_contract(
            path,
            expected_sha256=_sha256(path),
            expected_evidence_root=evidence_root,
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
        "stdout": json.dumps(payload, sort_keys=True, separators=(",", ":")),
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
                {**outcome, "stdout": json.dumps(mutated)},
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
        scans[0]["outcome"]["stdout"] = json.dumps([_record(run_uuid="wrong")])
    elif mutation == "pgrp_swap":
        record = _record()
        record["pgrp"] += 1
        scans[0]["outcome"]["stdout"] = json.dumps([record])
    elif mutation == "session_swap":
        record = _record()
        record["session"] += 1
        scans[0]["outcome"]["stdout"] = json.dumps([record])
    elif mutation == "stdio_still_open":
        record = _record()
        record["open_fd_count"] = 1
        record["stdio_fds_present"] = [1]
        scans[0]["outcome"]["stdout"] = json.dumps([record])
    elif mutation == "non_stdio_fd_still_open":
        record = _record()
        record["open_fd_count"] = 1
        scans[0]["outcome"]["stdout"] = json.dumps([record])
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
    initial["stdout"] = json.dumps([_record()])
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
    real_link = qualifier.os.link

    def corrupt_after_link(source: Path, destination: Path, **kwargs: Any) -> None:
        real_link(source, destination, **kwargs)
        Path(destination).write_bytes(b"corrupt-after-link")

    monkeypatch.setattr(qualifier.os, "link", corrupt_after_link)
    with pytest.raises(
        qualifier.R7S2QualificationError,
        match="atomic_publication_readback_mismatch",
    ):
        qualifier._atomic_exclusive_json(path, {"value": 1})
    assert path.read_bytes() == b"corrupt-after-link"
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
        match="explicit_execute_non_credit_once_required",
    ):
        qualifier.main(
            [
                "--contract",
                str(missing),
                "--expected-contract-sha256",
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
    assert isolated.returncode == 0
    assert "--execute-non-credit-once" in isolated.stdout
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
                    "stdout": "[]" if "linux-toolchain" not in name else "{}",
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
                    "stdout": "",
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
                    "stdout": "[]" if "linux-toolchain" not in name else "{}",
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

    class ScanRunner:
        def run(self, _command: Any, *, name: str, **_kwargs: Any) -> _FakeOutcome:
            names.append(name)
            if "concurrent-proc-observer" in name:
                observer_started.set()
            return _FakeOutcome(
                {
                    "safe_for_followup": True,
                    "forced_termination_attempts": 0,
                    "stdout": "[]" if "linux-toolchain" not in name else "{}",
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
                    "stdout": "",
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
    assert sum("post-launch-uuid-zero-scan" in name for name in names) == 1


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
    result = qualifier.execute_once(contract, expected_contract_sha256="a" * 64)
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
        qualifier.execute_once(contract, expected_contract_sha256="a" * 64)
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
    result = qualifier.execute_once(contract, expected_contract_sha256="b" * 64)
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
    result = qualifier.execute_once(contract, expected_contract_sha256="d" * 64)
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
    result = qualifier.execute_once(contract, expected_contract_sha256="e" * 64)
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
    result = qualifier.execute_once(contract, expected_contract_sha256="f" * 64)
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
    result = qualifier.execute_once(contract, expected_contract_sha256="c" * 64)
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
        qualifier.execute_once(contract, expected_contract_sha256="9" * 64)
    assert attempts == {"reservation": 1, "emergency": 1}
    assert not (contract.emergency_directory / "emergency-seal.json").exists()


def test_emergency_seal_is_exclusive_and_never_overwritten(tmp_path: Path) -> None:
    contract, _path, _raw = _load(tmp_path)
    contract.run_directory.mkdir()
    first = qualifier._publish_emergency_seal(
        contract,
        expected_contract_sha256="8" * 64,
        failed_stage="synthetic_primary_failure",
        exception=RuntimeError("first"),
        qualification=None,
        child_launch_attempted=False,
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
        )
    assert emergency_path.read_bytes() == before
    assert first["sha256"] == hashlib.sha256(before).hexdigest()


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
    assert "'open_fd_count':len(fds)" in scanner
    assert "'stdio_fds_present'" in scanner
    assert ".write_" not in scanner
    assert "unlink" not in scanner
