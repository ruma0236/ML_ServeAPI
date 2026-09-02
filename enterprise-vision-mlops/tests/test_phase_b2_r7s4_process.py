from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import types
import uuid
from pathlib import Path

import pytest

from evm.scale_validation import phase_b2_r7s4_process as process


def _snapshot(*pids: int, total: int | None = None) -> dict[str, object]:
    ordered = sorted(pids)
    return {
        "is_process_in_job": True,
        "limit_flags": 0,
        "active_processes": len(ordered),
        "total_processes": total if total is not None else len(ordered),
        "terminated_processes": 0,
        "assigned_processes": len(ordered),
        "process_ids": ordered,
    }


def _executable_identity(path: str | None = None, *, sha256: str = "a" * 64) -> dict[str, object]:
    value = str(Path(path or sys.executable).resolve())
    return {
        "path": value,
        "final_path": value,
        "sha256": sha256,
        "bytes": 123,
        "volume_serial": 9,
        "file_id": 10,
        "file_attributes": 0x80,
        "write_sharing_allowed": False,
        "delete_sharing_allowed": False,
        "lease_held_through_create": True,
    }


class ChildApi:
    def __init__(self, *, approval: bytes = process.APPROVAL_BYTE) -> None:
        self.bootstrap_pid = os.getpid()
        self.payload_pid = self.bootstrap_pid + 10_000
        self.approval = approval
        self.created = 0
        self.resumed = 0
        self.closed: list[int] = []
        self.cleared: list[int] = []
        self.payload_environment: dict[str, str] | None = None
        self.payload_command: tuple[str, ...] | None = None
        self.ack_raw = b""

    def clear_handle_inherit(self, handle: int) -> None:
        self.cleared.append(handle)

    def current_job_snapshot(self, _job: int | None) -> dict[str, object]:
        if self.created:
            return _snapshot(self.bootstrap_pid, self.payload_pid)
        return _snapshot(self.bootstrap_pid)

    def is_process_in_job(self, _process: int, _job: int | None) -> bool:
        return True

    def create_payload_suspended(
        self,
        *,
        command: tuple[str, ...],
        cwd: str | None,
        environment: dict[str, str],
        create_no_window: bool,
    ) -> types.SimpleNamespace:
        del cwd, create_no_window
        self.created += 1
        self.payload_environment = dict(environment)
        self.payload_command = tuple(command)
        return types.SimpleNamespace(
            hProcess=501,
            hThread=502,
            dwProcessId=self.payload_pid,
            dwThreadId=self.payload_pid + 1,
            executable_identity=_executable_identity(command[0]),
        )

    def write_ack(self, _handle: int, payload: bytes) -> None:
        self.ack_raw = payload

    def read_approval(self, _handle: int) -> bytes:
        return self.approval

    def resume(self, _thread: int) -> None:
        self.resumed += 1

    def wait_payload(self, _process: int) -> int:
        return 0

    def close(self, handle: int | None) -> None:
        if handle:
            self.closed.append(handle)

    def open_executable_lease(self, path: str) -> process.ExecutableLease:
        return process.ExecutableLease(handle=700, identity=_executable_identity(path), api=self)

    def verify_executable_path_binding(self, _identity: object) -> None:
        return None


def _child_environment(
    *,
    api: ChildApi,
    command: tuple[str, ...] | None = None,
    run_uuid: str | None = None,
    nonce: bytes | None = None,
) -> tuple[dict[str, str], str, bytes]:
    execution_uuid = run_uuid or str(uuid.uuid4())
    raw_nonce = nonce or bytes(range(32))
    payload = command or (str(Path(sys.executable).resolve()), "-c", "pass")
    source_sha = "a" * 64
    source_identity = _executable_identity(process.__file__, sha256=source_sha)
    r7s3_identity = _executable_identity(process.r7s3.__file__, sha256=source_sha)
    environment = {
        process.r7s3.RUN_UUID_ENV: execution_uuid,
        process.r7s3.JOB_CAPABILITY_HANDLE_ENV: "101",
        process.r7s3.JOB_CAPABILITY_NONCE_ENV: raw_nonce.hex(),
        process.r7s3.JOB_CAPABILITY_COMMITMENT_ENV: process.r7s3.job_capability_commitment(
            raw_nonce, execution_uuid
        ),
        process.ACK_HANDLE_ENV: "102",
        process.CONTROL_HANDLE_ENV: "103",
        process.ADMISSION_ID_ENV: str(uuid.uuid4()),
        process.PAYLOAD_ENVELOPE_ENV: process._encode_payload_envelope(payload, None, True),
        process.COMMAND_DIGEST_ENV: process.normalized_command_digest(payload),
        process.BOOTSTRAP_SHA256_ENV: source_sha,
        process.BOOTSTRAP_SOURCE_IDENTITY_ENV: process._encode_source_identity(source_identity),
        process.BOOTSTRAP_R7S3_IDENTITY_ENV: process._encode_source_identity(r7s3_identity),
        "R7S4_PUBLIC_SENTINEL": "preserved",
    }
    return environment, execution_uuid, raw_nonce


def test_child_consumes_private_environment_and_resumes_only_after_ack() -> None:
    api = ChildApi()
    environment, execution_uuid, raw_nonce = _child_environment(api=api)

    assert process.run_bootstrap_child(environment=environment, api=api) == 0

    assert api.created == 1
    assert api.resumed == 1
    assert set(api.cleared) == {101, 102, 103}
    assert api.payload_environment == {
        process.r7s3.RUN_UUID_ENV: execution_uuid,
        "R7S4_PUBLIC_SENTINEL": "preserved",
    }
    assert all(name not in environment for name in process._PRIVATE_ENVIRONMENT_NAMES)
    ack = json.loads(api.ack_raw)
    assert ack["raw_nonce_recorded"] is False
    assert raw_nonce.hex() not in api.ack_raw.decode("utf-8")
    assert ack["payload_pid"] == api.payload_pid
    assert ack["explicit_job"] == ack["null_job_observation"]
    assert ack["null_job_matches_explicit"] is True
    assert ack["payload_executable_identity"]["lease_held_through_create"] is True
    assert ack["nonce_commitment"] == process.r7s3.job_capability_commitment(
        raw_nonce, execution_uuid
    )


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda env: env.pop(process.r7s3.JOB_CAPABILITY_HANDLE_ENV),
            "job_capability_environment_fields_invalid",
        ),
        (
            lambda env: env.__setitem__(process.r7s3.JOB_CAPABILITY_HANDLE_ENV, "0"),
            "job_capability_handle_encoding_invalid",
        ),
        (
            lambda env: env.__setitem__(process.r7s3.JOB_CAPABILITY_COMMITMENT_ENV, "0" * 64),
            "job_capability_commitment_mismatch",
        ),
        (
            lambda env: env.__setitem__(process.CONTROL_HANDLE_ENV, env[process.ACK_HANDLE_ENV]),
            "bootstrap_private_handle_roles_not_unique",
        ),
        (
            lambda env: env.__setitem__(process.BOOTSTRAP_SHA256_ENV, "0" * 64),
            "bootstrap_source_sha256_mismatch",
        ),
        (
            lambda env: env.__setitem__(
                process.BOOTSTRAP_R7S3_IDENTITY_ENV,
                process._encode_source_identity(
                    _executable_identity(process.r7s3.__file__, sha256="b" * 64)
                ),
            ),
            "bootstrap_r7s3_identity_mismatch",
        ),
    ],
)
def test_missing_malformed_or_swapped_capability_never_resumes(mutate: object, match: str) -> None:
    api = ChildApi()
    environment, _, _ = _child_environment(api=api)
    mutate(environment)  # type: ignore[operator]

    with pytest.raises(process.R7S4ProcessError, match=match):
        process.run_bootstrap_child(environment=environment, api=api)

    assert api.resumed == 0
    assert not any(name in environment for name in process._PRIVATE_ENVIRONMENT_NAMES)


def test_missing_or_malformed_parent_approval_leaves_payload_suspended() -> None:
    api = ChildApi(approval=b"X")
    environment, _, _ = _child_environment(api=api)

    with pytest.raises(process.R7S4ProcessError, match="parent_approval_missing_or_invalid"):
        process.run_bootstrap_child(environment=environment, api=api)

    assert api.created == 1
    assert api.resumed == 0
    assert api.ack_raw.endswith(b"\n")


def test_nested_outer_null_job_mismatch_is_observed_not_used_as_identity() -> None:
    class MismatchApi(ChildApi):
        def current_job_snapshot(self, job: int | None) -> dict[str, object]:
            value = super().current_job_snapshot(job)
            if job is None:
                value["total_processes"] = 9
                value["limit_flags"] = 12288
            return value

    api = MismatchApi()
    environment, _, _ = _child_environment(api=api)

    assert process.run_bootstrap_child(environment=environment, api=api) == 0
    ack = json.loads(api.ack_raw)
    assert ack["null_job_matches_explicit"] is False
    assert ack["explicit_job"]["limit_flags"] == 0
    assert ack["null_job_observation"]["limit_flags"] == 12288
    assert ack["null_job_observation"]["total_processes"] == 9
    assert api.created == 1
    assert api.resumed == 1


def _valid_ack(expectation: process.BootstrapAckExpectation, payload_pid: int) -> dict[str, object]:
    snapshot = _snapshot(expectation.bootstrap_pid, payload_pid)
    return {
        "schema": process.ACK_SCHEMA,
        "run_uuid": expectation.run_uuid,
        "admission_id": expectation.admission_id,
        "nonce_commitment": expectation.nonce_commitment,
        "command_sha256": expectation.command_sha256,
        "bootstrap_sha256": expectation.bootstrap_sha256,
        "bootstrap_source_identity": dict(expectation.bootstrap_source_identity),
        "bootstrap_r7s3_identity": dict(expectation.bootstrap_r7s3_identity),
        "bootstrap_pid": expectation.bootstrap_pid,
        "payload_pid": payload_pid,
        "payload_executable_identity": _executable_identity(),
        "explicit_job": snapshot,
        "null_job_observation": snapshot,
        "null_job_matches_explicit": True,
        "payload_in_explicit_job": True,
        "environment_consumed": True,
        "raw_nonce_recorded": False,
    }


def _expectation() -> process.BootstrapAckExpectation:
    return process.BootstrapAckExpectation(
        run_uuid=str(uuid.uuid4()),
        admission_id=str(uuid.uuid4()),
        nonce_commitment=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        command_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        bootstrap_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        bootstrap_source_identity=_executable_identity(process.__file__),
        bootstrap_r7s3_identity=_executable_identity(process.r7s3.__file__),
        bootstrap_pid=300,
    )


def test_ack_replay_and_swaps_are_rejected() -> None:
    expectation = _expectation()
    consumed: set[str] = set()
    ack = _valid_ack(expectation, 301)
    assert (
        process.validate_bootstrap_ack(ack, expectation, consumed_admission_ids=consumed)[
            "payload_pid"
        ]
        == 301
    )
    with pytest.raises(process.R7S4ProcessError, match="bootstrap_ack_replay"):
        process.validate_bootstrap_ack(ack, expectation, consumed_admission_ids=consumed)

    other = _expectation()
    for field in ("run_uuid", "nonce_commitment", "command_sha256", "bootstrap_sha256"):
        swapped = _valid_ack(other, 401)
        swapped[field] = ack[field]
        with pytest.raises(process.R7S4ProcessError, match=f"bootstrap_ack_{field}_mismatch"):
            process.validate_bootstrap_ack(swapped, other, consumed_admission_ids=set())


def test_ack_malformed_job_or_schema_is_rejected() -> None:
    expectation = _expectation()
    ack = _valid_ack(expectation, 301)
    ack["unknown"] = True
    with pytest.raises(process.R7S4ProcessError, match="fields_invalid"):
        process.validate_bootstrap_ack(ack, expectation, consumed_admission_ids=set())

    ack = _valid_ack(expectation, 301)
    ack["null_job_observation"] = _snapshot(300, 999)
    with pytest.raises(process.R7S4ProcessError, match="null_job_matches_explicit"):
        process.validate_bootstrap_ack(ack, expectation, consumed_admission_ids=set())


def test_command_digest_binds_normalized_absolute_argv() -> None:
    executable = str(Path(sys.executable).resolve())
    assert process.normalized_command_digest((executable, "a", "b")) == (
        process.normalized_command_digest((os.path.abspath(executable), "a", "b"))
    )
    assert process.normalized_command_digest((executable, "a", "b")) != (
        process.normalized_command_digest((executable, "b", "a"))
    )
    with pytest.raises(process.R7S4ProcessError, match="must_be_absolute"):
        process.normalized_command_digest(("python.exe", "-V"))


def test_executable_lease_is_held_until_create_returns() -> None:
    class LeaseApi:
        def __init__(self) -> None:
            self.events: list[str] = []

        def open_executable_lease(self, path: str) -> process.ExecutableLease:
            self.events.append("open_and_measure")
            return process.ExecutableLease(handle=77, identity=_executable_identity(path), api=self)

        def verify_executable_path_binding(self, _identity: object) -> None:
            self.events.append("verify_binding")

        def close(self, _handle: int | None) -> None:
            self.events.append("close_lease")

    api = LeaseApi()

    def create(path: str) -> types.SimpleNamespace:
        assert os.path.isabs(path)
        api.events.append("create_returned")
        return types.SimpleNamespace(hProcess=1, hThread=2, dwProcessId=3, dwThreadId=4)

    created = process._create_with_executable_lease(api, sys.executable, create)
    assert api.events == [
        "open_and_measure",
        "verify_binding",
        "create_returned",
        "close_lease",
    ]
    assert created.executable_identity["write_sharing_allowed"] is False
    assert created.executable_identity["delete_sharing_allowed"] is False


def test_measured_executable_path_replacement_blocks_create() -> None:
    class SwappedLeaseApi:
        def __init__(self) -> None:
            self.created = 0
            self.closed = 0

        def open_executable_lease(self, path: str) -> process.ExecutableLease:
            return process.ExecutableLease(handle=88, identity=_executable_identity(path), api=self)

        def verify_executable_path_binding(self, _identity: object) -> None:
            raise process.R7S4ProcessError("executable_lease_path_binding_changed")

        def close(self, _handle: int | None) -> None:
            self.closed += 1

    api = SwappedLeaseApi()

    def create(_path: str) -> object:
        api.created += 1
        return object()

    with pytest.raises(process.R7S4ProcessError, match="path_binding_changed"):
        process._create_with_executable_lease(api, sys.executable, create)
    assert api.created == 0
    assert api.closed == 1


class ParentApi:
    def __init__(self) -> None:
        self.pipe_values = iter(((10, 11), (12, 13), (14, 15)))
        self.root_pid = 5001
        self.payload_pid = 5002
        self.member_calls = 0
        self.resumes = 0
        self.approvals = 0
        self.create_kwargs: dict[str, object] = {}
        self.ack_raw = b""
        self.active_queries = 0
        self.identity_sequence = 0
        self.completion_batches: list[list[tuple[int, int | None]]] = []

    def create_job_and_completion_port(self) -> tuple[int, int]:
        return 1, 2

    def create_pipe(self) -> tuple[int, int]:
        return next(self.pipe_values)

    def create_control_pipe(self) -> tuple[int, int]:
        return 16, 17

    def open_inheritable_null(self) -> int:
        return 18

    def create_bootstrap_suspended(self, **kwargs: object) -> types.SimpleNamespace:
        self.create_kwargs = dict(kwargs)
        nonce = kwargs["nonce"]
        expectation = process.BootstrapAckExpectation(
            run_uuid=str(kwargs["run_uuid"]),
            admission_id=str(kwargs["admission_id"]),
            nonce_commitment=process.r7s3.job_capability_commitment(nonce, str(kwargs["run_uuid"])),
            command_sha256=str(kwargs["command_sha256"]),
            bootstrap_sha256=str(kwargs["bootstrap_sha256"]),
            bootstrap_source_identity=dict(kwargs["bootstrap_source_identity"]),
            bootstrap_r7s3_identity=dict(kwargs["bootstrap_r7s3_identity"]),
            bootstrap_pid=self.root_pid,
        )
        self.ack_raw = process._canonical_json_bytes(_valid_ack(expectation, self.payload_pid))
        self.completion_batches = [
            [
                (6, self.root_pid),
                (6, self.payload_pid),
                (7, self.payload_pid),
                (7, self.root_pid),
                (4, None),
            ]
        ]
        return types.SimpleNamespace(
            hProcess=19,
            hThread=20,
            dwProcessId=self.root_pid,
            dwThreadId=self.root_pid + 1,
            executable_identity=_executable_identity(str(kwargs["command"][0])),
        )

    def is_process_in_job(self, _process: int, _job: int | None) -> bool:
        return True

    def member_job_snapshot(self, _job: int, _process: int) -> dict[str, object]:
        self.member_calls += 1
        if self.member_calls == 1:
            return _snapshot(self.root_pid)
        return _snapshot(self.root_pid, self.payload_pid)

    def resume(self, _thread: int) -> None:
        self.resumes += 1

    def read_pipe(self, _handle: int, _sink: bytearray, drained: object) -> None:
        drained.set()

    def read_bounded_pipe(
        self, _handle: int, sink: bytearray, drained: object, maximum: int
    ) -> None:
        assert len(self.ack_raw) <= maximum
        sink.extend(self.ack_raw)
        drained.set()

    def read_bounded_discarding_pipe(
        self,
        handle: int,
        sink: bytearray,
        drained: object,
        state: process.StreamCaptureState,
    ) -> None:
        if handle == 14:
            state.bytes_observed += len(self.ack_raw)
            sink.extend(self.ack_raw[: state.limit_bytes])
            state.overflowed = len(self.ack_raw) > state.limit_bytes
        drained.set()

    def write_approval(self, _handle: int) -> None:
        self.approvals += 1

    def query_active_pids(self, _job: int) -> tuple[int, ...]:
        self.active_queries += 1
        return ()

    def job_accounting_snapshot(self, _job: int) -> dict[str, object]:
        return {
            "limit_flags": 0,
            "active_processes": 0,
            "total_processes": 2,
            "terminated_processes": 0,
            "assigned_processes": 0,
            "process_ids": [],
        }

    def exit_code(self, _process: int) -> int:
        return 0

    def open_process(self, pid: int) -> int | None:
        return 21 if pid == self.payload_pid else None

    def process_is_active(self, _process: int) -> bool:
        return True

    def process_identity(self, _process: int, **kwargs: object) -> object:
        self.identity_sequence += 1
        return types.SimpleNamespace(
            pid=int(kwargs["pid"]),
            ppid=kwargs["fallback_ppid"],
            creation_time_ns=10_000 + self.identity_sequence,
            creation_time_utc=f"2026-09-02T00:00:0{self.identity_sequence}+00:00",
            image=sys.executable,
            run_uuid=str(kwargs["run_uuid"]),
            observed_sequence=int(kwargs["observed_sequence"]),
        )

    def completion_events(self, _completion: int) -> list[tuple[int, int | None]]:
        if self.completion_batches:
            return self.completion_batches.pop(0)
        return []

    def open_executable_lease(self, path: str) -> process.ExecutableLease:
        source_sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return process.ExecutableLease(
            handle=71 if Path(path).name == "phase_b2_r7s3_process.py" else 70,
            identity=_executable_identity(path, sha256=source_sha),
            api=self,
        )

    def verify_executable_path_binding(self, _identity: object) -> None:
        return None

    def close(self, _handle: int | None) -> None:
        return None


def test_mocked_parent_verifies_ack_before_single_approval() -> None:
    api = ParentApi()
    source_sha = hashlib.sha256(Path(process.__file__).read_bytes()).hexdigest()
    r7s3_sha = hashlib.sha256(Path(process.r7s3.__file__).read_bytes()).hexdigest()
    runner = process.WindowsBootstrapProcessRunner(source_sha, r7s3_sha, api_factory=lambda: api)
    payload = (str(Path(sys.executable).resolve()), "-c", "pass")

    outcome = runner.run(payload)

    assert outcome.safe_for_followup is True
    assert outcome.payload_resume_authorized is True
    assert outcome.forced_termination_attempts == 0
    assert outcome.residual_pids == ()
    assert outcome.bootstrap_executable_identity is not None
    assert outcome.payload_executable_identity is not None
    assert api.approvals == 1
    assert api.resumes == 1  # parent resumes the trusted bootstrap, not payload directly
    bootstrap_command = api.create_kwargs["command"]
    assert os.path.isabs(bootstrap_command[0])
    assert bootstrap_command[-1] == "--bootstrap"
    inherited_roles = {
        api.create_kwargs["stdin_handle"],
        api.create_kwargs["stdout_handle"],
        api.create_kwargs["stderr_handle"],
        api.create_kwargs["ack_handle"],
        api.create_kwargs["control_handle"],
    }
    assert len(inherited_roles) == 5


def test_mocked_parent_snapshot_swap_never_sends_approval() -> None:
    class SwappedParentApi(ParentApi):
        def member_job_snapshot(self, _job: int, _process: int) -> dict[str, object]:
            self.member_calls += 1
            if self.member_calls == 1:
                return _snapshot(self.root_pid)
            return _snapshot(self.root_pid, 9000)

    api = SwappedParentApi()
    source_sha = hashlib.sha256(Path(process.__file__).read_bytes()).hexdigest()
    r7s3_sha = hashlib.sha256(Path(process.r7s3.__file__).read_bytes()).hexdigest()
    runner = process.WindowsBootstrapProcessRunner(source_sha, r7s3_sha, api_factory=lambda: api)
    outcome = runner.run((str(Path(sys.executable).resolve()), "-c", "pass"))

    assert outcome.safe_for_followup is False
    assert outcome.payload_resume_authorized is False
    assert api.approvals == 0
    assert outcome.forced_termination_attempts == 0


def _fast_clock() -> tuple[object, object]:
    state = {"now": 0.0}

    def clock() -> float:
        return state["now"]

    def sleep(seconds: float) -> None:
        state["now"] += max(0.001, seconds)

    return clock, sleep


def _tiny_contract() -> object:
    return types.SimpleNamespace(
        wrapper_timeout_seconds=0.02,
        residual_repoll_seconds=0.02,
        stream_drain_seconds=0.01,
    )


def _mock_runner(api: ParentApi, **kwargs: object) -> process.WindowsBootstrapProcessRunner:
    source_sha = hashlib.sha256(Path(process.__file__).read_bytes()).hexdigest()
    r7s3_sha = hashlib.sha256(Path(process.r7s3.__file__).read_bytes()).hexdigest()
    clock, sleep = _fast_clock()
    return process.WindowsBootstrapProcessRunner(
        source_sha,
        r7s3_sha,
        contract=_tiny_contract(),
        api_factory=lambda: api,
        clock=clock,
        sleep=sleep,
        **kwargs,
    )


def _mock_runner_with_clock(
    api: ParentApi, clock: object, sleep: object, **kwargs: object
) -> process.WindowsBootstrapProcessRunner:
    source_sha = hashlib.sha256(Path(process.__file__).read_bytes()).hexdigest()
    r7s3_sha = hashlib.sha256(Path(process.r7s3.__file__).read_bytes()).hexdigest()
    return process.WindowsBootstrapProcessRunner(
        source_sha,
        r7s3_sha,
        contract=_tiny_contract(),
        api_factory=lambda: api,
        clock=clock,
        sleep=sleep,
        **kwargs,
    )


def _payload() -> tuple[str, ...]:
    return (str(Path(sys.executable).resolve()), "-c", "pass")


def test_parent_source_lease_is_held_through_ack_and_approval() -> None:
    class SourceLeaseApi(ParentApi):
        def __init__(self) -> None:
            super().__init__()
            self.events: list[str] = []

        def open_executable_lease(self, path: str) -> process.ExecutableLease:
            label = (
                "r7s3_open_and_measured"
                if Path(path).name == "phase_b2_r7s3_process.py"
                else "source_open_and_measured"
            )
            self.events.append(label)
            return super().open_executable_lease(path)

        def create_bootstrap_suspended(self, **kwargs: object) -> object:
            self.events.append("bootstrap_create_returned")
            return super().create_bootstrap_suspended(**kwargs)

        def read_bounded_discarding_pipe(
            self,
            handle: int,
            sink: bytearray,
            drained: object,
            state: process.StreamCaptureState,
        ) -> None:
            if handle == 14:
                self.events.append("ack_received")
            super().read_bounded_discarding_pipe(handle, sink, drained, state)

        def write_approval(self, handle: int) -> None:
            self.events.append("approval_written")
            super().write_approval(handle)

        def close(self, handle: int | None) -> None:
            if handle == 70:
                self.events.append("source_lease_closed")
            elif handle == 71:
                self.events.append("r7s3_lease_closed")

    api = SourceLeaseApi()
    outcome = _mock_runner(api).run(_payload())

    assert outcome.safe_for_followup is True
    assert api.events.index("source_open_and_measured") < api.events.index(
        "bootstrap_create_returned"
    )
    assert api.events.index("ack_received") < api.events.index("source_lease_closed")
    assert api.events.index("approval_written") < api.events.index("source_lease_closed")
    assert api.events.index("approval_written") < api.events.index("r7s3_lease_closed")
    assert outcome.bootstrap_source_identity == outcome.ack["bootstrap_source_identity"]
    assert outcome.bootstrap_r7s3_identity == outcome.ack["bootstrap_r7s3_identity"]


def test_stale_r7s3_dependency_pin_rejects_before_bootstrap_create() -> None:
    api = ParentApi()
    source_sha = hashlib.sha256(Path(process.__file__).read_bytes()).hexdigest()
    runner = process.WindowsBootstrapProcessRunner(
        source_sha,
        "0" * 64,
        contract=_tiny_contract(),
        api_factory=lambda: api,
    )
    outcome = runner.run(_payload())
    assert api.create_kwargs == {}
    assert outcome.bootstrap_pid is None
    assert outcome.safe_for_followup is False


def test_ack_timeout_is_distinct_from_ack_overflow() -> None:
    class NoAckApi(ParentApi):
        def read_bounded_discarding_pipe(
            self,
            handle: int,
            sink: bytearray,
            drained: object,
            state: process.StreamCaptureState,
        ) -> None:
            if handle != 14:
                drained.set()

    outcome = _mock_runner(NoAckApi()).run(_payload())
    assert outcome.ack_timeout is True
    assert outcome.ack_overflow is False
    assert outcome.payload_resume_authorized is False
    assert outcome.forced_termination_attempts == 0


def test_ack_overflow_is_not_reported_as_timeout() -> None:
    class AckOverflowApi(ParentApi):
        def read_bounded_discarding_pipe(
            self,
            handle: int,
            sink: bytearray,
            drained: object,
            state: process.StreamCaptureState,
        ) -> None:
            if handle == 14:
                sink.extend(b"x" * state.limit_bytes)
                state.bytes_observed = state.limit_bytes + 1
                state.overflowed = True
                drained.set()
            else:
                drained.set()

    outcome = _mock_runner(AckOverflowApi()).run(_payload())
    assert outcome.ack_overflow is True
    assert outcome.ack_timeout is False
    assert outcome.timed_out is False
    assert outcome.payload_resume_authorized is False


def test_active_pid_query_failure_is_unknown_and_retains_observer() -> None:
    class QueryFailureApi(ParentApi):
        def query_active_pids(self, _job: int) -> tuple[int, ...]:
            raise OSError("simulated_query_failure")

    registry = process.LocalResidualObservationRegistry()
    outcome = _mock_runner(QueryFailureApi(), observation_registry=registry).run(_payload())
    assert outcome.active_pid_query_succeeded is False
    assert outcome.active_process_zero is False
    assert outcome.residual_state == "unknown"
    assert outcome.observation_continuity == "process_local_handle_registry"
    assert outcome.observation_id is not None
    assert registry.observe(outcome.observation_id)["residual_state"] == "unknown"
    assert outcome.safe_for_followup is False


def test_timeout_residual_retains_job_observation_without_kill() -> None:
    class ResidualApi(ParentApi):
        def query_active_pids(self, _job: int) -> tuple[int, ...]:
            return (self.payload_pid,)

    registry = process.LocalResidualObservationRegistry()
    outcome = _mock_runner(ResidualApi(), observation_registry=registry).run(_payload())
    assert outcome.timed_out is True
    assert outcome.residual_state == "nonzero"
    assert outcome.residual_pids == (5002,)
    assert outcome.observation_continuity == "process_local_handle_registry"
    assert registry.observe(outcome.observation_id)["residual_pids"] == [5002]
    assert outcome.forced_termination_attempts == 0


def test_undrained_stdio_is_explicit_unobservable_manual_latch() -> None:
    class UndrainedStdoutApi(ParentApi):
        def read_bounded_discarding_pipe(
            self,
            handle: int,
            sink: bytearray,
            drained: object,
            state: process.StreamCaptureState,
        ) -> None:
            if handle != 10:
                super().read_bounded_discarding_pipe(handle, sink, drained, state)

    outcome = _mock_runner(UndrainedStdoutApi()).run(_payload())
    assert outcome.active_process_zero is True
    assert outcome.stdout_drained is False
    assert outcome.observation_continuity == "unobservable_manual_latch"
    assert outcome.manual_intervention_required is True


def test_unbounded_stdout_is_capped_drained_discarded_and_redacted() -> None:
    class UnboundedStdoutApi(ParentApi):
        def read_bounded_discarding_pipe(
            self,
            handle: int,
            sink: bytearray,
            drained: object,
            state: process.StreamCaptureState,
        ) -> None:
            if handle == 10:
                raw = b"secret-output" * 1000
                state.bytes_observed = len(raw)
                sink.extend(raw[: state.limit_bytes])
                state.overflowed = len(raw) > state.limit_bytes
                drained.set()
            else:
                super().read_bounded_discarding_pipe(handle, sink, drained, state)

    outcome = _mock_runner(UnboundedStdoutApi(), stream_capture_limit_bytes=64).run(_payload())
    assert outcome.stdout_bytes_observed > 64
    assert outcome.stdout_capture_overflow is True
    assert outcome.stdout_drained is True
    assert outcome.stdout == ""
    assert outcome.command == ()
    assert outcome.output_redaction_policy == "raw_command_and_stream_content_omitted"
    assert outcome.manual_intervention_required is True


def test_bounded_capture_discards_all_excess_without_unbounded_growth() -> None:
    sink = bytearray()
    state = process.StreamCaptureState(limit_bytes=31)
    for _ in range(10_000):
        process._append_bounded_capture(sink, state, b"0123456789")
    assert len(sink) == 31
    assert state.bytes_observed == 100_000
    assert state.overflowed is True


def test_reparent_events_and_pid_reuse_keep_creation_time_identity() -> None:
    class ReparentPidReuseApi(ParentApi):
        def __init__(self) -> None:
            super().__init__()
            self.events_sent = False
            self.reused_creation = 100

        def completion_events(self, _completion: int) -> list[tuple[int, int | None]]:
            if self.events_sent:
                return []
            self.events_sent = True
            return [
                (6, self.root_pid),
                (6, self.payload_pid),
                (6, 6000),
                (7, 6000),
                (6, 6000),
                (7, 6000),
                (7, self.payload_pid),
                (7, self.root_pid),
                (4, None),
            ]

        def job_accounting_snapshot(self, _job: int) -> dict[str, object]:
            value = super().job_accounting_snapshot(_job)
            value["total_processes"] = 4
            return value

        def open_process(self, pid: int) -> int | None:
            if pid in {self.payload_pid, 6000}:
                return 21 if pid == self.payload_pid else 22
            return None

        def process_identity(self, _process: int, **kwargs: object) -> object:
            if int(kwargs["pid"]) != 6000:
                return super().process_identity(_process, **kwargs)
            self.reused_creation += 1
            return types.SimpleNamespace(
                pid=6000,
                ppid=9999,
                creation_time_ns=self.reused_creation,
                creation_time_utc=f"2026-09-02T00:00:{self.reused_creation}+00:00",
                image=sys.executable,
                run_uuid=str(kwargs["run_uuid"]),
                observed_sequence=int(kwargs["observed_sequence"]),
            )

    outcome = _mock_runner(ReparentPidReuseApi()).run(_payload())
    reused = [item for item in outcome.process_identities if item["pid"] == 6000]
    assert len(reused) == 2
    assert len({item["creation_time_ns"] for item in reused}) == 2
    assert any(event["event"] == "job_exit_process" for event in outcome.process_events)
    assert outcome.safe_for_followup is True


def test_late_fast_descendant_is_drained_and_reconciled_after_first_zero_query() -> None:
    class LateFastDescendantApi(ParentApi):
        def __init__(self) -> None:
            super().__init__()
            self.completion_call = 0

        def completion_events(self, _completion: int) -> list[tuple[int, int | None]]:
            self.completion_call += 1
            if self.completion_call == 1:
                return [
                    (6, self.root_pid),
                    (6, self.payload_pid),
                    (7, self.payload_pid),
                    (7, self.root_pid),
                ]
            if self.completion_call == 2:
                return [(6, 6000), (7, 6000), (4, None)]
            return []

        def open_process(self, pid: int) -> int | None:
            if pid == 6000:
                return 22
            return super().open_process(pid)

        def job_accounting_snapshot(self, _job: int) -> dict[str, object]:
            value = super().job_accounting_snapshot(_job)
            value["total_processes"] = 3
            return value

    outcome = _mock_runner(LateFastDescendantApi()).run(_payload())
    assert outcome.safe_for_followup is True
    assert outcome.completion_accounting_reconciled is True
    assert outcome.completion_event_sequence_complete is True
    assert outcome.final_job_accounting["total_processes"] == 3
    assert {item["pid"] for item in outcome.process_identities} == {5001, 5002, 6000}


def test_missing_fast_descendant_event_cannot_be_safe_despite_zero_pid_list() -> None:
    class MissingDescendantEventApi(ParentApi):
        def job_accounting_snapshot(self, _job: int) -> dict[str, object]:
            value = super().job_accounting_snapshot(_job)
            value["total_processes"] = 3
            return value

    outcome = _mock_runner(MissingDescendantEventApi()).run(_payload())
    assert outcome.active_process_zero is False
    assert outcome.completion_accounting_reconciled is False
    assert outcome.completion_event_sequence_complete is False
    assert outcome.safe_for_followup is False
    assert "completion_identity_accounting_mismatch" in outcome.errors
    assert "completion_event_accounting_mismatch" in outcome.errors


def test_first_zero_observed_after_wrapper_deadline_is_timeout() -> None:
    class LateZeroApi(ParentApi):
        def query_active_pids(self, _job: int) -> tuple[int, ...]:
            self.active_queries += 1
            return (self.payload_pid,) if self.active_queries == 1 else ()

    outcome = _mock_runner(LateZeroApi()).run(_payload())
    assert outcome.timed_out is True
    assert "job_zero_first_observed_after_wrapper_deadline" in outcome.errors
    assert outcome.safe_for_followup is False


def test_zero_observed_after_residual_deadline_remains_zero_credit() -> None:
    clock_state = {"now": 0.0}

    def clock() -> float:
        return clock_state["now"]

    def sleep(seconds: float) -> None:
        clock_state["now"] += max(0.001, seconds)

    class ResidualDeadlineApi(ParentApi):
        def query_active_pids(self, _job: int) -> tuple[int, ...]:
            self.active_queries += 1
            return (self.payload_pid,) if self.active_queries <= 2 else ()

    outcome = _mock_runner_with_clock(ResidualDeadlineApi(), clock, sleep).run(_payload())
    assert outcome.residual_state == "zero"
    assert outcome.timed_out is True
    assert "job_zero_first_observed_after_residual_deadline" in outcome.errors
    assert outcome.safe_for_followup is False


@pytest.mark.parametrize(("ack_time", "expected_safe"), [(0.02, True), (0.021, False)])
def test_ack_completion_timestamp_enforces_exact_wrapper_boundary(
    ack_time: float, expected_safe: bool
) -> None:
    clock_state = {"now": 0.0}

    def clock() -> float:
        return clock_state["now"]

    def sleep(seconds: float) -> None:
        clock_state["now"] += max(0.001, seconds)

    class ClockedAckApi(ParentApi):
        def __init__(self) -> None:
            super().__init__()
            self.resumed = threading.Event()
            self.ack_complete = threading.Event()

        def resume(self, thread: int) -> None:
            clock_state["now"] = ack_time
            self.resumed.set()
            assert self.ack_complete.wait(timeout=1)
            super().resume(thread)

        def read_bounded_discarding_pipe(
            self,
            handle: int,
            sink: bytearray,
            drained: object,
            state: process.StreamCaptureState,
        ) -> None:
            if handle == 14:
                assert self.resumed.wait(timeout=1)
                super().read_bounded_discarding_pipe(handle, sink, drained, state)
                self.ack_complete.set()
                return
            super().read_bounded_discarding_pipe(handle, sink, drained, state)

    outcome = _mock_runner_with_clock(ClockedAckApi(), clock, sleep).run(_payload())
    assert outcome.safe_for_followup is expected_safe
    assert outcome.ack_drained is True
    if expected_safe:
        assert outcome.ack_timeout is False
        assert outcome.ack_drained_monotonic == pytest.approx(0.02)
    else:
        assert outcome.ack_timeout is True
        assert outcome.payload_resume_authorized is False
        assert "bootstrap_ack_completed_after_wrapper_deadline" in outcome.errors


def test_stream_drain_after_deadline_is_observed_but_not_accepted() -> None:
    clock_state = {"now": 0.0}
    release_stdout = threading.Event()
    stdout_complete = threading.Event()

    def clock() -> float:
        return clock_state["now"]

    def sleep(seconds: float) -> None:
        clock_state["now"] += max(0.001, seconds)
        if clock_state["now"] >= 0.075:
            release_stdout.set()
            assert stdout_complete.wait(timeout=1)

    class LateStdoutApi(ParentApi):
        def read_bounded_discarding_pipe(
            self,
            handle: int,
            sink: bytearray,
            drained: object,
            state: process.StreamCaptureState,
        ) -> None:
            if handle == 10:
                assert release_stdout.wait(timeout=1)
                drained.set()
                stdout_complete.set()
                return
            super().read_bounded_discarding_pipe(handle, sink, drained, state)

    outcome = _mock_runner_with_clock(LateStdoutApi(), clock, sleep).run(_payload())
    assert outcome.stdout_drained is True
    assert outcome.stream_drain_within_deadline is False
    assert "payload_streams_not_timely_drained_within_contract" in outcome.errors
    assert outcome.safe_for_followup is False


def test_staged_thread_start_failure_transfers_each_read_handle_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OwnershipApi(ParentApi):
        def __init__(self) -> None:
            super().__init__()
            self.closed_handles: list[int] = []

        def read_bounded_discarding_pipe(
            self,
            handle: int,
            sink: bytearray,
            drained: object,
            state: process.StreamCaptureState,
        ) -> None:
            self.close(handle)
            super().read_bounded_discarding_pipe(handle, sink, drained, state)

        def close(self, handle: int | None) -> None:
            if handle is not None:
                self.closed_handles.append(handle)

    class InlineThenFailThread:
        starts = 0

        def __init__(self, *, target: object, args: tuple[object, ...], **_kwargs: object) -> None:
            self.target = target
            self.args = args

        def start(self) -> None:
            type(self).starts += 1
            if type(self).starts == 2:
                raise RuntimeError("staged_thread_start_failure")
            self.target(*self.args)

    api = OwnershipApi()
    monkeypatch.setattr(process.threading, "Thread", InlineThenFailThread)
    outcome = _mock_runner(api).run(_payload())
    assert outcome.safe_for_followup is False
    assert api.closed_handles.count(10) == 1
    assert api.closed_handles.count(12) == 1
    assert api.closed_handles.count(14) == 1


def test_concurrent_residual_release_closes_each_handle_exactly_once() -> None:
    class CloseCountingApi:
        def __init__(self) -> None:
            self.queries = 0
            self.closed: list[int] = []

        def query_active_pids(self, _job: int) -> tuple[int, ...]:
            self.queries += 1
            return ()

        def close(self, handle: int | None) -> None:
            if handle is not None:
                self.closed.append(handle)

    api = CloseCountingApi()
    registry = process.LocalResidualObservationRegistry()
    observation_id = registry.retain(
        run_uuid=str(uuid.uuid4()),
        api=api,
        job=101,
        completion=102,
        root_process=103,
        bootstrap_source_lease=process.ExecutableLease(104, {}, api),
        bootstrap_r7s3_lease=process.ExecutableLease(105, {}, api),
    )
    barrier = threading.Barrier(3)
    results: list[bool] = []

    def release() -> None:
        barrier.wait(timeout=1)
        results.append(registry.release_if_zero(observation_id))

    workers = [threading.Thread(target=release) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=1)
    for worker in workers:
        worker.join(timeout=1)
        assert not worker.is_alive()
    assert any(results)
    assert api.queries == 1
    assert sorted(api.closed) == [101, 102, 103, 104, 105]


def test_observe_and_release_are_serialized_before_single_close() -> None:
    class CoordinatedApi:
        def __init__(self) -> None:
            self.first_query_entered = threading.Event()
            self.allow_first_query = threading.Event()
            self.queries = 0
            self.closed: list[int] = []

        def query_active_pids(self, _job: int) -> tuple[int, ...]:
            self.queries += 1
            if self.queries == 1:
                self.first_query_entered.set()
                assert self.allow_first_query.wait(timeout=1)
            return ()

        def close(self, handle: int | None) -> None:
            if handle is not None:
                self.closed.append(handle)

    api = CoordinatedApi()
    registry = process.LocalResidualObservationRegistry()
    observation_id = registry.retain(
        run_uuid=str(uuid.uuid4()),
        api=api,
        job=201,
        completion=202,
        root_process=203,
        bootstrap_source_lease=None,
        bootstrap_r7s3_lease=None,
    )
    observations: list[dict[str, object]] = []
    releases: list[bool] = []
    observer = threading.Thread(
        target=lambda: observations.append(registry.observe(observation_id))
    )
    releaser = threading.Thread(
        target=lambda: releases.append(registry.release_if_zero(observation_id))
    )
    observer.start()
    assert api.first_query_entered.wait(timeout=1)
    releaser.start()
    api.allow_first_query.set()
    observer.join(timeout=1)
    releaser.join(timeout=1)
    assert observations[0]["query_succeeded"] is True
    assert releases == [True]
    assert sorted(api.closed) == [201, 202, 203]


def test_contract_is_local_no_kill_and_not_production_enabled() -> None:
    contract = process.R7S4_LOCAL_PROCESS_CONTRACT
    assert contract["terminate_process_calls"] == 0
    assert contract["terminate_job_calls"] == 0
    assert contract["kill_on_job_close"] is False
    assert contract["bootstrap_executable_lease_through_create"] is True
    assert contract["payload_executable_lease_through_create"] is True
    assert contract["path_based_createprocess_toctou_eliminated"] is False
    assert contract["bootstrap_r7s3_dependency_lease_through_ack"] is True
    assert contract["bootstrap_transitive_python_tcb_pinned"] is False
    assert contract["completion_queue_stable_zero_reconciliation"] is True
    assert contract["job_total_process_identity_reconciliation"] is True
    assert contract["production_fresh_wired"] is False
    assert contract["go_evidence_eligible"] is False
    source = Path(process.__file__).read_text(encoding="utf-8")
    assert "TerminateJobObject(" not in source
    assert "TerminateProcess(" not in source


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("EVM_RUN_R7S4_WINDOWS_LIVE") != "1",
    reason="explicit opt-in Windows local-process qualification only",
)
def test_windows_live_local_bootstrap_admission() -> None:
    source_sha = hashlib.sha256(Path(process.__file__).read_bytes()).hexdigest()
    r7s3_sha = hashlib.sha256(Path(process.r7s3.__file__).read_bytes()).hexdigest()
    runner = process.WindowsBootstrapProcessRunner(source_sha, r7s3_sha)
    outcome = runner.run(
        (
            str(Path(sys.executable).resolve()),
            "-I",
            "-S",
            "-B",
            "-c",
            "print('r7s4-payload-ok')",
        )
    )
    assert outcome.safe_for_followup is True
    assert outcome.stdout == ""
    assert outcome.stdout_bytes_observed > 0
    assert outcome.stdout_capture_overflow is False
    assert outcome.output_redaction_policy == "raw_command_and_stream_content_omitted"
    assert outcome.residual_pids == ()
    assert outcome.forced_termination_attempts == 0
