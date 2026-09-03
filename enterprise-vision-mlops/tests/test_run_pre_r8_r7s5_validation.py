from __future__ import annotations

import dis
import inspect
import os
import stat
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Mapping

import pytest

from scripts.dev import publish_pre_r8_r7s5_review as publisher
from scripts.dev import run_pre_r8_r7s5_validation as runner
from evm.scale_validation.phase_b2_r7s3_process import (
    JobAccountingSnapshot,
    JobEvent,
    ProcessIdentity,
)


PROJECT_ROOT = Path(__file__).parents[1]
REPOSITORY = PROJECT_ROOT.parent
GIT = runner._resolved_executable("git")
POWERSHELL = runner._resolved_executable("powershell.exe")


def _current_untracked_inventory() -> dict[str, object]:
    completed = subprocess.run(
        (str(GIT), "ls-files", "--others", "--exclude-standard", "-z"),
        cwd=REPOSITORY,
        capture_output=True,
        check=True,
    )
    raw = completed.stdout.decode("utf-8")
    assert not raw or raw.endswith("\0")
    paths = raw[:-1].split("\0") if raw else []
    paths = [
        path
        for path in paths
        if not PurePosixPath(path)
        .name.casefold()
        .endswith(runner._IMPORT_ACTIVE_UNTRACKED_SUFFIXES)
        and PurePosixPath(path).name.casefold() not in runner._TOOL_CONTROL_UNTRACKED_BASENAMES
    ]
    return runner._inventory_from_untracked_paths(
        REPOSITORY,
        paths,
    )


def _contained_outcome(**overrides: object) -> runner.ProcessOutcome:
    run_uuid = "b2ad7fd0-7d34-4a3e-a670-70fa997a9513"
    executable = str(Path(sys.executable).resolve(strict=True))
    pid = 43210
    parent_pid = 12345
    timestamp = "2026-09-02T00:00:00+00:00"
    executable_sha256 = runner.sha256_file(Path(executable))
    events = (
        JobEvent(1, "job_created", 1, timestamp, details={"run_uuid": run_uuid}),
        JobEvent(2, "root_created_suspended", 2, timestamp, pid=pid),
        JobEvent(
            3,
            "job_membership_verified",
            3,
            timestamp,
            pid=pid,
            details={"active_processes": 1, "job_limit_flags": 0},
        ),
        JobEvent(4, "identity_observed", 4, timestamp, pid=pid),
        JobEvent(5, "root_resumed", 5, timestamp, pid=pid),
        JobEvent(7, "active_process_count_zero", 7, timestamp),
        JobEvent(8, "streams_drained", 8, timestamp),
    )
    identities = (
        ProcessIdentity(
            pid=pid,
            ppid=parent_pid,
            creation_time_ns=1_000_000_000,
            creation_time_utc=timestamp,
            image=executable,
            run_uuid=run_uuid,
            observed_sequence=4,
        ),
    )
    accounting = (
        JobAccountingSnapshot(
            sequence=6,
            monotonic_ns=6,
            timestamp_utc=timestamp,
            total_processes=1,
            active_processes=0,
            total_terminated_processes=0,
            active_pids=(),
        ),
    )
    value = runner.ProcessOutcome(
        name="test",
        run_uuid=run_uuid,
        command=(executable, "--version"),
        started_at_utc="2026-09-02T00:00:00+00:00",
        ended_at_utc="2026-09-02T00:00:01+00:00",
        duration_seconds=1.0,
        timed_out=False,
        cancelled=False,
        return_code=2,
        manual_intervention_required=False,
        residual_pids=(),
        stdout="",
        stderr="",
        stdout_drained=True,
        stderr_drained=True,
        streams_drained=True,
        active_process_zero=True,
        final_active_process_count=0,
        identity_coverage_complete=True,
        safe_for_followup=False,
        forced_termination_attempts=0,
        job_limit_flags=0,
        identities=identities,
        events=events,
        accounting=accounting,
        executable_identity={
            "path": executable,
            "sha256": executable_sha256,
            "bytes": Path(executable).stat().st_size,
            "device": 1,
            "file_id": 2,
            "expected_sha256": executable_sha256,
            "pin_required": True,
            "pin_match": True,
            "measurement_scope": "immediately_before_CreateProcessW",
            "handle_lock_held_through_create": True,
            "handle_lock_share_mode": "FILE_SHARE_READ_only",
            "handle_lock_inheritable": False,
            "ancestor_directory_locks_held_through_create": True,
            "ancestor_directory_lock_count": 4,
            "ancestor_directory_lock_share_mode": "FILE_SHARE_READ_WRITE_no_delete",
            "path_lock_scope": "all_nonroot_ancestors_and_leaf",
            "pre_kernel_create_gate_required": True,
            "pre_kernel_create_gate_passed": True,
            "pre_kernel_create_gate_invocations": 1,
            "pre_kernel_remaining_seconds": 3_000.0,
            "pre_kernel_required_seconds": 2_000.0,
        },
        stdout_total_bytes=0,
        stderr_total_bytes=0,
        stdout_capture_overflow=False,
        stderr_capture_overflow=False,
        stream_cleanup={
            "schema": "evm.phase-b2.stream-reader-cleanup.v1",
            "reason": "stream_drain_gate",
            "read_handle_owner": "reader_thread",
            "bounded_by_restore_deadline": True,
            "readers": [
                {
                    "stream": stream,
                    "started": True,
                    "native_thread_id": native_id,
                    "drained_before_cleanup": True,
                    "exited_before_cleanup": True,
                    "cancel_attempted": False,
                    "cancel_succeeded": False,
                    "no_pending_io": False,
                    "cancel_error_code": None,
                    "exited_after_cleanup": True,
                    "thread_alive_after_cleanup": False,
                    "read_handle_close_scope": "reader_read_pipe_finally",
                    "bounded_join_timeout_seconds": 0.0,
                }
                for stream, native_id in (("stdout", 101), ("stderr", 102))
            ],
            "all_reader_threads_exited": True,
            "forced_termination_attempts": 0,
        },
    )
    value = replace(value, **overrides)
    if "stdout" in overrides and "stdout_total_bytes" not in overrides:
        value = replace(value, stdout_total_bytes=len(value.stdout.encode("utf-8")))
    if "stderr" in overrides and "stderr_total_bytes" not in overrides:
        value = replace(value, stderr_total_bytes=len(value.stderr.encode("utf-8")))
    return value


def _outcome_for_command(
    outcome: runner.ProcessOutcome,
    argv: tuple[str, ...],
    name: str,
) -> runner.ProcessOutcome:
    executable = Path(argv[0]).resolve(strict=True)
    digest = runner.sha256_file(executable)
    return replace(
        outcome,
        name=name,
        command=argv,
        executable_identity={
            "path": str(executable),
            "sha256": digest,
            "bytes": executable.stat().st_size,
            "device": 1,
            "file_id": 2,
            "expected_sha256": digest,
            "pin_required": True,
            "pin_match": True,
            "measurement_scope": "immediately_before_CreateProcessW",
            "handle_lock_held_through_create": True,
            "handle_lock_share_mode": "FILE_SHARE_READ_only",
            "handle_lock_inheritable": False,
            "ancestor_directory_locks_held_through_create": True,
            "ancestor_directory_lock_count": 4,
            "ancestor_directory_lock_share_mode": "FILE_SHARE_READ_WRITE_no_delete",
            "path_lock_scope": "all_nonroot_ancestors_and_leaf",
            "pre_kernel_create_gate_required": True,
            "pre_kernel_create_gate_passed": True,
            "pre_kernel_create_gate_invocations": 1,
            "pre_kernel_remaining_seconds": 3_000.0,
            "pre_kernel_required_seconds": 2_000.0,
        },
        identities=(replace(outcome.identities[0], image=str(executable)),),
    )


class _FakePublication:
    def __init__(self, path: Path, raw: bytes) -> None:
        self.path = path
        self.raw = raw

    def to_dict(self) -> dict[str, object]:
        return {
            "final_path": str(self.path),
            "temporary_leaf": f".{self.path.name}.partial",
            "sha256": runner.sha256_bytes(self.raw),
            "bytes": len(self.raw),
            "directory_flush_succeeded": True,
            "replace_if_exists": False,
            "same_handle_readback": True,
            "file_identity_stable_across_rename": True,
        }


class _FakeOutput:
    def __init__(self, path: Path, *, fail_publish: bool = False) -> None:
        self.path = path
        self.path.mkdir()
        self.fail_publish = fail_publish
        self.close_count = 0

    def publish(self, leaf: str, raw: bytes) -> _FakePublication:
        if self.fail_publish:
            raise runner.ValidationRunnerError("injected_publish_failure")
        path = self.path / leaf
        path.write_bytes(raw)
        return _FakePublication(path, raw)

    def close(self) -> None:
        self.close_count += 1


def _args(tmp_path: Path) -> runner.argparse.Namespace:
    inventory = _current_untracked_inventory()
    python_sha256 = runner.sha256_file(Path(sys.executable))
    pins = {
        "python_general": (Path(sys.executable), python_sha256),
        "python_host": (Path(sys.executable), python_sha256),
        "python_ruff": (Path(sys.executable), python_sha256),
        "git": (GIT, runner.sha256_file(GIT)),
        "powershell": (POWERSHELL, runner.sha256_file(POWERSHELL)),
    }
    validation_run_uuid = "10000000-0000-4000-8000-000000000001"
    pycache_prefix = runner.validation_pycache_prefix(tmp_path, validation_run_uuid)
    specs = runner.build_command_specs(
        repository=REPOSITORY,
        project_root=PROJECT_ROOT,
        python_general=pins["python_general"][0],
        python_host=pins["python_host"][0],
        python_ruff=pins["python_ruff"][0],
        git_executable=pins["git"][0],
        git_executable_sha256=pins["git"][1],
        powershell_executable=pins["powershell"][0],
        pycache_prefix=pycache_prefix,
    )
    now = datetime.now(UTC)
    trusted_outer = PROJECT_ROOT / "scripts" / "dev" / "invoke_pre_r8_r7s7_review.ps1"
    trusted_outer_sha256 = runner.sha256_file(trusted_outer)
    work_order = {
        "schema": runner.WORK_ORDER_SCHEMA,
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "validation_run_uuid": validation_run_uuid,
        "validation_attempt_uuid": "20000000-0000-4000-8000-000000000002",
        "handoff_challenge_sha256": "c" * 64,
        "issued_at_utc": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at_utc": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "expected_head": "a" * 40,
        "expected_tree": "b" * 40,
        "tool_file_bindings": {
            name: runner.work_order_tool_binding(name, path, sha256)
            for name, (path, sha256) in sorted(pins.items())
        },
        "code_file_bindings": runner.work_order_code_file_bindings(
            trusted_outer, trusted_outer_sha256
        ),
        "immutable_checkout_namespace_authority": False,
        "runtime_stdlib_native_closure_verified": False,
        "command_invocation_sha256": runner.command_invocation_commitment(specs),
        "pycache_prefix": str(pycache_prefix),
    }
    authority_root = tmp_path.parent / f"{tmp_path.name}-authority-inputs"
    authority_root.mkdir()
    work_order_path = authority_root / "external-work-order.json"
    work_order_raw = runner.canonical_json_bytes(work_order)
    work_order_path.write_bytes(work_order_raw)
    telemetry = {
        "schema": runner.LIVE_TELEMETRY_SCHEMA,
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "observation_state": "unknown",
        "observation_scope": "internal_non_authoritative",
        "collector_authority_verified": False,
        "counts": {name: None for name in publisher.REQUIRED_ZERO_LIVE_CALLS},
        "raw_events_sha256": runner.sha256_bytes(runner.canonical_json_bytes([])),
    }
    telemetry_path = authority_root / "live-call-telemetry.json"
    telemetry_raw = runner.canonical_json_bytes(telemetry)
    telemetry_path.write_bytes(telemetry_raw)
    return runner.argparse.Namespace(
        repository=REPOSITORY,
        project_root=PROJECT_ROOT,
        output_parent=tmp_path,
        expected_output_parent=tmp_path,
        expected_output_parent_sha256=runner.output_parent_commitment(tmp_path)["sha256"],
        output_leaf="validation-output",
        python_general=Path(sys.executable),
        python_general_sha256=python_sha256,
        python_host=Path(sys.executable),
        python_host_sha256=python_sha256,
        python_ruff=Path(sys.executable),
        python_ruff_sha256=python_sha256,
        git=GIT,
        git_sha256=runner.sha256_file(GIT),
        powershell=POWERSHELL,
        powershell_sha256=runner.sha256_file(POWERSHELL),
        expected_untracked_count=inventory["count"],
        expected_untracked_path_list_sha256=inventory["path_list_sha256"],
        expected_untracked_content_inventory_sha256=inventory["content_inventory_sha256"],
        expected_head="a" * 40,
        expected_tree="b" * 40,
        external_work_order=work_order_path,
        external_work_order_sha256=runner.sha256_bytes(work_order_raw),
        live_call_telemetry=telemetry_path,
        live_call_telemetry_sha256=runner.sha256_bytes(telemetry_raw),
        _work_order_specs=specs,
        trusted_outer=trusted_outer,
        trusted_outer_sha256=trusted_outer_sha256,
        _validation_pycache_prefix=pycache_prefix,
    )


def _repin_work_order_specs(
    args: runner.argparse.Namespace, specs: tuple[runner.CommandSpec, ...]
) -> None:
    work_order = runner.publisher.read_json_mapping(args.external_work_order, "external_work_order")
    work_order["command_invocation_sha256"] = runner.command_invocation_commitment(specs)
    raw = runner.canonical_json_bytes(work_order)
    args.external_work_order.write_bytes(raw)
    args.external_work_order_sha256 = runner.sha256_bytes(raw)


def _single_command_plan(spec: runner.CommandSpec) -> dict[str, object]:
    return {
        "repository": str(REPOSITORY),
        "project_root": str(PROJECT_ROOT),
        "head": "a" * 40,
        "tree": "b" * 40,
        "commands": [
            {
                "name": spec.name,
                "tool": {"sha256": runner.sha256_file(Path(spec.argv[0]))},
            }
        ],
        "environment_commitment": {},
        "observation_scope": runner.VALIDATION_OBSERVATION_SCOPE,
        "sha256": "c" * 64,
    }


def _repository_observation(
    *, head: str = "a" * 40, tree: str = "b" * 40, tracked_clean: bool = True
) -> dict[str, object]:
    return {
        "head": head,
        "tree": tree,
        "tracked_clean": tracked_clean,
        "untracked_inventory": {"matches_expected": True},
    }


def test_external_work_order_and_raw_telemetry_are_external_digest_bound(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    pins = runner._executable_pins_from_args(args)
    validated = runner._validate_external_work_order(args, pins, args._work_order_specs)
    assert validated["authority_scope"] == "internal_non_authoritative"
    assert validated["authority_verified"] is False
    telemetry = runner._validate_live_call_telemetry(args)
    assert telemetry["payload"]["observation_state"] == "unknown"
    assert all(value is None for value in telemetry["payload"]["counts"].values())

    work_order = runner.publisher.read_json_mapping(args.external_work_order, "external_work_order")
    work_order["handoff_challenge_sha256"] = "d" * 64
    args.external_work_order.write_bytes(runner.canonical_json_bytes(work_order))
    with pytest.raises(runner.ValidationRunnerError, match="sha256_mismatch"):
        runner._validate_external_work_order(args, pins, args._work_order_specs)


def test_external_work_order_binds_python_tool_origin_version_and_content(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    pins = runner._executable_pins_from_args(args)
    validated = runner._validate_external_work_order(args, pins, args._work_order_specs)
    pytest_binding = validated["payload"]["tool_file_bindings"]["python_general"][
        "python_tool_module"
    ]
    assert pytest_binding["distribution"] == "pytest"
    assert pytest_binding["version"]
    assert pytest_binding["module_origins"]["pytest.__main__"]["sha256"]
    assert pytest_binding["content_file_count"] > 1
    assert pytest_binding["pth_processing_disabled"] is True
    dependencies = {item["name"] for item in pytest_binding["dependency_distributions"]}
    assert {"pytest", "pluggy", "iniconfig", "packaging"} <= dependencies
    assert any(item["path"].startswith("pluggy/") for item in pytest_binding["content_files"])

    work_order = runner.publisher.read_json_mapping(args.external_work_order, "external_work_order")
    work_order["tool_file_bindings"]["python_general"]["python_tool_module"][
        "content_inventory_sha256"
    ] = "0" * 64
    raw = runner.canonical_json_bytes(work_order)
    args.external_work_order.write_bytes(raw)
    args.external_work_order_sha256 = runner.sha256_bytes(raw)
    with pytest.raises(runner.ValidationRunnerError, match="binding_mismatch"):
        runner._validate_external_work_order(args, pins, args._work_order_specs)


def test_external_work_order_rejects_transitive_python_tool_dependency_mutation(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    pins = runner._executable_pins_from_args(args)
    work_order = runner.publisher.read_json_mapping(args.external_work_order, "external_work_order")
    records = work_order["tool_file_bindings"]["python_general"]["python_tool_module"][
        "content_files"
    ]
    dependency = next(item for item in records if item["path"].startswith("pluggy/"))
    dependency["sha256"] = "0" * 64
    raw = runner.canonical_json_bytes(work_order)
    args.external_work_order.write_bytes(raw)
    args.external_work_order_sha256 = runner.sha256_bytes(raw)
    with pytest.raises(runner.ValidationRunnerError, match="binding_mismatch"):
        runner._validate_external_work_order(args, pins, args._work_order_specs)


@pytest.mark.parametrize(
    "field",
    ["immutable_checkout_namespace_authority", "runtime_stdlib_native_closure_verified"],
)
def test_internal_work_order_cannot_promote_unproven_runtime_closure(
    tmp_path: Path,
    field: str,
) -> None:
    args = _args(tmp_path)
    pins = runner._executable_pins_from_args(args)
    work_order = runner.publisher.read_json_mapping(args.external_work_order, "external_work_order")
    work_order[field] = True
    raw = runner.canonical_json_bytes(work_order)
    args.external_work_order.write_bytes(raw)
    args.external_work_order_sha256 = runner.sha256_bytes(raw)
    with pytest.raises(runner.ValidationRunnerError, match="binding_mismatch"):
        runner._validate_external_work_order(args, pins, args._work_order_specs)


@pytest.mark.parametrize("mutation", ["missing", "digest", "duplicate", "path_escape"])
def test_external_work_order_rejects_python_tool_content_file_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    args = _args(tmp_path)
    pins = runner._executable_pins_from_args(args)
    work_order = runner.publisher.read_json_mapping(args.external_work_order, "external_work_order")
    module = work_order["tool_file_bindings"]["python_general"]["python_tool_module"]
    records = module["content_files"]
    if mutation == "missing":
        records.pop()
    elif mutation == "digest":
        records[0]["sha256"] = "0" * 64
    elif mutation == "duplicate":
        records.insert(1, dict(records[0]))
    else:
        records[0]["path"] = "../outside.py"
    raw = runner.canonical_json_bytes(work_order)
    args.external_work_order.write_bytes(raw)
    args.external_work_order_sha256 = runner.sha256_bytes(raw)
    with pytest.raises(runner.ValidationRunnerError, match="binding_mismatch"):
        runner._validate_external_work_order(args, pins, args._work_order_specs)


def test_project_preimport_code_closure_is_exactly_nineteen_files() -> None:
    outer = PROJECT_ROOT / "scripts" / "dev" / "invoke_pre_r8_r7s7_review.ps1"
    bindings = runner.work_order_code_file_bindings(outer, runner.sha256_file(outer))
    assert len(bindings) == 19
    assert {
        "evm_init",
        "scale_validation_init",
        "phase_b2_r7s3_handle_io",
        "phase_b2_r7s4_authority",
        "phase_b2_r7s4_evidence",
        "phase_b2_r7s5_evidence",
    } < set(bindings)
    assert len({value["path"].casefold() for value in bindings.values()}) == 19


@pytest.mark.skipif(os.name != "nt", reason="Windows retained-share semantics")
def test_retained_read_share_blocks_write_and_rename(tmp_path: Path) -> None:
    target = tmp_path / "pinned-tool.py"
    target.write_text("pass\n", encoding="utf-8")
    moved = tmp_path / "replaced-tool.py"
    command = (
        "$ErrorActionPreference='Stop';"
        f"$p='{str(target).replace(chr(39), chr(39) * 2)}';"
        f"$m='{str(moved).replace(chr(39), chr(39) * 2)}';"
        "$s=[IO.FileStream]::new($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);"
        "$w=$false;$r=$false;try{"
        "try{[IO.File]::WriteAllText($p,'tamper')}catch{$w=$true};"
        "try{Move-Item -LiteralPath $p -Destination $m -ErrorAction Stop}catch{$r=$true}"
        "}finally{$s.Dispose()};"
        "Write-Output ('write_blocked='+$w);Write-Output ('rename_blocked='+$r)"
    )
    completed = subprocess.run(
        (str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", command),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "write_blocked=True" in completed.stdout
    assert "rename_blocked=True" in completed.stdout
    assert target.read_text(encoding="utf-8") == "pass\n"
    assert not moved.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows retained directory-share semantics")
def test_directory_handle_does_not_claim_immutable_namespace_authority(tmp_path: Path) -> None:
    target = tmp_path / "pinned-directory"
    target.mkdir()
    moved = tmp_path / "replaced-directory"
    quoted_target = str(target).replace("'", "''")
    quoted_moved = str(moved).replace("'", "''")
    member = (
        '[System.Runtime.InteropServices.DllImport("kernel32.dll",CharSet='
        "System.Runtime.InteropServices.CharSet.Unicode,SetLastError=true)] "
        "public static extern Microsoft.Win32.SafeHandles.SafeFileHandle CreateFile("
        "string n,uint a,uint s,System.IntPtr x,uint d,uint f,System.IntPtr t);"
    )
    command = (
        "$ErrorActionPreference='Stop';"
        f"$n=Add-Type -PassThru -Namespace T -Name D -MemberDefinition '{member}';"
        f"$h=$n::CreateFile('{quoted_target}',0x80,1,[IntPtr]::Zero,3,0x02000000,[IntPtr]::Zero);"
        "if($h.IsInvalid){throw 'directory_handle_failed'};"
        "$blocked=$false;try{"
        f"try{{Move-Item -LiteralPath '{quoted_target}' -Destination '{quoted_moved}' -ErrorAction Stop}}catch{{$blocked=$true}}"
        "}finally{$h.Dispose()};Write-Output ('rename_blocked='+$blocked)"
    )
    completed = subprocess.run(
        (str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    # A directory handle plus a launch-edge identity recheck narrows ordinary
    # substitution, but Windows permits this parent namespace rename.  The
    # internal work order must therefore preserve an explicit hard NO-GO.
    assert "rename_blocked=False" in completed.stdout
    assert not target.exists()
    assert moved.is_dir()
    validation_dir = tmp_path / "work-order"
    validation_dir.mkdir()
    args = _args(validation_dir)
    work_order = runner.publisher.read_json_mapping(args.external_work_order, "external_work_order")
    assert work_order["immutable_checkout_namespace_authority"] is False
    assert work_order["runtime_stdlib_native_closure_verified"] is False


def test_external_work_order_expiry_and_observed_telemetry_self_claim_fail_closed(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    pins = runner._executable_pins_from_args(args)
    work_order = runner.publisher.read_json_mapping(args.external_work_order, "external_work_order")
    now = datetime.now(UTC)
    work_order["issued_at_utc"] = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    work_order["expires_at_utc"] = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    raw = runner.canonical_json_bytes(work_order)
    args.external_work_order.write_bytes(raw)
    args.external_work_order_sha256 = runner.sha256_bytes(raw)
    with pytest.raises(runner.ValidationRunnerError, match="expired"):
        runner._validate_external_work_order(args, pins, args._work_order_specs)

    telemetry = runner.publisher.read_json_mapping(args.live_call_telemetry, "live_call_telemetry")
    telemetry["observation_state"] = "observed"
    telemetry["collector_authority_verified"] = True
    telemetry["counts"] = {name: 0 for name in publisher.REQUIRED_ZERO_LIVE_CALLS}
    raw = runner.canonical_json_bytes(telemetry)
    args.live_call_telemetry.write_bytes(raw)
    args.live_call_telemetry_sha256 = runner.sha256_bytes(raw)
    with pytest.raises(runner.ValidationRunnerError, match="unobserved_contract_invalid"):
        runner._validate_live_call_telemetry(args)


def test_command_plan_reconstructs_exact_run_environment_including_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = (
        runner.CommandSpec("python-one", (sys.executable, "--version")),
        runner.CommandSpec("python-two", (sys.executable, "-c", "pass")),
    )
    expected = runner.build_child_environment(
        PROJECT_ROOT,
        (str(GIT), *(spec.argv[0] for spec in specs)),
    )
    monkeypatch.setattr(
        runner,
        "command_tool_identity",
        lambda spec, *, child_environment=None, **kwargs: {
            "name": spec.name,
            "environment_commitment": child_environment.commitment,
        },
    )

    plan = runner.command_plan(
        repository=REPOSITORY,
        project_root=PROJECT_ROOT,
        head="a" * 40,
        tree="b" * 40,
        specs=specs,
        expected_executable_sha256_by_command={
            spec.name: runner.sha256_file(Path(spec.argv[0])) for spec in specs
        },
    )

    assert plan["environment_commitment"] == expected.commitment
    assert all(
        command["tool"]["environment_commitment"] == expected.commitment
        for command in plan["commands"]
    )

    shared_executable = Path(sys.executable)
    shared_sha256 = runner.sha256_file(shared_executable)
    role_specs = (
        runner.CommandSpec(
            "r7s5-focused-pytest-py311",
            (str(shared_executable), "--version"),
            python_tool_distribution="pytest",
            work_order_tool_role="python_general",
        ),
        runner.CommandSpec(
            "pinned-host-pytest-py313",
            (str(shared_executable), "--version"),
            python_tool_distribution="pytest",
            work_order_tool_role="python_host",
        ),
        runner.CommandSpec(
            "ruff-check-0.12.2",
            (str(shared_executable), "--version"),
            python_tool_distribution="ruff",
            work_order_tool_role="python_ruff",
        ),
    )
    original_commitment = runner.command_invocation_commitment(role_specs)
    assert original_commitment != runner.command_invocation_commitment(
        (
            replace(role_specs[0], work_order_tool_role="python_host"),
            *role_specs[1:],
        )
    )
    module_bindings = {
        distribution: runner.python_tool_module_binding(shared_executable, distribution)
        for distribution in ("pytest", "ruff")
    }

    def shared_tool_identity(
        spec: runner.CommandSpec,
        *,
        child_environment: runner.ChildEnvironment | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        assert child_environment is not None
        return {
            "path": str(shared_executable.resolve()),
            "sha256": shared_sha256,
            "environment_commitment": child_environment.commitment,
            "python_tool_module": module_bindings[spec.python_tool_distribution],
        }

    monkeypatch.setattr(runner, "command_tool_identity", shared_tool_identity)
    work_order_bindings = {
        role: runner.work_order_tool_binding(role, shared_executable, shared_sha256)
        for role in ("python_general", "python_host", "python_ruff")
    }
    shared_plan = runner.command_plan(
        repository=REPOSITORY,
        project_root=PROJECT_ROOT,
        head="a" * 40,
        tree="b" * 40,
        specs=role_specs,
        expected_executable_sha256_by_command={spec.name: shared_sha256 for spec in role_specs},
        expected_work_order_tool_bindings=work_order_bindings,
    )
    assert [command["tool"]["work_order_binding_role"] for command in shared_plan["commands"]] == [
        "python_general",
        "python_host",
        "python_ruff",
    ]

    tampered_bindings = dict(work_order_bindings)
    tampered_bindings["python_ruff"] = work_order_bindings["python_general"]
    with pytest.raises(
        runner.ValidationRunnerError,
        match="command_plan_tool_work_order_binding_mismatch:ruff-check",
    ):
        runner.command_plan(
            repository=REPOSITORY,
            project_root=PROJECT_ROOT,
            head="a" * 40,
            tree="b" * 40,
            specs=role_specs,
            expected_executable_sha256_by_command={spec.name: shared_sha256 for spec in role_specs},
            expected_work_order_tool_bindings=tampered_bindings,
        )

    def stale_tool_identity(
        spec: runner.CommandSpec,
        *,
        child_environment: runner.ChildEnvironment | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        value = shared_tool_identity(
            spec,
            child_environment=child_environment,
            **kwargs,
        )
        value["python_tool_module"] = {"distribution": "stale"}
        return value

    monkeypatch.setattr(runner, "command_tool_identity", stale_tool_identity)
    with pytest.raises(
        runner.ValidationRunnerError,
        match="command_plan_tool_work_order_binding_mismatch:r7s5-focused",
    ):
        runner.command_plan(
            repository=REPOSITORY,
            project_root=PROJECT_ROOT,
            head="a" * 40,
            tree="b" * 40,
            specs=role_specs,
            expected_executable_sha256_by_command={spec.name: shared_sha256 for spec in role_specs},
            expected_work_order_tool_bindings=work_order_bindings,
        )

    metadata_calls: list[str] = []
    monkeypatch.setattr(
        runner,
        "command_tool_identity",
        lambda spec, **kwargs: metadata_calls.append(spec.name),
    )
    unsupported = runner.CommandSpec(
        "unmapped-pytest-command",
        (str(shared_executable), "--version"),
        python_tool_distribution="pytest",
        work_order_tool_role="python_general",
    )
    with pytest.raises(
        runner.ValidationRunnerError,
        match="command_plan_work_order_tool_contract_invalid",
    ):
        runner.command_plan(
            repository=REPOSITORY,
            project_root=PROJECT_ROOT,
            head="a" * 40,
            tree="b" * 40,
            specs=(unsupported,),
            expected_executable_sha256_by_command={unsupported.name: shared_sha256},
            expected_work_order_tool_bindings=work_order_bindings,
        )
    assert metadata_calls == []

    downgraded = replace(role_specs[0], python_tool_distribution=None)
    missing_role = replace(role_specs[0], work_order_tool_role=None)
    stale_role_bindings = {**work_order_bindings, "stale_role": work_order_bindings["python_host"]}
    for rejected_specs, rejected_bindings in (
        ((downgraded, *role_specs[1:]), work_order_bindings),
        ((missing_role, *role_specs[1:]), work_order_bindings),
        (role_specs, stale_role_bindings),
    ):
        with pytest.raises(
            runner.ValidationRunnerError,
            match="command_plan_work_order_tool_contract_invalid",
        ):
            runner.command_plan(
                repository=REPOSITORY,
                project_root=PROJECT_ROOT,
                head="a" * 40,
                tree="b" * 40,
                specs=rejected_specs,
                expected_executable_sha256_by_command={
                    spec.name: shared_sha256 for spec in rejected_specs
                },
                expected_work_order_tool_bindings=rejected_bindings,
            )
    assert metadata_calls == []


def test_command_plan_passes_independent_executable_pin_before_tool_metadata_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = runner.CommandSpec("python-one", (sys.executable, "--version"))
    expected_sha256 = runner.sha256_file(Path(sys.executable))
    observed: list[str | None] = []

    def fake_identity(
        _spec: runner.CommandSpec,
        *,
        child_environment: runner.ChildEnvironment | None = None,
        expected_executable_sha256: str | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        assert child_environment is not None
        observed.append(expected_executable_sha256)
        return {"environment_commitment": child_environment.commitment}

    monkeypatch.setattr(runner, "command_tool_identity", fake_identity)
    runner.command_plan(
        repository=REPOSITORY,
        project_root=PROJECT_ROOT,
        head="a" * 40,
        tree="b" * 40,
        specs=(spec,),
        expected_executable_sha256_by_command={spec.name: expected_sha256},
    )

    assert observed == [expected_sha256]


def test_command_tool_independent_pin_mismatch_runs_no_metadata_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        runner,
        "_run_metadata_child",
        lambda *args, **kwargs: calls.append((*args, kwargs)),
    )
    spec = runner.CommandSpec("pinned-runtime", (sys.executable, "--version"))

    with pytest.raises(runner.ValidationRunnerError, match="independent_pin_mismatch"):
        runner.command_tool_identity(spec, expected_executable_sha256="0" * 64)

    assert calls == []


def test_command_tool_metadata_ledger_retains_every_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = runner.CommandSpec("pinned-runtime", (sys.executable, "--version"))
    executable_sha256 = runner.sha256_file(Path(sys.executable))
    first = _contained_outcome(return_code=0, stdout="Python 3.11.15\n")
    second_failure = runner.MetadataChildError(
        "synthetic_runtime_failure",
        name="runtime-version-pinned-runtime",
        failure_kind="exit_nonzero",
        process_evidence=_contained_outcome(return_code=7).to_dict(),
    )
    calls = 0

    def metadata_child(*args: object, **kwargs: object) -> runner.ProcessOutcome:
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        raise second_failure

    monkeypatch.setattr(runner, "_run_metadata_child", metadata_child)
    evidence: list[dict[str, object]] = []
    with pytest.raises(runner.MetadataChildError, match="runtime_failure"):
        runner.command_tool_identity(
            spec,
            expected_executable_sha256=executable_sha256,
            metadata_evidence=evidence,
        )

    assert calls == 2
    assert [item["status"] for item in evidence] == ["PASS", "FAIL"]
    assert all(item["child_invoked"] is True for item in evidence)
    assert runner._metadata_child_call_count(evidence) == 2
    assert second_failure.evidence_recorded is True


def test_inventory_gate_runs_before_each_command_tool_metadata_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = runner.CommandSpec("pinned-runtime", (sys.executable, "--version"))
    executable_sha256 = runner.sha256_file(Path(sys.executable))
    order: list[str] = []

    def before_child(name: str) -> None:
        order.append(f"inventory:{name}")

    def metadata_child(argv: object, *, name: str, **kwargs: object) -> runner.ProcessOutcome:
        order.append(f"child:{name}")
        return _contained_outcome(return_code=0, stdout="Python 3.11.15\n")

    monkeypatch.setattr(runner, "_run_metadata_child", metadata_child)
    runner.command_tool_identity(
        spec,
        expected_executable_sha256=executable_sha256,
        before_metadata_child=before_child,
    )

    assert order == [
        "inventory:tool-version-pinned-runtime",
        "child:tool-version-pinned-runtime",
        "inventory:runtime-version-pinned-runtime",
        "child:runtime-version-pinned-runtime",
    ]


def test_run_validation_rejects_independent_pin_before_any_child_or_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    args.git_sha256 = "0" * 64
    child_calls = 0
    output_calls = 0

    class UnexpectedRunner:
        def __init__(self, *args: object, **kwargs: object) -> None:
            nonlocal child_calls
            child_calls += 1

    def unexpected_output(*args: object, **kwargs: object) -> _FakeOutput:
        nonlocal output_calls
        output_calls += 1
        raise AssertionError("output creation must remain unreachable")

    monkeypatch.setattr(runner, "WindowsJobProcessRunner", UnexpectedRunner)
    monkeypatch.setattr(runner._BoundValidationOutput, "create", unexpected_output)
    with pytest.raises(runner.ValidationRunnerError, match="git_independent_sha256_mismatch"):
        runner.run_validation(args)

    assert child_calls == 0
    assert output_calls == 0
    assert list(tmp_path.iterdir()) == []


def test_untracked_content_mutation_and_import_active_paths_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    evidence_file = repository / "evidence.json"
    evidence_file.write_text("before", encoding="utf-8")
    baseline = runner._inventory_from_untracked_paths(repository, ["evidence.json"])
    pin = runner.UntrackedInventoryPin(
        count=baseline["count"],
        path_list_sha256=baseline["path_list_sha256"],
        content_inventory_sha256=baseline["content_inventory_sha256"],
    )
    git_pin = runner.ExecutablePin(label="git", path=GIT, sha256=runner.sha256_file(GIT))
    environment = runner.build_child_environment(PROJECT_ROOT, (str(GIT),))
    git_calls = 0

    def fake_git(*args: object, **kwargs: object) -> str:
        nonlocal git_calls
        git_calls += 1
        if "--ignored" in args:
            return ""
        return "evidence.json\0"

    monkeypatch.setattr(runner, "_git", fake_git)
    evidence_file.write_text("after", encoding="utf-8")
    with pytest.raises(runner.ValidationRunnerError, match="content_inventory_sha256"):
        runner._verify_isolated_untracked_inventory(
            repository,
            pin=pin,
            git_pin=git_pin,
            child_environment=environment,
            metadata_evidence=[],
            phase="mutation",
        )
    assert git_calls == 2

    shadow = repository / "sitecustomize.py"
    shadow.write_text("raise SystemExit", encoding="utf-8")
    with pytest.raises(runner.ValidationRunnerError, match="import_active_path_forbidden"):
        runner._inventory_from_untracked_paths(repository, ["sitecustomize.py"])
    pth = repository / "hostile.PTH"
    pth.write_text("hostile", encoding="utf-8")
    with pytest.raises(runner.ValidationRunnerError, match="import_active_path_forbidden"):
        runner._inventory_from_untracked_paths(repository, ["hostile.PTH"])


@pytest.mark.parametrize(
    "relative",
    [
        "pytest.pyc",
        "pytest/__init__.pyo",
        "ruff.cp311-win_amd64.pyd",
        "evm/__init__.so",
        "pyproject.toml",
        ".ruff.toml",
        "setup.cfg",
        "tox.ini",
    ],
)
def test_nonignored_importable_or_tool_control_untracked_artifact_is_rejected(
    tmp_path: Path,
    relative: str,
) -> None:
    repository = tmp_path / "repository"
    candidate = repository / Path(*PurePosixPath(relative).parts)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"synthetic")
    with pytest.raises(runner.ValidationRunnerError, match="import_active_path_forbidden"):
        runner._inventory_from_untracked_paths(repository, [relative])


@pytest.mark.parametrize(
    "shadow_path",
    [
        "ignored/sitecustomize.pyc",
        "ignored/pytest/__init__.pyc",
        "ignored/ruff.cp311-win_amd64.pyd",
        "ignored/evm/__init__.so",
        "ignored/pyproject.toml",
    ],
)
def test_ignored_import_active_shadow_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    shadow_path: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    empty = runner._inventory_from_untracked_paths(repository, [])
    pin = runner.UntrackedInventoryPin(
        count=0,
        path_list_sha256=empty["path_list_sha256"],
        content_inventory_sha256=empty["content_inventory_sha256"],
    )
    git_pin = runner.ExecutablePin(label="git", path=GIT, sha256=runner.sha256_file(GIT))
    environment = runner.build_child_environment(PROJECT_ROOT, (str(GIT),))

    def fake_git(*args: object, **kwargs: object) -> str:
        return f"{shadow_path}\0" if "--ignored" in args else ""

    monkeypatch.setattr(runner, "_git", fake_git)
    with pytest.raises(runner.ValidationRunnerError, match="ignored_import_active_path_forbidden"):
        runner._verify_isolated_untracked_inventory(
            repository,
            pin=pin,
            git_pin=git_pin,
            child_environment=environment,
            metadata_evidence=[],
            phase="ignored-shadow",
        )


def test_validation_plan_is_exact_offline_and_has_no_retry_or_live_command() -> None:
    specs = runner.build_command_specs(
        repository=REPOSITORY,
        project_root=PROJECT_ROOT,
        python_general=Path(sys.executable),
        python_host=Path(sys.executable),
        python_ruff=Path(sys.executable),
        git_executable=GIT,
        git_executable_sha256=runner.sha256_file(GIT),
        powershell_executable=POWERSHELL,
    )
    assert {item.name for item in specs} == publisher.REQUIRED_VALIDATION_COMMANDS
    assert len(specs) == len(publisher.REQUIRED_VALIDATION_COMMANDS)
    manifest_spec = next(item for item in specs if item.name == "ci-manifest-validator")
    assert manifest_spec.argv[-2:] == ("--lane", "portable")
    assert '"status":"manual_intervention_required"' in manifest_spec.required_output_tokens
    workflow_spec = next(
        item for item in specs if item.name == "ci-active-workflow-required-rejection"
    )
    assert workflow_spec.expected_exit_code == 2
    assert "workflow_action_ref_inventory_mismatch" in workflow_spec.required_output_tokens
    compile_spec = next(item for item in specs if item.name == "py-compile-py311")
    assert compile_spec.argv[1:4] == ("-I", "-B", "-S")
    assert compile_spec.argv[4] == "-X"
    assert compile_spec.argv[5].startswith("pycache_prefix=")
    assert compile_spec.argv[6] == "-c"
    assert "py_compile" not in compile_spec.argv
    assert "in_memory_no_bytecode" in compile_spec.argv[7]
    assert compile_spec.required_output_tokens == ("py_compile_mode=in_memory_no_bytecode",)
    assert all(Path(item.argv[0]).is_absolute() for item in specs)
    for spec in specs:
        if Path(spec.argv[0]).resolve(strict=True) in {
            Path(sys.executable).resolve(strict=True),
        }:
            assert spec.argv[1:4] == ("-I", "-B", "-S")
    ast_spec = next(item for item in specs if item.name == "powershell-ast")
    assert str(GIT) in ast_spec.argv[-1]
    assert runner.sha256_file(GIT) in ast_spec.argv[-1]
    assert "& git " not in ast_spec.argv[-1]
    diff_spec = next(item for item in specs if item.name == "git-diff-check")
    assert diff_spec.argv[-4:] == (
        "diff",
        "--check",
        f"{runner.ci.EXPECTED_BASELINE_COMMIT}..HEAD",
        "--",
    )
    metadata_sequence = runner.expected_success_metadata_child_sequence(
        specs,
        git_executable=GIT,
    )
    assert len(metadata_sequence) == 5 + 18 * len(specs)
    assert metadata_sequence[:5] == runner._repository_snapshot_metadata_child_sequence(
        phase="initial_repository_preflight",
        git_executable=GIT,
    )
    assert all(Path(argv[0]).is_absolute() for _, _, argv in metadata_sequence)
    assert len({phase for _, phase, _ in metadata_sequence}) == len(metadata_sequence)
    rendered = "\n".join(" ".join(item.argv).lower() for item in specs)
    for forbidden in (
        "docker compose",
        "wsl --shutdown",
        "logman",
        "taskkill",
        "fresh_phase_b2",
        "integrated_v4",
    ):
        assert forbidden not in rendered


def test_git_diff_check_covers_committed_changes_since_pinned_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (str(GIT), "-C", str(tmp_path), *arguments),
            capture_output=True,
            text=True,
            check=False,
        )

    assert git("init").returncode == 0
    assert git("config", "user.email", "offline-validation@example.invalid").returncode == 0
    assert git("config", "user.name", "Offline Validation").returncode == 0
    (tmp_path / "probe.txt").write_bytes(b"clean\n")
    assert git("add", "probe.txt").returncode == 0
    assert git("commit", "-m", "baseline").returncode == 0
    baseline = git("rev-parse", "HEAD").stdout.strip()
    (tmp_path / "probe.txt").write_bytes(b"committed trailing whitespace \n")
    assert git("add", "probe.txt").returncode == 0
    assert git("commit", "-m", "introduce whitespace defect").returncode == 0
    assert git("status", "--porcelain=v1", "--untracked-files=no").stdout == ""

    monkeypatch.setattr(runner.ci, "EXPECTED_BASELINE_COMMIT", baseline)
    specs = runner.build_command_specs(
        repository=tmp_path,
        project_root=PROJECT_ROOT,
        python_general=Path(sys.executable),
        python_host=Path(sys.executable),
        python_ruff=Path(sys.executable),
        git_executable=GIT,
        git_executable_sha256=runner.sha256_file(GIT),
        powershell_executable=POWERSHELL,
    )
    diff_spec = next(item for item in specs if item.name == "git-diff-check")
    completed = subprocess.run(
        diff_spec.argv,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "trailing whitespace" in (completed.stdout + completed.stderr)
    assert f"{baseline}..HEAD" in diff_spec.argv


def test_isolated_python_bootstrap_loads_pytest_with_only_explicit_project_roots(
    tmp_path: Path,
) -> None:
    argv = runner._isolated_python_module_argv(
        Path(sys.executable),
        PROJECT_ROOT,
        "pytest",
        "--version",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tmp_path / "hostile-pythonpath")
    completed = subprocess.run(
        argv,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("pytest ")
    assert argv[1:4] == ("-I", "-B", "-S")
    assert argv[4] == "-X"
    assert argv[5].startswith("pycache_prefix=")
    asserted_prefix = Path(argv[5].removeprefix("pycache_prefix="))
    assert argv[6] == "-c"
    assert repr(str(PROJECT_ROOT.resolve(strict=True))) in argv[7]
    assert repr(str((PROJECT_ROOT / "src").resolve(strict=True))) in argv[7]
    assert "sys.flags.no_site==1" in argv[7]
    startup_source = inspect.getsource(runner._require_isolated_no_bytecode_startup)
    assert "sys.flags.no_site != 1" in startup_source
    assert "requires_python_I_B_S_startup" in startup_source
    assert not asserted_prefix.exists()
    bootstrap = argv[7]
    site_packages = runner.verified_site_packages_binding(Path(sys.executable))["path"]
    assert (
        f"[*sys.path,{str((PROJECT_ROOT / 'src').resolve())!r},"
        f"{str(PROJECT_ROOT.resolve())!r},{site_packages!r}]"
    ) in bootstrap
    module_source = Path(runner.__file__).read_text(encoding="utf-8")
    assert module_source.index('if __name__ == "__main__":') < module_source.index(
        "from scripts.dev import publish_pre_r8_r7s5_review"
    )
    occupied = tmp_path / "occupied-pycache-prefix"
    occupied.mkdir()
    with pytest.raises(runner.ValidationRunnerError, match="pycache_prefix_must_not_exist"):
        runner._isolated_python_module_argv(
            Path(sys.executable), PROJECT_ROOT, "pytest", pycache_prefix=occupied
        )


def test_isolated_python_script_bootstrap_is_runnable_and_path_ordered(
    tmp_path: Path,
) -> None:
    pycache_prefix = tmp_path / "script-pycache-prefix-must-remain-absent"
    argv = runner._isolated_python_script_argv(
        Path(sys.executable),
        PROJECT_ROOT,
        "scripts/dev/validate_pre_r8_r7s5_ci.py",
        "--help",
        pycache_prefix=pycache_prefix,
    )
    completed = subprocess.run(
        argv,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout
    assert not pycache_prefix.exists()
    bootstrap = argv[7]
    site_packages = runner.verified_site_packages_binding(Path(sys.executable))["path"]
    assert "import pathlib,runpy,sys" in bootstrap
    assert (
        f"[*sys.path,{str((PROJECT_ROOT / 'src').resolve())!r},"
        f"{str(PROJECT_ROOT.resolve())!r},{site_packages!r}]"
    ) in bootstrap


def test_focused_plan_explicitly_covers_r7s3_through_r7s7_adversarial_files() -> None:
    specs = runner.build_command_specs(
        repository=REPOSITORY,
        project_root=PROJECT_ROOT,
        python_general=Path(sys.executable),
        python_host=Path(sys.executable),
        python_ruff=Path(sys.executable),
        git_executable=GIT,
        git_executable_sha256=runner.sha256_file(GIT),
        powershell_executable=POWERSHELL,
    )
    focused = next(item for item in specs if item.name == "r7s5-focused-pytest-py311")
    required = {
        "tests/test_phase_b2_r7s3_handle_io.py",
        "tests/test_phase_b2_r7s4_evidence.py",
        "tests/test_phase_b2_r7s4_handle_io.py",
        "tests/test_phase_b2_r7s4_process.py",
        "tests/test_pre_r8_r7s3_oob_candidate.py",
        "tests/test_pre_r8_r7s3_python_tcb_inventory.py",
        "tests/test_pre_r8_r7s3_toolchain_pin.py",
        "tests/test_pre_r8_r7s4_ci_bootstrap.py",
        "tests/test_phase_b2_r7s7_admission.py",
        "tests/test_phase_b2_r7s7_qualification_work_order.py",
        "tests/test_qualify_pre_r8_r7s7_windows.py",
    }
    assert required <= set(focused.argv)


def test_validation_runner_has_terminal_pycache_absence_gate() -> None:
    source = inspect.getsource(runner._run_validation_with_bound_output)
    postcondition = 'failure_stage = "pycache_prefix_postcondition"'
    summary = 'failure_stage = "summary_serialization"'
    assert postcondition in source
    assert "validation_pycache_prefix_created_during_execution" in source
    assert source.index(postcondition) < source.index(summary)


def test_prepare_internal_inputs_is_runnable_without_future_pid_and_remains_no_go(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    validation_parent = tmp_path / "validation"
    validation_parent.mkdir()
    args = _args(validation_parent)
    prepare_parent = tmp_path / "prepared"
    prepare_parent.mkdir()
    args.parent = prepare_parent
    args.expected_parent = prepare_parent
    args.expected_parent_sha256 = runner.output_parent_commitment(prepare_parent)["sha256"]
    args.output_leaf = "internal-inputs-r7s7"
    args.validation_run_uuid = "30000000-0000-4000-8000-000000000003"
    args.validation_attempt_uuid = "40000000-0000-4000-8000-000000000004"
    args.handoff_challenge_sha256 = "c" * 64
    now = datetime.now(UTC)
    args.issued_at_utc = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    args.expires_at_utc = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    args.codex_pid = 123
    args.publisher_python = Path(sys.executable)
    args.publisher_python_sha256 = runner.sha256_file(Path(sys.executable))
    runtime_pid = os.getpid()
    parent_pid = os.getppid()
    codex_executable = tmp_path / "codex.exe"
    codex_executable.write_bytes(b"test-only-codex")
    identities = {
        runtime_pid: {"pid": runtime_pid, "ppid": parent_pid, "path": str(Path(sys.executable))},
        parent_pid: {"pid": parent_pid, "ppid": 123, "path": str(POWERSHELL)},
        123: {"pid": 123, "ppid": 1, "path": str(codex_executable)},
    }
    monkeypatch.setattr(
        publisher, "measure_process_identity", lambda pid, **_kwargs: identities[pid]
    )
    monkeypatch.setattr(publisher, "measure_current_token", lambda: {})
    monkeypatch.setattr(publisher, "measure_process_token", lambda _pid: {})
    monkeypatch.setattr(publisher, "_token_requirements", lambda _token, _label: None)
    observed: dict[str, object] = {}

    class Batch:
        def to_dict(self) -> dict[str, object]:
            return {"output_directory": str(prepare_parent / args.output_leaf)}

    def publish(
        parent: Path, leaf: str, documents: Mapping[str, object], *, run_uuid: str
    ) -> Batch:
        observed.update(parent=parent, leaf=leaf, documents=documents, run_uuid=run_uuid)
        return Batch()

    monkeypatch.setattr(publisher.evidence, "publish_pre_serialized_batch", publish)
    result = runner.prepare_internal_inputs(args)
    documents = observed["documents"]
    assert isinstance(documents, Mapping)
    assert set(documents) == {
        "external-work-order.json",
        "live-call-telemetry.json",
        "publisher-token-evidence.json",
        "lineage-work-order.json",
        "internal-input-index.json",
    }
    lineage = documents["lineage-work-order.json"]
    assert isinstance(lineage, Mapping)
    assert "pid" not in str(lineage["executable_bindings"])
    index = documents["internal-input-index.json"]
    assert index["blocking_unproven_invariants"] == [
        "immutable_checkout_namespace_authority",
        "runtime_stdlib_native_closure_verified",
    ]
    assert result["decision"] == "NO-GO"
    assert result["authority_verified"] is False
    assert result["external_worm_receipt_created"] is False


def test_selected_validation_files_include_runner_publisher_and_all_r7s5_modules() -> None:
    files = runner._selected_validation_files(PROJECT_ROOT)
    assert "scripts/dev/run_pre_r8_r7s5_validation.py" in files
    assert "scripts/dev/publish_pre_r8_r7s5_review.py" in files
    assert "src/evm/scale_validation/phase_b2_r7s3_process.py" in files
    assert "src/evm/scale_validation/phase_b2_r7s5_evidence.py" in files
    assert "src/evm/scale_validation/phase_b2_r7s6_evidence.py" in files
    assert "tests/test_phase_b2_r7_process.py" in files
    assert "tests/test_phase_b2_r7s3_job_capability.py" in files
    assert "tests/test_phase_b2_r7s3_process.py" in files
    assert "tests/test_phase_b2_r7s4_authority.py" in files
    assert "tests/test_phase_b2_r7s5_evidence.py" in files
    assert "tests/test_phase_b2_r7s6_evidence.py" in files
    assert "src/evm/scale_validation/phase_b2_r7s7_admission.py" in files
    assert "tests/test_phase_b2_r7s7_admission.py" in files
    assert "scripts/dev/pre_r8_r7s7_windows_fixture.py" in files
    assert "scripts/dev/qualify_pre_r8_r7s7_windows.py" in files
    assert "tests/test_qualify_pre_r8_r7s7_windows.py" in files
    assert len(files) == len(set(files))


def test_validation_command_timeout_is_bounded_with_exact_residual_contract() -> None:
    spec = runner.CommandSpec("bounded", ("test.exe",), wrapper_timeout_seconds=30.0)
    contract = runner._validation_timeout_contract(spec)
    assert contract.wrapper_timeout_seconds == 30.0
    assert contract.residual_repoll_seconds == 120.0
    assert contract.stream_drain_seconds == 30.0
    assert contract.restore_deadline_seconds == 190.0
    with pytest.raises(runner.ValidationRunnerError, match="wrapper_timeout_too_small"):
        runner._validation_timeout_contract(
            runner.CommandSpec("unsafe", ("test.exe",), wrapper_timeout_seconds=8.0)
        )


def test_expected_nonzero_exit_can_clear_containment_but_never_residual_or_timeout() -> None:
    clean_expected_rejection = _contained_outcome(return_code=2, safe_for_followup=False)
    assert runner._containment_cleared(clean_expected_rejection) is True

    mutations = (
        {"timed_out": True},
        {"cancelled": True},
        {"manual_intervention_required": True},
        {"residual_pids": (1234,), "active_process_zero": False},
        {"streams_drained": False, "stdout_drained": False},
        {"identity_coverage_complete": False},
        {"forced_termination_attempts": 1},
        {"job_limit_flags": 1},
        {"stdout_capture_overflow": True},
        {"stderr_capture_overflow": True},
        {"stdout_total_bytes": 1},
        {"executable_identity": {}},
        {"identities": ()},
        {"events": ()},
        {"accounting": ()},
    )
    for mutation in mutations:
        assert runner._containment_cleared(_contained_outcome(**mutation)) is False


def test_git_metadata_probe_uses_no_kill_job_containment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected = _contained_outcome(return_code=0, stdout="ok\n", safe_for_followup=True)

    class FakeRunner:
        def __init__(self, contract: runner.TimeoutContract) -> None:
            captured["contract"] = contract

        def run(self, argv: tuple[str, ...], **kwargs: object) -> runner.ProcessOutcome:
            captured["argv"] = argv
            captured.update(kwargs)
            return _outcome_for_command(expected, argv, str(kwargs["name"]))

    monkeypatch.setattr(runner, "WindowsJobProcessRunner", FakeRunner)
    child_environment = runner.build_child_environment(PROJECT_ROOT, (str(GIT),))
    metadata_evidence: list[dict[str, object]] = []
    assert (
        runner._git(
            REPOSITORY,
            "rev-parse",
            "HEAD",
            git_executable=GIT,
            expected_git_sha256=runner.sha256_file(GIT),
            child_environment=child_environment,
            metadata_evidence=metadata_evidence,
            phase="test",
        )
        == "ok"
    )
    command = captured["argv"]
    assert isinstance(command, tuple)
    assert Path(command[0]).is_absolute()
    assert Path(command[0]) == runner._resolved_executable("git")
    assert command[1:] == ("rev-parse", "HEAD")
    assert captured["name"] == "r7s6-validation-metadata-git-rev-parse"
    assert captured["expected_executable_sha256"] == runner.sha256_file(
        runner._resolved_executable("git")
    )
    contract = captured["contract"]
    assert isinstance(contract, runner.TimeoutContract)
    assert contract.wrapper_timeout_seconds == runner.METADATA_WRAPPER_TIMEOUT_SECONDS
    assert contract.residual_repoll_seconds == runner.VALIDATION_RESIDUAL_REPOLL_SECONDS
    assert contract.stream_drain_seconds == runner.METADATA_STREAM_DRAIN_SECONDS
    assert metadata_evidence[0]["child_invoked"] is True
    assert metadata_evidence[0]["phase"] == "test"


def test_metadata_child_nonzero_exit_preserves_pinned_process_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected = _contained_outcome(return_code=7, stderr="expected metadata rejection")

    class FakeRunner:
        def __init__(self, contract: runner.TimeoutContract) -> None:
            captured["contract"] = contract

        def run(self, argv: tuple[str, ...], **kwargs: object) -> runner.ProcessOutcome:
            captured.update(kwargs)
            return _outcome_for_command(expected, argv, str(kwargs["name"]))

    executable_sha256 = runner.sha256_file(Path(sys.executable))
    monkeypatch.setattr(runner, "WindowsJobProcessRunner", FakeRunner)
    with pytest.raises(runner.MetadataChildError, match="exit_nonzero") as raised:
        runner._run_metadata_child(
            (sys.executable, "--version"),
            cwd=PROJECT_ROOT,
            name="direct-nonzero",
            expected_executable_sha256=executable_sha256,
        )
    assert captured["expected_executable_sha256"] == executable_sha256
    assert raised.value.failure_kind == "exit_nonzero"
    assert raised.value.process_evidence["return_code"] == 7
    assert raised.value.process_evidence["executable_identity"]["sha256"] == (executable_sha256)


def test_unexpected_metadata_runner_exception_is_counted_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedRunner:
        def __init__(self, contract: runner.TimeoutContract) -> None:
            pass

        def run(self, *args: object, **kwargs: object) -> runner.ProcessOutcome:
            raise RuntimeError("undisclosed synthetic failure")

    monkeypatch.setattr(runner, "WindowsJobProcessRunner", UnexpectedRunner)
    environment = runner.build_child_environment(PROJECT_ROOT, (sys.executable,))
    evidence: list[dict[str, object]] = []
    with pytest.raises(runner.MetadataChildError, match="outcome_unproven") as raised:
        runner._run_metadata_child_recorded(
            (sys.executable, "--version"),
            cwd=PROJECT_ROOT,
            name="unexpected",
            child_environment=environment,
            expected_executable_sha256=runner.sha256_file(Path(sys.executable)),
            metadata_evidence=evidence,
            phase="test",
        )

    assert raised.value.evidence_recorded is True
    assert runner._metadata_child_call_count(evidence) == 1
    assert evidence[0]["status"] == "FAIL"
    assert evidence[0]["failure_kind"] == "process_outcome_unproven"
    assert evidence[0]["process_containment"]["child_start_attempted"] is True
    assert "undisclosed synthetic failure" not in str(evidence)


def test_metadata_child_attempt_is_registered_before_base_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted_child(*_args: object, **_kwargs: object) -> runner.ProcessOutcome:
        raise KeyboardInterrupt("sensitive metadata interruption")

    monkeypatch.setattr(runner, "_run_metadata_child", interrupted_child)
    environment = runner.build_child_environment(PROJECT_ROOT, (sys.executable,))
    evidence: list[dict[str, object]] = []

    with pytest.raises(KeyboardInterrupt):
        runner._run_metadata_child_recorded(
            (sys.executable, "--version"),
            cwd=PROJECT_ROOT,
            name="interrupted",
            child_environment=environment,
            expected_executable_sha256=runner.sha256_file(Path(sys.executable)),
            metadata_evidence=evidence,
            phase="test",
        )

    assert runner._metadata_child_call_count(evidence) == 1
    assert evidence[0]["status"] == "FAIL"
    assert evidence[0]["failure_kind"] == "unexpected_base_exception"
    assert evidence[0]["process_containment"]["terminal_process_evidence_recorded"] is False
    assert "sensitive metadata interruption" not in str(evidence)


def test_metadata_terminal_observer_receives_replacement_and_cannot_mask_failure() -> None:
    record = runner._pending_metadata_evidence_record(name="child", phase="phase")
    assert isinstance(record, runner._MetadataEvidenceRecord)
    observed: list[dict[str, object]] = []

    def observe(value: Mapping[str, object]) -> None:
        observed.append(dict(value))

    def interrupt_observer(_value: Mapping[str, object]) -> None:
        raise KeyboardInterrupt("observer interruption")

    record.bind_terminal_observer(observe)
    record.bind_terminal_observer(interrupt_observer)
    replacement = {
        "name": "child",
        "phase": "phase",
        "status": "FAIL",
        "child_invoked": True,
        "process_containment": {"residual_pids": [4242]},
    }

    runner._replace_metadata_evidence_record(record, replacement)

    assert observed == [replacement]
    assert record == replacement
    assert record._observer_error_types == ["builtins.KeyboardInterrupt"]


@pytest.mark.parametrize("leaf", ["..", "../escape", "C:\\escape", "CON", "bad."])
def test_validation_output_leaf_rejects_escape_and_ambiguous_windows_names(
    tmp_path: Path, leaf: str
) -> None:
    with pytest.raises(runner.ValidationRunnerError, match="output_leaf_invalid"):
        runner._BoundValidationOutput.create(tmp_path, leaf)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows handle binding is host-specific")
def test_validation_output_writes_remain_bound_to_held_directory_handle(
    tmp_path: Path,
) -> None:
    moved = tmp_path / "attacker-replacement"
    with runner._BoundValidationOutput.create(tmp_path, "bound-output") as output:
        first = output.publish("first.json", b'{"sequence":1}\n')
        with pytest.raises(OSError):
            os.rename(output.path, moved)
        second = output.publish("second.json", b'{"sequence":2}\n')
        with pytest.raises(runner.ValidationRunnerError, match="rename_no_replace"):
            output.publish("first.json", b'{"overwrite":true}\n')
        assert (output.path / "first.json").read_bytes() == b'{"sequence":1}\n'
        assert first.directory_identity.file_id_hex == second.directory_identity.file_id_hex
        assert first.directory_identity.volume_serial_number == (
            second.directory_identity.volume_serial_number
        )
        assert first.directory_identity.reparse_tag == 0
        assert second.directory_identity.reparse_tag == 0
    assert not moved.exists()
    assert (tmp_path / "bound-output" / "first.json").is_file()
    assert (tmp_path / "bound-output" / "second.json").is_file()


def test_validation_child_uses_no_kill_job_object_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    expected = _contained_outcome(return_code=0, safe_for_followup=True)

    class FakeRunner:
        def __init__(self, contract: runner.TimeoutContract) -> None:
            observed["contract"] = contract

        def run(self, argv: tuple[str, ...], **kwargs: object) -> runner.ProcessOutcome:
            observed["argv"] = argv
            observed.update(kwargs)
            return _outcome_for_command(expected, argv, str(kwargs["name"]))

    monkeypatch.setattr(runner, "WindowsJobProcessRunner", FakeRunner)
    result = runner._run_validation_child(
        runner.CommandSpec(
            "contained",
            (sys.executable, "--version"),
            wrapper_timeout_seconds=30.0,
        ),
        project_root=PROJECT_ROOT,
        env={"SAFE": "1"},
        expected_executable_sha256=runner.sha256_file(Path(sys.executable)),
    )
    assert result.return_code == expected.return_code
    assert observed["argv"] == (str(Path(sys.executable).resolve(strict=True)), "--version")
    assert observed["name"] == "r7s6-validation-contained"
    assert observed["expected_executable_sha256"] == runner.sha256_file(Path(sys.executable))
    contract = observed["contract"]
    assert isinstance(contract, runner.TimeoutContract)
    assert contract.wrapper_timeout_seconds == 30.0


def test_final_command_containment_failure_sets_terminal_latch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    plan = _single_command_plan(spec)
    output = _FakeOutput(tmp_path / "validation")
    monkeypatch.setattr(
        runner._BoundValidationOutput,
        "create",
        lambda parent, leaf: output,
    )
    monkeypatch.setattr(
        runner,
        "_git",
        lambda repository, *args, **kwargs: (
            "a" * 40 if args[-1] == "HEAD" else "b" * 40 if args[-1] == "HEAD^{tree}" else ""
        ),
    )
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "command_plan", lambda **kwargs: plan)
    monkeypatch.setattr(
        runner,
        "_verify_isolated_untracked_inventory",
        lambda *args, **kwargs: {"matches_expected": True},
    )
    monkeypatch.setattr(
        runner,
        "_run_validation_child",
        lambda *args, **kwargs: _contained_outcome(
            return_code=None,
            timed_out=True,
            manual_intervention_required=True,
            safe_for_followup=False,
        ),
    )
    args = _args(tmp_path)
    result = runner.run_validation(args)
    summary = publisher.read_json_mapping(
        output.path / "code-validation-summary.json",
        "summary",
    )
    assert result["status"] == "FAIL"
    assert summary["executed_command_count"] == 1
    assert summary["not_run_commands"] == []
    assert summary["terminal_containment_latch"] is True
    assert summary["followup_child_count_after_containment_latch"] == 0
    assert summary["commands"][0]["publication"]["directory_flush_succeeded"] is True
    assert result["summary_publication"]["directory_flush_succeeded"] is True
    publication_index = publisher.read_json_mapping(
        output.path / "code-validation-publication-index.json",
        "publication index",
    )
    assert publication_index["schema"] == runner.PUBLICATION_INDEX_SCHEMA
    assert publication_index["summary"]["sha256"] == result["summary_sha256"]
    assert publication_index["summary"]["publication"] == result["summary_publication"]
    assert publication_index["command_publication_receipts_bound_through_summary"] is True
    assert publication_index["self_publication_receipt_embedded"] is False
    assert result["publication_index_publication"]["directory_flush_succeeded"] is True
    assert result["publication_index_sha256"] == runner.sha256_file(
        output.path / "code-validation-publication-index.json"
    )
    assert output.close_count == 1


def test_required_token_cannot_be_synthesized_across_stream_boundary() -> None:
    token = '"status":"manual_intervention_required"'
    assert not runner._required_tokens_present(
        (token,),
        stdout='{"status":"manual_intervention_',
        stderr='required"}',
    )
    assert runner._required_tokens_present((token,), stdout=token, stderr="")
    assert runner._required_tokens_present((token,), stdout="", stderr=token)


def test_child_environment_is_allowlisted_committed_and_removes_secret_or_hostile_values() -> None:
    secret = "synthetic-secret-value-7842"
    access_key = "synthetic-access-key-value-9127"
    hostile_path = r"C:\hostile-python-plugin"
    source = {
        "SystemRoot": os.environ["SystemRoot"],
        "TEMP": os.environ["TEMP"],
        "TMP": os.environ["TMP"],
        "USERPROFILE": os.environ["USERPROFILE"],
        "PATH": hostile_path,
        "PYTEST_ADDOPTS": "--capture=no",
        "PYTHONHOME": hostile_path,
        "SERVICE_API_TOKEN": secret,
        "AWS_ACCESS_KEY_ID": access_key,
    }
    environment = runner.build_child_environment(
        PROJECT_ROOT,
        (sys.executable,),
        source=source,
    )
    assert "SERVICE_API_TOKEN" not in environment.values
    assert "AWS_ACCESS_KEY_ID" not in environment.values
    assert "PYTEST_ADDOPTS" not in environment.values
    assert "PYTHONHOME" not in environment.values
    assert hostile_path not in environment.values["PATH"]
    assert environment.values["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert environment.values["PYTHONPATH"].split(os.pathsep) == [
        str(PROJECT_ROOT.resolve(strict=True)),
        str((PROJECT_ROOT / "src").resolve(strict=True)),
    ]
    assert environment.commitment["values_disclosed"] is False
    assert environment.commitment["removed_secret_like_variable_count"] == 2
    rendered = runner.canonical_json_bytes(environment.commitment)
    assert secret.encode() not in rendered
    assert access_key.encode() not in rendered
    assert set(environment.secret_values) == {secret, access_key}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows validation child environment")
def test_child_environment_imports_only_the_isolated_project_first() -> None:
    environment = runner.build_child_environment(
        PROJECT_ROOT,
        (sys.executable,),
    )
    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            (
                "import evm;"
                "import scripts.dev.publish_pre_r8_r7s5_review as publisher;"
                "print(publisher.__file__);print(evm.__file__)"
            ),
        ),
        cwd=PROJECT_ROOT,
        env=environment.values,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    origins = [Path(item).resolve(strict=True) for item in completed.stdout.splitlines()]
    assert len(origins) == 2
    assert all(PROJECT_ROOT.resolve(strict=True) in item.parents for item in origins)


def test_child_environment_rejects_case_colliding_input_keys() -> None:
    with pytest.raises(runner.ValidationRunnerError, match="environment_case_collision"):
        runner.build_child_environment(
            PROJECT_ROOT,
            (sys.executable,),
            source={"SystemRoot": os.environ["SystemRoot"], "SYSTEMROOT": "hostile"},
        )


def test_secret_scanner_redacts_nested_process_evidence_without_persisting_raw_value() -> None:
    secret = "synthetic-secret-value-9513"
    value = {
        "stdout": f"before {secret} after",
        "errors": [f"nested={secret}"],
    }
    sanitized, detected = runner._sanitize_for_evidence(value, (secret,))
    assert detected is True
    rendered = runner.canonical_json_bytes(sanitized)
    assert secret.encode() not in rendered
    assert rendered.count(runner._SECRET_REDACTION.encode()) == 2


def test_output_parent_requires_exact_path_sha_and_rejects_repository_descendant(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    commitment = runner.output_parent_commitment(approved)
    resolved, observed = runner._validate_output_parent_gate(
        approved,
        expected_path=approved,
        expected_sha256=commitment["sha256"],
    )
    assert resolved == approved.resolve(strict=True)
    assert observed == commitment
    with pytest.raises(runner.ValidationRunnerError, match="path_mismatch"):
        runner._validate_output_parent_gate(
            approved,
            expected_path=other,
            expected_sha256=commitment["sha256"],
        )
    with pytest.raises(runner.ValidationRunnerError, match="sha256_mismatch"):
        runner._validate_output_parent_gate(
            approved,
            expected_path=approved,
            expected_sha256="0" * 64,
        )
    nested = approved / "nested"
    nested.mkdir()
    with pytest.raises(runner.ValidationRunnerError, match="inside_forbidden_root"):
        runner._validate_output_parent_gate(
            nested,
            expected_path=nested,
            expected_sha256=runner.output_parent_commitment(nested)["sha256"],
            forbidden_roots=(approved,),
        )


def test_run_validation_rejects_pinned_output_parent_inside_repository_before_create(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    args = _args(tmp_path)
    args.output_parent = REPOSITORY
    args.expected_output_parent = REPOSITORY
    args.expected_output_parent_sha256 = runner.output_parent_commitment(REPOSITORY)["sha256"]
    create_calls = 0

    def unexpected_create(*args: object, **kwargs: object) -> _FakeOutput:
        nonlocal create_calls
        create_calls += 1
        raise AssertionError("output creation must remain unreachable")

    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    _repin_work_order_specs(args, (spec,))
    monkeypatch.setattr(runner._BoundValidationOutput, "create", unexpected_create)
    with pytest.raises(runner.ValidationRunnerError, match="inside_forbidden_root"):
        runner.run_validation(args)
    assert create_calls == 0


def test_repository_preflight_mismatch_starts_no_validation_child_and_seals_terminal_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    output = _FakeOutput(tmp_path / "output")
    snapshot_count = 0
    validation_calls = 0

    def snapshot(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal snapshot_count
        snapshot_count += 1
        if snapshot_count == 1:
            return _repository_observation()
        return _repository_observation(tracked_clean=False)

    def validation_child(*args: object, **kwargs: object) -> runner.ProcessOutcome:
        nonlocal validation_calls
        validation_calls += 1
        return _contained_outcome(return_code=0)

    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "command_plan", lambda **kwargs: _single_command_plan(spec))
    monkeypatch.setattr(runner, "_repository_snapshot", snapshot)
    monkeypatch.setattr(
        runner,
        "_verify_isolated_untracked_inventory",
        lambda *args, **kwargs: {"matches_expected": True},
    )
    monkeypatch.setattr(runner, "_run_validation_child", validation_child)
    result = runner.run_validation(_args(tmp_path))
    terminal = publisher.read_json_mapping(
        output.path / "terminal-validation-failure.json", "terminal"
    )
    assert result["status"] == "FAIL"
    assert validation_calls == 0
    assert terminal["failure_stage"].endswith("repository_preflight")
    assert terminal["validation_child_call_count"] == 0
    assert terminal["not_run_commands"] == [spec.name]
    assert terminal["terminal_containment_latch"] is True
    assert terminal["followup_child_count_after_terminal_latch"] == 0
    assert result["terminal_failure_publication"]["directory_flush_succeeded"] is True
    assert output.close_count == 1


def test_first_child_containment_failure_latches_and_starts_no_later_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    specs = (
        runner.CommandSpec("first-command", (sys.executable, "--version")),
        runner.CommandSpec("second-command", (sys.executable, "--version")),
    )
    executable_sha = runner.sha256_file(Path(sys.executable))
    plan = {
        "commands": [{"name": spec.name, "tool": {"sha256": executable_sha}} for spec in specs],
        "sha256": "c" * 64,
    }
    output = _FakeOutput(tmp_path / "output")
    clean = _repository_observation()
    validation_calls: list[str] = []

    def validation_child(spec: runner.CommandSpec, **kwargs: object) -> runner.ProcessOutcome:
        validation_calls.append(spec.name)
        absolute = (str(Path(sys.executable).resolve(strict=True)), "--version")
        return _outcome_for_command(
            _contained_outcome(
                return_code=None,
                timed_out=True,
                manual_intervention_required=True,
            ),
            absolute,
            f"r7s6-validation-{spec.name}",
        )

    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: specs)
    monkeypatch.setattr(runner, "command_plan", lambda **kwargs: plan)
    monkeypatch.setattr(runner, "_repository_snapshot", lambda *args, **kwargs: clean)
    monkeypatch.setattr(
        runner,
        "_verify_isolated_untracked_inventory",
        lambda *args, **kwargs: {"matches_expected": True},
    )
    monkeypatch.setattr(runner, "_run_validation_child", validation_child)
    result = runner.run_validation(_args(tmp_path))
    summary = publisher.read_json_mapping(output.path / "code-validation-summary.json", "summary")
    assert result["status"] == "FAIL"
    assert validation_calls == ["first-command"]
    assert summary["validation_child_call_count"] == 1
    assert summary["not_run_commands"] == ["second-command"]
    assert summary["terminal_containment_latch"] is True
    assert summary["followup_child_count_after_containment_latch"] == 0
    assert output.close_count == 1


def test_postflight_metadata_failure_preserves_validation_evidence_and_blocks_later_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    specs = (
        runner.CommandSpec("first-command", (sys.executable, "--version")),
        runner.CommandSpec("second-command", (sys.executable, "--version")),
    )
    executable_sha = runner.sha256_file(Path(sys.executable))
    plan = {
        "commands": [{"name": spec.name, "tool": {"sha256": executable_sha}} for spec in specs],
        "sha256": "c" * 64,
    }
    output = _FakeOutput(tmp_path / "output")
    clean = _repository_observation()
    snapshot_calls = 0
    validation_calls: list[str] = []

    def snapshot(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 3:
            raise runner.MetadataChildError(
                "synthetic postflight failure",
                name="git-postflight",
                failure_kind="exit_nonzero",
                process_evidence=_contained_outcome(return_code=9).to_dict(),
            )
        return clean

    def validation_child(spec: runner.CommandSpec, **kwargs: object) -> runner.ProcessOutcome:
        validation_calls.append(spec.name)
        absolute = (str(Path(sys.executable).resolve(strict=True)), "--version")
        return _outcome_for_command(
            _contained_outcome(return_code=0),
            absolute,
            f"r7s6-validation-{spec.name}",
        )

    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: specs)
    monkeypatch.setattr(runner, "command_plan", lambda **kwargs: plan)
    monkeypatch.setattr(runner, "_repository_snapshot", snapshot)
    monkeypatch.setattr(
        runner,
        "_verify_isolated_untracked_inventory",
        lambda *args, **kwargs: {"matches_expected": True},
    )
    monkeypatch.setattr(runner, "_run_validation_child", validation_child)
    result = runner.run_validation(_args(tmp_path))
    terminal = publisher.read_json_mapping(
        output.path / "terminal-validation-failure.json", "terminal"
    )
    assert result["status"] == "FAIL"
    assert validation_calls == ["first-command"]
    assert terminal["validation_child_call_count"] == 1
    assert terminal["not_run_commands"] == ["second-command"]
    assert terminal["failure_evidence"]["validation_command_name"] == "first-command"
    assert (
        terminal["failure_evidence"]["validation_process_containment"]["active_process_zero"]
        is True
    )
    assert terminal["followup_child_count_after_terminal_latch"] == 0
    assert output.close_count == 1


@pytest.mark.parametrize("failure_kind", ["process_containment_failure", "exit_nonzero"])
def test_metadata_failure_is_preserved_and_starts_no_validation_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_kind: str,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    output = _FakeOutput(tmp_path / "output")
    validation_calls = 0
    metadata_process = _contained_outcome(return_code=7).to_dict()

    def failed_snapshot(*args: object, **kwargs: object) -> dict[str, object]:
        raise runner.MetadataChildError(
            "synthetic_metadata_failure",
            name="git-rev-parse",
            failure_kind=failure_kind,
            process_evidence=metadata_process,
        )

    def validation_child(*args: object, **kwargs: object) -> runner.ProcessOutcome:
        nonlocal validation_calls
        validation_calls += 1
        return _contained_outcome(return_code=0)

    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "_repository_snapshot", failed_snapshot)
    monkeypatch.setattr(runner, "_run_validation_child", validation_child)
    result = runner.run_validation(_args(tmp_path))
    terminal = publisher.read_json_mapping(
        output.path / "terminal-validation-failure.json", "terminal"
    )
    assert result["status"] == "FAIL"
    assert validation_calls == 0
    assert terminal["validation_child_call_count"] == 0
    failed = terminal["metadata_children"][-1]
    assert failed["failure_kind"] == failure_kind
    assert terminal["metadata_child_call_count"] == 1
    sanitized_process, _ = runner._sanitize_for_evidence(metadata_process, ())
    assert runner.canonical_json_bytes(failed["process_containment"]) == (
        runner.canonical_json_bytes(sanitized_process)
    )
    assert terminal["followup_child_count_after_terminal_latch"] == 0
    assert output.close_count == 1


def test_unexpected_exception_still_closes_bound_output_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    output = _FakeOutput(tmp_path / "output")
    clean = _repository_observation()
    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "command_plan", lambda **kwargs: _single_command_plan(spec))
    monkeypatch.setattr(runner, "_repository_snapshot", lambda *args, **kwargs: clean)
    monkeypatch.setattr(
        runner,
        "_verify_isolated_untracked_inventory",
        lambda *args, **kwargs: {"matches_expected": True},
    )
    monkeypatch.setattr(
        runner,
        "_run_validation_child",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected child failure")),
    )
    result = runner.run_validation(_args(tmp_path))
    terminal = publisher.read_json_mapping(
        output.path / "terminal-validation-failure.json", "terminal"
    )
    assert result["status"] == "FAIL"
    assert terminal["failure_evidence"]["exception_type"] == "builtins.RuntimeError"
    assert terminal["failure_evidence"]["exception_message_disclosed"] is False
    assert output.close_count == 1


def test_publication_failure_still_closes_bound_output_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))

    class TerminalFailOutput(_FakeOutput):
        def publish(self, leaf: str, raw: bytes) -> _FakePublication:
            if leaf == "terminal-validation-failure.json":
                raise runner.ValidationRunnerError("injected_terminal_publish_failure")
            return super().publish(leaf, raw)

    output = TerminalFailOutput(tmp_path / "output")
    mismatch = _repository_observation(head="0" * 40)
    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "_repository_snapshot", lambda *args, **kwargs: mismatch)
    result = runner.run_validation(_args(tmp_path))
    emergency = publisher.read_json_mapping(
        Path(result["terminal_emergency_seal_path"]), "emergency"
    )
    assert result["status"] == "FAIL"
    assert emergency["terminal_seal_attempt_count"] == 1
    assert emergency["emergency_seal_attempt_count"] == 1
    assert emergency["automatic_retry_count"] == 0
    assert emergency["emergency_publication_scope"] == "independent_parent_sibling_writer"
    assert Path(result["terminal_emergency_seal_path"]).parent == output.path.parent
    assert output.close_count == 1


def test_permanently_failed_bound_output_uses_independent_sibling_emergency_writer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    output = _FakeOutput(tmp_path / "output", fail_publish=True)
    mismatch = _repository_observation(head="0" * 40)
    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "_repository_snapshot", lambda *args, **kwargs: mismatch)

    result = runner.run_validation(_args(tmp_path))

    seal_path = Path(result["terminal_emergency_seal_path"])
    emergency = publisher.read_json_mapping(seal_path, "emergency")
    assert result["status"] == "FAIL"
    assert seal_path.parent == tmp_path
    assert seal_path.is_file()
    assert list(output.path.iterdir()) == []
    assert emergency["terminal_seal_attempt_count"] == 1
    assert emergency["emergency_seal_attempt_count"] == 1
    assert emergency["completion_marker_created"] is False
    assert output.close_count == 1


def test_command_plan_plain_exception_is_terminally_sealed_without_reentry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    output = _FakeOutput(tmp_path / "output")
    clean = _repository_observation()
    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "_repository_snapshot", lambda *args, **kwargs: clean)
    monkeypatch.setattr(
        runner,
        "command_plan",
        lambda **kwargs: (_ for _ in ()).throw(
            runner.ValidationRunnerError("injected_plan_failure")
        ),
    )

    result = runner.run_validation(_args(tmp_path))
    terminal = publisher.read_json_mapping(
        output.path / "terminal-validation-failure.json", "terminal"
    )

    assert result["status"] == "FAIL"
    assert terminal["failure_stage"] == "command_plan"
    assert terminal["validation_child_call_count"] == 0
    assert list(path.name for path in output.path.iterdir()) == ["terminal-validation-failure.json"]
    assert output.close_count == 1


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_base_exception_during_validation_child_is_zero_credit_sealed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interrupt_type: type[BaseException],
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    output = _FakeOutput(tmp_path / "output")
    clean = _repository_observation()
    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "command_plan", lambda **kwargs: _single_command_plan(spec))
    monkeypatch.setattr(runner, "_repository_snapshot", lambda *args, **kwargs: clean)
    monkeypatch.setattr(
        runner,
        "_verify_isolated_untracked_inventory",
        lambda *args, **kwargs: {"matches_expected": True},
    )

    def interrupted(*args: object, **kwargs: object) -> runner.ProcessOutcome:
        raise interrupt_type()

    monkeypatch.setattr(runner, "_run_validation_child", interrupted)
    result = runner.run_validation(_args(tmp_path))
    terminal = publisher.read_json_mapping(
        output.path / "terminal-validation-failure.json", "terminal"
    )
    assert result["status"] == "FAIL"
    assert terminal["failure_evidence"]["exception_type"] == (
        f"builtins.{interrupt_type.__qualname__}"
    )
    assert terminal["validation_child_call_count"] == 1
    assert terminal["completion_marker_created"] is False
    assert output.close_count == 1


def test_run_validation_is_not_coupled_to_raising_stdout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    output = _FakeOutput(tmp_path / "output")
    mismatch = _repository_observation(head="0" * 40)
    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args, **kwargs: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "_repository_snapshot", lambda *args, **kwargs: mismatch)

    def unavailable_stdout(*args: object, **kwargs: object) -> None:
        raise ValueError("closed stdout")

    monkeypatch.setattr("builtins.print", unavailable_stdout)
    result = runner.run_validation(_args(tmp_path))
    assert result["status"] == "FAIL"
    assert (output.path / "terminal-validation-failure.json").is_file()
    assert not (output.path / "terminal-validation-emergency-seal.json").exists()


def test_main_console_emission_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_require_isolated_no_bytecode_startup", lambda: None)
    monkeypatch.setattr(runner, "parse_args", lambda argv: object())
    monkeypatch.setattr(
        runner,
        "run_validation",
        lambda args: {"status": "PASS", "decision": "NO-GO"},
    )

    def unavailable_stdout(*args: object, **kwargs: object) -> None:
        raise ValueError("closed stdout")

    monkeypatch.setattr("builtins.print", unavailable_stdout)
    assert runner.main([]) == 1


def test_output_initialization_failure_is_published_as_atomic_sibling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    intended = tmp_path / "validation-output"
    child_calls = 0

    def failed_create(*args: object, **kwargs: object) -> _FakeOutput:
        raise runner.ValidationOutputInitializationFailure(
            "synthetic binding failure",
            stage="bind_output_directory_identity",
            output_path=intended,
            original_error=OSError("undisclosed"),
        )

    def atomic_publish(parent: Path, leaf: str, raw: bytes, **kwargs: object) -> _FakePublication:
        path = Path(parent) / leaf
        path.write_bytes(raw)
        return _FakePublication(path, raw)

    def unexpected_child(*args: object, **kwargs: object) -> runner.ProcessOutcome:
        nonlocal child_calls
        child_calls += 1
        return _contained_outcome()

    monkeypatch.setattr(runner._BoundValidationOutput, "create", failed_create)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "publish_bound_no_replace_durable", atomic_publish)
    monkeypatch.setattr(runner, "_run_validation_child", unexpected_child)
    result = runner.run_validation(_args(tmp_path))
    seal = publisher.read_json_mapping(Path(result["initialization_failure_path"]), "init seal")
    assert result["status"] == "FAIL"
    assert child_calls == 0
    assert Path(result["initialization_failure_path"]).parent == tmp_path
    assert seal["failure_stage"] == "bind_output_directory_identity"
    assert seal["exception_type"] == "builtins.OSError"
    assert seal["partial_output_directory"]["path"] == str(intended)
    assert seal["completion_marker_created"] is False


def test_output_initialization_failure_seal_failure_creates_emergency_sibling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    intended = tmp_path / "validation-output"
    publish_calls = 0

    def failed_create(*args: object, **kwargs: object) -> _FakeOutput:
        raise runner.ValidationOutputInitializationFailure(
            "synthetic create failure",
            stage="create_output_directory",
            output_path=intended,
            original_error=PermissionError("undisclosed"),
        )

    def fail_then_publish(
        parent: Path, leaf: str, raw: bytes, **kwargs: object
    ) -> _FakePublication:
        nonlocal publish_calls
        publish_calls += 1
        if publish_calls == 1:
            raise OSError("synthetic primary seal failure")
        path = Path(parent) / leaf
        path.write_bytes(raw)
        return _FakePublication(path, raw)

    monkeypatch.setattr(runner._BoundValidationOutput, "create", failed_create)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    monkeypatch.setattr(runner, "publish_bound_no_replace_durable", fail_then_publish)
    result = runner.run_validation(_args(tmp_path))
    emergency = publisher.read_json_mapping(
        Path(result["initialization_emergency_seal_path"]), "init emergency"
    )
    assert result["status"] == "FAIL"
    assert publish_calls == 2
    assert emergency["failure_seal_exception_type"] == "builtins.OSError"
    assert emergency["failure_seal_expected_sha256"]
    assert emergency["emergency_seal_attempt_count"] == 1
    assert emergency["completion_marker_created"] is False


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize(
    "preparation_site",
    ["partial_observation", "canonical_serialization", "leaf_derivation", "run_uuid"],
)
def test_output_initialization_failure_preparation_interrupt_is_emergency_sealed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interrupt_type: type[BaseException],
    preparation_site: str,
) -> None:
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    intended = tmp_path / "validation-output"
    sensitive = "sensitive-initialization-preparation-detail"
    publish_calls = 0
    initialization_error = runner.ValidationOutputInitializationFailure(
        sensitive,
        stage="bind_output_directory_identity",
        output_path=intended,
        original_error=PermissionError(sensitive),
    )

    def atomic_publish(parent: Path, leaf: str, raw: bytes, **kwargs: object) -> _FakePublication:
        nonlocal publish_calls
        publish_calls += 1
        path = Path(parent) / leaf
        path.write_bytes(raw)
        return _FakePublication(path, raw)

    monkeypatch.setattr(runner, "publish_bound_no_replace_durable", atomic_publish)

    if preparation_site == "partial_observation":
        original = runner._partial_output_directory_observation
        target = "_partial_output_directory_observation"
    elif preparation_site == "canonical_serialization":
        original = runner.canonical_json_bytes
        target = "canonical_json_bytes"
    elif preparation_site == "leaf_derivation":
        original = runner._initialization_failure_leaf
        target = "_initialization_failure_leaf"
    else:
        original = runner.uuid.uuid4
        target = "uuid.uuid4"
    injected_calls = 0

    def interrupt_once(*call_args: object, **call_kwargs: object) -> object:
        nonlocal injected_calls
        injected_calls += 1
        if injected_calls == 1:
            raise interrupt_type(sensitive)
        return original(*call_args, **call_kwargs)

    if target == "uuid.uuid4":
        monkeypatch.setattr(runner.uuid, "uuid4", interrupt_once)
    else:
        monkeypatch.setattr(runner, target, interrupt_once)

    result = runner._publish_initialization_failure(
        parent=tmp_path,
        output_leaf="validation-output",
        error=initialization_error,
        specs=(spec,),
        environment_commitment={"sha256": "a" * 64},
        output_parent={"sha256": "b" * 64},
    )
    seal_path = Path(result["initialization_emergency_seal_path"])
    emergency = publisher.read_json_mapping(seal_path, "initialization emergency")
    raw = seal_path.read_text(encoding="utf-8")

    assert result["status"] == "FAIL"
    assert publish_calls == 1
    assert emergency["failure_seal_exception_type"] == (f"builtins.{interrupt_type.__qualname__}")
    assert emergency["exception_message_disclosed"] is False
    assert emergency["automatic_retry_count"] == 0
    assert emergency["completion_marker_created"] is False
    assert sensitive not in raw


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_post_writer_setup_interrupt_is_independently_emergency_sealed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interrupt_type: type[BaseException],
) -> None:
    args = _args(tmp_path)
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    sensitive = "sensitive-post-writer-setup-detail"
    publish_calls = 0

    class InterruptingPathOutput:
        def __init__(self, path: Path) -> None:
            self._path = path
            self._path.mkdir()
            self.path_read_count = 0
            self.close_count = 0

        @property
        def path(self) -> Path:
            self.path_read_count += 1
            if self.path_read_count == 1:
                raise interrupt_type(sensitive)
            return self._path

        def publication_attempts(self) -> list[dict[str, object]]:
            return []

        def close(self) -> None:
            self.close_count += 1

    output = InterruptingPathOutput(tmp_path / "validation-output")

    def atomic_publish(parent: Path, leaf: str, raw: bytes, **kwargs: object) -> _FakePublication:
        nonlocal publish_calls
        publish_calls += 1
        path = Path(parent) / leaf
        path.write_bytes(raw)
        return _FakePublication(path, raw)

    monkeypatch.setattr(runner._BoundValidationOutput, "create", lambda *args: output)
    monkeypatch.setattr(runner, "build_command_specs", lambda **kwargs: (spec,))
    _repin_work_order_specs(args, (spec,))
    monkeypatch.setattr(runner, "publish_bound_no_replace_durable", atomic_publish)

    result = runner.run_validation(args)
    seal_path = Path(result["post_writer_emergency_seal_path"])
    emergency = publisher.read_json_mapping(seal_path, "post-writer emergency")
    raw = seal_path.read_text(encoding="utf-8")

    assert result["status"] == "FAIL"
    assert publish_calls == 1
    assert seal_path.parent == tmp_path
    assert emergency["exception_type"] == f"builtins.{interrupt_type.__qualname__}"
    assert emergency["exception_message_disclosed"] is False
    assert emergency["terminal_seal_attempt_state"] == "unproven"
    assert emergency["automatic_retry_count"] == 0
    assert emergency["forced_termination_attempts"] == 0
    assert emergency["completion_marker_created"] is False
    assert sensitive not in raw
    assert output.close_count == 1


def _marked_source_line(function: object, marker: str) -> int:
    source, start = inspect.getsourcelines(function)
    matches = [start + index for index, line in enumerate(source) if marker in line]
    assert len(matches) == 1
    return matches[0]


def _line_offsets(function: object, line: int) -> list[int]:
    return [
        instruction.offset
        for instruction in dis.get_instructions(function)
        if instruction.positions is not None and instruction.positions.lineno == line
    ]


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize(
    "marker",
    ["validation-failure-handler-continuation", "validation-failure-dispatch-call"],
)
def test_validation_dispatch_trace_interrupt_is_sealed_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interrupt_type: type[BaseException],
    marker: str,
) -> None:
    args = _args(tmp_path)
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    intended = tmp_path / args.output_leaf
    ordinary_dispatch_calls = 0
    publication_calls = 0

    def failed_create(*_args: object, **_kwargs: object) -> _FakeOutput:
        intended.mkdir()
        raise runner.ValidationOutputInitializationFailure(
            "must not persist",
            stage="bind_output_directory_identity",
            output_path=intended,
            original_error=PermissionError("must not persist"),
        )

    original_publish_failure = runner._publish_initialization_failure

    def publish_failure_once(**kwargs: object) -> dict[str, object]:
        nonlocal ordinary_dispatch_calls
        ordinary_dispatch_calls += 1
        return original_publish_failure(**kwargs)

    def atomic_publish(parent: Path, leaf: str, raw: bytes, **_kwargs: object) -> _FakePublication:
        nonlocal publication_calls
        publication_calls += 1
        path = Path(parent) / leaf
        with path.open("xb") as stream:
            stream.write(raw)
        return _FakePublication(path, raw)

    monkeypatch.setattr(runner._BoundValidationOutput, "create", failed_create)
    monkeypatch.setattr(runner, "build_command_specs", lambda **_kwargs: (spec,))
    _repin_work_order_specs(args, (spec,))
    monkeypatch.setattr(runner, "_publish_initialization_failure", publish_failure_once)
    monkeypatch.setattr(runner, "publish_bound_no_replace_durable", atomic_publish)

    target_line = _marked_source_line(runner.run_validation, marker)

    def trace(frame: object, event: str, _arg: object) -> object:
        if (
            getattr(frame, "f_code", None) is runner.run_validation.__code__
            and event == "line"
            and getattr(frame, "f_lineno", None) == target_line
        ):
            sys.settrace(None)
            raise interrupt_type("must not persist")
        return trace

    sys.settrace(trace)
    try:
        result = runner.run_validation(args)
    finally:
        sys.settrace(None)

    seal_path = Path(result["initialization_failure_path"])
    seal = publisher.read_json_mapping(seal_path, "dispatch failure seal")
    assert result["status"] == "FAIL"
    assert ordinary_dispatch_calls == 1
    assert publication_calls == 1
    assert seal_path.parent == tmp_path
    assert seal["automatic_retry_count"] == 0
    assert seal["completion_marker_created"] is False
    assert not any(path.name.startswith("completion") for path in tmp_path.rglob("*"))
    assert len([path for path in tmp_path.iterdir() if path.is_file()]) == 1
    assert "must not persist" not in seal_path.read_text(encoding="utf-8")


def test_validation_dispatch_call_and_handler_are_outer_exception_protected() -> None:
    function = runner.run_validation
    entries = dis.Bytecode(function).exception_entries
    dispatch_line = _marked_source_line(function, "validation-failure-dispatch-call")
    handler_line = _marked_source_line(function, "validation-failure-handler-continuation")

    dispatch_offsets = _line_offsets(function, dispatch_line)
    handler_offsets = _line_offsets(function, handler_line)
    call_offsets = {
        instruction.offset
        for instruction in dis.get_instructions(function)
        if instruction.opname == "CALL" and instruction.offset in dispatch_offsets
    }
    assert call_offsets
    assert handler_offsets
    for offset in (*call_offsets, *handler_offsets):
        assert any(entry.start <= offset < entry.end for entry in entries)


def test_validation_dispatch_failure_uses_distinct_emergency_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    spec = runner.CommandSpec("only-command", (sys.executable, "--version"))
    intended = tmp_path / args.output_leaf
    ordinary_dispatch_calls = 0
    publication_calls = 0

    def failed_create(*_args: object, **_kwargs: object) -> _FakeOutput:
        intended.mkdir()
        raise PermissionError("undisclosed create failure")

    def failed_ordinary_dispatch(**_kwargs: object) -> dict[str, object]:
        nonlocal ordinary_dispatch_calls
        ordinary_dispatch_calls += 1
        raise KeyboardInterrupt("undisclosed ordinary seal failure")

    def atomic_publish(parent: Path, leaf: str, raw: bytes, **_kwargs: object) -> _FakePublication:
        nonlocal publication_calls
        publication_calls += 1
        path = Path(parent) / leaf
        with path.open("xb") as stream:
            stream.write(raw)
        return _FakePublication(path, raw)

    monkeypatch.setattr(runner._BoundValidationOutput, "create", failed_create)
    monkeypatch.setattr(runner, "build_command_specs", lambda **_kwargs: (spec,))
    _repin_work_order_specs(args, (spec,))
    monkeypatch.setattr(runner, "_publish_initialization_failure", failed_ordinary_dispatch)
    monkeypatch.setattr(runner, "publish_bound_no_replace_durable", atomic_publish)

    result = runner.run_validation(args)
    seal_path = Path(result["dispatch_boundary_emergency_seal_path"])
    seal = publisher.read_json_mapping(seal_path, "dispatch boundary emergency")

    assert result["status"] == "FAIL"
    assert ordinary_dispatch_calls == 1
    assert publication_calls == 1
    assert seal["ordinary_failure_dispatch_retry_count"] == 0
    assert seal["automatic_retry_count"] == 0
    assert seal["completion_marker_created"] is False
    assert not (tmp_path / "completion-marker.json").exists()


def test_validation_failure_contract_does_not_guarantee_ultimate_emergency_writer() -> None:
    contract = runner.validation_failure_seal_contract()

    assert contract["post_writer_setup_or_outer_terminal_escape_sibling_seal_attempted"] is True
    assert contract["durable_record_after_independent_emergency_writer_failure_guaranteed"] is False
    assert contract["production_go_enabled"] is False
    assert contract["go_evidence_eligible"] is False


def test_partial_output_observation_is_lstat_only_and_marks_reparse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "attacker-junction"
    lstat_calls = 0

    class Metadata:
        st_mode = stat.S_IFDIR
        st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        st_size = 0
        st_mtime_ns = 123

    def fake_lstat(value: object) -> Metadata:
        nonlocal lstat_calls
        lstat_calls += 1
        assert os.fspath(value) == os.fspath(path)
        return Metadata()

    monkeypatch.setattr(runner.os.path, "lexists", lambda value: True)
    monkeypatch.setattr(runner.os, "lstat", fake_lstat)
    observation = runner._partial_output_directory_observation(path)
    assert lstat_calls == 1
    assert observation["lstat_only"] is True
    assert observation["is_directory_entry"] is True
    assert observation["is_reparse_point"] is True
    assert observation["observation_error_type"] is None


def _handle_identity(path: str, *, size: int, directory: bool) -> runner.handle_io.HandleIdentity:
    return runner.handle_io.HandleIdentity(
        final_path=path,
        volume_serial_number=1,
        file_id_hex="01" if directory else "02",
        size=size,
        link_count=1,
        attributes=runner.handle_io.FILE_ATTRIBUTE_DIRECTORY if directory else 0,
        reparse_tag=0,
        file_type=runner.handle_io.FILE_TYPE_DISK,
        owner_sid="S-1-5-21-test",
        security_descriptor_sha256="a" * 64,
        dacl_present=True,
        dacl_protected=True,
    )


def test_post_rename_flush_failure_records_final_leaf_sha_and_stage(tmp_path: Path) -> None:
    output_path = tmp_path / "bound-output"
    output_path.mkdir()
    raw = b'{"partial":true}\n'

    class PostRenameFailureApi:
        def __init__(self) -> None:
            self.renamed = False
            self.file_flush_count = 0

        def identity(self, handle: int) -> runner.handle_io.HandleIdentity:
            if handle == 1:
                return _handle_identity(str(output_path), size=0, directory=True)
            leaf = "result.json" if self.renamed else ".result.json.test-run.partial"
            return _handle_identity(
                runner.ntpath.join(str(output_path), leaf), size=len(raw), directory=False
            )

        def create_relative_file(self, directory_handle: int, leaf: str) -> int:
            return 2

        def protect_dacl(self, handle: int) -> None:
            pass

        def write_all(self, handle: int, value: bytes) -> None:
            assert value == raw

        def flush(self, handle: int) -> None:
            self.file_flush_count += 1
            if self.renamed:
                raise OSError("synthetic post-rename flush failure")

        def read_all(self, handle: int, size: int) -> bytes:
            return raw

        def rename_no_replace(self, file_handle: int, directory_handle: int, leaf: str) -> None:
            self.renamed = True

        def flush_directory(self, handle: int) -> None:
            raise AssertionError("directory flush must not follow failed file flush")

        def close(self, handle: int | None) -> None:
            pass

    api = PostRenameFailureApi()
    writer = runner._BoundValidationOutput(
        path=output_path,
        run_uuid="test-run",
        api=api,
        directory_handle=1,
        directory_identity=_handle_identity(str(output_path), size=0, directory=True),
    )
    with pytest.raises(runner.ValidationRunnerError, match="flush_final_and_directory"):
        writer.publish("result.json", raw)
    attempts = writer.publication_attempts()
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["stage"] == "flush_final_and_directory"
    assert attempt["rename_completed"] is True
    assert attempt["publication_complete"] is False
    assert attempt["intended_final_path"] == runner.ntpath.join(str(output_path), "result.json")
    assert attempt["expected_sha256"] == runner.sha256_bytes(raw)
    assert attempt["failure_observation"]["status"] == "same_handle_observed"
    assert attempt["failure_observation"]["current_sha256"] == runner.sha256_bytes(raw)


def test_terminal_seal_binds_prior_publication_attempt_ledger(tmp_path: Path) -> None:
    output = _FakeOutput(tmp_path / "output")
    output.publication_attempts = lambda: [
        {
            "sequence": 1,
            "leaf": "01-command.json",
            "stage": "flush_final_and_directory",
            "rename_completed": True,
            "publication_complete": False,
            "expected_sha256": "a" * 64,
        }
    ]
    result = runner._publish_terminal_failure(
        output_writer=output,
        output=output.path,
        stage="command:evidence_publication",
        reason="unexpected_validation_or_publication_exception",
        specs=(runner.CommandSpec("command", (sys.executable, "--version")),),
        validation_child_call_count=1,
        command_refs=[],
        metadata_evidence=[],
        environment_commitment={},
        output_parent={},
    )
    terminal = publisher.read_json_mapping(
        output.path / "terminal-validation-failure.json", "terminal"
    )
    assert result["status"] == "FAIL"
    assert terminal["publication_attempts_before_terminal_seal"][0]["rename_completed"] is True
    assert result["publication_attempts"][0]["expected_sha256"] == "a" * 64


def test_containment_rejects_extra_identity_event_zero_ppid_and_membership_extras() -> None:
    base = _contained_outcome(return_code=0)
    extra_identity_event = JobEvent(
        sequence=9,
        event="identity_observed",
        monotonic_ns=9,
        timestamp_utc="2026-09-02T00:00:00+00:00",
        pid=base.identities[0].pid,
    )
    assert "identity_event_binding" in runner._containment_evidence_errors(
        replace(base, events=(*base.events, extra_identity_event))
    )
    assert "identity_fields" in runner._containment_evidence_errors(
        replace(base, identities=(replace(base.identities[0], ppid=0),))
    )
    membership_events = tuple(
        replace(event, details={**event.details, "unexpected": True})
        if event.event == "job_membership_verified"
        else event
        for event in base.events
    )
    assert "membership_evidence" in runner._containment_evidence_errors(
        replace(base, events=membership_events)
    )
