from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evm.scale_validation.phase_b2_r5 import (
    LifecycleTimeoutContract,
    RESTORE_STAGE_ORDER,
    RESTORE_LIFECYCLE_COUNTS,
    RestoreCheckpoint,
    RestoreReport,
    TimeoutContract,
)
from evm.scale_validation.phase_b2_r5_fresh import (
    REQUIRED_RUNTIME_INVARIANTS,
    FreshContext,
    FreshContract,
    SampleRequest,
)

from scripts.dev import run_x1_phase_b2_r5 as runner


REVISION = "a" * 40
TREE = "b" * 40
PRIMARY_SHA = "c" * 64
INDEX_SHA = "d" * 64


def _args(
    tmp_path: Path,
    *,
    mode: str,
    checkpoint: Path | None = None,
    output: Path | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        manifest=tmp_path / "phase-b2-r5-work-order.json",
        checkpoint=checkpoint or tmp_path / "failure-seal.json",
        output_directory=output or tmp_path / "new-output",
        expected_revision=REVISION,
        launcher_evidence_base64="encoded",
        repository_root=tmp_path / "repository",
        mode=mode,
    )


def _manifest(args: argparse.Namespace, *, companion: Path) -> dict[str, Any]:
    return {
        "bundle_id": f"r5-{args.mode}-unit",
        "execution_mode": args.mode,
        "canonical_revision": REVISION,
        "canonical_tree": TREE,
        "repository": {
            "branch": "codex/distributed-scale-validation-plan",
            "preserved_untracked_count": 4244,
        },
        "checkpoint": {
            "path": str(args.checkpoint.resolve()),
            "sha256": PRIMARY_SHA,
            "companion_index": {
                "path": str(companion.resolve()),
                "sha256": INDEX_SHA,
            },
        },
        "output": {"path": str(args.output_directory.resolve())},
    }


def _launcher(mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "git": {
            "branch": "codex/distributed-scale-validation-plan",
            "revision": REVISION,
            "origin_revision": REVISION,
            "remote_revision": REVISION,
            "tree": TREE,
            "tracked": 0,
            "untracked": 4244,
        },
    }


def _prepared(tmp_path: Path, *, mode: str, output: Path | None = None) -> runner.PreparedExecution:
    args = _args(tmp_path, mode=mode, output=output)
    companion = tmp_path / (
        "failure-evidence-index.json" if mode == "restore-only" else "restore-only-index.json"
    )
    manifest = _manifest(args, companion=companion)
    args.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    return runner.PreparedExecution(
        args=args,
        manifest=manifest,
        launcher_evidence=_launcher(mode),
        checkpoint_payload={},
        checkpoint_index={},
        restore_checkpoint=(
            RestoreCheckpoint(
                source="r4_failure_seal_checkpoint",
                historical_call_counts=RESTORE_LIFECYCLE_COUNTS,
                previous_attempt_failed=True,
            )
            if mode == "restore-only"
            else None
        ),
        timeout_contract=TimeoutContract(),
        lifecycle_timeout_contract=LifecycleTimeoutContract(),
        output_directory=args.output_directory.resolve(),
        run_id=f"r5-{mode}-unit",
    )


def _restore_report(
    *,
    passed: bool,
    manual: bool = False,
    residual: tuple[int, ...] = (),
    call_counts: dict[str, int] | None = None,
) -> RestoreReport:
    invariants = {name: passed for name in runner.R5_RESTORE_INVARIANTS}
    return RestoreReport(
        mode="restore-only",
        started_at="2026-09-01T00:00:00Z",
        ended_at="2026-09-01T00:00:01Z",
        duration_seconds=1.0,
        expected_revision=REVISION,
        passed=passed,
        manual_intervention_required=manual,
        deadline_exceeded=False,
        last_error=None if passed else "compose_not_healthy",
        stages=[],
        call_counts=call_counts or dict(RESTORE_LIFECYCLE_COUNTS),
        residual_pids=residual,
        checkpoint={},
        success_invariants=invariants,
        required_invariants=runner.R5_RESTORE_INVARIANTS,
        decision="restore_only_pass" if passed else "manual_intervention_required",
    )


def test_prepare_reads_companion_index_only_from_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _args(tmp_path, mode="restore-only")
    args.repository_root.mkdir()
    companion = tmp_path / "failure-evidence-index.json"
    manifest = _manifest(args, companion=companion)
    args.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    observed: dict[str, Any] = {}

    monkeypatch.setattr(runner, "validate_r5_manifest", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner,
        "decode_launcher_evidence",
        lambda _encoded, _manifest: _launcher("restore-only"),
    )
    monkeypatch.setattr(runner, "_verify_launcher_files", lambda *_args: {})

    def read_pair(
        primary_path: Path,
        primary_sha: str,
        index_path: Path,
        index_sha: str,
        *,
        mode: str,
    ) -> tuple[dict[str, Any], dict[str, Any], RestoreCheckpoint]:
        observed.update(
            {
                "primary": primary_path,
                "primary_sha": primary_sha,
                "index": index_path,
                "index_sha": index_sha,
                "mode": mode,
            }
        )
        return (
            {},
            {"files": [{"path": args.checkpoint.name, "sha256": PRIMARY_SHA}]},
            RestoreCheckpoint("r4", RESTORE_LIFECYCLE_COUNTS),
        )

    monkeypatch.setattr(runner, "read_checkpoint_pair", read_pair)
    prepared = runner.prepare_execution(args)

    assert prepared.run_id == "r5-restore-only-unit"
    assert observed["index"] == companion.resolve()
    assert observed["index_sha"] == INDEX_SHA
    assert not hasattr(args, "checkpoint_index")


def test_restore_pass_writes_only_restore_report_and_index(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path, mode="restore-only")
    calls = 0

    def execute(
        _prepared: runner.PreparedExecution, _checkpoint: RestoreCheckpoint
    ) -> RestoreReport:
        nonlocal calls
        calls += 1
        return _restore_report(passed=True)

    code, result = runner.execute_restore_only(prepared, restore_executor=execute)

    assert code == 0
    assert calls == 1
    assert result["decision"] == "restore_only_pass"
    assert (prepared.output_directory / "restore-only-report.json").is_file()
    assert (prepared.output_directory / "restore-only-index.json").is_file()
    assert not (prepared.output_directory / "completion-marker.json").exists()
    assert not (prepared.output_directory / "private-evidence-index.json").exists()


def test_restore_failure_seals_zero_credit_without_marker(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path, mode="restore-only")
    code, result = runner.execute_restore_only(
        prepared,
        restore_executor=lambda *_args: _restore_report(passed=False),
    )

    assert code == 2
    assert result["decision"] == "manual_intervention_required"
    assert (prepared.output_directory / "failure-seal.json").is_file()
    assert (prepared.output_directory / "failure-evidence-index.json").is_file()
    assert not (prepared.output_directory / "completion-marker.json").exists()


def test_duplicate_output_blocks_before_any_restore_probe(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    prepared = _prepared(tmp_path, mode="restore-only", output=output)
    calls = 0

    def forbidden(*_args: Any) -> RestoreReport:
        nonlocal calls
        calls += 1
        raise AssertionError("must not run")

    with pytest.raises(runner.R5RunnerError, match="output_directory_exists"):
        runner.execute_restore_only(prepared, restore_executor=forbidden)
    assert calls == 0
    assert list(output.iterdir()) == []


class _Outcome:
    def __init__(
        self,
        *,
        safe: bool = True,
        timed_out: bool = False,
        manual: bool = False,
        residual: tuple[int, ...] = (),
        return_code: int | None = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.safe_for_followup = safe
        self.timed_out = timed_out
        self.manual_intervention_required = manual
        self.residual_pids = residual
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr

    def to_dict(self) -> dict[str, Any]:
        return {
            "safe_for_followup": self.safe_for_followup,
            "timed_out": self.timed_out,
            "manual_intervention_required": self.manual_intervention_required,
            "residual_pids": list(self.residual_pids),
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "forced_termination_attempts": 0,
        }


class _ProcessLedger:
    def __init__(self, outcomes: list[_Outcome] | None = None) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.outcomes = list(outcomes or [])

    def run(self, command: Any, **_kwargs: Any) -> _Outcome:
        self.commands.append(tuple(str(item) for item in command))
        return self.outcomes.pop(0) if self.outcomes else _Outcome()


class _Stream:
    def __init__(self, **_kwargs: Any) -> None:
        self.starts = 0
        self.samples = 0
        self.finalizes = 0
        self.started = False
        self.finished = False
        self.details: dict[str, Any] = {}

    def start(self, context: FreshContext) -> None:
        self.starts += 1
        self.started = True
        self.details["wsl_run_uuid"] = context.run_uuid

    def sample(self, _request: Any) -> dict[str, int]:
        self.samples += 1
        return {}

    def finalize_after_collection_failure(self) -> None:
        self.finalizes += 1
        self.finished = True


def test_fresh_wires_separate_live_path_and_exact_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path, mode="fresh")
    ledger = _ProcessLedger()
    stream = _Stream()
    monkeypatch.setattr(
        runner.R5ProbeSet, "_find_executable", staticmethod(lambda *_args: "docker")
    )
    monkeypatch.setattr(
        runner,
        "_run_restore_harness",
        lambda *_args, **_kwargs: _restore_report(passed=True),
    )
    captured: dict[str, Any] = {}

    def fake_fresh_executor(**callbacks: Any) -> Any:
        context = FreshContext(
            run_uuid="11111111-1111-4111-8111-111111111111",
            contract=FreshContract(),
            started_at="2026-09-01T00:00:00Z",
            schedule_origin_monotonic_ns=0,
        )
        assert callbacks["preflight"](context).clean_pass
        for name in ("compose_stop", "desktop_stop", "desktop_start", "compose_start"):
            assert callbacks["lifecycle_callbacks"][name](context).clean_pass
        assert callbacks["recovery"](context).clean_pass
        invariants = callbacks["invariant_probe"](context)
        assert set(invariants) == set(REQUIRED_RUNTIME_INVARIANTS)
        assert all(invariants.values())
        captured.update(callbacks)
        report = SimpleNamespace(decision="phase_b2_pass", to_dict=lambda: {"passed": True})
        return SimpleNamespace(report=report, success_eligible=True)

    def fake_evidence(_path: Path, _execution: Any, *, metadata: Any) -> dict[str, Any]:
        captured["metadata"] = metadata
        return {"private_index": "index", "completion_marker": "marker"}

    code, result = runner.execute_fresh(
        prepared,
        fresh_executor=fake_fresh_executor,
        evidence_writer=fake_evidence,
        process_runner_factory=lambda _contract: ledger,
        wsl_stream_factory=lambda **_kwargs: stream,
    )

    assert code == 0
    assert result["decision"] == "phase_b2_pass"
    assert stream.starts == 1
    assert stream.finalizes == 1
    assert len(ledger.commands) == 4
    rendered = [" ".join(command) for command in ledger.commands]
    assert (
        sum(" compose " in f" {command} " and " stop " in f" {command} " for command in rendered)
        == 1
    )
    assert sum(" desktop stop " in f" {command} " for command in rendered) == 1
    assert sum(" desktop start " in f" {command} " for command in rendered) == 1
    assert (
        sum(" compose " in f" {command} " and " start " in f" {command} " for command in rendered)
        == 1
    )
    compose_start = next(
        command
        for command in rendered
        if " compose " in f" {command} " and " start " in f" {command} "
    )
    assert compose_start.endswith("start --wait --wait-timeout 120")
    assert all("--shutdown" not in command for command in rendered)
    assert captured["metadata"]["restore_report_synthesized"] is False


def test_fresh_timeout_latch_runs_no_followup_and_writes_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path, mode="fresh")
    ledger = _ProcessLedger(
        [_Outcome(safe=False, timed_out=True, manual=True, residual=(4242,), return_code=None)]
    )
    stream = _Stream()
    monkeypatch.setattr(
        runner.R5ProbeSet, "_find_executable", staticmethod(lambda *_args: "docker")
    )
    monkeypatch.setattr(
        runner,
        "_run_restore_harness",
        lambda *_args, **_kwargs: _restore_report(passed=True),
    )

    code, result = runner.execute_fresh(
        prepared,
        process_runner_factory=lambda _contract: ledger,
        wsl_stream_factory=lambda **_kwargs: stream,
    )

    assert code == 2
    assert result["decision"] == "zero_credit_failure"
    assert len(ledger.commands) == 1
    assert " compose " in f" {' '.join(ledger.commands[0])} "
    assert " stop " in f" {' '.join(ledger.commands[0])} "
    assert stream.starts == 0
    assert (prepared.output_directory / "failure-seal.json").is_file()
    assert (prepared.output_directory / "failure-evidence-index.json").is_file()
    assert not (prepared.output_directory / "completion-marker.json").exists()


def test_bootstrap_failure_never_reuses_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    args = _args(tmp_path, mode="restore-only", output=output)

    result = runner._seal_bootstrap_failure(args, runner.R5RunnerError("duplicate"))

    assert "output_directory_exists" in str(result["failure_seal_error"])
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert sorted(path.name for path in output.iterdir()) == ["sentinel.txt"]


def test_containment_exception_latches_reconcile_and_followup_zero(tmp_path: Path) -> None:
    probe_set = object.__new__(runner.R5ProbeSet)
    probe_set.contract = TimeoutContract()
    probe_set.repository_root = tmp_path

    class ExplodingRunner:
        @staticmethod
        def run(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("job_accounting_unavailable")

    probe_set.runner = ExplodingRunner()
    followup_calls = 0

    def first(deadline: Any) -> dict[str, Any]:
        return probe_set._run(deadline, ("read-only-probe",), name="first")

    def followup(_deadline: Any) -> bool:
        nonlocal followup_calls
        followup_calls += 1
        return True

    probes = {stage.value: followup for stage in RESTORE_STAGE_ORDER}
    probes[RESTORE_STAGE_ORDER[0].value] = first
    harness = runner.ReconcileRestoreHarness(contract=TimeoutContract(), probes=probes)
    report = harness.run_restore_only(
        RestoreCheckpoint("unit", RESTORE_LIFECYCLE_COUNTS, previous_attempt_failed=True)
    )

    assert report.passed is False
    assert report.manual_intervention_required is True
    assert len(report.stages) == 1
    assert followup_calls == 0
    details = report.stages[0].attempts[0]["details"]
    assert details["residual_status"] == "unknown"
    assert details["residual_process_zero"] is False
    assert details["timeout_manual_latch"] is False
    assert details["containment_manual_latch"] is True
    assert details["process_evidence"]["safe_for_followup"] is False


def test_typed_post_child_containment_failure_preserves_residual_identity(
    tmp_path: Path,
) -> None:
    probe_set = object.__new__(runner.R5ProbeSet)
    probe_set.contract = TimeoutContract()
    probe_set.repository_root = tmp_path

    class TypedFailure(RuntimeError):
        child_created = True
        residual_pids = (9123,)

        def to_dict(self) -> dict[str, Any]:
            return {
                "stage": "job_accounting",
                "child_created": True,
                "root_pid": 9123,
                "residual_pids": [9123],
                "forced_termination_attempts": 0,
            }

    class ExplodingRunner:
        @staticmethod
        def run(*_args: Any, **_kwargs: Any) -> Any:
            raise TypedFailure("accounting_failed_after_resume")

    probe_set.runner = ExplodingRunner()
    result = probe_set._run(
        runner.RestoreDeadline(600.0),
        ("read-only-probe",),
        name="typed-failure",
    )

    assert result["manual_intervention_required"] is True
    assert result["residual_status"] == "present"
    assert result["residual_process_zero"] is False
    assert result["residual_pids"] == [9123]
    assert result["process_evidence"]["root_pid"] == 9123
    assert result["process_evidence"]["forced_termination_attempts"] == 0


def test_wsl_finalize_without_metadata_still_joins_and_uuid_scans(tmp_path: Path) -> None:
    scan_runner = _ProcessLedger([_Outcome(stdout="[]")])
    stream = runner.WslClockStream(
        output_directory=tmp_path / "output",
        process_runner=_ProcessLedger(),
        scan_runner=scan_runner,
    )
    stream.run_uuid = "11111111-1111-4111-8111-111111111111"
    stream.protocol = runner.WslResidualProtocol(stream.run_uuid)
    stream.metadata = None
    stream.spool_path = tmp_path / "partial-spool.jsonl"
    stream.spool_path.write_bytes(b"")
    stream._outcome = _Outcome()
    thread = runner.threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    stream._thread = thread

    stream.finalize_after_collection_failure()

    assert stream.finished is True
    assert stream.details["wsl_metadata_available"] is False
    assert stream.details["wsl_proc_residuals"] == []
    assert len(scan_runner.commands) == 1
    assert stream.run_uuid in " ".join(scan_runner.commands[0])


def test_live_sampler_adapters_supply_required_domain_and_sequence(tmp_path: Path) -> None:
    request = SampleRequest(
        run_uuid="11111111-1111-4111-8111-111111111111",
        domain="windows_host",
        sequence=7,
        target_monotonic_ns=700_000_000,
    )
    windows = runner._windows_sample(request)
    assert windows["domain"] == "windows_host"
    assert windows["sequence"] == 7

    stream = runner.WslClockStream(
        output_directory=tmp_path / "output",
        process_runner=_ProcessLedger(),
        scan_runner=_ProcessLedger(),
    )
    stream._next_line = lambda _timeout: {
        "kind": "sample",
        "sequence": 7,
        "raw_before_ns": 1,
        "realtime_unix_ns": 2,
        "raw_after_ns": 3,
        "monotonic_ns": 4,
        "auxiliary_monotonic_ns": 5,
    }
    wsl = stream.sample(
        SampleRequest(
            run_uuid=request.run_uuid,
            domain="wsl_ubuntu",
            sequence=7,
            target_monotonic_ns=request.target_monotonic_ns,
        )
    )
    assert wsl["domain"] == "wsl_ubuntu"
    assert wsl["sequence"] == 7


def test_fresh_shared_deadline_blocks_preflight_before_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path, mode="fresh")
    ledger = _ProcessLedger()
    stream = _Stream()
    monkeypatch.setattr(
        runner.R5ProbeSet, "_find_executable", staticmethod(lambda *_args: "docker")
    )
    restore_calls = 0

    def forbidden_restore(*_args: Any, **_kwargs: Any) -> RestoreReport:
        nonlocal restore_calls
        restore_calls += 1
        raise AssertionError("budget gate must prevent this probe")

    monkeypatch.setattr(runner, "_run_restore_harness", forbidden_restore)

    class Clock:
        calls = 0

        def __call__(self) -> float:
            self.calls += 1
            return 0.0 if self.calls == 1 else 700.0

    code, result = runner.execute_fresh(
        prepared,
        process_runner_factory=lambda _contract: ledger,
        wsl_stream_factory=lambda **_kwargs: stream,
        monotonic_clock=Clock(),
    )

    assert code == 2
    assert result["decision"] == "zero_credit_failure"
    assert restore_calls == 0
    assert ledger.commands == []
    assert stream.starts == 0
    assert (prepared.output_directory / "failure-seal.json").is_file()


def test_partial_collection_triggers_bounded_wsl_finalize_before_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path, mode="fresh")
    ledger = _ProcessLedger()
    stream = _Stream()
    monkeypatch.setattr(
        runner.R5ProbeSet, "_find_executable", staticmethod(lambda *_args: "docker")
    )
    monkeypatch.setattr(
        runner,
        "_run_restore_harness",
        lambda *_args, **_kwargs: _restore_report(passed=True),
    )
    captured: dict[str, Any] = {}

    def partial_executor(**callbacks: Any) -> Any:
        context = FreshContext(
            run_uuid="11111111-1111-4111-8111-111111111111",
            contract=FreshContract(),
            started_at="2026-09-01T00:00:00Z",
            schedule_origin_monotonic_ns=0,
        )
        assert callbacks["preflight"](context).clean_pass
        assert callbacks["lifecycle_callbacks"]["compose_stop"](context).clean_pass
        assert callbacks["lifecycle_callbacks"]["desktop_stop"](context).clean_pass
        report = SimpleNamespace(
            decision="zero_credit_failure",
            to_dict=lambda: {"manual_intervention_required": True},
        )
        return SimpleNamespace(report=report, success_eligible=False)

    def evidence(_path: Path, _execution: Any, *, metadata: Any) -> dict[str, Any]:
        captured.update(metadata)
        return {"failure_seal": "seal", "failure_index": "index"}

    code, _result = runner.execute_fresh(
        prepared,
        fresh_executor=partial_executor,
        evidence_writer=evidence,
        process_runner_factory=lambda _contract: ledger,
        wsl_stream_factory=lambda **_kwargs: stream,
    )

    assert code == 2
    assert len(ledger.commands) == 2
    assert stream.starts == 1
    assert stream.finalizes == 1
    assert stream.finished is True
    assert captured["restore_report_synthesized"] is False
