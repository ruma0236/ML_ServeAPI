from __future__ import annotations

import concurrent.futures
import hashlib
import inspect
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r7s3_process as process

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/dev/qualify_pre_r8_r7s7_windows.py"
FIXTURE = ROOT / "scripts/dev/pre_r8_r7s7_windows_fixture.py"
OUTER = ROOT / "scripts/dev/invoke_pre_r8_r7s7_windows_qualification.ps1"
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
COMMAND_PROCESSOR = Path(r"C:\Windows\System32\cmd.exe")
SPEC = importlib.util.spec_from_file_location("qualify_pre_r8_r7s7_windows", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
qualifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qualifier
SPEC.loader.exec_module(qualifier)

RUN_UUID = "12345678-1234-4234-8234-123456789abc"
ATTEMPT_UUID = "abcdefab-cdef-4def-8def-abcdefabcdef"
STAMP = "2026-09-03T00:00:00+00:00"
PYCACHE_PREFIX = ROOT / f".nonexistent-pycache-{RUN_UUID}-{ATTEMPT_UUID}"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config(tmp_path: Path) -> Any:
    interpreter = Path(sys.executable).resolve()
    test_tools = tmp_path / ".test-pinned-tools"
    test_tools.mkdir(exist_ok=True)
    codex = test_tools / "codex.exe"
    if not codex.exists():
        codex.write_bytes(interpreter.read_bytes())
    runner_source = Path(process.__file__).resolve()
    return qualifier.QualificationConfig(
        run_uuid=RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        interpreter=str(interpreter),
        interpreter_sha256=_sha(interpreter),
        fixture=str(FIXTURE.resolve()),
        fixture_sha256=_sha(FIXTURE),
        qualifier=str(SCRIPT.resolve()),
        qualifier_sha256=_sha(SCRIPT),
        runner_source=str(runner_source),
        runner_source_sha256=_sha(runner_source),
        powershell=str(POWERSHELL.resolve()),
        powershell_sha256=_sha(POWERSHELL),
        codex=str(codex.resolve()),
        codex_sha256=_sha(codex),
        command_processor=str(COMMAND_PROCESSOR.resolve()),
        command_processor_sha256=_sha(COMMAND_PROCESSOR),
        pycache_prefix=str(PYCACHE_PREFIX.resolve()),
        output_root=str(tmp_path.resolve()),
    )


def _approval(**changes: Any) -> Any:
    values = {
        "schema": qualifier.INTERNAL_APPROVAL_SCHEMA,
        "run_uuid": RUN_UUID,
        "attempt_uuid": ATTEMPT_UUID,
        "root_pid": os.getpid(),
        "administrator": True,
        "integrity": "High",
        "token_elevation_type": "Full",
        "powershell_parent_observed": True,
        "approve_exactly_once": True,
        "internal_non_authoritative": True,
        "production_go": False,
    }
    values.update(changes)
    return qualifier.InternalRootApproval(**values)


def _live_lineage(config: Any, **record_changes: dict[str, Any]) -> dict[str, Any]:
    base = datetime(2026, 9, 3, tzinfo=UTC)

    def record(
        pid: int,
        ppid: int,
        path: str,
        created: datetime,
        *,
        image_sha256: str,
        codex_policy: bool = False,
    ) -> dict[str, Any]:
        return {
            "pid": pid,
            "ppid": ppid,
            "session_id": 7,
            "creation_time_utc": created.isoformat().replace("+00:00", "Z"),
            "path": path,
            "image_sha256": image_sha256,
            "command_line_sha256": f"{pid:064x}",
            "danger_full_access_flag_present": codex_policy,
            "approval_never_flag_present": codex_policy,
            "command_line_persisted": False,
            "token": {
                "administrator": True,
                "administrator_group_member": True,
                "integrity": "High",
                "integrity_rid": 0x3000,
                "token_elevation_type": "Full",
                "token_elevation_value": 2,
            },
            "measurement": "win32_direct_no_child_process",
        }

    powershell_pid = 200
    codex_pid = 150
    value = {
        "python": record(
            os.getpid(),
            powershell_pid,
            config.interpreter,
            base + timedelta(seconds=2),
            image_sha256=config.interpreter_sha256,
        ),
        "powershell": record(
            powershell_pid,
            codex_pid,
            config.powershell,
            base + timedelta(seconds=1),
            image_sha256=config.powershell_sha256,
        ),
        "codex": record(
            codex_pid,
            1,
            config.codex,
            base,
            image_sha256=config.codex_sha256,
            codex_policy=True,
        ),
    }
    for key, changes in record_changes.items():
        value[key].update(changes)
    return value


def _fixture_payload(**changes: Any) -> dict[str, Any]:
    snapshot = {
        "is_process_in_job": True,
        "limit_flags": 0,
        "active_processes": 1,
        "total_processes": 1,
        "terminated_processes": 0,
        "assigned_processes": 1,
        "process_ids": [100],
    }
    value: dict[str, Any] = {
        "schema": qualifier.FIXTURE_SCHEMA,
        "run_uuid": RUN_UUID,
        "pycache": {
            "prefix": str(PYCACHE_PREFIX.resolve()),
            "initially_absent": True,
            "absent_before_root_exit": True,
            "dont_write_bytecode": True,
        },
        "capability": {
            "schema": "evm.phase-b2.windows-job-capability-consumption.v1",
            "run_uuid": RUN_UUID,
            "pid": 100,
            "requested_access": process.JOB_CAPABILITY_QUERY_ACCESS,
            "nonce_commitment": "a" * 64,
            "snapshots_equal": True,
            "environment_consumed": True,
            "raw_nonce_recorded": False,
            "explicit_job": snapshot,
            "implicit_job": dict(snapshot),
        },
        "pids": {
            "root": 100,
            "child": 101,
            "grandchild": 102,
            "closed_stdio": 103,
            "console_child": 104,
        },
        "stdio": {"closed_stdio_child": True, "full_drain_required": True},
        "timing_contract": {
            "descendant_hold_seconds": 2.5,
            "child_handoff_seconds": 0.15,
            "minimum_descendant_margin_seconds": 2.35,
        },
        "breakaway": {
            "attempted": True,
            "denied": True,
            "error_code": 5,
            "spawned_pid": None,
        },
        "tool_pins": {
            "interpreter": {
                "path": str(Path(sys.executable).resolve()),
                "sha256": _sha(Path(sys.executable).resolve()),
            },
            "fixture": {"path": str(FIXTURE.resolve()), "sha256": _sha(FIXTURE)},
            "command_processor": {
                "path": str(COMMAND_PROCESSOR.resolve()),
                "sha256": _sha(COMMAND_PROCESSOR),
            },
        },
    }
    value.update(changes)
    return value


def _identity(pid: int, ppid: int, image: str, sequence: int) -> process.ProcessIdentity:
    return process.ProcessIdentity(
        pid=pid,
        ppid=ppid,
        creation_time_ns=pid * 1_000,
        creation_time_utc=STAMP,
        image=image,
        run_uuid=RUN_UUID,
        observed_sequence=sequence,
    )


def _outcome(**changes: Any) -> process.ProcessOutcome:
    fixture = _fixture_payload()
    stdout = json.dumps(fixture, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"
    identities = (
        _identity(100, os.getpid(), r"C:\Python\python.exe", 1),
        _identity(101, 100, r"C:\Python\python.exe", 2),
        _identity(102, 101, r"C:\Python\python.exe", 3),
        _identity(103, 100, r"C:\Python\python.exe", 4),
        _identity(104, 100, r"C:\Windows\System32\cmd.exe", 5),
        _identity(105, 104, r"C:\Windows\System32\conhost.exe", 6),
    )
    events = (
        process.JobEvent(1, "root_created_suspended", 1, STAMP, pid=100),
        process.JobEvent(2, "root_resumed", 2, STAMP, pid=100),
        process.JobEvent(3, "job_exit_process", 3, STAMP, pid=101),
        process.JobEvent(4, "job_exit_process", 4, STAMP, pid=100),
        process.JobEvent(5, "job_exit_process", 5, STAMP, pid=102),
        process.JobEvent(6, "job_exit_process", 6, STAMP, pid=103),
        process.JobEvent(7, "job_exit_process", 7, STAMP, pid=104),
        process.JobEvent(8, "job_exit_process", 8, STAMP, pid=105),
        process.JobEvent(9, "active_process_count_zero", 9, STAMP),
        process.JobEvent(12, "streams_drained", 12, STAMP),
    )
    accounting = (
        process.JobAccountingSnapshot(10, 10, STAMP, 6, 0, 6, ()),
        process.JobAccountingSnapshot(11, 11, STAMP, 6, 0, 6, ()),
    )
    values: dict[str, Any] = {
        "name": f"pre-r8-r7s7-windows-{RUN_UUID}",
        "run_uuid": RUN_UUID,
        "command": (sys.executable,),
        "started_at_utc": STAMP,
        "ended_at_utc": STAMP,
        "duration_seconds": 1.0,
        "timed_out": False,
        "cancelled": False,
        "return_code": 0,
        "manual_intervention_required": False,
        "residual_pids": (),
        "stdout": stdout,
        "stderr": "",
        "stdout_drained": True,
        "stderr_drained": True,
        "streams_drained": True,
        "active_process_zero": True,
        "final_active_process_count": 0,
        "identity_coverage_complete": True,
        "safe_for_followup": True,
        "forced_termination_attempts": 0,
        "job_limit_flags": 0,
        "identities": identities,
        "events": events,
        "accounting": accounting,
        "errors": (),
        "executable_identity": {"sha256": "a" * 64},
        "stdout_total_bytes": len(stdout.encode()),
        "stderr_total_bytes": 0,
        "stdout_capture_overflow": False,
        "stderr_capture_overflow": False,
        "stream_capture_limit_bytes": 1024 * 1024,
        "stream_cleanup": {"complete": True},
    }
    values.update(changes)
    return process.ProcessOutcome(**values)


class _Store:
    def __init__(self) -> None:
        self.reservations: list[tuple[str, str]] = []
        self.evidence: list[bytes] = []
        self.failures: list[bytes] = []
        self.emergencies: list[bytes] = []
        self.pre_reservation_failures: list[bytes] = []
        self.pre_reservation_emergencies: list[bytes] = []

    @staticmethod
    def _publication(name: str, raw: bytes) -> Any:
        return qualifier.Publication(
            path=name,
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            atomic_rename_no_replace=True,
            file_fsync=True,
            directory_fsync=True,
            same_handle_readback=True,
            file_identity_stable_across_rename=True,
            file_identity={"volume_serial": 1, "file_id": name},
            directory_identity={"volume_serial": 1, "file_id": "run-directory"},
            create_attempt_count=1,
        )

    def reserve_once(self, run_uuid: str, attempt_uuid: str) -> Any:
        self.reservations.append((run_uuid, attempt_uuid))
        return self._publication("reservation.json", b"reservation")

    def publish_evidence_once(self, run_uuid: str, raw: bytes) -> Any:
        assert run_uuid == RUN_UUID
        self.evidence.append(raw)
        return self._publication("windows-qualification-evidence.json", raw)

    def publish_failure_once(self, run_uuid: str, raw: bytes) -> Any:
        assert run_uuid == RUN_UUID
        self.failures.append(raw)
        return self._publication("windows-qualification-failure-seal.json", raw)

    def publish_emergency_once(self, run_uuid: str, raw: bytes) -> Any:
        assert run_uuid == RUN_UUID
        self.emergencies.append(raw)
        return self._publication("windows-qualification-emergency-seal.json", raw)

    def publish_pre_reservation_failure_once(
        self, run_uuid: str, attempt_uuid: str, raw: bytes
    ) -> Any:
        assert run_uuid == RUN_UUID
        assert attempt_uuid == ATTEMPT_UUID
        self.pre_reservation_failures.append(raw)
        return self._publication("pre-reservation-failure-seal.json", raw)

    def publish_pre_reservation_emergency_once(
        self, run_uuid: str, attempt_uuid: str, raw: bytes
    ) -> Any:
        assert run_uuid == RUN_UUID
        assert attempt_uuid == ATTEMPT_UUID
        self.pre_reservation_emergencies.append(raw)
        return self._publication("pre-reservation-emergency-seal.json", raw)


class _Runner:
    def __init__(self, outcome: process.ProcessOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(self, command: list[str], **kwargs: Any) -> process.ProcessOutcome:
        self.calls.append((command, kwargs))
        return self.outcome


def _run(
    tmp_path: Path, outcome: process.ProcessOutcome | None = None
) -> tuple[Any, _Store, _Runner]:
    store = _Store()
    runner = _Runner(outcome or _outcome())
    result = qualifier._qualify_windows_non_credit_for_test(
        _config(tmp_path),
        _approval(),
        runner_factory=lambda contract: runner,
        store=store,
    )
    return result, store, runner


def test_public_entry_fails_before_process_creation() -> None:
    with pytest.raises(
        qualifier.WindowsQualificationError,
        match="external_authority_receipt_adapter_unprovisioned",
    ) as caught:
        qualifier.qualify_windows_non_credit(object(), forged_verifier=lambda *_: True)
    assert caught.value.stage == "root_gate"
    assert caught.value.counts.process_creation == 0
    assert caught.value.counts.runner_invocation == 0
    assert caught.value.counts.reservation == 0


def test_internal_candidate_observes_all_contracts_but_remains_no_go(tmp_path: Path) -> None:
    result, store, runner = _run(tmp_path)
    assert len(store.reservations) == 1
    assert len(runner.calls) == 1
    assert len(store.evidence) == 1
    assert store.failures == []
    payload = json.loads(store.evidence[0])
    assert payload["status"] == "internally_observed"
    assert payload["decision"] == "NO-GO"
    assert payload["credit"] == "zero_credit"
    assert payload["production_go"] is False
    assert payload["r8_or_phase_b2_completion"] is False
    assert payload["observations"] == {
        "breakaway_denied_observed": True,
        "child_grandchild_observed": True,
        "closed_stdio_residual_branch_observed": True,
        "conhost_descendant_observed": True,
        "explicit_implicit_query_only_job_snapshots_equal": True,
        "residual_process_count": 0,
        "root_exit_before_descendant_exit_observed": True,
        "stable_zero_snapshot_count": 2,
        "streams_fully_drained": True,
        "pycache_prefix_initially_and_post_run_absent": True,
    }
    assert payload["call_counts"]["process_creation"] == 1
    assert payload["call_counts"]["automatic_retry"] == 0
    assert payload["call_counts"]["followup_probe"] == 0
    assert payload["call_counts"]["force_termination"] == 0
    assert payload["success_marker_count"] == 0
    assert payload["completion_marker_count"] == 0
    assert result["status"] == "internal_non_authoritative"
    assert "passed" not in result
    command, kwargs = runner.calls[0]
    config = _config(tmp_path)
    assert command == [
        config.interpreter,
        "-I",
        "-B",
        "-S",
        "-X",
        f"pycache_prefix={config.pycache_prefix}",
        config.fixture,
        "--mode",
        "root",
        "--run-uuid",
        RUN_UUID,
        "--pycache-prefix",
        config.pycache_prefix,
        "--interpreter-sha256",
        config.interpreter_sha256,
        "--fixture-sha256",
        config.fixture_sha256,
        "--command-processor",
        config.command_processor,
        "--command-processor-sha256",
        config.command_processor_sha256,
    ]
    assert kwargs["run_uuid"] == RUN_UUID
    assert kwargs["create_no_window"] is False
    assert kwargs["expected_executable_sha256"] == _config(tmp_path).interpreter_sha256


def test_missing_conhost_is_unproven_and_sealed_without_retry(tmp_path: Path) -> None:
    without_conhost = replace(_outcome(), identities=_outcome().identities[:-1])
    store = _Store()
    runner = _Runner(without_conhost)
    with pytest.raises(
        qualifier.WindowsQualificationError, match="conhost_descendant_unobserved"
    ) as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: runner,
            store=store,
        )
    assert caught.value.classification == "unproven"
    assert len(runner.calls) == 1
    assert store.evidence == []
    assert len(store.failures) == 1
    seal = json.loads(store.failures[0])
    assert seal["status"] == "unproven"
    assert seal["decision"] == "NO-GO"
    assert seal["automatic_retry_count"] == 0
    assert seal["followup_probe_count"] == 0
    assert seal["force_termination_count"] == 0


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"timed_out": True, "manual_intervention_required": True}, "process_outcome_not_clean"),
        ({"residual_pids": (999,), "final_active_process_count": 1}, "process_outcome_not_clean"),
        ({"streams_drained": False, "stderr_drained": False}, "process_outcome_not_clean"),
    ],
)
def test_terminal_process_failure_seals_once_without_followup(
    tmp_path: Path, changes: dict[str, Any], error: str
) -> None:
    store = _Store()
    runner = _Runner(replace(_outcome(), **changes))
    with pytest.raises(qualifier.WindowsQualificationError, match=error) as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: runner,
            store=store,
        )
    assert len(runner.calls) == 1
    assert len(store.failures) == 1
    assert caught.value.counts.automatic_retry == 0
    assert caught.value.counts.followup_probe == 0
    assert caught.value.counts.force_termination == 0


def test_query_only_snapshots_must_match(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["capability"]["implicit_job"]["active_processes"] = 2
    stdout = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="query_only_job_mismatch"):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(replace(_outcome(), stdout=stdout)),
            store=store,
        )
    assert len(store.failures) == 1


def test_reparent_order_and_stable_zero_are_mandatory(tmp_path: Path) -> None:
    events = tuple(
        replace(event, sequence=2)
        if event.pid == 102 and event.event == "job_exit_process"
        else event
        for event in _outcome().events
    )
    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="reparent_order_unproven"):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(replace(_outcome(), events=events)),
            store=store,
        )
    three_zero = (
        *_outcome().accounting,
        process.JobAccountingSnapshot(13, 13, STAMP, 6, 0, 6, ()),
    )
    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="stable_zero_twice_unproven"):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(replace(_outcome(), accounting=three_zero)),
            store=store,
        )
    zero_then_nonzero_then_zero = (
        _outcome().accounting[0],
        process.JobAccountingSnapshot(13, 13, STAMP, 6, 1, 6, (777,)),
        process.JobAccountingSnapshot(14, 14, STAMP, 6, 0, 6, ()),
    )
    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="stable_zero_twice_unproven"):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(
                replace(_outcome(), accounting=zero_then_nonzero_then_zero)
            ),
            store=store,
        )

    child_after_root = tuple(
        replace(event, sequence=13, monotonic_ns=13)
        if event.pid == 101 and event.event == "job_exit_process"
        else event
        for event in _outcome().events
    )
    store = _Store()
    with pytest.raises(
        qualifier.WindowsQualificationError, match="child_exit_before_root_exit_unproven"
    ):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(replace(_outcome(), events=child_after_root)),
            store=store,
        )
    one_zero = (_outcome().accounting[0],)
    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="stable_zero_twice_unproven"):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(replace(_outcome(), accounting=one_zero)),
            store=store,
        )


def test_required_process_event_multiplicity_is_exactly_one(tmp_path: Path) -> None:
    duplicate = replace(_outcome().events[0], sequence=13, monotonic_ns=13)
    store = _Store()
    with pytest.raises(
        qualifier.WindowsQualificationError, match="required_event_multiplicity_mismatch"
    ):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(
                replace(_outcome(), events=(*_outcome().events, duplicate))
            ),
            store=store,
        )
    assert len(store.failures) == 1


def test_breakaway_success_is_never_accepted(tmp_path: Path) -> None:
    payload = _fixture_payload(
        breakaway={"attempted": True, "denied": False, "error_code": None, "spawned_pid": 777}
    )
    stdout = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="breakaway_denial_unproven"):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(replace(_outcome(), stdout=stdout)),
            store=store,
        )
    assert len(store.failures) == 1


def test_breakaway_denial_requires_access_denied_error_five(tmp_path: Path) -> None:
    payload = _fixture_payload(
        breakaway={"attempted": True, "denied": True, "error_code": 87, "spawned_pid": None}
    )
    stdout = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="breakaway_denial_unproven"):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(replace(_outcome(), stdout=stdout)),
            store=store,
        )
    assert len(store.failures) == 1


def test_short_descendant_timing_contract_is_rejected(tmp_path: Path) -> None:
    payload = _fixture_payload(
        timing_contract={
            "descendant_hold_seconds": 0.8,
            "child_handoff_seconds": 0.15,
            "minimum_descendant_margin_seconds": 0.65,
        }
    )
    stdout = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="timing_contract_mismatch"):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(replace(_outcome(), stdout=stdout)),
            store=store,
        )
    assert len(store.failures) == 1


def test_unapproved_root_stops_before_reservation_or_runner(tmp_path: Path) -> None:
    store = _Store()
    calls: list[int] = []
    with pytest.raises(
        qualifier.WindowsQualificationError, match="internal_root_approval_mismatch"
    ):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(administrator=False),
            runner_factory=lambda contract: calls.append(1),
            store=store,
        )
    assert store.reservations == []
    assert calls == []


def test_fixture_or_interpreter_hash_mismatch_stops_before_reservation(tmp_path: Path) -> None:
    store = _Store()
    config = replace(_config(tmp_path), fixture_sha256="0" * 64)
    with pytest.raises(qualifier.WindowsQualificationError, match="fixture_sha256_mismatch"):
        qualifier._qualify_windows_non_credit_for_test(
            config,
            _approval(),
            runner_factory=lambda contract: pytest.fail("runner must not be built"),
            store=store,
        )
    assert store.reservations == []


def test_file_store_is_atomic_no_replace_and_run_uuid_one_shot(tmp_path: Path) -> None:
    store = qualifier.FileQualificationStore(tmp_path)
    reservation = store.reserve_once(RUN_UUID, ATTEMPT_UUID)
    assert reservation.atomic_rename_no_replace is True
    assert reservation.file_fsync is True
    assert reservation.directory_fsync is True
    assert reservation.same_handle_readback is True
    assert reservation.file_identity_stable_across_rename is True
    anchor = tmp_path / RUN_UUID / "windows-qualification-run-directory-anchor.json"
    anchor_payload = json.loads(anchor.read_bytes())
    assert anchor_payload["schema"] == qualifier.RUN_DIRECTORY_ANCHOR_SCHEMA
    assert anchor_payload["identity_continuity"] == ("verify_before_and_after_each_publication")
    assert anchor_payload["run_directory_identity"]["file_id_hex"]
    raw = qualifier._canonical_json({"sealed": True})
    evidence = store.publish_evidence_once(RUN_UUID, raw)
    assert Path(evidence.path).read_bytes() == raw
    with pytest.raises(Exception, match="rename_no_replace|already exists|exists|collision"):
        store.publish_evidence_once(RUN_UUID, raw)
    with pytest.raises(qualifier.WindowsQualificationError, match="already_reserved"):
        store.reserve_once(RUN_UUID, ATTEMPT_UUID)


def test_run_directory_identity_change_fails_before_evidence_publication(
    tmp_path: Path,
) -> None:
    store = qualifier.FileQualificationStore(tmp_path)
    store.reserve_once(RUN_UUID, ATTEMPT_UUID)
    store._run_directory_identities[RUN_UUID] = {
        **store._run_directory_identities[RUN_UUID],
        "file_id_hex": "0" * 32,
    }
    final_path = tmp_path / RUN_UUID / "windows-qualification-evidence.json"
    with pytest.raises(
        qualifier.WindowsQualificationError,
        match="run_directory_identity_changed_before_publication",
    ):
        store.publish_evidence_once(RUN_UUID, qualifier._canonical_json({"value": 1}))
    assert not final_path.exists()


def test_file_store_reservation_is_cross_thread_exactly_once(tmp_path: Path) -> None:
    start = threading.Event()

    def reserve() -> tuple[str, str]:
        start.wait(timeout=2)
        try:
            publication = qualifier.FileQualificationStore(tmp_path).reserve_once(
                RUN_UUID, ATTEMPT_UUID
            )
        except qualifier.WindowsQualificationError as exc:
            return "rejected", str(exc)
        return "reserved", publication.sha256

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        start.set()
        results = [future.result(timeout=5) for future in futures]
    assert [status for status, _ in results].count("reserved") == 1
    assert [status for status, _ in results].count("rejected") == 1
    assert next(value for status, value in results if status == "rejected") in {
        "reservation_publication_failed",
        "run_uuid_already_reserved",
        "reserved_run_directory_collision",
    }


def test_existing_run_collision_creates_only_parent_sibling_failure_seal(
    tmp_path: Path,
) -> None:
    initial = qualifier.FileQualificationStore(tmp_path)
    reservation = initial.reserve_once(RUN_UUID, ATTEMPT_UUID)
    reservation_path = Path(reservation.path)
    reservation_raw = reservation_path.read_bytes()
    run_dir = tmp_path / RUN_UUID
    initial_run_entries = tuple(run_dir.iterdir())
    runner_calls: list[int] = []
    with pytest.raises(
        qualifier.WindowsQualificationError, match="run_uuid_already_reserved"
    ) as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: runner_calls.append(1),
            store=qualifier.FileQualificationStore(tmp_path),
        )
    assert runner_calls == []
    assert reservation_path.read_bytes() == reservation_raw
    assert tuple(run_dir.iterdir()) == initial_run_entries
    sibling = tmp_path / f"{RUN_UUID}.{ATTEMPT_UUID}.pre-reservation-failure-seal.json"
    assert sibling.is_file()
    seal = json.loads(sibling.read_bytes())
    assert seal["schema"] == qualifier.PRE_RESERVATION_FAILURE_SEAL_SCHEMA
    assert seal["seal_scope"] == "parent_sibling_outside_existing_run_namespace"
    assert seal["existing_run_namespace_touched"] is False
    assert seal["namespace_touch_observation"]["publication_state"] == "not_started"
    assert seal["namespace_touch_observation"]["existing_namespace_collision_observed"] is True
    assert seal["existing_run_artifact_overwrite_attempted"] is False
    assert seal["call_counts"]["reservation"] == 1
    assert seal["call_counts"]["process_creation"] == 0
    assert seal["call_counts"]["runner_invocation"] == 0
    assert seal["call_counts"]["failure_seal_publication"] == 1
    assert seal["automatic_retry_count"] == 0
    assert seal["success_marker_count"] == 0
    assert seal["completion_marker_count"] == 0
    assert caught.value.failure_publication is not None


def test_internal_once_measures_live_lineage_before_reservation_and_launch(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class OrderedStore(_Store):
        def reserve_once(self, run_uuid: str, attempt_uuid: str) -> Any:
            events.append("reservation")
            return super().reserve_once(run_uuid, attempt_uuid)

    store = OrderedStore()
    runner = _Runner(_outcome())
    config = _config(tmp_path)

    def probe() -> dict[str, Any]:
        events.append("lineage")
        return _live_lineage(config)

    def factory(contract: Any) -> _Runner:
        events.append("runner_construction")
        return runner

    result = qualifier._run_internal_non_authoritative_once_for_test(
        config,
        lineage_probe=probe,
        runner_factory=factory,
        store=store,
    )
    assert events == ["lineage", "reservation", "runner_construction"]
    payload = json.loads(store.evidence[0])
    assert payload["root_authority_measurement"] == _live_lineage(config)
    assert all(
        "command_line" not in record for record in payload["root_authority_measurement"].values()
    )
    assert result["status"] == "internal_non_authoritative"
    assert result["decision"] == "NO-GO"
    assert result["production_go"] is False


@pytest.mark.parametrize(
    ("record_changes", "error"),
    [
        ({"python": {"token": {}}}, "python_token_not_full_admin"),
        ({"powershell": {"path": r"C:\Windows\System32\cmd.exe"}}, "powershell_parent_required"),
        ({"codex": {"path": r"C:\Windows\System32\powershell.exe"}}, "codex_ancestor_required"),
        ({"codex": {"danger_full_access_flag_present": False}}, "command_policy_readback"),
        ({"codex": {"approval_never_flag_present": False}}, "command_policy_readback"),
        ({"powershell": {"session_id": 9}}, "session_or_creation_order_mismatch"),
    ],
)
def test_live_lineage_mutations_fail_before_reservation_or_process(
    tmp_path: Path, record_changes: dict[str, dict[str, Any]], error: str
) -> None:
    store = _Store()
    runner_construction: list[int] = []
    config = _config(tmp_path)
    with pytest.raises(qualifier.WindowsQualificationError, match=error):
        qualifier._run_internal_non_authoritative_once_for_test(
            config,
            lineage_probe=lambda: _live_lineage(config, **record_changes),
            runner_factory=lambda contract: runner_construction.append(1),
            store=store,
        )
    assert store.reservations == []
    assert runner_construction == []


def test_live_entry_accepts_config_only_and_cli_cannot_reach_it() -> None:
    assert tuple(inspect.signature(qualifier.run_internal_non_authoritative_once).parameters) == (
        "config",
        "work_order",
    )
    source = SCRIPT.read_text(encoding="utf-8")
    main_source = source[source.index("def main(") :]
    assert "run_internal_non_authoritative_once" not in main_source
    assert "_measure_live_lineage()" in inspect.getsource(
        qualifier.run_internal_non_authoritative_once
    )


def test_internal_actual_entry_requires_typed_work_order_before_lineage_or_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        qualifier,
        "_measure_live_lineage",
        lambda: pytest.fail("lineage must not run without a verified work order"),
    )
    with pytest.raises(
        qualifier.WindowsQualificationError, match="verified_internal_work_order_required"
    ) as caught:
        qualifier.run_internal_non_authoritative_once(_config(tmp_path))
    assert caught.value.stage == "internal_work_order"
    assert caught.value.counts.reservation == 0
    assert caught.value.counts.process_creation == 0
    assert caught.value.counts.runner_invocation == 0


def test_internal_actual_entry_rejects_forged_work_order_before_lineage_or_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        qualifier,
        "_measure_live_lineage",
        lambda: pytest.fail("lineage must not run for a forged token"),
    )
    with pytest.raises(
        qualifier.WindowsQualificationError, match="verified_internal_work_order_required"
    ) as caught:
        qualifier.run_internal_non_authoritative_once(_config(tmp_path), work_order=object())
    assert caught.value.stage == "internal_work_order"
    assert caught.value.counts == qualifier.QualificationCallCounts()


def test_internal_actual_entry_orders_typed_gate_before_lineage_and_reservation() -> None:
    source = inspect.getsource(qualifier.run_internal_non_authoritative_once)
    assert source.index("require_verified_qualification_work_order") < source.index(
        "_measure_live_lineage()"
    )
    assert source.index("require_verified_qualification_work_order") < source.index(
        "FileQualificationStore"
    )
    assert "INTERNAL_PREIMPORT_OUTER_CONFIGURED" not in SCRIPT.read_text(encoding="utf-8")


def test_direct_exec_bootstrap_gate_precedes_project_imports() -> None:
    isolated = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={PYCACHE_PREFIX}",
            str(SCRIPT),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert isolated.returncode == 2
    assert json.loads(isolated.stderr)["stage"] == "root_gate"
    nonisolated = subprocess.run(
        [
            sys.executable,
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={PYCACHE_PREFIX}",
            str(SCRIPT),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert nonisolated.returncode != 0
    assert "pre_r8_r7s7_qualifier_requires_python_i_b_s" in nonisolated.stderr
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.index("_REQUIRED_BOOTSTRAP_FLAGS") < source.index("sys.path.insert")
    assert source.index("sys.pycache_prefix") < source.index("sys.path.insert")
    assert source.index("_REQUIRED_BOOTSTRAP_FLAGS") < source.index("from evm.scale_validation")
    fixture_source = FIXTURE.read_text(encoding="utf-8")
    assert fixture_source.index("_REQUIRED_BOOTSTRAP_FLAGS") < fixture_source.index(
        "sys.path.insert"
    )
    assert fixture_source.index("sys.pycache_prefix") < fixture_source.index("sys.path.insert")
    assert fixture_source.index("_REQUIRED_BOOTSTRAP_FLAGS") < fixture_source.index(
        "from evm.scale_validation"
    )


def test_run_unique_pycache_prefix_bypasses_existing_source_local_pyc(
    tmp_path: Path,
) -> None:
    outer_prefix = Path(sys.pycache_prefix) if sys.pycache_prefix else None

    def outer_prefix_snapshot() -> tuple[tuple[str, str], ...]:
        if outer_prefix is None or not outer_prefix.exists():
            return ()
        return tuple(
            (str(path.relative_to(outer_prefix)), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(outer_prefix.rglob("*"))
            if path.is_file()
        )

    outer_prefix_before = outer_prefix_snapshot()
    module_dir = tmp_path / "ambient"
    module_dir.mkdir()
    source = module_dir / "victim.py"
    source.write_text("VALUE = 'malicious-pyc'\n", encoding="utf-8")
    local_cache = module_dir / "__pycache__"
    local_cache.mkdir()
    local_pyc = local_cache / f"victim.{sys.implementation.cache_tag}.pyc"
    py_compile.compile(
        str(source),
        cfile=str(local_pyc),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    source.write_text("VALUE = 'pinned-source'\n", encoding="utf-8")
    fresh_prefix = tmp_path / "fresh-prefix-must-remain-absent"
    code = (
        "import pathlib,sys;"
        f"sys.path.insert(0,{str(module_dir)!r});"
        "import victim;"
        "print(victim.VALUE);"
        "assert sys.dont_write_bytecode;"
        f"assert sys.pycache_prefix == {str(fresh_prefix)!r};"
        f"assert not pathlib.Path({str(fresh_prefix)!r}).exists()"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={fresh_prefix}",
            "-c",
            code,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "pinned-source\n"
    assert not fresh_prefix.exists()
    assert outer_prefix_snapshot() == outer_prefix_before


@pytest.mark.parametrize(
    "missing_flag",
    ["isolated", "no_user_site", "no_site", "dont_write_bytecode"],
)
def test_actual_bootstrap_rejects_missing_i_b_s_semantics(
    tmp_path: Path, missing_flag: str
) -> None:
    config = _config(tmp_path)
    values = {
        "isolated": 1,
        "no_user_site": 1,
        "no_site": 1,
        "dont_write_bytecode": 1,
    }
    values[missing_flag] = 0
    with pytest.raises(
        qualifier.WindowsQualificationError, match="isolated_i_b_s_bootstrap_required"
    ):
        qualifier._validate_internal_runtime_bootstrap(
            config,
            _live_lineage(config),
            flags=SimpleNamespace(**values),
        )


def test_module_origin_or_qualifier_sha_mutation_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(qualifier.WindowsQualificationError, match="qualifier_sha256_mismatch"):
        qualifier._validate_internal_runtime_bootstrap(
            replace(config, qualifier_sha256="0" * 64),
            _live_lineage(config),
        )


def test_ambient_or_editable_import_origin_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    ambient = tmp_path / "ambient-checkout" / "phase_b2_r7s3_process.py"
    ambient.parent.mkdir()
    ambient.write_bytes(Path(process.__file__).read_bytes())
    monkeypatch.setattr(qualifier.process_module, "__file__", str(ambient))
    with pytest.raises(
        qualifier.WindowsQualificationError, match="isolated_module_origin_mismatch"
    ):
        qualifier._validate_internal_runtime_bootstrap(
            config,
            _live_lineage(config),
        )
    wrong_source = FIXTURE.resolve()
    with pytest.raises(
        qualifier.WindowsQualificationError, match="isolated_module_origin_mismatch"
    ):
        qualifier._validate_internal_runtime_bootstrap(
            replace(
                config,
                runner_source=str(wrong_source),
                runner_source_sha256=_sha(wrong_source),
            ),
            _live_lineage(config),
        )


def test_exact_lineage_path_and_sha_pins_reject_same_basename_swap(tmp_path: Path) -> None:
    config = _config(tmp_path)
    alternate = tmp_path / "alternate" / "powershell.exe"
    alternate.parent.mkdir()
    alternate.write_bytes(POWERSHELL.read_bytes())
    with pytest.raises(
        qualifier.WindowsQualificationError, match="powershell_path_or_sha_pin_mismatch"
    ):
        qualifier._validate_internal_runtime_bootstrap(
            config,
            _live_lineage(
                config,
                powershell={
                    "path": str(alternate.resolve()),
                    "image_sha256": _sha(alternate),
                },
            ),
        )
    with pytest.raises(qualifier.WindowsQualificationError, match="codex_path_or_sha_pin_mismatch"):
        qualifier._validate_internal_runtime_bootstrap(
            config,
            _live_lineage(config, codex={"image_sha256": "0" * 64}),
        )


def test_fixture_tool_pin_mutation_is_rejected_and_failure_sealed(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["tool_pins"]["command_processor"]["sha256"] = "0" * 64
    stdout = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="fixture_tool_pin_mismatch"):
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(replace(_outcome(), stdout=stdout)),
            store=store,
        )
    assert len(store.failures) == 1
    assert json.loads(store.failures[0])["call_counts"]["automatic_retry"] == 0


def test_fixture_pin_verifier_rejects_mutation_without_spawning(tmp_path: Path) -> None:
    prefix = tmp_path / "fixture-fresh-prefix"
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={prefix}",
            str(FIXTURE),
            "--mode",
            "sleep",
            "--run-uuid",
            RUN_UUID,
            "--pycache-prefix",
            str(prefix),
            "--interpreter-sha256",
            _sha(Path(sys.executable).resolve()),
            "--fixture-sha256",
            "0" * 64,
            "--command-processor",
            str(COMMAND_PROCESSOR.resolve()),
            "--command-processor-sha256",
            _sha(COMMAND_PROCESSOR),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode != 0
    assert "fixture_sha256_mismatch" in completed.stderr
    assert not prefix.exists()


class _PartialObservation:
    def __init__(
        self,
        partial_path: Path,
        stage: str = "atomic_rename",
        *,
        rename_completed: bool = False,
    ) -> None:
        self.partial_path = partial_path
        self.stage = stage
        self.rename_completed = rename_completed

    def to_dict(self) -> dict[str, Any]:
        raw = self.partial_path.read_bytes()
        return {
            "stage": self.stage,
            "temporary_leaf": self.partial_path.name,
            "intended_final_path": str(self.partial_path.with_suffix(".final")),
            "rename_completed": self.rename_completed,
            "observation_status": "partial_preserved",
            "current_sha256": hashlib.sha256(raw).hexdigest(),
            "current_bytes": len(raw),
            "expected_sha256": "f" * 64,
            "expected_bytes": 99,
            "current_identity": {"volume_serial": 1, "file_id": "partial"},
        }


class _PublicationFailure(RuntimeError):
    def __init__(self, observation: _PartialObservation) -> None:
        super().__init__("injected_publication_failure")
        self.observation = observation


def test_fresh_reservation_publication_failure_gets_parent_seal_with_partial_sha(
    tmp_path: Path,
) -> None:
    partial = tmp_path / "reservation.partial"
    partial.write_bytes(b"reservation-partial")

    class ReservationFailureStore(_Store):
        def reserve_once(self, run_uuid: str, attempt_uuid: str) -> Any:
            self.reservations.append((run_uuid, attempt_uuid))
            raise _PublicationFailure(_PartialObservation(partial, "reservation_write"))

    store = ReservationFailureStore()
    runner_calls: list[int] = []
    with pytest.raises(qualifier.WindowsQualificationError, match="reservation_failed") as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: runner_calls.append(1),
            store=store,
        )
    assert partial.read_bytes() == b"reservation-partial"
    assert runner_calls == []
    assert len(store.pre_reservation_failures) == 1
    seal = json.loads(store.pre_reservation_failures[0])
    observed = seal["reservation_partial_artifact"]
    assert observed["current_sha256"] == hashlib.sha256(partial.read_bytes()).hexdigest()
    assert observed["partial_preserved_unmodified"] is True
    assert observed["cleanup_attempted"] is False
    assert seal["namespace_touch_observation"]["publication_state"] == "partial"
    assert seal["namespace_touch_observation"]["reservation_parent_namespace_touched"] is True
    assert seal["existing_run_namespace_touched"] == "unproven"
    assert seal["call_counts"]["reservation"] == 1
    assert seal["call_counts"]["process_creation"] == 0
    assert seal["call_counts"]["failure_seal_publication"] == 1
    assert seal["automatic_retry_count"] == 0
    assert seal["success_marker_count"] == 0
    assert seal["completion_marker_count"] == 0
    assert caught.value.counts.process_creation == 0


def test_reservation_namespace_observation_distinguishes_final_rename(
    tmp_path: Path,
) -> None:
    final = tmp_path / "reservation.final"
    final.write_bytes(b"renamed")
    observation = qualifier._reservation_namespace_observation(
        _PublicationFailure(_PartialObservation(final, "verify_final", rename_completed=True))
    )
    assert observation["publication_state"] == "final_renamed"
    assert observation["reservation_parent_namespace_touched"] is True
    assert observation["existing_run_namespace_touched"] == "unproven"


def test_pre_reservation_failure_seal_failure_uses_parent_emergency_once(
    tmp_path: Path,
) -> None:
    partial = tmp_path / "pre-reservation-seal.partial"
    partial.write_bytes(b"pre-reservation-seal-partial")

    class PreSealFailureStore(_Store):
        def reserve_once(self, run_uuid: str, attempt_uuid: str) -> Any:
            self.reservations.append((run_uuid, attempt_uuid))
            raise qualifier.WindowsQualificationError(
                "run_uuid_already_reserved", stage="reservation"
            )

        def publish_pre_reservation_failure_once(
            self, run_uuid: str, attempt_uuid: str, raw: bytes
        ) -> Any:
            self.pre_reservation_failures.append(raw)
            raise _PublicationFailure(_PartialObservation(partial, "parent_failure_seal"))

    store = PreSealFailureStore()
    with pytest.raises(
        qualifier.WindowsQualificationError,
        match="pre_reservation_failure_seal_publication_failed",
    ) as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: pytest.fail("runner construction forbidden"),
            store=store,
        )
    assert partial.read_bytes() == b"pre-reservation-seal-partial"
    assert len(store.pre_reservation_failures) == 1
    assert len(store.pre_reservation_emergencies) == 1
    emergency = json.loads(store.pre_reservation_emergencies[0])
    assert emergency["schema"] == qualifier.PRE_RESERVATION_EMERGENCY_SEAL_SCHEMA
    assert emergency["existing_run_namespace_touched"] is False
    assert (
        emergency["failure_seal_partial_artifact"]["current_sha256"]
        == hashlib.sha256(partial.read_bytes()).hexdigest()
    )
    assert emergency["call_counts"]["reservation"] == 1
    assert emergency["call_counts"]["process_creation"] == 0
    assert emergency["call_counts"]["failure_seal_publication"] == 1
    assert emergency["call_counts"]["emergency_seal_publication"] == 1
    assert emergency["automatic_retry_count"] == 0
    assert caught.value.emergency_publication is not None


def test_evidence_publication_failure_is_sealed_with_partial_sha(tmp_path: Path) -> None:
    partial = tmp_path / "evidence.partial"
    partial.write_bytes(b"evidence-partial")

    class EvidenceFailureStore(_Store):
        def publish_evidence_once(self, run_uuid: str, raw: bytes) -> Any:
            self.evidence.append(raw)
            raise _PublicationFailure(_PartialObservation(partial, "evidence_rename"))

    store = EvidenceFailureStore()
    with pytest.raises(qualifier.WindowsQualificationError, match="qualification_failed") as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(_outcome()),
            store=store,
        )
    assert partial.read_bytes() == b"evidence-partial"
    assert len(store.evidence) == 1
    assert len(store.failures) == 1
    seal = json.loads(store.failures[0])
    assert (
        seal["partial_artifact"]["current_sha256"]
        == hashlib.sha256(partial.read_bytes()).hexdigest()
    )
    assert seal["partial_artifact"]["cleanup_attempted"] is False
    assert seal["call_counts"]["evidence_publication"] == 1
    assert seal["call_counts"]["failure_seal_publication"] == 1
    assert caught.value.counts.automatic_retry == 0


def test_process_containment_failure_preserves_residual_drain_and_job_accounting(
    tmp_path: Path,
) -> None:
    containment = process.ProcessContainmentFailure(
        "injected_containment_failure",
        name=f"pre-r8-r7s7-windows-{RUN_UUID}",
        stage="residual_wait",
        run_uuid=RUN_UUID,
        root_pid=777,
        child_created=True,
        job_membership_verified=True,
        root_resumed=True,
        residual_pids=(777, 778),
        stdout="partial stdout",
        stderr="partial stderr",
        stdout_drained=True,
        stderr_drained=False,
        events=(process.JobEvent(1, "root_resumed", 1, STAMP, pid=777),),
        identities=(_identity(777, os.getpid(), r"C:\Python\python.exe", 1),),
        accounting=(process.JobAccountingSnapshot(2, 2, STAMP, 2, 2, 0, (777, 778)),),
        errors=("residual_processes_observed",),
        stream_cleanup={"complete": False},
        timed_out=True,
    )

    class RaisingRunner:
        def run(self, command: list[str], **kwargs: Any) -> Any:
            raise containment

    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="qualification_failed") as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: RaisingRunner(),
            store=store,
        )
    assert len(store.failures) == 1
    seal = json.loads(store.failures[0])
    preserved = seal["process_containment_failure"]
    assert preserved == json.loads(json.dumps(containment.to_dict()))
    assert preserved["process_evidence"]["residual_pids"] == [777, 778]
    assert preserved["process_evidence"]["stdout_drained"] is True
    assert preserved["process_evidence"]["stderr_drained"] is False
    assert preserved["process_evidence"]["accounting"][0]["active_processes"] == 2
    assert preserved["safe_for_followup"] is False
    assert preserved["forced_termination_attempts"] == 0
    assert seal["status"] == "manual_intervention_required"
    assert seal["decision"] == "NO-GO"
    assert seal["call_counts"]["process_creation_requested"] == 1
    assert seal["call_counts"]["process_creation"] == 1
    assert seal["call_counts"]["child_created_observed"] is True
    assert caught.value.classification == "manual_intervention_required"
    assert caught.value.counts.followup_probe == 0
    assert caught.value.counts.automatic_retry == 0


def test_runner_exception_before_child_telemetry_does_not_invent_process_creation(
    tmp_path: Path,
) -> None:
    class RaisingBeforeCreateRunner:
        def run(self, command: list[str], **kwargs: Any) -> Any:
            raise RuntimeError("pre_create_gate_failed")

    store = _Store()
    with pytest.raises(qualifier.WindowsQualificationError, match="qualification_failed") as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: RaisingBeforeCreateRunner(),
            store=store,
        )
    seal = json.loads(store.failures[0])
    assert seal["call_counts"]["runner_invocation"] == 1
    assert seal["call_counts"]["process_creation_requested"] == 1
    assert seal["call_counts"]["process_creation"] == 0
    assert seal["call_counts"]["child_created_observed"] is None
    assert caught.value.counts.process_creation == 0
    assert caught.value.counts.automatic_retry == 0
    assert caught.value.counts.followup_probe == 0
    assert caught.value.counts.force_termination == 0


def test_failure_seal_failure_writes_emergency_with_partial_sha(tmp_path: Path) -> None:
    partial = tmp_path / "failure.partial"
    partial.write_bytes(b"immutable-partial")

    class FailureStore(_Store):
        def publish_failure_once(self, run_uuid: str, raw: bytes) -> Any:
            self.failures.append(raw)
            raise _PublicationFailure(_PartialObservation(partial))

    store = FailureStore()
    without_conhost = replace(_outcome(), identities=_outcome().identities[:-1])
    with pytest.raises(
        qualifier.WindowsQualificationError, match="failure_seal_publication_failed"
    ) as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(without_conhost),
            store=store,
        )
    assert partial.read_bytes() == b"immutable-partial"
    assert len(store.failures) == 1
    assert len(store.emergencies) == 1
    emergency = json.loads(store.emergencies[0])
    observed = emergency["failure_seal_partial_artifact"]
    assert observed["current_sha256"] == hashlib.sha256(partial.read_bytes()).hexdigest()
    assert observed["partial_preserved_unmodified"] is True
    assert observed["cleanup_attempted"] is False
    assert caught.value.counts.failure_seal_publication == 1
    assert caught.value.counts.emergency_seal_publication == 1
    assert caught.value.counts.automatic_retry == 0
    assert caught.value.emergency_publication is not None


def test_emergency_failure_is_fail_closed_without_retry(tmp_path: Path) -> None:
    partial = tmp_path / "both.partial"
    partial.write_bytes(b"partial")

    class BrokenStore(_Store):
        def publish_failure_once(self, run_uuid: str, raw: bytes) -> Any:
            self.failures.append(raw)
            raise _PublicationFailure(_PartialObservation(partial, "failure_seal"))

        def publish_emergency_once(self, run_uuid: str, raw: bytes) -> Any:
            self.emergencies.append(raw)
            raise _PublicationFailure(_PartialObservation(partial, "emergency_seal"))

    store = BrokenStore()
    with pytest.raises(
        qualifier.WindowsQualificationError, match="emergency_seal_publication_failed"
    ) as caught:
        qualifier._qualify_windows_non_credit_for_test(
            _config(tmp_path),
            _approval(),
            runner_factory=lambda contract: _Runner(
                replace(_outcome(), timed_out=True, manual_intervention_required=True)
            ),
            store=store,
        )
    assert partial.read_bytes() == b"partial"
    assert len(store.failures) == 1
    assert len(store.emergencies) == 1
    assert caught.value.counts.failure_seal_publication == 1
    assert caught.value.counts.emergency_seal_publication == 1
    assert caught.value.counts.automatic_retry == 0
    assert caught.value.counts.force_termination == 0


@pytest.mark.parametrize(
    "mutation",
    [
        {"directory_flush_count": 0, "directory_flush_succeeded": False},
        {"file_flush_count": 0},
        {"same_handle_readback": False},
        {"file_identity_stable_across_rename": False},
        {"replace_if_exists": True},
        {"sha256": "0" * 64},
        {"bytes": 2},
    ],
)
def test_durable_publication_contract_rejects_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, Any],
) -> None:
    identity = SimpleNamespace(to_dict=lambda: {"volume_serial": 1, "file_id": "x"})
    published = SimpleNamespace(
        sha256=hashlib.sha256(b"x").hexdigest(),
        bytes=1,
        replace_if_exists=False,
        same_handle_readback=True,
        file_identity_stable_across_rename=True,
        file_flush_count=2,
        directory_flush_count=1,
        directory_flush_succeeded=True,
        final_path=str(tmp_path / "final"),
        identity=identity,
        directory_identity=identity,
    )
    for key, value in mutation.items():
        setattr(published, key, value)
    monkeypatch.setattr(
        qualifier, "publish_bound_no_replace_durable", lambda *args, **kwargs: published
    )
    with pytest.raises(
        qualifier.WindowsQualificationError, match="durable_publication_contract_mismatch"
    ):
        qualifier.FileQualificationStore._as_publication(published, b"x")


def test_cli_is_fail_closed_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert qualifier.main([]) == 2
    output = json.loads(capsys.readouterr().err)
    assert output["decision"] == "NO-GO"
    assert output["credit"] == "zero_credit"
    assert output["call_counts"]["process_creation"] == 0


def test_candidate_uses_existing_hardened_job_runner_and_has_no_force_path() -> None:
    assert qualifier.WindowsJobProcessRunner is process.WindowsJobProcessRunner
    source = SCRIPT.read_text(encoding="utf-8")
    fixture_source = FIXTURE.read_text(encoding="utf-8")
    forbidden = ("TerminateJobObject", "KILL_ON_JOB_CLOSE", "taskkill", "force-kill")
    assert all(token not in source for token in forbidden)
    assert all(token not in fixture_source for token in forbidden)
    assert ".terminate(" not in source
    assert ".terminate(" not in fixture_source
    assert ".unlink(" not in source
    assert "os.link(" not in source
    assert fixture_source.count("*_python_prefix_argv(pycache_prefix)") == 3
    assert 'return ["-I", "-B", "-S", "-X", f"pycache_prefix={pycache_prefix}"]' in (fixture_source)
    assert 'parser.add_argument("--pycache-prefix", required=True)' in fixture_source
    assert "publish_bound_no_replace_durable" in source


def test_trusted_outer_retains_exact_transitive_closure_and_is_non_destructive() -> None:
    source = OUTER.read_text(encoding="utf-8")
    required_roles = {
        "admission_source",
        "codex",
        "command_processor",
        "evm_package_init_source",
        "fixture",
        "interpreter",
        "powershell",
        "preparer",
        "qualifier",
        "r7s3_handle_io_source",
        "r7s4_handle_io_source",
        "runner_source",
        "scale_validation_package_init_source",
        "trusted_outer",
        "work_order_gate",
    }
    for role in required_roles:
        assert f"'{role}'" in source
    assert "FileShare]::Read" in source
    assert "read_only_no_write_no_delete" in source
    assert "retained_handle_sha_changed" in source
    assert "retained_work_order_sha_changed" in source
    assert "verify_internal_qualification_work_order" in source
    assert "qualification_config_projection" in source
    assert "run_internal_non_authoritative_once(config, work_order=token)" in source
    assert "'-I' '-B' '-S' '-X' \"pycache_prefix=$pycachePrefix\"" in source
    assert "pycache_prefix_postcondition_violated" in source
    assert 'assert_directory_identity(order_json["output_parent_identity"])' in source
    assert 'assert_directory_identity(order_json["pycache_parent_identity"])' in source
    assert 'assert_directory_identity(order_json["work_order_parent_identity"])' in source
    assert "for role in gate.FILE_BINDING_ROLES:" in source
    assert "retained_file_identity_mismatch" in source
    forbidden = (
        "Remove-Item",
        "Move-Item",
        "Start-Process",
        "Invoke-Expression",
        "taskkill",
        "TerminateProcess",
        "TerminateJobObject",
    )
    assert all(item not in source for item in forbidden)
    assert source.count("qualification_bootstrap_failed") == 1


def test_trusted_outer_has_no_live_execution_in_python_test_contract() -> None:
    source = OUTER.read_text(encoding="utf-8")
    assert "[Parameter(Mandatory = $true)]" in source
    assert "ExpectedOuterSha256" in source
    assert "ExpectedWorkOrderSha256" in source
    assert "ConvertFrom-Json" in source
    assert "finally {" in source
    assert "$openHandles[$index].Dispose()" in source
    assert "$directoryGuards[$index]" in source
    assert "preimport_directory_identity_guards" in source
    assert "Open-AncestorDirectoryGuards" in source
    assert "retained_no_delete_share = $true" in source
    assert "security_descriptor_sha256" in source
    assert "Assert-IdentityMatchesExpected" in source
    assert "retained_identity_field_mismatch" in source


@pytest.mark.parametrize(
    ("failure_case", "required_source"),
    [
        ("work_order_sha_mismatch", "work_order_sha256"),
        ("outer_sha_mismatch", "trusted_outer_sha256"),
        ("source_pin_mismatch", "source_tool_pin"),
        ("python_launch_failure", "python_launch_or_child_execution"),
        ("child_nonzero", "qualification_bootstrap_failed"),
        ("failure_seal_collision", "outer_${Kind}_seal_collision"),
        ("failure_seal_write_failure", '"${Kind}_write"'),
        ("failure_seal_rename_failure", '"${Kind}_atomic_move_no_replace"'),
    ],
)
def test_trusted_outer_failure_cases_enter_seal_state_machine(
    failure_case: str, required_source: str
) -> None:
    del failure_case
    source = OUTER.read_text(encoding="utf-8")
    assert required_source in source
    assert "Publish-OuterTerminalSeal -Kind 'failure'" in source
    assert "-Kind 'emergency'" in source
    assert "unsealed_manual_intervention_required" in source
    assert "failure_seal_attempt_count = 1" in source
    assert "emergency_seal_attempt_count = 1" in source


def test_trusted_outer_seals_are_canonical_atomic_no_replace_and_redacted() -> None:
    source = OUTER.read_text(encoding="utf-8")
    assert "$canonicalSealRoot = 'F:\\EnterpriseMLOps_Data" in source
    assert "ExpectedCanonicalRootVolumeSerial" in source
    assert "ExpectedCanonicalRootFileIdHex" in source
    assert "ExpectedCanonicalRootSecurityDescriptorSha256" in source
    assert "DefineDynamicAssembly" in source
    assert "Add-InMemoryPInvoke" in source
    assert "Add-Type" not in source
    assert "FILE_FLAG_OPEN_REPARSE_POINT" in source
    assert "canonical_seal_root_reparse_forbidden" in source
    assert "bound_create_new" in source
    assert "$stream.Flush($true)" in source
    assert "SetFileInformationByHandle" in source
    assert "Rename-BoundNoReplace" in source
    assert "[System.IO.File]::Move" not in source
    assert "FileShare]::Delete" not in source
    assert "Open-AncestorDirectoryGuards -Path $resolved -Role 'canonical_seal_root'" in source
    assert "0x40000000 -bor 0x00000001" in source
    assert "same_handle_pre_and_post_rename_readback = $true" in source
    assert "same_handle_final_path_readback = $true" in source
    assert "Flush-DirectoryGuard $guard" in source
    assert "Get-OuterPartialObservation" in source
    assert "partial_artifact" in source
    assert "preserved_unmodified = $true" in source
    assert "cleanup_attempted = $false" in source
    assert "raw_work_order_recorded = $false" in source
    assert "secret_recorded = $false" in source
    assert "nonce_recorded = $false" in source
    assert "command_line_recorded = $false" in source
    assert "automatic_retry = 0" in source
    assert "followup_probe = 0" in source
    assert "force_termination = 0" in source
    assert "success_marker = 0" in source
    assert "completion_marker = 0" in source
    assert "overwrite = 0" in source
    assert "delete = 0" in source
    assert "Remove-Item" not in source
    assert "Move-Item" not in source


def test_trusted_outer_work_order_transport_is_bounded_and_not_raw_argv() -> None:
    source = OUTER.read_text(encoding="utf-8")
    assert "rawBase64" not in source
    assert "expectationBase64" not in source
    assert "ToBase64String" not in source
    assert "import base64" not in source
    assert "$workOrderHandle.Path" in source
    assert "work_order_path.read_bytes()" in source
    bootstrap = source.split("$bootstrap = @'", 1)[1].split("'@", 1)[0]
    assert len(bootstrap) < 20_000
    assert "toolchain_runtime_closure_unproven" in source


def test_trusted_outer_rejects_copied_work_order_origin_before_any_import() -> None:
    source = OUTER.read_text(encoding="utf-8")
    helper = source.split("function Assert-WorkOrderOriginBound", 1)[1].split("\n}\n\ntry {", 1)[0]
    assert "HandleRecord.Path" in helper
    assert "HandleRecord.Identity.final_path" in helper
    assert "DeclaredParentIdentity.final_path" in helper
    assert "canonicalWorkOrderRoot" in helper
    assert "work_order_declared_path_not_canonical" in helper
    assert "work_order_handle_path_origin_mismatch" in helper
    assert "work_order_handle_parent_origin_mismatch" in helper
    origin_gate = source.index("$outerStage = 'work_order_origin_binding'")
    parent_guard = source.index("-Role 'work_order_parent'", origin_gate)
    source_pins = source.index("$outerStage = 'source_tool_pin'")
    runtime_import = source.index("import hashlib, importlib")
    assert origin_gate < parent_guard < source_pins < runtime_import
    assert "-DeclaredPath ([string]$workOrder.work_order_path)" in source
    assert "-DeclaredParentIdentity $workOrder.work_order_parent_identity" in source


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL.is_file(), reason="Windows-only outer gate")
def test_trusted_outer_actual_origin_gate_rejects_canonical_bytes_at_copied_path(
    tmp_path: Path,
) -> None:
    source = OUTER.read_text(encoding="utf-8")
    start = source.index("function Test-IsFullyQualifiedWindowsPath")
    end = source.index("\ntry {", start)
    gate_functions = source[start:end]
    script = tmp_path / "copied-work-order-origin.ps1"
    script.write_text(
        "\n".join(
            (
                "Set-StrictMode -Version Latest",
                "$ErrorActionPreference = 'Stop'",
                f"$ExpectedRunUuid = '{RUN_UUID}'",
                f"$ExpectedAttemptUuid = '{ATTEMPT_UUID}'",
                "$canonicalWorkOrderRoot = 'C:\\trusted-work-orders'",
                gate_functions,
                f"$leaf = 'windows-qualification-work-order-{RUN_UUID}-{ATTEMPT_UUID}.json'",
                "$declared = [IO.Path]::Combine($canonicalWorkOrderRoot, $leaf)",
                "$record = [pscustomobject]@{Path=[IO.Path]::Combine('C:\\copied', $leaf);Identity=[pscustomobject]@{final_path=[IO.Path]::Combine('C:\\copied', $leaf)}}",
                "$parent = [pscustomobject]@{final_path=$canonicalWorkOrderRoot}",
                "try { Assert-WorkOrderOriginBound -HandleRecord $record -DeclaredPath $declared -DeclaredParentIdentity $parent; exit 9 }",
                "catch { if ($_.Exception.Message -cne 'work_order_handle_path_origin_mismatch') { throw }; [Console]::Out.WriteLine($_.Exception.Message) }",
            )
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "work_order_handle_path_origin_mismatch"


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL.is_file(), reason="Windows-only native smoke")
def test_outer_native_failure_seal_publication_smoke_uses_same_handle_no_replace(
    tmp_path: Path,
) -> None:
    source = OUTER.read_text(encoding="utf-8")
    start = source.index("$assemblyName =")
    end = source.index("\ntry {\n    $outerStage = 'oob_identity_validation'")
    definitions = source[start:end]
    seal_root = tmp_path / "outer-seal-root"
    seal_root.mkdir()
    smoke_path = tmp_path / "outer-seal-smoke.ps1"
    smoke_path.write_text(
        "\n".join(
            (
                "param([Parameter(Mandatory=$true)][string]$Root)",
                "Set-StrictMode -Version Latest",
                "$ErrorActionPreference = 'Stop'",
                "$canonicalSealRoot = [IO.Path]::GetFullPath($Root)",
                "$outerStage = 'native_smoke'",
                "$outerProcessLaunchAttempts = 0",
                "$terminalSealPublicationStage = 'not_started'",
                "$FILE_FLAG_OPEN_REPARSE_POINT = [UInt32]0x00200000",
                "$FILE_FLAG_BACKUP_SEMANTICS = [UInt32]0x02000000",
                definitions,
                "$acl = Get-Acl -LiteralPath $canonicalSealRoot",
                "$acl.SetAccessRuleProtection($true, $true)",
                "Set-Acl -LiteralPath $canonicalSealRoot -AclObject $acl",
                "$probe = Open-MeasuredDirectoryGuard -Path $canonicalSealRoot -Role 'smoke-root'",
                "$ExpectedCanonicalRootVolumeSerial = $probe.Identity.volume_serial_number",
                "$ExpectedCanonicalRootFileIdHex = $probe.Identity.file_id_hex",
                "$ExpectedCanonicalRootSecurityDescriptorSha256 = $probe.Identity.security_descriptor_sha256",
                f"$ExpectedRunUuid = '{RUN_UUID}'",
                f"$ExpectedAttemptUuid = '{ATTEMPT_UUID}'",
                "$payload = [ordered]@{schema='smoke.v1';status='failed';decision='NO-GO';qualification_child_process_count=0}",
                "try {",
                "  $publication = Publish-OuterTerminalSeal -Kind 'failure' -Payload $payload",
                "  $raw = [IO.File]::ReadAllBytes($publication.path)",
                "  if ((Get-BytesSha256 $raw) -cne $publication.sha256) { throw 'smoke_sha_mismatch' }",
                "  if ($publication.atomic_move_no_replace_count -ne 1) { throw 'smoke_rename_count' }",
                "  if ($publication.directory_flush_count -ne 1) { throw 'smoke_directory_flush_count' }",
                "  if ($publication.same_handle_final_path_readback -ne $true) { throw 'smoke_handle_readback' }",
                "  [Console]::Out.WriteLine(($publication | ConvertTo-Json -Compress))",
                "}",
                "finally {",
                "  for ($index=$directoryGuards.Count-1; $index -ge 0; $index--) { Close-DirectoryGuard $directoryGuards[$index] }",
                "}",
            )
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(smoke_path),
            "-Root",
            str(seal_root),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    publication = json.loads(completed.stdout)
    assert publication["atomic_move_no_replace_count"] == 1
    assert publication["directory_flush_count"] == 1
    assert publication["same_handle_pre_and_post_rename_readback"] is True
    assert publication["same_handle_final_path_readback"] is True
    assert publication["same_handle_file_identity_continuity"] is True
    before = publication["pre_rename_file_identity"]
    after = publication["post_rename_file_identity"]
    for key in (
        "volume_serial_number",
        "file_id_hex",
        "owner_sid",
        "security_descriptor_sha256",
        "dacl_present",
        "dacl_protected",
        "link_count",
        "reparse_tag",
        "file_type",
        "creation_time_ns",
        "is_directory",
    ):
        assert before[key] == after[key]
    assert publication["publication_directory_identity"]["is_directory"] is True
    assert publication["publication_directory_identity"]["reparse_tag"] == 0
    assert publication["target_namespace_guarded_by_retained_handles"] is True
    assert publication["path_rename_fallback_count"] == 0
    assert publication["overwrite_count"] == 0
    assert publication["delete_count"] == 0
    assert list(seal_root.glob("*.json")) == [Path(publication["path"])]
