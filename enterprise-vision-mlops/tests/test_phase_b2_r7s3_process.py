from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r7s3_process as process


def _identity(**overrides: Any) -> process.ProcessIdentity:
    values: dict[str, Any] = {
        "pid": 101,
        "ppid": 100,
        "creation_time_ns": 1,
        "creation_time_utc": "2026-09-02T00:00:00+00:00",
        "image": r"C:\runtime.exe",
        "run_uuid": str(uuid.uuid4()),
        "observed_sequence": 1,
    }
    values.update(overrides)
    return process.ProcessIdentity(**values)


@pytest.mark.parametrize("non_finite", [float("inf"), float("-inf"), float("nan")])
def test_timeout_contract_rejects_non_finite_values(non_finite: float) -> None:
    for field in (
        "kubectl_timeout_seconds",
        "wrapper_timeout_seconds",
        "restore_deadline_seconds",
        "residual_repoll_seconds",
        "stream_drain_seconds",
    ):
        values = {
            "kubectl_timeout_seconds": 1.0,
            "wrapper_timeout_seconds": 2.0,
            "restore_deadline_seconds": 5.0,
            "residual_repoll_seconds": 1.0,
            "stream_drain_seconds": 1.0,
        }
        values[field] = non_finite
        with pytest.raises(ValueError, match="timeout values must be positive"):
            process.TimeoutContract(**values)


@pytest.mark.parametrize("non_finite", [float("inf"), float("-inf"), float("nan")])
def test_runner_rejects_non_finite_poll_interval_before_api_creation(
    monkeypatch: pytest.MonkeyPatch, non_finite: float
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: calls.append(True))

    with pytest.raises(ValueError, match="poll_interval_seconds must be positive"):
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], poll_interval_seconds=non_finite)

    assert calls == []


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("image", ""),
        ("run_uuid", "not-a-uuid"),
        ("observed_sequence", 0),
        ("creation_time_utc", "naive-or-invalid"),
        ("ppid", -1),
    ],
)
def test_identity_coverage_rejects_incomplete_identity_fields(override: str, value: Any) -> None:
    assert process.identity_coverage_complete(1, [_identity(**{override: value})]) is False


def test_identity_coverage_rejects_duplicate_sequences_and_mixed_run_ids() -> None:
    run_uuid = str(uuid.uuid4())
    first = _identity(pid=101, run_uuid=run_uuid, observed_sequence=7)
    duplicate_sequence = _identity(
        pid=102,
        creation_time_ns=2,
        run_uuid=run_uuid,
        observed_sequence=7,
    )
    mixed_run = _identity(
        pid=102,
        creation_time_ns=2,
        run_uuid=str(uuid.uuid4()),
        observed_sequence=8,
    )

    assert process.identity_coverage_complete(2, [first, duplicate_sequence]) is False
    assert process.identity_coverage_complete(2, [first, mixed_run]) is False
    assert (
        process.identity_coverage_complete(
            2,
            [
                first,
                _identity(
                    pid=102,
                    creation_time_ns=2,
                    run_uuid=run_uuid,
                    observed_sequence=8,
                ),
            ],
        )
        is True
    )


def _wsl_scan_envelope(
    protocol: process.WslResidualProtocol,
    *,
    row_update: dict[str, Any] | None = None,
    envelope_update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assert protocol.boot_id is not None
    assert protocol.root_process_group is not None
    assert protocol.root_start_time_ticks is not None
    boot_id = protocol.boot_id
    row: dict[str, Any] = {
        "pid": 77,
        "ppid": 1,
        "pgrp": 42,
        "session": 42,
        "start_time_ticks": 901,
        "boot_id": boot_id,
        "run_uuid_match": False,
        "process_group_match": True,
        "cmdline_sha256": "a" * 64,
        "environ_readable": True,
        "cmdline_readable": True,
    }
    row.update(row_update or {})
    envelope: dict[str, Any] = {
        "schema": "evm.phase-b2.wsl-residual-scan.v2",
        "run_uuid": protocol.run_uuid,
        "scan_nonce": protocol.scan_nonce,
        "boot_id": boot_id,
        "expected_process_group": protocol.root_process_group,
        "expected_start_time_ticks": protocol.root_start_time_ticks,
        "expected_boot_id": boot_id,
        "scan_complete": True,
        "resource_limit_exceeded": False,
        "processes_examined": 2,
        "vanished_during_scan": 0,
        "stat_parse_errors": 0,
        "unreadable_stat": 0,
        "unreadable_environ": 0,
        "unreadable_cmdline": 0,
        "records": [row],
    }
    envelope.update(envelope_update or {})
    return envelope


def test_wsl_scanner_records_but_rejects_unreadable_metadata_as_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_uuid = str(uuid.uuid4())
    boot_id = str(uuid.uuid4())
    fields = ["S", "1", "42", "42", *("0" for _ in range(15)), "901"]
    stat_text = f"77 (worker) {' '.join(fields)}"

    class _ScannerPath:
        def __init__(self, value: str) -> None:
            self.value = value

        @property
        def name(self) -> str:
            return self.value.rsplit("/", 1)[-1]

        def __truediv__(self, leaf: str) -> _ScannerPath:
            return _ScannerPath(f"{self.value}/{leaf}")

        def iterdir(self) -> tuple[_ScannerPath, ...]:
            assert self.value == "/proc"
            return (_ScannerPath("/proc/77"),)

        def read_text(self) -> str:
            if self.value == "/proc/sys/kernel/random/boot_id":
                return f"{boot_id}\n"
            if self.value == "/proc/77/stat":
                return stat_text
            raise AssertionError(self.value)

        def read_bytes(self) -> bytes:
            if self.value in {"/proc/77/environ", "/proc/77/cmdline"}:
                raise PermissionError(self.value)
            raise AssertionError(self.value)

    fake_pathlib = SimpleNamespace(Path=_ScannerPath)
    protocol = process.WslResidualProtocol(
        run_uuid,
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=boot_id,
    )
    with monkeypatch.context() as context:
        context.setitem(sys.modules, "pathlib", fake_pathlib)
        context.setattr(
            sys,
            "argv",
            ["scanner", run_uuid, "42", "900", boot_id, protocol.scan_nonce],
        )
        exec(compile(protocol.scanner_python_source(), "<wsl-scanner>", "exec"), {})

    payload = capsys.readouterr().out
    scan = json.loads(payload)
    assert scan["scan_complete"] is False
    assert scan["unreadable_environ"] == 1
    assert scan["unreadable_cmdline"] == 1
    assert scan["records"][0]["pid"] == 77
    with pytest.raises(ValueError, match="scan is incomplete"):
        protocol.parse_scan_json(payload)


@pytest.mark.parametrize(
    "row_update",
    [
        {"cmdline_readable": False, "cmdline_sha256": "a" * 64},
        {"cmdline_readable": True, "cmdline_sha256": None},
        {"environ_readable": "unknown"},
        {"pid": "77"},
        {"run_uuid_match": ""},
        {"process_group_match": 1},
        {"cmdline_sha256": 7},
        {"run_uuid_match": False, "process_group_match": False},
    ],
)
def test_wsl_scan_parser_rejects_inconsistent_readability_evidence(
    row_update: dict[str, Any],
) -> None:
    protocol = process.WslResidualProtocol(
        str(uuid.uuid4()),
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=str(uuid.uuid4()),
    )
    with pytest.raises(ValueError):
        protocol.parse_scan_json(json.dumps(_wsl_scan_envelope(protocol, row_update=row_update)))


@pytest.mark.parametrize(
    "envelope_update",
    [
        {"scan_complete": "true"},
        {"resource_limit_exceeded": "false"},
        {"resource_limit_exceeded": True, "scan_complete": False},
        {"unreadable_environ": "0"},
        {"unreadable_environ": 1, "scan_complete": False},
        {"boot_id": "NOT-A-CANONICAL-UUID"},
        {"processes_examined": 0},
    ],
)
def test_wsl_scan_parser_rejects_incomplete_or_coerced_envelope(
    envelope_update: dict[str, Any],
) -> None:
    protocol = process.WslResidualProtocol(
        str(uuid.uuid4()),
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=str(uuid.uuid4()),
    )
    with pytest.raises(ValueError):
        protocol.parse_scan_json(
            json.dumps(_wsl_scan_envelope(protocol, envelope_update=envelope_update))
        )


def test_wsl_scan_parser_requires_readability_keys() -> None:
    protocol = process.WslResidualProtocol(
        str(uuid.uuid4()),
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=str(uuid.uuid4()),
    )
    envelope = _wsl_scan_envelope(protocol)
    del envelope["records"][0]["environ_readable"]
    with pytest.raises(ValueError, match="unexpected WSL residual scan schema"):
        protocol.parse_scan_json(json.dumps(envelope))


def test_wsl_scan_parser_accepts_only_current_bound_scan_identity() -> None:
    protocol = process.WslResidualProtocol(
        str(uuid.uuid4()),
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=str(uuid.uuid4()),
    )
    records = protocol.parse_scan_json(json.dumps(_wsl_scan_envelope(protocol)))
    assert len(records) == 1
    assert records[0].stable_key == (protocol.boot_id, 77, 901)


def test_wsl_scan_parser_rejects_replay_from_another_protocol_instance() -> None:
    boot_id = str(uuid.uuid4())
    original = process.WslResidualProtocol(
        str(uuid.uuid4()),
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=boot_id,
    )
    current = process.WslResidualProtocol(
        str(uuid.uuid4()),
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=boot_id,
    )
    stale = _wsl_scan_envelope(original)
    stale["records"] = []

    with pytest.raises(ValueError, match="run identity or nonce mismatch"):
        current.parse_scan_json(json.dumps(stale))


def test_wsl_scan_nonce_is_unique_per_poll_and_each_response_is_one_shot() -> None:
    protocol = process.WslResidualProtocol(
        str(uuid.uuid4()),
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=str(uuid.uuid4()),
    )
    first_command = protocol.scan_command("Ubuntu")
    first_nonce = protocol.scan_nonce
    first_payload = json.dumps(_wsl_scan_envelope(protocol))
    assert first_command[:5] == (
        "wsl.exe",
        "--distribution",
        "Ubuntu",
        "--exec",
        "/usr/bin/python3",
    )
    assert first_command[5:9] == ("-I", "-S", "-P", "-B")

    protocol.parse_scan_json(first_payload)
    with pytest.raises(ValueError, match="already consumed"):
        protocol.parse_scan_json(first_payload)

    second_command = protocol.scan_command("Ubuntu")
    assert protocol.scan_nonce != first_nonce
    assert second_command[-1] == protocol.scan_nonce
    with pytest.raises(ValueError, match="run identity or nonce mismatch"):
        protocol.parse_scan_json(first_payload)
    protocol.parse_scan_json(json.dumps(_wsl_scan_envelope(protocol)))


def test_wsl_scan_parser_recomputes_process_group_match() -> None:
    protocol = process.WslResidualProtocol(
        str(uuid.uuid4()),
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=str(uuid.uuid4()),
    )
    payload = _wsl_scan_envelope(protocol, row_update={"pgrp": 999})

    with pytest.raises(ValueError, match="process-group identity mismatch"):
        protocol.parse_scan_json(json.dumps(payload))


def test_wsl_scan_payload_and_record_counts_are_hard_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = process.WslResidualProtocol(
        str(uuid.uuid4()),
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id=str(uuid.uuid4()),
    )
    with pytest.raises(ValueError, match="payload exceeds bounded size"):
        protocol.parse_scan_json("x" * (process.DEFAULT_MAX_WSL_SCAN_PAYLOAD_BYTES + 1))

    monkeypatch.setattr(process, "DEFAULT_MAX_WSL_SCAN_RECORDS", 1)
    payload = _wsl_scan_envelope(protocol)
    payload["records"].append({**payload["records"][0], "pid": 78})
    payload["processes_examined"] = 2
    with pytest.raises(ValueError, match="record count"):
        protocol.parse_scan_json(json.dumps(payload))


class _CreateKernel:
    def __init__(self) -> None:
        self.create_calls = 0

    def CreateProcessW(self, *_args: Any) -> int:
        self.create_calls += 1
        info = ctypes.cast(_args[-1], ctypes.POINTER(process._PROCESS_INFORMATION)).contents
        info.hProcess = 501
        info.hThread = 502
        info.dwProcessId = 503
        info.dwThreadId = 504
        return 1

    @staticmethod
    def DeleteProcThreadAttributeList(_attributes: Any) -> None:
        return None


class _InterruptAfterKernelCreate(_CreateKernel):
    def CreateProcessW(self, *_args: Any) -> int:
        super().CreateProcessW(*_args)
        raise KeyboardInterrupt()


class _DirectoryLockKernel:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def CreateFileW(self, *args: Any) -> int:
        self.calls.append(args)
        return 900


def test_directory_lock_allows_read_write_sharing_but_denies_delete_sharing() -> None:
    api = process._WindowsJobApi.__new__(process._WindowsJobApi)
    kernel = _DirectoryLockKernel()
    api.kernel32 = kernel

    handle = api.open_directory_rename_lock(r"C:\tools")

    assert handle == 900
    assert len(kernel.calls) == 1
    call = kernel.calls[0]
    assert call[1] == process._WindowsJobApi._FILE_READ_ATTRIBUTES
    assert call[2] == (
        process._WindowsJobApi._FILE_SHARE_READ | process._WindowsJobApi._FILE_SHARE_WRITE
    )
    assert call[5] == (
        process._WindowsJobApi._FILE_FLAG_BACKUP_SEMANTICS
        | process._WindowsJobApi._FILE_FLAG_OPEN_REPARSE_POINT
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CreateProcess semantics")
def test_stale_executable_pin_is_rejected_immediately_before_create(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "runtime.exe"
    executable.write_bytes(b"MZ-original")
    expected = hashlib.sha256(executable.read_bytes()).hexdigest()
    executable.write_bytes(b"MZ-mutated-after-preflight")

    api = process._WindowsJobApi.__new__(process._WindowsJobApi)
    kernel = _CreateKernel()
    api.kernel32 = kernel
    api.duplicate_inheritable_job_capability = lambda _job: 444
    closed: list[int | None] = []
    api.close = closed.append
    api.open_executable_path_locks = lambda path: ((path, 445),)
    api.initialise_attributes = lambda _job, _handles: (
        ctypes.create_string_buffer(1),
        ctypes.c_void_p(),
        None,
        None,
    )

    with pytest.raises(process.ProcessContainmentError, match="changed before CreateProcessW"):
        api.create_suspended_process(
            command=(str(executable),),
            cwd=str(tmp_path),
            environment={},
            job=100,
            stdin_handle=11,
            stdout_handle=12,
            stderr_handle=13,
            job_capability_nonce=b"n" * process.JOB_CAPABILITY_NONCE_BYTES,
            job_capability_run_uuid=str(uuid.uuid4()),
            create_no_window=True,
            expected_executable_sha256=expected,
        )

    assert kernel.create_calls == 0
    assert (
        api.last_application_identity["sha256"]
        == hashlib.sha256(executable.read_bytes()).hexdigest()
    )
    assert api.last_application_identity["expected_sha256"] == expected
    assert api.last_application_identity["pin_match"] is False
    assert api.last_application_identity["handle_lock_held_through_create"] is True
    assert 445 in closed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CreateProcess semantics")
def test_kernel_interrupt_after_process_information_is_latched(tmp_path: Path) -> None:
    executable = tmp_path / "runtime.exe"
    executable.write_bytes(b"MZ-pinned")
    api = process._WindowsJobApi.__new__(process._WindowsJobApi)
    kernel = _InterruptAfterKernelCreate()
    api.kernel32 = kernel
    api.duplicate_inheritable_job_capability = lambda _job: 444
    api.close = lambda _handle: None
    api.open_executable_path_locks = lambda path: ((path, 445),)
    api.initialise_attributes = lambda _job, _handles: (
        ctypes.create_string_buffer(1),
        ctypes.c_void_p(),
        None,
        None,
    )

    with pytest.raises(process._KernelCreateAftermathError):
        api.create_suspended_process(
            command=(str(executable),),
            cwd=str(tmp_path),
            environment={},
            job=100,
            stdin_handle=11,
            stdout_handle=12,
            stderr_handle=13,
            job_capability_nonce=b"n" * process.JOB_CAPABILITY_NONCE_BYTES,
            job_capability_run_uuid=str(uuid.uuid4()),
            create_no_window=True,
            pre_kernel_create_gate=lambda: None,
        )

    assert api.last_process_information == {
        "process_handle": 501,
        "thread_handle": 502,
        "pid": 503,
        "thread_id": 504,
    }
    assert api.pending_process_information.dwProcessId == 503


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory sharing semantics")
def test_executable_path_locks_prevent_parent_directory_swap(tmp_path: Path) -> None:
    parent = tmp_path / "runtime-parent"
    moved = tmp_path / "runtime-parent-moved"
    parent.mkdir()
    executable = parent / "runtime.exe"
    executable.write_bytes(b"MZ-pinned")
    api = process._WindowsJobApi()

    locks = api.open_executable_path_locks(str(executable.resolve()))
    try:
        assert locks[-1][0] == str(executable.resolve())
        assert len(locks) >= 2
        with pytest.raises(OSError):
            os.replace(parent, moved)
        assert executable.read_bytes() == b"MZ-pinned"
    finally:
        for _path, handle in reversed(locks):
            api.close(handle)

    os.replace(parent, moved)
    assert (moved / "runtime.exe").read_bytes() == b"MZ-pinned"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_partial_ancestor_lock_failure_closes_every_acquired_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = process._WindowsJobApi.__new__(process._WindowsJobApi)
    issued = iter((701, 702))
    closed: list[int] = []

    monkeypatch.setattr(api, "open_directory_rename_lock", lambda _path: next(issued))
    monkeypatch.setattr(
        api,
        "open_executable_lock",
        lambda _path: (_ for _ in ()).throw(process.ProcessContainmentError("leaf lock failed")),
    )
    monkeypatch.setattr(api, "close", closed.append)

    with pytest.raises((process.ProcessContainmentError, StopIteration)):
        api.open_executable_path_locks(r"C:\one\two\runtime.exe")

    assert closed == [702, 701]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_partial_ancestor_lock_keyboard_interrupt_closes_every_acquired_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = process._WindowsJobApi.__new__(process._WindowsJobApi)
    calls = 0
    closed: list[int] = []

    def open_directory(_path: str) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return 711

    monkeypatch.setattr(api, "open_directory_rename_lock", open_directory)
    monkeypatch.setattr(api, "close", closed.append)

    with pytest.raises(KeyboardInterrupt):
        api.open_executable_path_locks(r"C:\one\two\runtime.exe")

    assert closed == [711]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows synchronous I/O semantics")
def test_real_cancel_synchronous_io_releases_reader_handle_and_thread() -> None:
    api = process._WindowsJobApi()
    read_handle, write_handle = api.create_pipe()
    drained = threading.Event()
    exited = threading.Event()
    started = threading.Event()
    sink = process._BoundedStreamCapture(1024)

    def reader() -> None:
        started.set()
        try:
            api.read_pipe(read_handle, sink, drained)
        finally:
            exited.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    assert started.wait(timeout=1.0)
    time.sleep(0.02)
    try:
        assert thread.native_id is not None
        cancellation = api.cancel_synchronous_reader_io(thread.native_id)
        thread.join(timeout=1.0)
        assert cancellation["cancel_succeeded"] is True
        assert exited.is_set()
        assert thread.is_alive() is False
        assert drained.is_set() is False
    finally:
        api.close(write_handle)
        thread.join(timeout=1.0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CreateProcess semantics")
def test_relative_application_is_rejected_before_lock_or_create(tmp_path: Path) -> None:
    api = process._WindowsJobApi.__new__(process._WindowsJobApi)
    kernel = _CreateKernel()
    api.kernel32 = kernel
    api.duplicate_inheritable_job_capability = lambda _job: 444
    api.close = lambda _handle: None
    api.initialise_attributes = lambda _job, _handles: (
        ctypes.create_string_buffer(1),
        ctypes.c_void_p(),
        None,
        None,
    )
    lock_calls = 0

    def open_lock(_path: str) -> int:
        nonlocal lock_calls
        lock_calls += 1
        return 445

    api.open_executable_lock = open_lock

    with pytest.raises(process.ProcessContainmentError, match="absolute path"):
        api.create_suspended_process(
            command=("relative.exe",),
            cwd=str(tmp_path),
            environment={},
            job=100,
            stdin_handle=11,
            stdout_handle=12,
            stderr_handle=13,
            job_capability_nonce=b"n" * process.JOB_CAPABILITY_NONCE_BYTES,
            job_capability_run_uuid=str(uuid.uuid4()),
            create_no_window=True,
        )

    assert lock_calls == 0
    assert kernel.create_calls == 0


class _RunnerApi:
    _JOB_MESSAGE_ACTIVE_ZERO = 4
    _JOB_MESSAGE_NEW_PROCESS = 6
    _JOB_MESSAGE_EXIT_PROCESS = 7
    _JOB_MESSAGE_ABNORMAL_EXIT = 8

    def __init__(self, *, remain_active: bool = False, stream_bytes: bytes = b"") -> None:
        self.remain_active = remain_active
        self.stream_bytes = stream_bytes
        self.accounting_calls = 0
        self.last_application_identity = {
            "path": r"C:\runtime.exe",
            "sha256": "a" * 64,
            "bytes": 10,
            "device": 1,
            "file_id": 2,
            "expected_sha256": "a" * 64,
            "pin_required": True,
            "pin_match": True,
            "measurement_scope": "immediately_before_CreateProcessW",
            "handle_lock_held_through_create": True,
            "handle_lock_share_mode": "FILE_SHARE_READ_only",
            "handle_lock_inheritable": False,
            "ancestor_directory_locks_held_through_create": True,
            "ancestor_directory_lock_count": 2,
            "ancestor_directory_lock_share_mode": "FILE_SHARE_READ_WRITE_no_delete",
            "path_lock_scope": "all_nonroot_ancestors_and_leaf",
        }

    @staticmethod
    def create_job_and_completion_port() -> tuple[int, int]:
        return 100, 101

    @staticmethod
    def create_pipe() -> tuple[int, int]:
        return 201, 202

    @staticmethod
    def open_inheritable_null() -> int:
        return 300

    @staticmethod
    def create_suspended_process(**kwargs: Any) -> SimpleNamespace:
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
        active = 1 if self.remain_active or self.accounting_calls == 1 else 0
        return SimpleNamespace(
            TotalProcesses=1,
            ActiveProcesses=active,
            TotalTerminatedProcesses=0,
        )

    def query_active_pids(self, _job: int) -> tuple[int, ...]:
        return (402,) if self.remain_active or self.accounting_calls <= 2 else ()

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
            creation_time_utc="2026-09-02T00:00:00+00:00",
            image=r"C:\runtime.exe",
            run_uuid=run_uuid,
            observed_sequence=observed_sequence,
        )

    @staticmethod
    def completion_events(_completion: int) -> list[tuple[int, int | None]]:
        return []

    @staticmethod
    def resume(_thread: int) -> None:
        return None

    def read_pipe(self, _handle: int, sink: Any, drained: Any) -> None:
        sink.extend(self.stream_bytes)
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


class _ZeroAtBoundaryAccountingApi(_RunnerApi):
    def __init__(
        self,
        clock: _FakeClock,
        *,
        boundary: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self.clock = clock
        self.boundary = boundary
        self.cancel_event = cancel_event

    def query_accounting(self, job: int) -> SimpleNamespace:
        accounting = super().query_accounting(job)
        if self.accounting_calls == 2:
            if self.boundary is not None:
                self.clock.value = self.boundary
            if self.cancel_event is not None:
                self.cancel_event.set()
        return accounting


class _CancelDuringSetupApi(_RunnerApi):
    def __init__(self, cancel_event: threading.Event) -> None:
        super().__init__(remain_active=True)
        self.cancel_event = cancel_event
        self.resume_calls = 0

    def create_suspended_process(self, **kwargs: Any) -> SimpleNamespace:
        kwargs["pre_kernel_create_gate"]()
        self.cancel_event.set()
        return SimpleNamespace(hProcess=400, hThread=401, dwProcessId=402)

    def resume(self, _thread: int) -> None:
        self.resume_calls += 1


class _CancelBeforeKernelCreateApi(_RunnerApi):
    def __init__(self, cancel_event: threading.Event) -> None:
        super().__init__()
        self.cancel_event = cancel_event
        self.pipe_calls = 0
        self.create_process_calls = 0

    def create_pipe(self) -> tuple[int, int]:
        self.pipe_calls += 1
        if self.pipe_calls == 2:
            self.cancel_event.set()
        return 201 + self.pipe_calls * 2, 202 + self.pipe_calls * 2

    def create_suspended_process(self, **kwargs: Any) -> SimpleNamespace:
        kwargs["pre_kernel_create_gate"]()
        self.create_process_calls += 1
        return SimpleNamespace(hProcess=400, hThread=401, dwProcessId=402)


class _CancelDuringExecutableMeasurementApi(_RunnerApi):
    def __init__(self, cancel_event: threading.Event) -> None:
        super().__init__()
        self.cancel_event = cancel_event
        self.kernel_create_calls = 0

    def create_suspended_process(self, **kwargs: Any) -> SimpleNamespace:
        # Model capability setup, path locking, and full SHA measurement before
        # the API invokes its last pre-kernel callback.
        self.cancel_event.set()
        kwargs["pre_kernel_create_gate"]()
        self.kernel_create_calls += 1
        return SimpleNamespace(hProcess=400, hThread=401, dwProcessId=402)


class _KernelCreateFailureApi(_RunnerApi):
    @staticmethod
    def create_suspended_process(**kwargs: Any) -> SimpleNamespace:
        kwargs["pre_kernel_create_gate"]()
        raise process._KernelCreateError("CreateProcessW: injected kernel failure")


class _KernelAftermathInterruptApi(_RunnerApi):
    def __init__(self) -> None:
        super().__init__(remain_active=True)
        self.pending_process_information: Any | None = None

    def create_suspended_process(self, **kwargs: Any) -> SimpleNamespace:
        kwargs["pre_kernel_create_gate"]()
        self.pending_process_information = SimpleNamespace(
            hProcess=400,
            hThread=401,
            dwProcessId=402,
            dwThreadId=403,
        )
        raise KeyboardInterrupt()


class _PinMismatchRunnerApi(_RunnerApi):
    def __init__(self) -> None:
        super().__init__()
        self.last_application_identity["sha256"] = "b" * 64
        self.last_application_identity["pin_match"] = False

    def create_suspended_process(self, **_kwargs: Any) -> SimpleNamespace:
        raise process.ProcessContainmentError("executable SHA-256 changed before CreateProcessW")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_pin_mismatch_failure_preserves_actual_launch_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _PinMismatchRunnerApi()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run(
            [r"C:\runtime.exe"], expected_executable_sha256="a" * 64
        )

    failure = caught.value
    assert failure.child_created is False
    assert failure.executable_identity["sha256"] == "b" * 64
    assert failure.executable_identity["expected_sha256"] == "a" * 64
    assert failure.executable_identity["pin_match"] is False
    assert failure.process_evidence["executable_identity"] == failure.executable_identity
    assert failure.safe_for_followup is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_stream_capture_is_bounded_and_overflow_is_manual_no_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RunnerApi(stream_bytes=b"0123456789")
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)

    outcome = process.WindowsJobProcessRunner().run(
        [r"C:\runtime.exe"],
        expected_executable_sha256="a" * 64,
        max_stream_bytes=4,
        poll_interval_seconds=0.001,
    )

    assert outcome.stdout == "0123"
    assert outcome.stderr == "0123"
    assert outcome.stdout_total_bytes == 10
    assert outcome.stderr_total_bytes == 10
    assert outcome.stdout_capture_overflow is True
    assert outcome.stderr_capture_overflow is True
    assert outcome.stream_capture_limit_bytes == 4
    assert outcome.manual_intervention_required is True
    assert outcome.safe_for_followup is False
    assert outcome.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows Job containment")
def test_real_runner_binds_expected_executable_sha_to_launch_evidence() -> None:
    executable = Path(sys.executable).resolve()
    expected = hashlib.sha256(executable.read_bytes()).hexdigest()

    outcome = process.WindowsJobProcessRunner().run(
        [str(executable), "-I", "-S", "-B", "-c", "print('pinned', flush=True)"],
        expected_executable_sha256=expected,
        poll_interval_seconds=0.005,
    )

    assert outcome.return_code == 0
    assert outcome.safe_for_followup is True
    assert outcome.stdout.strip() == "pinned"
    assert outcome.executable_identity["sha256"] == expected
    assert outcome.executable_identity["expected_sha256"] == expected
    assert outcome.executable_identity["pin_match"] is True
    assert outcome.executable_identity["measurement_scope"] == ("immediately_before_CreateProcessW")
    assert outcome.stream_capture_limit_bytes == process.DEFAULT_MAX_STREAM_BYTES


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class _DeadlineDuringCompletionDrainApi(_RunnerApi):
    def __init__(self, clock: _FakeClock) -> None:
        super().__init__()
        self.clock = clock
        self.completion_calls = 0
        self.yielded_events = 0

    def completion_events(self, _completion: int) -> Any:
        self.completion_calls += 1
        if self.completion_calls > 1:
            return ()

        owner = self

        class _ContinuousRecords:
            def __iter__(self) -> Any:
                while True:
                    owner.clock.value += 0.1
                    owner.yielded_events += 1
                    yield (owner._JOB_MESSAGE_EXIT_PROCESS, 10_000 + owner.yielded_events)

        return _ContinuousRecords()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_completion_drain_stops_at_restore_deadline_during_continuous_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    fake = _DeadlineDuringCompletionDrainApi(clock)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=1.0,
        wrapper_timeout_seconds=2.0,
        restore_deadline_seconds=6.0,
        residual_repoll_seconds=2.0,
        stream_drain_seconds=1.0,
    )

    outcome = process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"]
    )

    assert clock.value <= 6.0
    assert fake.yielded_events < process.DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN
    assert outcome.timed_out is True
    assert outcome.manual_intervention_required is True
    assert outcome.safe_for_followup is False
    assert "completion_drain_restore_deadline_reached" in outcome.errors
    assert any(
        event.event == "completion_drain_restore_deadline_reached" for event in outcome.events
    )
    assert outcome.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows completion API semantics")
def test_kernel_completion_queue_drain_has_hard_event_batch_limit() -> None:
    class _AlwaysReadyKernel:
        def __init__(self) -> None:
            self.calls = 0

        def GetQueuedCompletionStatus(
            self,
            _completion: int,
            message: Any,
            _key: Any,
            overlapped: Any,
            _wait_milliseconds: int,
        ) -> int:
            self.calls += 1
            ctypes.cast(message, ctypes.POINTER(ctypes.wintypes.DWORD)).contents.value = 7
            ctypes.cast(overlapped, ctypes.POINTER(ctypes.c_void_p)).contents.value = self.calls
            return 1

    kernel = _AlwaysReadyKernel()
    api = object.__new__(process._WindowsJobApi)
    api.kernel32 = kernel

    events = api.completion_events(101)

    assert len(events) == process.DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN
    assert kernel.calls == process.DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
@pytest.mark.parametrize(
    ("boundary", "expected_event"),
    [
        (2.0, "timeout_latched"),
        (4.75, "restore_deadline_cleanup_reserve_entered"),
    ],
)
def test_zero_accounting_cannot_erase_timeout_latched_during_accounting(
    monkeypatch: pytest.MonkeyPatch,
    boundary: float,
    expected_event: str,
) -> None:
    clock = _FakeClock()
    fake = _ZeroAtBoundaryAccountingApi(clock, boundary=boundary)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=1.0,
        wrapper_timeout_seconds=2.0,
        restore_deadline_seconds=6.0,
        residual_repoll_seconds=2.0,
        stream_drain_seconds=1.0,
    )

    outcome = process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"]
    )

    assert outcome.final_active_process_count == 0
    assert outcome.residual_pids == ()
    assert outcome.timed_out is True
    assert outcome.cancelled is False
    assert outcome.manual_intervention_required is True
    assert outcome.safe_for_followup is False
    assert any(event.event == expected_event for event in outcome.events)
    assert outcome.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_zero_accounting_cannot_erase_cancel_latched_during_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    cancel_event = threading.Event()
    fake = _ZeroAtBoundaryAccountingApi(clock, cancel_event=cancel_event)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)

    outcome = process.WindowsJobProcessRunner(clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"], cancel_event=cancel_event
    )

    assert outcome.final_active_process_count == 0
    assert outcome.residual_pids == ()
    assert outcome.timed_out is False
    assert outcome.cancelled is True
    assert outcome.manual_intervention_required is True
    assert outcome.safe_for_followup is False
    assert any(event.event == "cancel_latched" for event in outcome.events)
    assert outcome.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
@pytest.mark.parametrize("latch", ["cancel", "restore_deadline"])
def test_final_gate_cannot_return_safe_after_late_cancel_or_restore_deadline(
    monkeypatch: pytest.MonkeyPatch,
    latch: str,
) -> None:
    clock = _FakeClock()
    cancel_event = threading.Event()
    fake = _RunnerApi()

    def exit_code(_process: int) -> int:
        if latch == "cancel":
            cancel_event.set()
        else:
            clock.value = 6.0
        return 0

    fake.exit_code = exit_code  # type: ignore[method-assign]
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=1.0,
        wrapper_timeout_seconds=2.0,
        restore_deadline_seconds=6.0,
        residual_repoll_seconds=2.0,
        stream_drain_seconds=1.0,
    )

    outcome = process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"], cancel_event=cancel_event
    )

    assert outcome.final_active_process_count == 0
    assert outcome.residual_pids == ()
    assert outcome.safe_for_followup is False
    assert outcome.manual_intervention_required is True
    assert outcome.cancelled is (latch == "cancel")
    assert outcome.timed_out is (latch == "restore_deadline")
    expected_event = (
        "cancel_latched_at_final_gate"
        if latch == "cancel"
        else "restore_deadline_exhausted_at_final_gate"
    )
    assert any(event.event == expected_event for event in outcome.events)
    assert outcome.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_stream_decode_deadline_crossing_is_latched_before_safe_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    fake = _RunnerApi(stream_bytes=b"bounded-output")
    real_capture = process._BoundedStreamCapture

    class DeadlineCrossingCapture(real_capture):
        decode_calls = 0

        def __bytes__(self) -> bytes:
            raw = super().__bytes__()
            type(self).decode_calls += 1
            if type(self).decode_calls == 1:
                clock.value = 6.0
            return raw

    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process, "_BoundedStreamCapture", DeadlineCrossingCapture)
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=1.0,
        wrapper_timeout_seconds=2.0,
        restore_deadline_seconds=6.0,
        residual_repoll_seconds=2.0,
        stream_drain_seconds=1.0,
    )

    outcome = process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"]
    )

    assert DeadlineCrossingCapture.decode_calls == 2
    assert outcome.duration_seconds == contract.restore_deadline_seconds
    assert outcome.timed_out is True
    assert outcome.manual_intervention_required is True
    assert outcome.safe_for_followup is False
    assert any(
        event.event == "restore_deadline_exhausted_at_final_gate" for event in outcome.events
    )
    assert outcome.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_entry_cancellation_creates_no_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_construction_calls: list[bool] = []
    cancel_event = threading.Event()
    cancel_event.set()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: api_construction_calls.append(True))

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], cancel_event=cancel_event)

    failure = caught.value
    assert api_construction_calls == []
    assert failure.stage == "pre_create_cancel_gate"
    assert failure.child_created is False
    assert failure.root_pid is None
    assert failure.cancelled is True
    assert failure.safe_for_followup is False
    assert failure.residual_pids == ()
    assert failure.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_setup_cancellation_never_resumes_suspended_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_event = threading.Event()
    fake = _CancelDuringSetupApi(cancel_event)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], cancel_event=cancel_event)

    failure = caught.value
    assert fake.resume_calls == 0
    assert failure.stage == "resume_cancel_gate"
    assert failure.child_created is True
    assert failure.root_pid == 402
    assert failure.root_resumed is False
    assert failure.cancelled is True
    assert failure.manual_intervention_required is True
    assert failure.safe_for_followup is False
    assert failure.residual_pids == (402,)
    assert any(event.event == "cancel_latched_before_resume" for event in failure.events)
    assert failure.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_cancel_set_during_stdio_setup_prevents_kernel_child_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_event = threading.Event()
    fake = _CancelBeforeKernelCreateApi(cancel_event)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], cancel_event=cancel_event)

    failure = caught.value
    assert fake.create_process_calls == 0
    assert failure.stage == "pre_kernel_create_gate"
    assert failure.child_created is False
    assert failure.root_pid is None
    assert failure.cancelled is True
    assert failure.residual_pids == ()
    assert failure.forced_termination_attempts == 0
    assert failure.executable_identity["pre_kernel_create_gate_invocations"] == 1
    assert failure.executable_identity["pre_kernel_create_gate_passed"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_cancel_latched_during_executable_hash_prevents_kernel_child_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_event = threading.Event()
    fake = _CancelDuringExecutableMeasurementApi(cancel_event)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], cancel_event=cancel_event)

    failure = caught.value
    assert fake.kernel_create_calls == 0
    assert failure.stage == "pre_kernel_create_gate"
    assert failure.child_created is False
    assert failure.cancelled is True
    assert failure.executable_identity["pre_kernel_create_gate_invocations"] == 1
    assert failure.executable_identity["pre_kernel_create_gate_passed"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_kernel_create_failure_is_not_mislabelled_as_pre_kernel_gate_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: _KernelCreateFailureApi())

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"])

    failure = caught.value
    assert failure.stage == "kernel_create_suspended_root"
    assert failure.child_created is False
    assert failure.executable_identity["pre_kernel_create_gate_passed"] is True
    assert failure.executable_identity["pre_kernel_create_gate_invocations"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_kernel_aftermath_interrupt_latches_child_and_residual_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _KernelAftermathInterruptApi()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"])

    failure = caught.value
    assert failure.stage == "kernel_create_aftermath"
    assert failure.cause_type == "KeyboardInterrupt"
    assert failure.child_created is True
    assert failure.root_pid == 402
    assert failure.root_resumed is False
    assert failure.residual_pids == (402,)
    assert failure.manual_intervention_required is True
    assert failure.forced_termination_attempts == 0


class _BlockingReaderResumeFailureApi(_RunnerApi):
    def __init__(self) -> None:
        super().__init__()
        self.release_readers = process.threading.Event()
        self.cancel_calls: list[int] = []
        self.closed_read_handles: list[int] = []

    def read_pipe(self, handle: int, sink: Any, drained: Any) -> None:
        del sink, drained
        try:
            self.release_readers.wait(timeout=5.0)
        finally:
            self.closed_read_handles.append(handle)

    @staticmethod
    def resume(_thread: int) -> None:
        raise process.ProcessContainmentError("injected resume failure")

    def cancel_synchronous_reader_io(self, native_thread_id: int) -> dict[str, Any]:
        self.cancel_calls.append(native_thread_id)
        self.release_readers.set()
        return {
            "native_thread_id": native_thread_id,
            "cancel_attempted": True,
            "cancel_succeeded": True,
            "no_pending_io": False,
            "error_code": None,
        }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_resume_failure_cancels_and_joins_reader_threads_without_child_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _BlockingReaderResumeFailureApi()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], poll_interval_seconds=0.001)

    failure = caught.value
    assert failure.child_created is True
    assert failure.root_resumed is False
    assert failure.residual_pids == (402,)
    assert failure.timed_out is False
    assert fake.cancel_calls
    assert fake.closed_read_handles == [201, 201]
    assert failure.stream_cleanup["all_reader_threads_exited"] is True
    assert all(
        reader["thread_alive_after_cleanup"] is False
        for reader in failure.stream_cleanup["readers"]
    )
    assert failure.forced_termination_attempts == 0
    assert failure.safe_for_followup is False


def test_reader_handle_ownership_transfers_immediately_after_each_thread_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _HandleAuditApi(_RunnerApi):
        def __init__(self) -> None:
            super().__init__()
            self.pipe_calls = 0
            self.closed: list[int] = []

        def create_pipe(self) -> tuple[int, int]:
            self.pipe_calls += 1
            return (40 + self.pipe_calls, 50 + self.pipe_calls)

        def read_pipe(self, handle: int, sink: Any, drained: Any) -> None:
            del sink
            try:
                drained.set()
            finally:
                self.close(handle)

        def close(self, handle: int | None) -> None:
            if handle is not None:
                self.closed.append(handle)

    class _ImmediateThread:
        created = 0

        def __init__(self, *, target: Any, args: tuple[Any, ...], **_kwargs: Any) -> None:
            type(self).created += 1
            self.index = type(self).created
            self.target = target
            self.args = args
            self.native_id = 1000 + self.index
            self.started = False

        def start(self) -> None:
            if self.index == 2:
                raise RuntimeError("injected stderr thread start failure")
            self.started = True
            self.target(*self.args)

        @staticmethod
        def is_alive() -> bool:
            return False

        @staticmethod
        def join(timeout: float = 0.0) -> None:
            del timeout

    fake = _HandleAuditApi()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(process.sys, "platform", "win32")

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"])

    stdout_read_handle = 41
    stderr_read_handle = 42
    assert fake.closed.count(stdout_read_handle) == 1
    assert fake.closed.count(stderr_read_handle) == 1
    cleanup_by_stream = {item["stream"]: item for item in caught.value.stream_cleanup["readers"]}
    assert cleanup_by_stream["stdout"]["read_handle_close_scope"] == ("reader_read_pipe_finally")
    assert cleanup_by_stream["stderr"]["read_handle_close_scope"] == (
        "parent_runner_finally_if_allocated"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_reader_that_starts_then_raises_is_cancelled_joined_and_not_parent_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _BlockingReaderResumeFailureApi()
    real_thread = process.threading.Thread

    class _StartThenRaiseThread:
        created = 0

        def __init__(self, **kwargs: Any) -> None:
            type(self).created += 1
            self.index = type(self).created
            self.inner = real_thread(**kwargs)

        @property
        def ident(self) -> int | None:
            return self.inner.ident

        @property
        def native_id(self) -> int | None:
            return self.inner.native_id

        def start(self) -> None:
            self.inner.start()
            if self.index == 1:
                raise KeyboardInterrupt("reader start returned by raising")

        def is_alive(self) -> bool:
            return self.inner.is_alive()

        def join(self, timeout: float | None = None) -> None:
            self.inner.join(timeout=timeout)

    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.threading, "Thread", _StartThenRaiseThread)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], poll_interval_seconds=0.001)

    failure = caught.value
    cleanup_by_stream = {item["stream"]: item for item in failure.stream_cleanup["readers"]}
    assert failure.cause_type == "KeyboardInterrupt"
    assert cleanup_by_stream["stdout"]["started"] is True
    assert cleanup_by_stream["stdout"]["thread_alive_after_cleanup"] is False
    assert cleanup_by_stream["stdout"]["read_handle_close_scope"] == ("reader_read_pipe_finally")
    assert cleanup_by_stream["stderr"]["started"] is False
    assert cleanup_by_stream["stderr"]["read_handle_close_scope"] == (
        "parent_runner_finally_if_allocated"
    )
    assert fake.cancel_calls
    assert fake.closed_read_handles == [201]
    assert failure.stream_cleanup["all_reader_threads_exited"] is True
    assert failure.safe_for_followup is False
    assert failure.forced_termination_attempts == 0


class _SetupDeadlineApi(_RunnerApi):
    def __init__(self, clock: _FakeClock) -> None:
        super().__init__()
        self.clock = clock
        self.resume_calls = 0
        self.kernel_create_calls = 0

    def create_suspended_process(self, **kwargs: Any) -> SimpleNamespace:
        self.clock.value += 6.0
        kwargs["pre_kernel_create_gate"]()
        self.kernel_create_calls += 1
        return SimpleNamespace(hProcess=400, hThread=401, dwProcessId=402)

    def resume(self, _thread: int) -> None:
        self.resume_calls += 1


class _SetupBudgetApi(_RunnerApi):
    def __init__(self, clock: _FakeClock) -> None:
        super().__init__()
        self.clock = clock
        self.kernel_create_calls = 0

    def create_suspended_process(self, **kwargs: Any) -> SimpleNamespace:
        self.clock.value += 1.0
        kwargs["pre_kernel_create_gate"]()
        self.kernel_create_calls += 1
        return SimpleNamespace(hProcess=400, hThread=401, dwProcessId=402)


class _PostCreateResumeDeadlineApi(_RunnerApi):
    def __init__(self, clock: _FakeClock) -> None:
        super().__init__(remain_active=True)
        self.clock = clock
        self.resume_calls = 0

    def create_suspended_process(self, **kwargs: Any) -> SimpleNamespace:
        kwargs["pre_kernel_create_gate"]()
        self.clock.value += 2.0
        return SimpleNamespace(hProcess=400, hThread=401, dwProcessId=402)

    def resume(self, _thread: int) -> None:
        self.resume_calls += 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_restore_deadline_exhausted_during_setup_never_resumes_suspended_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    fake = _SetupDeadlineApi(clock)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=1.0,
        wrapper_timeout_seconds=2.0,
        restore_deadline_seconds=6.0,
        residual_repoll_seconds=2.0,
        stream_drain_seconds=1.0,
    )

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
            [r"C:\runtime.exe"]
        )

    failure = caught.value
    assert clock.value == 6.0
    assert failure.child_created is False
    assert failure.root_resumed is False
    assert failure.residual_pids == ()
    assert failure.timed_out is True
    assert failure.cancelled is False
    assert failure.duration_seconds == pytest.approx(6.0)
    assert failure.restore_deadline_seconds == 6.0
    assert failure.restore_deadline_exhausted is True
    assert failure.to_dict()["timed_out"] is True
    assert fake.resume_calls == 0
    assert fake.kernel_create_calls == 0
    assert failure.safe_for_followup is False
    assert failure.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_pre_kernel_gate_requires_full_wrapper_residual_and_drain_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    fake = _SetupBudgetApi(clock)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=1.0,
        wrapper_timeout_seconds=2.0,
        restore_deadline_seconds=6.0,
        residual_repoll_seconds=2.0,
        stream_drain_seconds=1.0,
    )

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
            [r"C:\runtime.exe"]
        )

    failure = caught.value
    assert fake.kernel_create_calls == 0
    assert failure.child_created is False
    assert failure.restore_deadline_exhausted is False
    assert failure.timed_out is True
    assert failure.stage == "pre_kernel_create_gate"
    assert failure.executable_identity["pre_kernel_remaining_seconds"] == pytest.approx(5.0)
    assert failure.executable_identity["pre_kernel_required_seconds"] == pytest.approx(5.25)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_deadline_after_kernel_create_but_before_resume_keeps_root_suspended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    fake = _PostCreateResumeDeadlineApi(clock)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=1.0,
        wrapper_timeout_seconds=2.0,
        restore_deadline_seconds=6.0,
        residual_repoll_seconds=2.0,
        stream_drain_seconds=1.0,
    )

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
            [r"C:\runtime.exe"]
        )

    failure = caught.value
    assert failure.stage == "resume_wrapper_deadline_gate"
    assert failure.child_created is True
    assert failure.root_resumed is False
    assert failure.timed_out is True
    assert failure.manual_intervention_required is True
    assert failure.residual_pids == (402,)
    assert fake.resume_calls == 0
    assert failure.forced_termination_attempts == 0


class _UndrainedRunnerApi(_RunnerApi):
    def read_pipe(self, _handle: int, sink: Any, drained: Any) -> None:
        del sink, drained


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_restore_deadline_caps_undrained_stream_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: _UndrainedRunnerApi())
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=0.2,
        wrapper_timeout_seconds=0.5,
        restore_deadline_seconds=5.0,
        residual_repoll_seconds=2.0,
        stream_drain_seconds=2.0,
    )

    outcome = process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"], poll_interval_seconds=0.3
    )

    assert clock.value <= 5.0
    assert all(value <= 0.3 for value in clock.sleeps)
    assert outcome.streams_drained is False
    assert outcome.manual_intervention_required is True
    assert outcome.safe_for_followup is False


class _AccountingFailureAfterStreamsApi(_RunnerApi):
    def query_accounting(self, _job: int) -> SimpleNamespace:
        self.accounting_calls += 1
        if self.accounting_calls > 1:
            raise process.ProcessContainmentError("injected accounting failure")
        return SimpleNamespace(
            TotalProcesses=1,
            ActiveProcesses=1,
            TotalTerminatedProcesses=0,
        )


class _PostLatchAccountingFailureApi(_RunnerApi):
    def __init__(self, clock: _FakeClock, fail_at: float) -> None:
        super().__init__()
        self.clock = clock
        self.fail_at = fail_at

    def query_accounting(self, _job: int) -> SimpleNamespace:
        self.accounting_calls += 1
        if self.accounting_calls > 1 and self.clock.value >= self.fail_at:
            raise process.ProcessContainmentError("injected post-latch accounting failure")
        return SimpleNamespace(
            TotalProcesses=1,
            ActiveProcesses=1,
            TotalTerminatedProcesses=0,
        )

    @staticmethod
    def query_active_pids(_job: int) -> tuple[int, ...]:
        return (402,)


class _ExceptionBoundaryLatchApi(_RunnerApi):
    def __init__(self, clock: _FakeClock, cancel_event: threading.Event) -> None:
        super().__init__(remain_active=True)
        self.clock = clock
        self.cancel_event = cancel_event

    def query_accounting(self, _job: int) -> SimpleNamespace:
        self.accounting_calls += 1
        if self.accounting_calls > 1:
            self.clock.value = 0.5
            self.cancel_event.set()
            raise KeyboardInterrupt("injected exception-boundary race")
        return SimpleNamespace(
            TotalProcesses=1,
            ActiveProcesses=1,
            TotalTerminatedProcesses=0,
        )

    @staticmethod
    def query_active_pids(_job: int) -> tuple[int, ...]:
        return (402,)


@pytest.mark.parametrize("latch", ["timeout", "cancel"])
def test_failure_after_latch_preserves_timeout_and_cancel_state(
    monkeypatch: pytest.MonkeyPatch, latch: str
) -> None:
    clock = _FakeClock()
    fail_at = 1.0 if latch == "timeout" else 0.5
    fake = _PostLatchAccountingFailureApi(clock, fail_at)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.sys, "platform", "win32")
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=0.2,
        wrapper_timeout_seconds=0.5,
        restore_deadline_seconds=2.0,
        residual_repoll_seconds=0.5,
        stream_drain_seconds=0.2,
    )
    cancel_event = process.threading.Event()
    if latch == "cancel":
        cancel_event.set()

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
            [r"C:\runtime.exe"],
            cancel_event=cancel_event,
            poll_interval_seconds=0.1,
        )

    failure = caught.value
    assert failure.timed_out is (latch == "timeout")
    assert failure.cancelled is (latch == "cancel")
    assert failure.restore_deadline_exhausted is False
    assert failure.duration_seconds <= contract.restore_deadline_seconds
    assert failure.to_dict()["timed_out"] is failure.timed_out
    assert failure.to_dict()["cancelled"] is failure.cancelled
    assert failure.safe_for_followup is False


def test_exception_boundary_relatches_cancel_and_wrapper_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    cancel_event = process.threading.Event()
    fake = _ExceptionBoundaryLatchApi(clock, cancel_event)
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.sys, "platform", "win32")
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=0.2,
        wrapper_timeout_seconds=0.5,
        restore_deadline_seconds=2.0,
        residual_repoll_seconds=0.5,
        stream_drain_seconds=0.2,
    )

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
            [r"C:\runtime.exe"],
            cancel_event=cancel_event,
            poll_interval_seconds=0.1,
        )

    failure = caught.value
    assert failure.cancelled is True
    assert failure.timed_out is True
    assert "cancel_latched_at_exception_boundary" in failure.errors
    assert "deadline_latched_at_exception_boundary" in failure.errors
    assert failure.safe_for_followup is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_failure_evidence_preserves_bounded_stream_totals_and_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _AccountingFailureAfterStreamsApi(stream_bytes=b"0123456789")
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run(
            [r"C:\runtime.exe"], max_stream_bytes=4, poll_interval_seconds=0.001
        )

    failure = caught.value
    assert failure.stdout == "0123"
    assert failure.stderr == "0123"
    assert failure.stdout_total_bytes == 10
    assert failure.stderr_total_bytes == 10
    assert failure.stdout_capture_overflow is True
    assert failure.stderr_capture_overflow is True
    assert failure.stream_capture_limit_bytes == 4
    assert failure.executable_identity["measurement_scope"] == ("immediately_before_CreateProcessW")
    assert failure.safe_for_followup is False
    assert failure.forced_termination_attempts == 0


def test_default_completion_poll_interval_is_one_millisecond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _OneIterationApi(_RunnerApi):
        def query_accounting(self, _job: int) -> SimpleNamespace:
            self.accounting_calls += 1
            active = 1 if self.accounting_calls <= 2 else 0
            return SimpleNamespace(
                TotalProcesses=1,
                ActiveProcesses=active,
                TotalTerminatedProcesses=0,
            )

    fake = _OneIterationApi()
    clock = _FakeClock()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.sys, "platform", "win32")

    process.WindowsJobProcessRunner(clock=clock, sleep=clock.sleep).run([r"C:\runtime.exe"])

    assert clock.sleeps
    assert max(clock.sleeps) <= process.DEFAULT_PROCESS_POLL_INTERVAL_SECONDS


def test_kernel_completion_wait_captures_short_lived_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CompletionWaitApi(_RunnerApi):
        def __init__(self) -> None:
            super().__init__()
            self.wait_calls = 0

        def query_accounting(self, _job: int) -> SimpleNamespace:
            self.accounting_calls += 1
            return SimpleNamespace(
                TotalProcesses=1 if self.accounting_calls <= 2 else 2,
                ActiveProcesses=1 if self.accounting_calls <= 2 else 0,
                TotalTerminatedProcesses=0,
            )

        def wait_completion_events(
            self, _completion: int, wait_milliseconds: int
        ) -> list[tuple[int, int | None]]:
            assert wait_milliseconds == 1
            self.wait_calls += 1
            return [(self._JOB_MESSAGE_NEW_PROCESS, 403)]

        @staticmethod
        def open_process(pid: int) -> int | None:
            return 500 if pid == 403 else None

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
                ppid=fallback_ppid if fallback_ppid is not None else 402,
                creation_time_ns=pid,
                creation_time_utc="2026-09-02T00:00:00+00:00",
                image=r"C:\Windows\System32\conhost.exe" if pid == 403 else r"C:\runtime.exe",
                run_uuid=run_uuid,
                observed_sequence=observed_sequence,
            )

    fake = _CompletionWaitApi()
    clock = _FakeClock()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.sys, "platform", "win32")

    outcome = process.WindowsJobProcessRunner(clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"]
    )

    assert fake.wait_calls == 1
    assert outcome.identity_coverage_complete is True
    assert outcome.safe_for_followup is True
    assert {identity.pid for identity in outcome.identities} == {402, 403}
    assert clock.sleeps == []


def test_duplicate_stable_identity_observation_does_not_emit_unbound_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DuplicateObservationApi(_RunnerApi):
        def __init__(self) -> None:
            super().__init__()
            self.wait_calls = 0

        @staticmethod
        def process_is_active(_process: int) -> bool:
            return False

        def query_accounting(self, _job: int) -> SimpleNamespace:
            self.accounting_calls += 1
            return SimpleNamespace(
                TotalProcesses=1,
                ActiveProcesses=1 if self.accounting_calls <= 2 else 0,
                TotalTerminatedProcesses=0,
            )

        def wait_completion_events(
            self, _completion: int, _wait_milliseconds: int
        ) -> list[tuple[int, int | None]]:
            self.wait_calls += 1
            return [(self._JOB_MESSAGE_NEW_PROCESS, 402)] if self.wait_calls == 1 else []

        @staticmethod
        def open_process(pid: int) -> int | None:
            return 500 if pid == 402 else None

    fake = _DuplicateObservationApi()
    clock = _FakeClock()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.sys, "platform", "win32")

    outcome = process.WindowsJobProcessRunner(clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"]
    )

    identity_events = [event for event in outcome.events if event.event == "identity_observed"]
    assert len(outcome.identities) == 1
    assert len(identity_events) == 1
    assert identity_events[0].sequence == outcome.identities[0].observed_sequence
    assert outcome.identity_coverage_complete is True


def test_incoherent_accounting_and_pid_observation_is_resampled_before_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _OneRaceApi(_RunnerApi):
        def query_accounting(self, _job: int) -> SimpleNamespace:
            self.accounting_calls += 1
            if self.accounting_calls == 2:
                return SimpleNamespace(
                    TotalProcesses=2,
                    ActiveProcesses=2,
                    TotalTerminatedProcesses=0,
                )
            if self.accounting_calls <= 3:
                return SimpleNamespace(
                    TotalProcesses=1,
                    ActiveProcesses=1,
                    TotalTerminatedProcesses=0,
                )
            return SimpleNamespace(
                TotalProcesses=1,
                ActiveProcesses=0,
                TotalTerminatedProcesses=0,
            )

        def query_active_pids(self, _job: int) -> tuple[int, ...]:
            return (402,) if self.accounting_calls <= 3 else ()

    fake = _OneRaceApi()
    clock = _FakeClock()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.sys, "platform", "win32")

    outcome = process.WindowsJobProcessRunner(clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"]
    )

    assert outcome.safe_for_followup is True
    assert any(event.event == "accounting_observation_incoherent" for event in outcome.events)
    assert all(
        snapshot.active_processes == len(snapshot.active_pids) for snapshot in outcome.accounting
    )


def test_keyboard_interrupt_after_child_creation_is_converted_to_residual_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _InterruptApi(_RunnerApi):
        def query_accounting(self, _job: int) -> SimpleNamespace:
            self.accounting_calls += 1
            if self.accounting_calls > 1:
                raise KeyboardInterrupt()
            return SimpleNamespace(
                TotalProcesses=1,
                ActiveProcesses=1,
                TotalTerminatedProcesses=0,
            )

        @staticmethod
        def query_active_pids(_job: int) -> tuple[int, ...]:
            return (402,)

    fake = _InterruptApi()
    clock = _FakeClock()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.sys, "platform", "win32")

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner(clock=clock, sleep=clock.sleep).run([r"C:\runtime.exe"])

    failure = caught.value
    assert failure.cause_type == "KeyboardInterrupt"
    assert failure.child_created is True
    assert failure.root_resumed is True
    assert failure.residual_pids == (402,)
    assert failure.forced_termination_attempts == 0
    assert failure.safe_for_followup is False


def test_stale_completion_pid_reuse_error_latches_manual_and_no_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StaleCompletionApi(_RunnerApi):
        def __init__(self) -> None:
            super().__init__()
            self.wait_calls = 0

        def query_accounting(self, _job: int) -> SimpleNamespace:
            self.accounting_calls += 1
            return SimpleNamespace(
                TotalProcesses=1,
                ActiveProcesses=1 if self.accounting_calls <= 2 else 0,
                TotalTerminatedProcesses=0,
            )

        def wait_completion_events(
            self, _completion: int, _wait_milliseconds: int
        ) -> list[tuple[int, int | None]]:
            self.wait_calls += 1
            if self.wait_calls == 1:
                return [(self._JOB_MESSAGE_NEW_PROCESS, 403)]
            return []

        @staticmethod
        def open_process(pid: int) -> int | None:
            return 500 if pid == 403 else None

        @staticmethod
        def is_process_in_job(process_handle: int, _job: int) -> bool:
            return process_handle != 500

    fake = _StaleCompletionApi()
    clock = _FakeClock()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    monkeypatch.setattr(process.sys, "platform", "win32")

    outcome = process.WindowsJobProcessRunner(clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"]
    )

    assert outcome.identity_coverage_complete is True
    assert "identity_not_in_job_pid=403" in outcome.errors
    assert outcome.manual_intervention_required is True
    assert outcome.safe_for_followup is False
    assert outcome.forced_termination_attempts == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner semantics")
def test_restore_deadline_caps_runtime_residual_wait_and_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RunnerApi(remain_active=True)
    clock = _FakeClock()
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: fake)
    contract = process.TimeoutContract(
        kubectl_timeout_seconds=1.0,
        wrapper_timeout_seconds=2.0,
        restore_deadline_seconds=6.0,
        residual_repoll_seconds=2.0,
        stream_drain_seconds=1.0,
    )

    outcome = process.WindowsJobProcessRunner(contract, clock=clock, sleep=clock.sleep).run(
        [r"C:\runtime.exe"], poll_interval_seconds=0.4
    )

    assert clock.value <= 6.0
    assert max(clock.sleeps) <= 0.4
    assert outcome.timed_out is True
    assert outcome.manual_intervention_required is True
    assert outcome.safe_for_followup is False
    assert outcome.residual_pids == (402,)
    assert any(event.event == "residual_repoll_exhausted" for event in outcome.events)
    assert outcome.forced_termination_attempts == 0


def test_default_stream_capture_is_finite() -> None:
    assert process.DEFAULT_MAX_STREAM_BYTES == 16 * 1024 * 1024
    capture = process._BoundedStreamCapture(3)
    capture.extend(b"ab")
    capture.extend(b"cdef")
    assert bytes(capture) == b"abc"
    assert capture.total_bytes == 6
    assert capture.overflow is True


def test_run_global_event_evidence_limit_fails_closed_and_stays_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: _RunnerApi(remain_active=True))

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], max_job_events=1)

    failure = caught.value
    assert failure.child_created is True
    assert failure.manual_intervention_required is True
    assert failure.safe_for_followup is False
    assert len(failure.events) == 1
    assert any("job_event_limit_exceeded:1" in error for error in failure.errors)


def test_cross_cap_terminal_evidence_never_escapes_raw_limit_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process, "_WindowsJobApi", lambda: _RunnerApi(remain_active=True))

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run(
            [r"C:\runtime.exe"],
            max_job_events=1,
            max_error_records=1,
        )

    failure = caught.value
    assert failure.cause_type == "_EvidenceLimitExceeded"
    assert failure.manual_intervention_required is True
    assert failure.safe_for_followup is False
    assert len(failure.events) <= 1
    assert len(failure.errors) <= 12


def test_run_global_identity_evidence_limit_fails_closed_and_stays_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DescendantApi(_RunnerApi):
        def __init__(self) -> None:
            super().__init__(remain_active=True)
            self.completion_calls = 0

        def completion_events(self, _completion: int) -> list[tuple[int, int | None]]:
            self.completion_calls += 1
            if self.completion_calls == 1:
                return [(self._JOB_MESSAGE_NEW_PROCESS, 403)]
            return []

        @staticmethod
        def open_process(pid: int) -> int | None:
            return 500 if pid == 403 else None

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
                ppid=fallback_ppid if fallback_ppid is not None else 402,
                creation_time_ns=pid,
                creation_time_utc="2026-09-02T00:00:00+00:00",
                image=rf"C:\process-{pid}.exe",
                run_uuid=run_uuid,
                observed_sequence=observed_sequence,
            )

    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process, "_WindowsJobApi", _DescendantApi)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], max_process_identities=1)

    failure = caught.value
    assert failure.child_created is True
    assert len(failure.identities) == 1
    assert any("process_identity_limit_exceeded:1" in error for error in failure.errors)
    assert failure.safe_for_followup is False


def test_run_global_accounting_evidence_limit_fails_closed_and_stays_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process, "_WindowsJobApi", _RunnerApi)

    with pytest.raises(process.ProcessContainmentFailure) as caught:
        process.WindowsJobProcessRunner().run([r"C:\runtime.exe"], max_accounting_snapshots=1)

    failure = caught.value
    assert failure.child_created is True
    assert len(failure.accounting) == 1
    assert any("accounting_snapshot_limit_exceeded:1" in error for error in failure.errors)
    assert failure.safe_for_followup is False
