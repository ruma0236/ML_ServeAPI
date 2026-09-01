from __future__ import annotations

import inspect
import json
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from evm.scale_validation import phase_b2_r7_process as process_module
from evm.scale_validation.phase_b2_r7_process import (
    ProcessContainmentError,
    ProcessContainmentFailure,
    ProcessIdentity,
    TimeoutContract,
    WindowsJobProcessRunner,
    WslResidualProtocol,
    identity_coverage_complete,
    parse_linux_proc_stat,
)


def _contract(
    *, wrapper: float = 1.5, residual: float = 0.6, stream: float = 0.4
) -> TimeoutContract:
    return TimeoutContract(
        kubectl_timeout_seconds=min(0.05, wrapper / 2),
        wrapper_timeout_seconds=wrapper,
        restore_deadline_seconds=max(3.0, wrapper + residual + stream + 0.5),
        residual_repoll_seconds=residual,
        stream_drain_seconds=stream,
    )


def _python(code: str) -> list[str]:
    return [sys.executable, "-I", "-c", code]


def _event_index(outcome, event: str) -> int:
    return next(index for index, item in enumerate(outcome.events) if item.event == event)


def test_default_timeout_contract_matches_r7_manifest_contract() -> None:
    contract = TimeoutContract()
    assert contract.kubectl_timeout_seconds == 8
    assert contract.wrapper_timeout_seconds == 15
    assert contract.restore_deadline_seconds == 600
    assert contract.residual_repoll_seconds == 120
    assert contract.stream_drain_seconds == 5


@pytest.mark.parametrize(
    "values",
    [
        dict(kubectl_timeout_seconds=0),
        dict(wrapper_timeout_seconds=8),
        dict(restore_deadline_seconds=15),
        dict(residual_repoll_seconds=-1),
        dict(stream_drain_seconds=0),
    ],
)
def test_timeout_contract_rejects_nonpositive_or_unordered_values(values) -> None:
    with pytest.raises(ValueError):
        TimeoutContract(**values)


def test_pid_reuse_is_distinguished_by_creation_time() -> None:
    common = dict(
        pid=991,
        ppid=10,
        creation_time_utc="2026-09-01T00:00:00+00:00",
        image="python.exe",
        run_uuid=str(uuid.uuid4()),
        observed_sequence=1,
    )
    first = ProcessIdentity(creation_time_ns=100, **common)
    second = ProcessIdentity(creation_time_ns=200, **common)
    assert first.stable_key != second.stable_key
    assert identity_coverage_complete(2, [first, second])


def test_identity_coverage_fails_closed_for_missing_or_incomplete_identity() -> None:
    identity = ProcessIdentity(
        pid=1,
        ppid=None,
        creation_time_ns=100,
        creation_time_utc="2026-09-01T00:00:00+00:00",
        image="python.exe",
        run_uuid=str(uuid.uuid4()),
        observed_sequence=1,
    )
    assert not identity_coverage_complete(1, [identity])
    assert not identity_coverage_complete(2, [identity])


def test_linux_proc_parser_handles_parenthesis_and_wsl_matching() -> None:
    run_uuid = str(uuid.uuid4())
    fields = ["S", "10", "20", "20", *("0" for _ in range(15)), "12345"]
    stat_text = f"77 (worker ) name) {' '.join(fields)}"
    parsed = parse_linux_proc_stat(stat_text)
    assert parsed.pid == 77
    assert parsed.comm == "worker ) name"
    assert parsed.ppid == 10
    assert parsed.pgrp == 20
    assert parsed.session == 20
    assert parsed.start_time_ticks == 12345

    protocol = WslResidualProtocol(
        run_uuid,
        root_process_group=20,
        root_start_time_ticks=12000,
        boot_id="boot-a",
    )
    by_uuid = protocol.record_from_proc(
        stat_text=stat_text,
        environ=f"A=1\0EVM_PHASE_B2_RUN_UUID={run_uuid}\0".encode(),
        cmdline=b"python3\0worker.py\0",
        boot_id="boot-a",
    )
    assert by_uuid.run_uuid_match
    assert by_uuid.process_group_match
    assert protocol.is_residual(by_uuid)
    assert len(by_uuid.cmdline_sha256) == 64

    group_fallback = protocol.record_from_proc(
        stat_text=stat_text,
        environ=b"A=1\0",
        cmdline=b"python3\0worker.py\0",
        boot_id="boot-a",
    )
    assert not group_fallback.run_uuid_match
    assert group_fallback.process_group_match
    assert protocol.is_residual(group_fallback)


def test_wsl_protocol_uses_uuid_session_and_read_only_proc_scanner() -> None:
    run_uuid = str(uuid.uuid4())
    protocol = WslResidualProtocol(
        run_uuid,
        root_process_group=42,
        root_start_time_ticks=900,
        boot_id="boot-id",
    )
    launch = protocol.launch_command("Ubuntu", ["python3", "worker.py"])
    assert launch[:5] == (
        "wsl.exe",
        "--distribution",
        "Ubuntu",
        "--exec",
        "env",
    )
    assert f"EVM_PHASE_B2_RUN_UUID={run_uuid}" in launch
    assert launch[6:9] == ("setsid", "--fork", "--wait")
    lowered = " ".join(launch).lower()
    assert "shutdown" not in lowered
    assert "unregister" not in lowered

    scanner_source = protocol.scanner_python_source()
    compile(scanner_source, "<wsl-residual-scanner>", "exec")
    scan = protocol.scan_command("Ubuntu")
    assert scan[:5] == (
        "wsl.exe",
        "--distribution",
        "Ubuntu",
        "--exec",
        "python3",
    )
    assert "/proc" in scanner_source
    assert "cmdline_sha256" in scanner_source
    assert "read_bytes" in scanner_source

    payload = json.dumps(
        [
            {
                "pid": 10,
                "ppid": 1,
                "pgrp": 42,
                "session": 42,
                "start_time_ticks": 901,
                "boot_id": "boot-id",
                "run_uuid_match": True,
                "process_group_match": True,
                "cmdline_sha256": "a" * 64,
            }
        ]
    )
    records = protocol.parse_scan_json(payload)
    assert len(records) == 1
    assert records[0].stable_key == ("boot-id", 10, 901)


def test_non_windows_runner_refuses_emulation() -> None:
    if sys.platform == "win32":
        pytest.skip("non-Windows contract test")
    with pytest.raises(ProcessContainmentError):
        WindowsJobProcessRunner(_contract()).run(_python("pass"))


class _PostCreateFailureApi:
    _JOB_MESSAGE_ACTIVE_ZERO = 4
    _JOB_MESSAGE_NEW_PROCESS = 6
    _JOB_MESSAGE_EXIT_PROCESS = 7
    _JOB_MESSAGE_ABNORMAL_EXIT = 8

    def __init__(self, *, fail_before_child: bool = False) -> None:
        self.fail_before_child = fail_before_child
        self.pipe_number = 0
        self.create_process_calls = 0
        self.resume_calls = 0
        self.closed_handles: list[int] = []

    def create_job_and_completion_port(self) -> tuple[int, int]:
        if self.fail_before_child:
            raise ProcessContainmentError("injected_pre_create_failure")
        return (100, 101)

    def create_pipe(self) -> tuple[int, int]:
        self.pipe_number += 1
        base = 200 + self.pipe_number * 10
        return (base, base + 1)

    def open_inheritable_null(self) -> int:
        return 300

    def create_suspended_process(self, **_kwargs) -> SimpleNamespace:
        self.create_process_calls += 1
        return SimpleNamespace(hProcess=400, hThread=401, dwProcessId=4242)

    def is_process_in_job(self, _process: int, _job: int) -> bool:
        return False

    def query_accounting(self, _job: int) -> SimpleNamespace:
        return SimpleNamespace(
            TotalProcesses=1,
            ActiveProcesses=1,
            TotalTerminatedProcesses=0,
        )

    def query_active_pids(self, _job: int) -> tuple[int, ...]:
        return (4242,)

    def process_is_active(self, _process: int) -> bool:
        return True

    def resume(self, _thread: int) -> None:
        self.resume_calls += 1

    def close(self, handle: int | None) -> None:
        if handle:
            self.closed_handles.append(handle)


def test_post_create_verification_failure_preserves_residual_and_never_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _PostCreateFailureApi()
    monkeypatch.setattr(process_module, "_WindowsJobApi", lambda: fake_api)
    followup_calls = 0

    with pytest.raises(ProcessContainmentFailure) as caught:
        WindowsJobProcessRunner(_contract()).run(["inert-mocked-command.exe"])

    failure = caught.value
    if failure.safe_for_followup:
        followup_calls += 1
    assert fake_api.create_process_calls == 1
    assert fake_api.resume_calls == 0
    assert failure.stage == "verify_suspended_root"
    assert failure.child_created
    assert not failure.no_child_created
    assert failure.root_pid == 4242
    assert not failure.job_membership_verified
    assert not failure.root_resumed
    assert failure.manual_intervention_required
    assert failure.residual_pids == (4242,)
    assert failure.forced_termination_attempts == 0
    assert not failure.safe_for_followup
    assert followup_calls == 0
    assert failure.process_evidence["root_pid"] == 4242
    assert failure.process_evidence["residual_pids"] == [4242]
    assert any(event.event == "root_created_suspended" for event in failure.events)
    assert any(event.event == "containment_failure" for event in failure.events)
    json.dumps(failure.to_dict(), sort_keys=True)


def test_pre_create_failure_is_explicitly_no_child_and_no_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api = _PostCreateFailureApi(fail_before_child=True)
    monkeypatch.setattr(process_module, "_WindowsJobApi", lambda: fake_api)

    with pytest.raises(ProcessContainmentFailure) as caught:
        WindowsJobProcessRunner(_contract()).run(["never-created.exe"])

    failure = caught.value
    assert failure.stage == "create_job"
    assert failure.no_child_created
    assert not failure.child_created
    assert failure.root_pid is None
    assert failure.residual_pids == ()
    assert not failure.manual_intervention_required
    assert not failure.safe_for_followup
    assert failure.forced_termination_attempts == 0
    assert fake_api.create_process_calls == 0
    assert fake_api.resume_calls == 0


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects")
def test_suspended_assignment_precedes_resume_and_evidence_is_serialisable() -> None:
    runner = WindowsJobProcessRunner(_contract())
    outcome = runner.run(
        _python("import sys; print('stdout-ok'); print('stderr-ok', file=sys.stderr)"),
        name="membership-order",
        poll_interval_seconds=0.01,
    )
    assert outcome.return_code == 0
    assert outcome.safe_for_followup
    assert not outcome.manual_intervention_required
    assert outcome.active_process_zero
    assert outcome.streams_drained
    assert outcome.identity_coverage_complete
    assert outcome.job_limit_flags == 0
    assert outcome.forced_termination_attempts == 0
    assert outcome.residual_pids == ()
    assert "stdout-ok" in outcome.stdout
    assert "stderr-ok" in outcome.stderr
    assert (
        _event_index(outcome, "root_created_suspended")
        < _event_index(outcome, "job_membership_verified")
        < _event_index(outcome, "root_resumed")
    )
    assert all(identity.run_uuid == outcome.run_uuid for identity in outcome.identities)
    json.dumps(outcome.to_dict(), sort_keys=True)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects")
def test_child_and_grandchild_created_between_polls_are_job_accounted() -> None:
    grandchild = "import time; print('grandchild'); time.sleep(0.22)"
    child = (
        "import subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-I','-c',{grandchild!r}]); "
        "print('child'); time.sleep(0.12); p.wait()"
    )
    root = (
        "import subprocess,sys,time; time.sleep(0.025); "
        f"p=subprocess.Popen([sys.executable,'-I','-c',{child!r}]); "
        "print('root'); p.wait()"
    )
    outcome = WindowsJobProcessRunner(_contract(wrapper=2.0)).run(
        _python(root),
        name="descendant-accounting",
        poll_interval_seconds=0.075,
    )
    assert outcome.return_code == 0
    assert outcome.safe_for_followup
    assert outcome.identity_coverage_complete
    # Some Windows Python distributions use a short-lived launcher process.
    # Job accounting must include it too, so three is a lower bound rather
    # than an assumption about the host interpreter packaging.
    assert len(outcome.identities) >= 3
    assert max(item.total_processes for item in outcome.accounting) >= 3
    assert sum(item.event == "job_new_process" for item in outcome.events) >= 3
    assert {"root", "child", "grandchild"} <= set(outcome.stdout.split())


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects")
def test_root_exit_reparent_and_closed_stdio_descendant_do_not_open_gate_early() -> None:
    descendant = "import os,time; os.close(1); os.close(2); time.sleep(0.32)"
    root = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-I','-c',{descendant!r}]); "
        "time.sleep(0.06)"
    )
    started = time.monotonic()
    outcome = WindowsJobProcessRunner(_contract(wrapper=1.5)).run(
        _python(root),
        name="reparent-closed-stdio",
        poll_interval_seconds=0.01,
    )
    elapsed = time.monotonic() - started
    assert elapsed >= 0.25
    assert outcome.return_code == 0
    assert outcome.safe_for_followup
    assert outcome.active_process_zero
    assert outcome.streams_drained
    assert len(outcome.identities) >= 2
    assert any(
        0 < snapshot.active_processes < snapshot.total_processes for snapshot in outcome.accounting
    )
    assert _event_index(outcome, "streams_drained") > _event_index(outcome, "root_resumed")


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects")
def test_timeout_latches_manual_but_child_naturally_exits_during_residual_wait() -> None:
    outcome = WindowsJobProcessRunner(_contract(wrapper=0.08, residual=0.5, stream=0.3)).run(
        _python("import time; time.sleep(0.24); print('natural-exit')"),
        name="timeout-natural-exit",
        poll_interval_seconds=0.01,
    )
    assert outcome.timed_out
    assert not outcome.cancelled
    assert outcome.manual_intervention_required
    assert outcome.active_process_zero
    assert outcome.residual_pids == ()
    assert outcome.streams_drained
    assert not outcome.safe_for_followup
    assert outcome.forced_termination_attempts == 0
    assert "natural-exit" in outcome.stdout
    assert any(item.event == "timeout_latched" for item in outcome.events)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects")
def test_residual_after_bounded_wait_blocks_followup_and_is_never_forced() -> None:
    followup_calls = 0
    outcome = WindowsJobProcessRunner(_contract(wrapper=0.05, residual=0.05, stream=0.03)).run(
        _python("import time; time.sleep(0.32)"),
        name="bounded-residual",
        poll_interval_seconds=0.005,
    )
    if outcome.safe_for_followup:
        followup_calls += 1
    assert outcome.timed_out
    assert outcome.manual_intervention_required
    assert outcome.residual_pids
    assert outcome.final_active_process_count >= 1
    assert not outcome.safe_for_followup
    assert followup_calls == 0
    assert outcome.forced_termination_attempts == 0
    assert any(item.event == "residual_repoll_exhausted" for item in outcome.events)
    assert any(item.event == "residual_processes_observed" for item in outcome.events)
    # The test helper is deliberately allowed to finish on its own.  This wait
    # is longer than its declared lifetime and performs no process-control call.
    time.sleep(0.30)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects")
def test_cancel_latches_manual_without_termination() -> None:
    cancelled = threading.Event()
    cancelled.set()
    outcome = WindowsJobProcessRunner(_contract(wrapper=0.5, residual=0.4, stream=0.2)).run(
        _python("import time; time.sleep(0.14)"),
        name="cancel-natural-exit",
        poll_interval_seconds=0.01,
        cancel_event=cancelled,
    )
    assert outcome.cancelled
    assert not outcome.timed_out
    assert outcome.manual_intervention_required
    assert outcome.active_process_zero
    assert outcome.residual_pids == ()
    assert outcome.forced_termination_attempts == 0
    assert not outcome.safe_for_followup


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects")
def test_abnormal_exit_is_not_safe_even_with_clean_containment() -> None:
    outcome = WindowsJobProcessRunner(_contract()).run(
        _python("import sys; sys.exit(7)"),
        name="abnormal-exit",
        poll_interval_seconds=0.01,
    )
    assert outcome.return_code == 7
    assert not outcome.timed_out
    assert not outcome.cancelled
    assert outcome.active_process_zero
    assert outcome.streams_drained
    assert outcome.identity_coverage_complete
    assert not outcome.safe_for_followup
    assert outcome.forced_termination_attempts == 0


def test_runtime_source_has_no_process_termination_or_escape_configuration() -> None:
    source = inspect.getsource(sys.modules[WindowsJobProcessRunner.__module__])
    forbidden = (
        "Terminate" + "JobObject",
        "KILL" + "_ON_JOB_CLOSE",
        "CREATE_" + "BREAKAWAY_FROM_JOB",
        "SILENT_" + "BREAKAWAY_OK",
        "LIMIT_" + "BREAKAWAY_OK",
    )
    for token in forbidden:
        assert token not in source
    assert "." + "ki" + "ll(" not in source
    assert "." + "termi" + "nate(" not in source
    assert "task" + "kill" not in source.lower()


def test_test_helpers_contain_no_process_termination_calls() -> None:
    source = inspect.getsource(sys.modules[__name__])
    assert "." + "ki" + "ll(" not in source
    assert "." + "termi" + "nate(" not in source
    assert "task" + "kill" not in source.lower()
    assert "os." + "ki" + "ll" not in source


def test_r7_process_module_is_independent_and_contract_exact() -> None:
    source = inspect.getsource(process_module)
    assert "phase_b2_r5" not in source
    assert WindowsJobProcessRunner.__module__ == process_module.__name__
    assert process_module.ProcessTimeoutContract is TimeoutContract
    contract = dict(process_module.PROCESS_CONTAINMENT_CONTRACT)
    assert process_module.validate_process_containment_contract(contract) == contract
    assert contract["force_termination_attempts"] == 0
    contract["force_termination_attempts"] = 1
    with pytest.raises(process_module.R7ProcessContractError, match="mismatch"):
        process_module.validate_process_containment_contract(contract)


def test_wsl_launcher_exit_does_not_hide_linux_residual_fixture() -> None:
    run_uuid = str(uuid.uuid4())
    protocol = WslResidualProtocol(
        run_uuid,
        root_process_group=77,
        root_start_time_ticks=1000,
        boot_id="boot-r7",
    )
    # The Windows wsl.exe launcher is no longer part of this observation.  A
    # later /proc scan must still match the surviving Linux process by UUID or
    # its original process group and boot-scoped creation identity.
    fields = ["S", "1", "77", "77", *("0" for _ in range(15)), "1001"]
    residual = protocol.record_from_proc(
        stat_text=f"900 (survivor) {' '.join(fields)}",
        environ=f"EVM_PHASE_B2_RUN_UUID={run_uuid}\\0".encode(),
        cmdline=b"python3\\0survivor.py\\0",
        boot_id="boot-r7",
    )
    assert residual.pid == 900
    assert protocol.is_residual(residual)
    assert residual.stable_key == ("boot-r7", 900, 1001)
