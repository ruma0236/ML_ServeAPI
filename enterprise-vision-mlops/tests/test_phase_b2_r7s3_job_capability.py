from __future__ import annotations

import ctypes
import json
import os
import stat
import sys
import uuid
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r7s3_process as process


def _capability_environment(nonce: bytes, run_uuid: str, handle: int = 444) -> dict[str, str]:
    return {
        process.RUN_UUID_ENV: run_uuid,
        process.JOB_CAPABILITY_HANDLE_ENV: str(handle),
        process.JOB_CAPABILITY_NONCE_ENV: nonce.hex(),
        process.JOB_CAPABILITY_COMMITMENT_ENV: process.job_capability_commitment(nonce, run_uuid),
        "KEEP": "preserved",
    }


def _exclusive_snapshot(pid: int) -> dict[str, Any]:
    return {
        "is_process_in_job": True,
        "limit_flags": 0,
        "active_processes": 1,
        "total_processes": 1,
        "terminated_processes": 0,
        "assigned_processes": 1,
        "process_ids": [pid],
    }


class _SnapshotApi:
    def __init__(
        self,
        *,
        explicit: dict[str, Any] | None = None,
        implicit: dict[str, Any] | None = None,
    ) -> None:
        current = _exclusive_snapshot(os.getpid())
        self.explicit = dict(current if explicit is None else explicit)
        self.implicit = dict(current if implicit is None else implicit)
        self.cleared: list[int] = []
        self.closed: list[int] = []
        self.queries: list[int | None] = []

    def clear_handle_inherit(self, handle: int) -> None:
        self.cleared.append(handle)

    def current_job_snapshot(self, handle: int | None) -> dict[str, Any]:
        self.queries.append(handle)
        return dict(self.implicit if handle is None else self.explicit)

    def close(self, handle: int | None) -> None:
        if handle is not None:
            self.closed.append(handle)


def test_commitment_is_domain_and_run_bound_and_requires_exact_nonce() -> None:
    nonce = bytes(range(process.JOB_CAPABILITY_NONCE_BYTES))
    first_run = str(uuid.uuid4())
    second_run = str(uuid.uuid4())
    first = process.job_capability_commitment(nonce, first_run)

    assert len(first) == 64
    assert first == process.job_capability_commitment(nonce.hex(), first_run)
    assert first != process.job_capability_commitment(nonce, second_run)
    assert nonce.hex() not in first
    with pytest.raises(process.ProcessContainmentError, match="exactly 32 bytes"):
        process.job_capability_commitment(nonce[:-1], first_run)
    with pytest.raises(process.ProcessContainmentError, match="canonical"):
        process.job_capability_commitment(nonce.hex().upper(), first_run)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE semantics")
def test_child_consumes_env_once_and_returns_commitment_only() -> None:
    nonce = b"n" * process.JOB_CAPABILITY_NONCE_BYTES
    run_uuid = str(uuid.uuid4())
    environment = _capability_environment(nonce, run_uuid)
    api = _SnapshotApi()

    evidence = process.consume_inherited_job_capability(environment=environment, api=api)

    assert api.cleared == [444]
    assert api.closed == [444]
    assert api.queries == [444, None]
    assert evidence["nonce_commitment"] == process.job_capability_commitment(nonce, run_uuid)
    assert evidence["requested_access"] == process.JOB_CAPABILITY_QUERY_ACCESS
    assert evidence["snapshots_equal"] is True
    assert evidence["environment_consumed"] is True
    assert evidence["raw_nonce_recorded"] is False
    assert nonce.hex() not in json.dumps(evidence, sort_keys=True)
    assert environment == {process.RUN_UUID_ENV: run_uuid, "KEEP": "preserved"}

    with pytest.raises(process.ProcessContainmentError, match="environment field invalid"):
        process.consume_inherited_job_capability(environment=environment, api=api)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE semantics")
def test_matching_job_snapshot_allows_a_real_descendant_such_as_conhost() -> None:
    nonce = b"d" * process.JOB_CAPABILITY_NONCE_BYTES
    run_uuid = str(uuid.uuid4())
    environment = _capability_environment(nonce, run_uuid)
    current_pid = os.getpid()
    descendant_pid = current_pid + 100_000
    snapshot = {
        "is_process_in_job": True,
        "limit_flags": 0,
        "active_processes": 2,
        "total_processes": 2,
        "terminated_processes": 0,
        "assigned_processes": 2,
        "process_ids": sorted([current_pid, descendant_pid]),
    }
    evidence = process.consume_inherited_job_capability(
        environment=environment,
        api=_SnapshotApi(explicit=snapshot, implicit=snapshot),
    )
    assert evidence["snapshots_equal"] is True
    assert evidence["explicit_job"]["process_ids"] == snapshot["process_ids"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE semantics")
def test_missing_handle_clears_nonce_and_commitment_fail_closed() -> None:
    nonce = b"m" * process.JOB_CAPABILITY_NONCE_BYTES
    run_uuid = str(uuid.uuid4())
    environment = _capability_environment(nonce, run_uuid)
    del environment[process.JOB_CAPABILITY_HANDLE_ENV]

    with pytest.raises(process.ProcessContainmentError, match="environment field invalid"):
        process.consume_inherited_job_capability(environment=environment, api=_SnapshotApi())

    assert process.JOB_CAPABILITY_NONCE_ENV not in environment
    assert process.JOB_CAPABILITY_COMMITMENT_ENV not in environment


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE semantics")
@pytest.mark.parametrize("invalid_field", ["run_uuid", "nonce", "commitment"])
def test_parsed_capability_handle_is_closed_on_prequery_validation_failure(
    invalid_field: str,
) -> None:
    nonce = b"v" * process.JOB_CAPABILITY_NONCE_BYTES
    run_uuid = str(uuid.uuid4())
    environment = _capability_environment(nonce, run_uuid)
    if invalid_field == "run_uuid":
        environment[process.RUN_UUID_ENV] = "invalid-run-uuid"
    elif invalid_field == "nonce":
        environment[process.JOB_CAPABILITY_NONCE_ENV] = "not-a-nonce"
    else:
        environment[process.JOB_CAPABILITY_COMMITMENT_ENV] = "0" * 64
    api = _SnapshotApi()

    with pytest.raises((ValueError, process.ProcessContainmentError)):
        process.consume_inherited_job_capability(environment=environment, api=api)

    assert api.closed == [444]
    assert process.JOB_CAPABILITY_HANDLE_ENV not in environment
    assert process.JOB_CAPABILITY_NONCE_ENV not in environment
    assert process.JOB_CAPABILITY_COMMITMENT_ENV not in environment


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE semantics")
@pytest.mark.parametrize(
    "missing_field",
    [process.JOB_CAPABILITY_NONCE_ENV, process.JOB_CAPABILITY_COMMITMENT_ENV],
)
def test_parsed_capability_handle_is_closed_when_later_field_is_missing(
    missing_field: str,
) -> None:
    nonce = b"x" * process.JOB_CAPABILITY_NONCE_BYTES
    run_uuid = str(uuid.uuid4())
    environment = _capability_environment(nonce, run_uuid)
    del environment[missing_field]
    api = _SnapshotApi()

    with pytest.raises(process.ProcessContainmentError, match="environment field invalid"):
        process.consume_inherited_job_capability(environment=environment, api=api)

    assert api.closed == [444]
    assert process.JOB_CAPABILITY_HANDLE_ENV not in environment
    assert process.JOB_CAPABILITY_NONCE_ENV not in environment
    assert process.JOB_CAPABILITY_COMMITMENT_ENV not in environment


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE semantics")
def test_api_initialization_interrupt_uses_native_fallback_handle_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nonce = b"f" * process.JOB_CAPABILITY_NONCE_BYTES
    run_uuid = str(uuid.uuid4())
    environment = _capability_environment(nonce, run_uuid)
    closed: list[int] = []

    class _CloseHandle:
        argtypes: Any = None
        restype: Any = None

        def __call__(self, handle: Any) -> int:
            closed.append(int(handle.value))
            return 1

    kernel = SimpleNamespace(CloseHandle=_CloseHandle())
    monkeypatch.setattr(
        process,
        "_WindowsJobApi",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(process.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)

    with pytest.raises(KeyboardInterrupt):
        process.consume_inherited_job_capability(environment=environment)

    assert closed == [444]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE semantics")
@pytest.mark.parametrize("failure", ["swapped_explicit", "implicit_mismatch"])
def test_swapped_or_null_job_mismatch_is_rejected_and_handle_closed(failure: str) -> None:
    nonce = b"s" * process.JOB_CAPABILITY_NONCE_BYTES
    run_uuid = str(uuid.uuid4())
    environment = _capability_environment(nonce, run_uuid)
    explicit = _exclusive_snapshot(os.getpid())
    implicit = _exclusive_snapshot(os.getpid())
    if failure == "swapped_explicit":
        explicit["is_process_in_job"] = False
    else:
        implicit["process_ids"] = [os.getpid() + 1]
    api = _SnapshotApi(explicit=explicit, implicit=implicit)

    with pytest.raises(process.ProcessContainmentError, match="Job"):
        process.consume_inherited_job_capability(environment=environment, api=api)

    assert api.cleared == [444]
    assert api.closed == [444]
    assert process.JOB_CAPABILITY_HANDLE_ENV not in environment
    assert process.JOB_CAPABILITY_NONCE_ENV not in environment


class _DuplicateKernel:
    def __init__(self, duplicate_value: int = 444) -> None:
        self.duplicate_value = duplicate_value
        self.calls: list[tuple[Any, ...]] = []

    @staticmethod
    def GetCurrentProcess() -> int:
        return -1

    def DuplicateHandle(
        self,
        source_process: int,
        source_handle: int,
        target_process: int,
        target_handle: Any,
        desired_access: int,
        inherit: bool,
        options: int,
    ) -> int:
        self.calls.append(
            (
                source_process,
                source_handle,
                target_process,
                desired_access,
                bool(inherit),
                options,
            )
        )
        ctypes.cast(
            target_handle, ctypes.POINTER(wintypes.HANDLE)
        ).contents.value = self.duplicate_value
        return 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE semantics")
def test_duplicate_handle_is_query_only_inheritable_and_full_access_is_rejected() -> None:
    kernel = _DuplicateKernel()
    api = process._WindowsJobApi.__new__(process._WindowsJobApi)
    api.kernel32 = kernel

    duplicate = api.duplicate_inheritable_job_capability(100)

    assert duplicate == 444
    assert kernel.calls == [(-1, 100, -1, 0x00020004, True, 0)]
    mutating_job_rights = 0x0001 | 0x0002 | 0x0008
    assert process.JOB_CAPABILITY_QUERY_ACCESS & mutating_job_rights == 0
    with pytest.raises(process.ProcessContainmentError, match="exact query-only"):
        api.duplicate_inheritable_job_capability(100, desired_access=0x001F001F)
    assert len(kernel.calls) == 1


class _CreateKernel(_DuplicateKernel):
    def __init__(self) -> None:
        super().__init__()
        self.application_name: str | None = None
        self.environment_block = ""
        self.closed: list[int] = []
        self.deleted_attribute_lists = 0
        self.executable_lock: tuple[str, int, int, int] | None = None

    def CreateFileW(
        self,
        path: str,
        access: int,
        share_mode: int,
        _security: Any,
        _creation: int,
        flags: int,
        _template: Any,
    ) -> int:
        self.executable_lock = (path, access, share_mode, flags)
        return 445

    def CreateProcessW(
        self,
        application_name: str,
        _command_line: Any,
        _process_security: Any,
        _thread_security: Any,
        _inherit_handles: bool,
        _flags: int,
        environment: Any,
        _cwd: str | None,
        _startup: Any,
        process_information: Any,
    ) -> int:
        self.application_name = application_name
        self.environment_block = environment[:]
        info = ctypes.cast(
            process_information, ctypes.POINTER(process._PROCESS_INFORMATION)
        ).contents
        info.hProcess = 501
        info.hThread = 502
        info.dwProcessId = 503
        info.dwThreadId = 504
        return 1

    def DeleteProcThreadAttributeList(self, _attributes: Any) -> None:
        self.deleted_attribute_lists += 1

    def CloseHandle(self, handle: int) -> int:
        self.closed.append(int(handle))
        return 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE semantics")
def test_create_process_uses_explicit_absolute_app_and_exact_handle_roles(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "pinned-runtime.exe"
    executable.write_bytes(b"MZ\x00fake-pinned-runtime")
    kernel = _CreateKernel()
    api = process._WindowsJobApi.__new__(process._WindowsJobApi)
    api.kernel32 = kernel
    captured_handles: list[tuple[int, ...]] = []

    def attributes(job: int, handles: Any) -> tuple[Any, Any, Any, Any]:
        assert job == 100
        captured_handles.append(tuple(handles))
        return ctypes.create_string_buffer(1), ctypes.c_void_p(), None, None

    api.initialise_attributes = attributes
    nonce = b"c" * process.JOB_CAPABILITY_NONCE_BYTES
    run_uuid = str(uuid.uuid4())
    hostile_environment = {
        process.JOB_CAPABILITY_HANDLE_ENV.lower(): "999",
        process.JOB_CAPABILITY_NONCE_ENV: "0" * 64,
        process.JOB_CAPABILITY_COMMITMENT_ENV.lower(): "f" * 64,
        process.RUN_UUID_ENV: run_uuid,
        "KEEP": "ok",
    }

    info = api.create_suspended_process(
        command=(str(executable), "--arg"),
        cwd=str(tmp_path),
        environment=hostile_environment,
        job=100,
        stdin_handle=11,
        stdout_handle=12,
        stderr_handle=13,
        job_capability_nonce=nonce,
        job_capability_run_uuid=run_uuid,
        create_no_window=True,
    )

    assert info.dwProcessId == 503
    assert kernel.application_name == os.path.normpath(os.path.abspath(executable))
    assert kernel.application_name is not None
    assert captured_handles == [(11, 12, 13, 444)]
    assert kernel.calls == [(-1, 100, -1, process.JOB_CAPABILITY_QUERY_ACCESS, True, 0)]
    assert 444 in kernel.closed
    assert 445 in kernel.closed
    assert kernel.executable_lock == (
        kernel.application_name,
        process._WindowsJobApi._GENERIC_READ,
        process._WindowsJobApi._FILE_SHARE_READ,
        process._WindowsJobApi._FILE_ATTRIBUTE_NORMAL
        | process._WindowsJobApi._FILE_FLAG_OPEN_REPARSE_POINT,
    )
    assert kernel.deleted_attribute_lists == 1
    fields = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in kernel.environment_block.split("\0")
        if "=" in item
    }
    assert fields[process.JOB_CAPABILITY_HANDLE_ENV] == "444"
    assert fields[process.JOB_CAPABILITY_NONCE_ENV] == nonce.hex()
    assert fields[process.JOB_CAPABILITY_COMMITMENT_ENV] == (
        process.job_capability_commitment(nonce, run_uuid)
    )
    assert fields["KEEP"] == "ok"
    assert "999" not in fields.values()
    assert "0" * 64 not in fields.values()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_application_path_must_be_absolute_and_reparse_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(process.ProcessContainmentError, match="must be absolute"):
        process._validated_executable_identity("relative.exe")

    executable = tmp_path / "runtime.exe"
    executable.write_bytes(b"MZ")
    original_lstat = process.os.lstat

    def reparse_lstat(path: str | os.PathLike[str]) -> Any:
        measured = original_lstat(path)
        if os.path.normcase(os.path.abspath(path)) != os.path.normcase(str(executable)):
            return measured
        return SimpleNamespace(
            st_mode=measured.st_mode,
            st_dev=measured.st_dev,
            st_ino=measured.st_ino,
            st_size=measured.st_size,
            st_mtime_ns=measured.st_mtime_ns,
            st_file_attributes=getattr(measured, "st_file_attributes", 0)
            | getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )

    monkeypatch.setattr(process.os, "lstat", reparse_lstat)
    with pytest.raises(process.ProcessContainmentError, match="reparse component"):
        process._validated_executable_identity(executable)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing semantics")
def test_executable_lock_denies_write_and_replacement_until_closed(tmp_path: Path) -> None:
    executable = tmp_path / "runtime.exe"
    replacement = tmp_path / "replacement.exe"
    executable.write_bytes(b"MZ-original")
    replacement.write_bytes(b"MZ-replacement")
    api = process._WindowsJobApi()

    handle = api.open_executable_lock(str(executable.resolve()))
    try:
        with pytest.raises(OSError):
            executable.write_bytes(b"MZ-mutated")
        with pytest.raises(OSError):
            os.replace(replacement, executable)
        assert executable.read_bytes() == b"MZ-original"
    finally:
        api.close(handle)

    os.replace(replacement, executable)
    assert executable.read_bytes() == b"MZ-replacement"


def test_primitive_contract_records_unwired_child_and_remaining_trust_boundaries() -> None:
    contract = process.R7S3_JOB_CAPABILITY_PRIMITIVE_CONTRACT
    assert contract["parent_provisions_private_inherited_job_capability"] is True
    assert contract["job_capability_access"] == 0x00020004
    assert contract["child_consumption_helper_available"] is True
    assert contract["child_consumption_wired_to_production"] is False
    assert contract["explicit_current_job_snapshot_equivalence_helper"] is True
    assert contract["explicit_current_job_snapshot_equivalence_enforced"] is False
    assert contract["explicit_application_name"] is True
    assert contract["completion_port_blocking_wait"] is True
    assert contract["completion_event_batch_limit"] == (
        process.DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN
    )
    assert contract["completion_drain_deadline_and_cancel_checks"] is True
    assert contract["final_safe_gate_after_bounded_stream_decode"] is True
    assert contract["reader_start_exception_native_state_cleanup"] is True
    assert contract["completion_poll_interval_seconds"] == 0.001
    assert contract["missed_descendant_identity_fails_closed"] is True
    assert contract["executable_handle_held_through_create"] is True
    assert contract["restore_deadline_bounds_runner_stages"] is False
    assert contract["pre_kernel_filesystem_setup_hard_deadline_bounded"] is False
    assert contract["base_exception_converted_to_containment_failure"] is False
    assert contract["base_exception_conversion_scope"] == (
        "post_windows_api_initialization_runner_body"
    )
    assert contract["job_capability_consumed_before_workload"] is False
    assert contract["ambient_ancestor_job_effective_limits_audited"] is False
    assert contract["residual_job_observer_lease_until_active_zero"] is False
    assert contract["wsl_kernel_lineage_containment"] is False
    assert contract["wsl_interpreter_sha256_pinned"] is False
    assert contract["wsl_scan_nonce_unique_per_poll"] is True
    assert contract["same_token_hostile_admin_protected"] is False
    assert contract["go_evidence_eligible"] is False
    assert contract["external_review_required"] is True


def test_stable_r7_manifest_contract_is_not_silently_strengthened() -> None:
    assert set(process.PROCESS_CONTAINMENT_CONTRACT) == {
        "provider",
        "create_suspended",
        "assign_before_resume",
        "breakaway_allowed",
        "kill_on_job_close",
        "terminate_job_object_allowed",
        "job_accounting_authoritative",
        "stdio_drain_before_followup",
        "residual_repoll_seconds",
        "force_termination_attempts",
        "wsl_run_uuid_and_process_group",
        "wsl_proc_residual_check",
    }


@pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows Job inheritance")
def test_real_child_consumes_inherited_query_capability_and_matches_null_job() -> None:
    module_path = str(Path(process.__file__).resolve())
    child = (
        "import importlib.util,json,sys;"
        f"p={module_path!r};"
        "s=importlib.util.spec_from_file_location('_r7s3_child_process',p);"
        "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;"
        "s.loader.exec_module(m);"
        "print(json.dumps(m.consume_inherited_job_capability(),sort_keys=True),flush=True)"
    )
    outcome = process.WindowsJobProcessRunner().run(
        [sys.executable, "-I", "-S", "-B", "-c", child],
        name="r7s3-real-job-capability-smoke",
        poll_interval_seconds=0.005,
    )

    assert outcome.return_code == 0, (
        f"identities={[(item.pid, item.ppid, item.image) for item in outcome.identities]!r} "
        f"stdout={outcome.stdout!r} stderr={outcome.stderr!r}"
    )
    assert outcome.safe_for_followup is True
    assert outcome.residual_pids == ()
    evidence = json.loads(outcome.stdout.strip())
    assert evidence["snapshots_equal"] is True
    assert evidence["environment_consumed"] is True
    assert evidence["raw_nonce_recorded"] is False
    assert evidence["pid"] in {item.pid for item in outcome.identities}
    assert evidence["explicit_job"] == evidence["implicit_job"]


class _RedactionRunnerApi:
    _JOB_MESSAGE_ACTIVE_ZERO = 4
    _JOB_MESSAGE_NEW_PROCESS = 6
    _JOB_MESSAGE_EXIT_PROCESS = 7
    _JOB_MESSAGE_ABNORMAL_EXIT = 8

    def __init__(self) -> None:
        self.pipe_index = 0
        self.accounting_calls = 0
        self.nonce_hex = ""

    @staticmethod
    def create_job_and_completion_port() -> tuple[int, int]:
        return 100, 101

    def create_pipe(self) -> tuple[int, int]:
        self.pipe_index += 1
        return 200 + self.pipe_index * 2, 201 + self.pipe_index * 2

    @staticmethod
    def open_inheritable_null() -> int:
        return 300

    def create_suspended_process(self, **kwargs: Any) -> SimpleNamespace:
        self.nonce_hex = bytes(kwargs["job_capability_nonce"]).hex()
        kwargs["pre_kernel_create_gate"]()
        return SimpleNamespace(hProcess=400, hThread=401, dwProcessId=402)

    @staticmethod
    def is_process_in_job(_process: int, _job: int) -> bool:
        return True

    @staticmethod
    def query_limit_flags(_job: int) -> int:
        return 0

    def query_accounting(self, _job: int) -> SimpleNamespace:
        self.accounting_calls += 1
        active = 1 if self.accounting_calls == 1 else 0
        return SimpleNamespace(
            TotalProcesses=1,
            ActiveProcesses=active,
            TotalTerminatedProcesses=0,
        )

    @staticmethod
    def query_active_pids(_job: int) -> tuple[int, ...]:
        return ()

    @staticmethod
    def process_is_active(_process: int) -> bool:
        return True

    @staticmethod
    def process_identity(
        _process: int,
        *,
        pid: int,
        fallback_ppid: int | None,
        run_uuid: str,
        observed_sequence: int,
    ) -> process.ProcessIdentity:
        return process.ProcessIdentity(
            pid=pid,
            ppid=fallback_ppid,
            creation_time_ns=1,
            creation_time_utc="2026-09-01T00:00:00+00:00",
            image="pinned.exe",
            run_uuid=run_uuid,
            observed_sequence=observed_sequence,
        )

    @staticmethod
    def completion_events(_completion: int) -> list[tuple[int, int | None]]:
        return []

    @staticmethod
    def resume(_thread: int) -> None:
        return None

    def read_pipe(self, _handle: int, sink: bytearray, drained: Any) -> None:
        sink.extend(self.nonce_hex.encode("ascii"))
        drained.set()

    @staticmethod
    def exit_code(_process: int) -> int:
        return 0

    @staticmethod
    def open_process(_pid: int) -> None:
        return None

    @staticmethod
    def close(_handle: int | None) -> None:
        return None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_raw_nonce_reflected_by_child_is_redacted_from_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RedactionRunnerApi()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    outcome = process.WindowsJobProcessRunner().run([r"C:\pinned.exe"], poll_interval_seconds=0.001)

    assert fake.nonce_hex
    assert fake.nonce_hex not in json.dumps(outcome.to_dict(), sort_keys=True)
    assert process._JOB_CAPABILITY_REDACTION in outcome.stdout
    assert process._JOB_CAPABILITY_REDACTION in outcome.stderr
    assert outcome.safe_for_followup is True


def test_source_retains_no_forced_job_termination_or_breakaway_token() -> None:
    source = Path(process.__file__).read_text(encoding="utf-8")
    forbidden = (
        "Terminate" + "JobObject",
        "KILL" + "_ON_JOB_CLOSE",
        "CREATE_" + "BREAKAWAY_FROM_JOB",
        "SILENT_" + "BREAKAWAY_OK",
    )
    assert all(token not in source for token in forbidden)
