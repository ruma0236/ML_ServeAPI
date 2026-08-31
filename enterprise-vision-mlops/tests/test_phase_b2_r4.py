from __future__ import annotations

import hashlib
import io
import json
import re
import threading
from dataclasses import fields, replace
from pathlib import Path

import pytest

from evm.scale_validation import phase_b2_r4 as r4


R3_DISRUPTIVE_COUNTS = {
    "docker_off_probe": 1,
    "compose_stop": 1,
    "desktop_stop": 1,
    "wsl_shutdown": 1,
    "desktop_start": 1,
    "compose_start": 1,
}

RESTORE_STAGE_NAMES = (
    "docker_engine",
    "compose",
    "kubernetes_api",
    "node_device_plugin_gpu",
    "b0_exact_identity_actual_cuda",
    "prometheus",
    "api_release_identity",
    "queue_jobs_lease_residue",
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScriptedProbe:
    def __init__(
        self,
        clock: FakeClock,
        outcomes: list[dict[str, object]],
        *,
        elapsed_seconds: float = 0.1,
    ) -> None:
        self.clock = clock
        self.outcomes = list(outcomes)
        self.elapsed_seconds = elapsed_seconds
        self.calls = 0

    def __call__(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        self.clock.advance(self.elapsed_seconds)
        index = min(self.calls - 1, len(self.outcomes) - 1)
        return dict(self.outcomes[index])


class FakeChild:
    def __init__(
        self,
        clock: FakeClock,
        *,
        exit_at: float | None,
        stdout: object | None = None,
        stderr: object | None = None,
    ) -> None:
        self.clock = clock
        self.exit_at = exit_at
        self.pid = 36768
        self.stdout = stdout if stdout is not None else io.StringIO("stdout\n")
        self.stderr = stderr if stderr is not None else io.StringIO("stderr\n")

    def poll(self) -> int | None:
        if self.exit_at is not None and self.clock.monotonic() >= self.exit_at:
            return 0
        return None


class FakeInspector:
    def __init__(self) -> None:
        self.root = r4.ProcessIdentity(36768, 11768, 1_700_000_000.0, "python.exe")
        self.child = r4.ProcessIdentity(26288, 36768, 1_700_000_001.0, "kubectl.exe")

    def identity(self, pid: int) -> r4.ProcessIdentity | None:
        return self.root if pid == self.root.pid else None

    def descendants(self, pid: int) -> list[r4.ProcessIdentity]:
        return [self.child] if pid == self.root.pid else []

    def same_process_alive(self, _identity: r4.ProcessIdentity) -> bool:
        return False


class BlockingStream:
    def __init__(self, release: threading.Event) -> None:
        self.release = release

    def read(self, _size: int) -> str:
        self.release.wait(timeout=5.0)
        return ""


def _contract(**updates: float) -> r4.TimeoutContract:
    values = {
        "kubectl_timeout_seconds": 8.0,
        "wrapper_timeout_seconds": 15.0,
        "restore_deadline_seconds": 600.0,
        "residual_repoll_seconds": 120.0,
        "stream_drain_seconds": 5.0,
    }
    values.update(updates)
    return r4.TimeoutContract(**values)


def _passing_probes(clock: FakeClock) -> dict[str, ScriptedProbe]:
    return {
        name: ScriptedProbe(clock, [{"passed": True, "detail": f"{name}:ok"}])
        for name in RESTORE_STAGE_NAMES
    }


def _run_restore(
    *,
    clock: FakeClock,
    probes: dict[str, ScriptedProbe],
    contract: r4.TimeoutContract | None = None,
) -> r4.RestoreReport:
    harness = r4.RestoreHarness(
        contract=contract or _contract(),
        probes=probes,
        clock=clock.monotonic,
        sleep=clock.sleep,
        retry_interval_seconds=1.0,
    )
    checkpoint = r4.RestoreCheckpoint.from_r3_call_counts(R3_DISRUPTIVE_COUNTS)
    return harness.run_restore_only(checkpoint=checkpoint)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _phase_b2_report() -> r4.RestoreReport:
    clock = FakeClock()
    restore_report = _run_restore(clock=clock, probes=_passing_probes(clock))
    return replace(
        restore_report,
        mode="phase-b2",
        decision="phase_b2_pass",
        call_counts={name: 1 for name in R3_DISRUPTIVE_COUNTS},
    )


def test_timeout_contract_enforces_nested_timeouts_and_exact_repoll() -> None:
    contract = _contract()

    contract.validate()

    assert contract.kubectl_timeout_seconds < contract.wrapper_timeout_seconds
    assert contract.wrapper_timeout_seconds < contract.restore_deadline_seconds
    assert contract.residual_repoll_seconds == 120.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kubectl_timeout_seconds", 15.0),
        ("wrapper_timeout_seconds", 600.0),
        ("residual_repoll_seconds", 4.0),
    ],
)
def test_timeout_contract_rejects_invalid_order_or_short_repoll(
    field: str, value: float
) -> None:
    contract = replace(_contract(), **{field: value})

    with pytest.raises(r4.ContractValidationError):
        contract.validate()


def test_manifest_runtime_timeout_mutations_are_rejected() -> None:
    runtime = _contract().to_dict()
    manifest = {"timeout_contract": dict(runtime)}
    r4.validate_manifest_runtime_contract(manifest=manifest, contract=_contract())

    mutations = (
        {"residual_repoll_seconds": 4.0},
        {"wrapper_timeout_seconds": 3.0},
        {"kubectl_timeout_seconds": 15.0},
    )
    for mutation in mutations:
        mutated = {"timeout_contract": dict(runtime)}
        mutated["timeout_contract"].update(mutation)
        with pytest.raises(r4.ContractValidationError):
            r4.validate_manifest_runtime_contract(manifest=mutated, contract=_contract())

    for field, value in (
        ("residual_repoll_seconds", 119.0),
        ("wrapper_timeout_seconds", 14.0),
        ("kubectl_timeout_seconds", 7.0),
    ):
        mutated_runtime = replace(_contract(), **{field: value})
        with pytest.raises(r4.ContractValidationError):
            r4.validate_manifest_runtime_contract(
                manifest={"timeout_contract": dict(runtime)},
                contract=mutated_runtime,
            )


def test_restore_state_machine_accepts_all_invariants() -> None:
    clock = FakeClock()
    probes = _passing_probes(clock)

    report = _run_restore(clock=clock, probes=probes)

    assert report.passed is True
    assert report.manual_intervention_required is False
    assert report.residual_pids == ()
    assert [stage.stage for stage in report.stages] == list(RESTORE_STAGE_NAMES)
    assert all(stage.passed for stage in report.stages)
    assert all(stage.duration_seconds >= 0 for stage in report.stages)
    assert all(stage.restore_deadline_monotonic == 1_600.0 for stage in report.stages)
    assert all(stage.ended_at and stage.started_at for stage in report.stages)


def test_kubernetes_eof_is_retried_and_delayed_recovery_is_recorded() -> None:
    clock = FakeClock()
    probes = _passing_probes(clock)
    probes["kubernetes_api"] = ScriptedProbe(
        clock,
        [
            {"passed": False, "retryable": True, "last_error": "unexpected EOF"},
            {"passed": True, "detail": "readyz ok"},
        ],
    )

    report = _run_restore(clock=clock, probes=probes)

    stage = next(item for item in report.stages if item.stage == "kubernetes_api")
    assert report.passed is True
    assert stage.probe_launches == 2
    assert stage.attempts[0]["last_error"] == "unexpected EOF"
    assert stage.attempts[1]["passed"] is True
    assert probes["kubernetes_api"].calls == 2


def test_total_restore_deadline_prevents_a_probe_without_wrapper_budget() -> None:
    clock = FakeClock()
    probes = _passing_probes(clock)
    probes["docker_engine"].elapsed_seconds = 6.0

    report = _run_restore(
        clock=clock,
        probes=probes,
        contract=_contract(
            kubectl_timeout_seconds=2.0,
            wrapper_timeout_seconds=5.0,
            restore_deadline_seconds=130.0,
            stream_drain_seconds=1.0,
        ),
    )

    assert report.passed is False
    assert report.manual_intervention_required is True
    assert probes["docker_engine"].calls == 1
    assert probes["compose"].calls == 0
    assert "budget" in report.last_error.lower()


def test_restore_only_never_repeats_disruptive_r3_calls() -> None:
    clock = FakeClock()
    report = _run_restore(clock=clock, probes=_passing_probes(clock))

    assert report.call_counts == {
        "docker_off_probe": 0,
        "compose_stop": 0,
        "desktop_stop": 0,
        "wsl_shutdown": 0,
        "desktop_start": 0,
        "compose_start": 0,
    }


def test_docker_and_compose_up_with_persistent_kubernetes_eof_is_partial_failure() -> None:
    clock = FakeClock()
    probes = _passing_probes(clock)
    probes["kubernetes_api"] = ScriptedProbe(
        clock,
        [
            {
                "passed": False,
                "retryable": False,
                "last_error": "EOF",
                "manual_intervention_required": True,
            }
        ],
    )

    report = _run_restore(clock=clock, probes=probes)

    assert report.passed is False
    assert report.manual_intervention_required is True
    assert probes["docker_engine"].calls == 1
    assert probes["compose"].calls == 1
    assert probes["kubernetes_api"].calls == 1
    assert probes["node_device_plugin_gpu"].calls == 0
    assert "EOF" in report.last_error


def test_residual_child_naturally_exits_inside_120_seconds() -> None:
    outcome = r4.classify_residual_process(
        initial_pid=36768,
        observations=[
            {
                "elapsed_seconds": 0.0,
                "identities": [
                    {"pid": 36768, "ppid": 11768, "creation_time": 1_700_000_000.0},
                    {"pid": 26288, "ppid": 36768, "creation_time": 1_700_000_001.0},
                ],
            },
            {"elapsed_seconds": 119.5, "identities": []},
        ],
        streams_drained=True,
        contract=_contract(),
    )

    assert outcome.naturally_exited is True
    assert outcome.residual_pids == ()
    assert outcome.manual_intervention_required is False


def test_residual_child_still_alive_after_120_seconds_requires_manual_action() -> None:
    outcome = r4.classify_residual_process(
        initial_pid=36768,
        observations=[
            {
                "elapsed_seconds": 0.0,
                "identities": [
                    {"pid": 36768, "ppid": 11768, "creation_time": 1_700_000_000.0},
                    {"pid": 26288, "ppid": 36768, "creation_time": 1_700_000_001.0},
                ],
            },
            {
                "elapsed_seconds": 120.0,
                "identities": [
                    {"pid": 26288, "ppid": 36768, "creation_time": 1_700_000_001.0}
                ],
            },
        ],
        streams_drained=True,
        contract=_contract(),
    )

    assert outcome.naturally_exited is False
    assert outcome.residual_pids == (26288,)
    assert outcome.manual_intervention_required is True


def test_stdout_or_stderr_handle_residue_requires_manual_action() -> None:
    outcome = r4.classify_residual_process(
        initial_pid=36768,
        observations=[{"elapsed_seconds": 1.0, "identities": []}],
        streams_drained=False,
        contract=_contract(),
    )

    assert outcome.residual_pids == ()
    assert outcome.streams_drained is False
    assert outcome.manual_intervention_required is True


def test_process_evidence_contract_records_identity_tree_and_stream_drain() -> None:
    names = {item.name for item in fields(r4.ProcessOutcome)}

    assert {
        "pid",
        "ppid",
        "creation_time",
        "descendants",
        "stdout",
        "stderr",
        "stdout_drained",
        "stderr_drained",
        "streams_drained",
        "residual_pids",
        "residual_observations",
        "forced_termination_attempts",
    } <= names


def test_bounded_runner_records_natural_child_exit_during_120_second_repoll() -> None:
    clock = FakeClock()
    inspector = FakeInspector()
    child = FakeChild(clock, exit_at=clock.monotonic() + 15.0 + 100.0)

    def observe(
        _root: r4.ProcessIdentity, _known: tuple[r4.ProcessIdentity, ...]
    ) -> list[r4.ProcessIdentity]:
        if clock.monotonic() < child.exit_at:
            return [inspector.root, inspector.child]
        return []

    runner = r4.BoundedProcessRunner(
        _contract(),
        popen_factory=lambda *_args, **_kwargs: child,
        clock=clock.monotonic,
        sleep=clock.sleep,
        process_inspector=inspector,
        utc_clock=lambda: "2026-08-31T00:00:00Z",
    )

    outcome = runner.run(
        ["python.exe", "bounded-test"],
        name="natural-exit",
        poll_interval=20.0,
        residual_observer=observe,
    )

    assert outcome.timed_out is True
    assert outcome.natural_exit_after_timeout is True
    assert outcome.residual_pids == ()
    assert outcome.manual_intervention_required is False
    assert outcome.pid == 36768
    assert outcome.ppid == 11768
    assert outcome.creation_time == 1_700_000_000.0
    assert outcome.descendants[0]["pid"] == 26288
    assert outcome.stdout == "stdout\n"
    assert outcome.stderr == "stderr\n"
    assert outcome.forced_termination_attempts == 0


def test_bounded_runner_returns_manual_with_child_alive_at_120_seconds() -> None:
    clock = FakeClock()
    inspector = FakeInspector()
    child = FakeChild(clock, exit_at=None)

    runner = r4.BoundedProcessRunner(
        _contract(),
        popen_factory=lambda *_args, **_kwargs: child,
        clock=clock.monotonic,
        sleep=clock.sleep,
        process_inspector=inspector,
        utc_clock=lambda: "2026-08-31T00:00:00Z",
    )

    outcome = runner.run(
        ["python.exe", "bounded-test"],
        name="persistent-child",
        poll_interval=30.0,
        residual_observer=lambda _root, _known: [inspector.child],
    )

    assert outcome.timed_out is True
    assert outcome.residual_repoll_seconds == 120.0
    assert outcome.residual_observations[-1]["elapsed_seconds"] == 120.0
    assert outcome.residual_pids == (26288,)
    assert outcome.manual_intervention_required is True
    assert outcome.forced_termination_attempts == 0


def test_bounded_runner_fails_closed_when_stdout_stderr_handles_do_not_drain() -> None:
    clock = FakeClock()
    inspector = FakeInspector()
    release = threading.Event()
    child = FakeChild(
        clock,
        exit_at=clock.monotonic(),
        stdout=BlockingStream(release),
        stderr=BlockingStream(release),
    )
    runner = r4.BoundedProcessRunner(
        _contract(),
        popen_factory=lambda *_args, **_kwargs: child,
        clock=clock.monotonic,
        sleep=clock.sleep,
        process_inspector=inspector,
        stream_joiner=lambda _threads, _timeout: False,
        utc_clock=lambda: "2026-08-31T00:00:00Z",
    )

    try:
        outcome = runner.run(
            ["python.exe", "bounded-test"],
            name="undrained-streams",
            residual_observer=lambda _root, _known: [],
        )
        assert outcome.timed_out is False
        assert outcome.stdout_drained is False
        assert outcome.stderr_drained is False
        assert outcome.streams_drained is False
        assert outcome.manual_intervention_required is True
        assert outcome.forced_termination_attempts == 0
    finally:
        release.set()


def test_release_readiness_requires_ready_200_and_exact_runtime_revision() -> None:
    revision = "a" * 40
    payload = {
        "runtime_source_commit": revision,
        "runtime_revision_matches": True,
    }

    result = r4.validate_release_readiness(200, payload, expected_revision=revision)
    assert all(result.values())


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status_code": 200,
            "runtime_source_commit": "b" * 40,
            "runtime_revision_matches": True,
        },
        {
            "status_code": 200,
            "runtime_source_commit": "unknown",
            "runtime_revision_matches": True,
        },
        {
            "status_code": 200,
            "runtime_source_commit": "a" * 40,
            "runtime_revision_matches": False,
        },
        {
            "status_code": 503,
            "runtime_source_commit": "a" * 40,
            "runtime_revision_matches": True,
        },
    ],
)
def test_release_readiness_rejects_stale_unknown_mismatch_or_non_200(
    payload: dict[str, object],
) -> None:
    value = dict(payload)
    status_code = int(value.pop("status_code"))
    result = r4.validate_release_readiness(
        status_code, value, expected_revision="a" * 40
    )

    assert not all(result.values())


def test_failure_seal_sha_chain_is_exact_and_create_new(tmp_path: Path) -> None:
    output_directory = tmp_path / "failure"
    payload = {
        "schema": "evm.phase-b2-r4.failure-seal.v1",
        "verdict": "manual_intervention_required",
        "acceptance_credit": False,
        "failure_only": True,
    }

    result = r4.create_failure_evidence(output_directory, payload)

    seal_path = Path(result["failure_seal"])
    index_path = Path(result["failure_index"])
    assert result["failure_seal_sha256"] == _sha256(seal_path)
    assert result["failure_index_sha256"] == _sha256(index_path)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["failure_only"] is True
    assert index["files"][0]["sha256"] == result["failure_seal_sha256"]
    assert not (output_directory / "completion-marker.json").exists()
    assert not (output_directory / "private-evidence-index.json").exists()

    with pytest.raises(r4.EvidenceExistsError):
        r4.create_failure_evidence(output_directory, payload)


def test_outer_bridge_manifest_sha_chain_rejects_each_mutation(tmp_path: Path) -> None:
    paths = {
        "outer": tmp_path / "outer.ps1",
        "bridge": tmp_path / "bridge.ps1",
        "manifest": tmp_path / "manifest.json",
    }
    paths["outer"].write_bytes(b"outer-v1\n")
    paths["bridge"].write_bytes(b"bridge-v1\n")
    paths["manifest"].write_bytes(b'{"schema":"r4"}\n')
    pins = {name: _sha256(path) for name, path in paths.items()}

    measured = r4.validate_sha_chain(
        outer_path=paths["outer"],
        expected_outer_sha256=pins["outer"],
        bridge_path=paths["bridge"],
        expected_bridge_sha256=pins["bridge"],
        manifest_path=paths["manifest"],
        expected_manifest_sha256=pins["manifest"],
    )
    assert measured == pins

    for name, path in paths.items():
        original = path.read_bytes()
        path.write_bytes(original + b"mutation")
        with pytest.raises(r4.ContractValidationError, match=f"sha_chain_mismatch:{name}"):
            r4.validate_sha_chain(
                outer_path=paths["outer"],
                expected_outer_sha256=pins["outer"],
                bridge_path=paths["bridge"],
                expected_bridge_sha256=pins["bridge"],
                manifest_path=paths["manifest"],
                expected_manifest_sha256=pins["manifest"],
            )
        path.write_bytes(original)


@pytest.mark.parametrize(
    "guard",
    [
        {
            "phase_b2_pass": False,
            "all_invariants_pass": True,
            "manual_intervention_required": False,
            "residual_pids": [],
        },
        {
            "phase_b2_pass": True,
            "all_invariants_pass": False,
            "manual_intervention_required": False,
            "residual_pids": [],
        },
        {
            "phase_b2_pass": True,
            "all_invariants_pass": True,
            "manual_intervention_required": True,
            "residual_pids": [],
        },
        {
            "phase_b2_pass": True,
            "all_invariants_pass": True,
            "manual_intervention_required": False,
            "residual_pids": [26288],
        },
    ],
)
def test_ordinary_no_go_or_manual_state_cannot_create_success_marker(
    tmp_path: Path, guard: dict[str, object]
) -> None:
    report = _phase_b2_report()
    report = replace(
        report,
        passed=bool(guard["phase_b2_pass"] and guard["all_invariants_pass"]),
        manual_intervention_required=bool(guard["manual_intervention_required"]),
        residual_pids=tuple(int(pid) for pid in guard["residual_pids"]),
        decision=(
            "manual_intervention_required"
            if guard["manual_intervention_required"] or guard["residual_pids"]
            else (
                "phase_b2_pass"
                if guard["phase_b2_pass"] and guard["all_invariants_pass"]
                else "phase_b2_no_go"
            )
        ),
    )
    output_directory = tmp_path / "success"

    with pytest.raises(r4.SuccessInvariantError):
        r4.create_success_evidence(output_directory, report)

    assert list(tmp_path.iterdir()) == []


def test_success_index_and_marker_are_written_only_after_all_guards(tmp_path: Path) -> None:
    report = _phase_b2_report()
    result = r4.create_success_evidence(
        tmp_path / "success",
        report,
        metadata={"samples": {"windows": 1_800, "wsl": 1_800}},
    )

    marker = Path(result["completion_marker"])
    index = Path(result["private_index"])
    assert marker.name == "completion-marker.json"
    assert index.name == "private-evidence-index.json"
    assert result["completion_marker_sha256"] == _sha256(marker)
    assert result["private_index_sha256"] == _sha256(index)


def test_runtime_source_has_no_forbidden_destructive_command() -> None:
    sources = (
        Path(r4.__file__),
        Path(__file__).parents[1] / "scripts" / "dev" / "run_x1_phase_b2_r4.py",
    )
    forbidden_patterns = (
        r"\btaskkill(?:\.exe)?\b",
        r"\bstop-process\b[^\n]*\b-force\b",
        r"\bdocker(?:\.exe)?\s+(?:compose\s+)?(?:down|up|system\s+prune)\b",
        r"\bwsl(?:\.exe)?\s+--unregister\b",
        r"\bkubectl(?:\.exe)?\s+(?:delete|drain|reset)\b",
        r"\bgit\s+(?:reset|clean|checkout)\b",
        r"\bchkdsk\b",
        r"\.kill\(",
        r"\.terminate\(",
    )

    for source_path in sources:
        source = source_path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert re.search(pattern, source, flags=re.IGNORECASE) is None, (
                source_path,
                pattern,
            )


def test_actual_restore_runner_non_vacuously_checks_jobs_and_claims() -> None:
    runner = (
        Path(__file__).parents[1] / "scripts" / "dev" / "run_x1_phase_b2_r4.py"
    ).read_text(encoding="utf-8")

    assert 'self._kubectl_command("get", "jobs", "-A", "-o", "json")' in runner
    assert "FROM evm_control_plane.lifecycle_claims" in runner
    assert "released_at IS NULL AND expires_at > clock_timestamp()" in runner
    assert '"active_jobs_zero": kubernetes_active_jobs == 0' in runner
    assert '"active_claims_zero": database_active_claims == 0' in runner
    assert '"name=evm-x1"' in runner
    assert "[31120, 31121, 31122]" in runner
    assert 'self._kubectl_command("get", "all", "-A"' in runner


def test_bundle_validator_does_not_leak_list_add_index_to_pipeline() -> None:
    validator = (
        Path(__file__).parents[1]
        / "scripts"
        / "dev"
        / "validate_phase_b2_r4_bundle.ps1"
    ).read_text(encoding="utf-8")

    assert "[void]$checks.Add(" in validator
