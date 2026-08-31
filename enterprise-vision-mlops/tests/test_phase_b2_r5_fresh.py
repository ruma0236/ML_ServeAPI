from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r5_fresh as fresh


RUN_UUID = "123e4567-e89b-42d3-a456-426614174000"


class SyntheticScheduler:
    def __init__(self) -> None:
        self.now_ns = 8_000_000_000_000
        self.sleep_calls: list[float] = []

    def monotonic_ns(self) -> int:
        return self.now_ns

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now_ns += round(seconds * 1_000_000_000)


class RawSampler:
    def __init__(self, *, mutation: str | None = None) -> None:
        self.mutation = mutation
        self.requests: list[fresh.SampleRequest] = []

    def __call__(self, request: fresh.SampleRequest) -> dict[str, Any]:
        self.requests.append(request)
        raw_before = 50_000_000_000 + request.sequence * fresh.FRESH_CADENCE_NS
        realtime = 1_900_000_000_000_000_000 + request.sequence * fresh.FRESH_CADENCE_NS
        if self.mutation == "backward" and request.sequence == 900:
            realtime -= 300_000_000
        sample: dict[str, Any] = {
            "domain": request.domain,
            "sequence": request.sequence,
            "raw_before_ns": raw_before,
            "realtime_unix_ns": realtime,
            "raw_after_ns": raw_before + 1_000,
            "monotonic_ns": raw_before + 2_000,
            "auxiliary_monotonic_ns": raw_before + 3_000,
        }
        if self.mutation == "bracket" and request.sequence == 700:
            sample["raw_after_ns"] = raw_before + 6_000_000
        if self.mutation == "restore-report":
            return {"mode": "restore-only", "passed": True}
        return sample


class CallbackLedger:
    def __init__(self, *, failed_action: str | None = None) -> None:
        self.calls: list[str] = []
        self.failed_action = failed_action

    def callback(self, name: str):
        def invoke(_context: fresh.FreshContext) -> dict[str, object]:
            self.calls.append(name)
            if name == self.failed_action:
                return {"passed": False, "error": f"{name}_failed"}
            return {"passed": True, "details": {"observed": name}}

        return invoke


def _all_invariants(_context: fresh.FreshContext) -> dict[str, bool]:
    return {name: True for name in fresh.REQUIRED_RUNTIME_INVARIANTS}


def _run(
    *,
    windows: RawSampler | None = None,
    wsl: RawSampler | None = None,
    ledger: CallbackLedger | None = None,
    invariant_probe=_all_invariants,
    contract: fresh.FreshContract | None = None,
) -> tuple[fresh.FreshExecution, SyntheticScheduler, RawSampler, RawSampler, CallbackLedger]:
    scheduler = SyntheticScheduler()
    windows_sampler = windows or RawSampler()
    wsl_sampler = wsl or RawSampler()
    callback_ledger = ledger or CallbackLedger()
    lifecycle = {name: callback_ledger.callback(name) for name in fresh.LIFECYCLE_SEQUENCE}
    execution = fresh.run_fresh(
        preflight=callback_ledger.callback("preflight"),
        lifecycle_callbacks=lifecycle,
        windows_sampler=windows_sampler,
        wsl_sampler=wsl_sampler,
        recovery=callback_ledger.callback("recovery"),
        invariant_probe=invariant_probe,
        contract=contract,
        monotonic_ns=scheduler.monotonic_ns,
        sleep=scheduler.sleep,
        utc_clock=lambda: "2026-08-31T18:00:00Z",
        run_uuid_factory=lambda: RUN_UUID,
    )
    return execution, scheduler, windows_sampler, wsl_sampler, callback_ledger


def _hashes(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def test_run_fresh_collects_exact_independent_raw_samples_and_is_eligible() -> None:
    execution, scheduler, windows, wsl, ledger = _run()

    assert execution.success_eligible is True
    assert execution.eligibility == fresh.FreshEligibility(
        eligible=True,
        decision="phase_b2_pass",
        reasons=(),
    )
    assert execution.report.decision == "phase_b2_pass"
    assert execution.report.mode == "fresh"
    assert execution.report.source_kind == "live_raw_collectors"
    assert len(execution.windows_samples) == 1_800
    assert len(execution.wsl_samples) == 1_800
    assert len(execution.schedule_observations) == 1_800
    assert len(windows.requests) == 1_800
    assert len(wsl.requests) == 1_800
    assert windows.requests is not wsl.requests
    assert execution.windows_samples is not execution.wsl_samples
    assert scheduler.now_ns == 8_000_000_000_000 + 180_000_000_000
    assert execution.report.schedule.duration_reached is True
    assert execution.report.schedule.cadence_gap_count == 0
    assert execution.report.windows.offset_discontinuity_count == 0
    assert execution.report.windows.backward_step_count == 0
    assert execution.report.windows.unclassified_gap_count == 0
    assert execution.report.windows.bracket_violation_count == 0
    assert execution.report.wsl.offset_discontinuity_count == 0
    assert execution.report.wsl.backward_step_count == 0
    assert execution.report.wsl.unclassified_gap_count == 0
    assert execution.report.wsl.bracket_violation_count == 0
    assert ledger.calls == [
        "preflight",
        "compose_stop",
        "desktop_stop",
        "desktop_start",
        "compose_start",
        "recovery",
    ]
    assert execution.report.call_counts == {
        "preflight": 1,
        "docker_off_probe": 1,
        "compose_stop": 1,
        "desktop_stop": 1,
        "wsl_shutdown": 0,
        "desktop_start": 1,
        "compose_start": 1,
        "windows_sampler": 1_800,
        "wsl_sampler": 1_800,
        "recovery": 1,
        "invariant_probe": 1,
    }
    fresh.validate_fresh_execution(execution)


def test_success_evidence_contains_actual_raw_files_and_marker(tmp_path: Path) -> None:
    execution, *_rest = _run()
    output = tmp_path / "fresh-success"

    result = fresh.write_fresh_evidence(output, execution, metadata={"attempt": "r5"})

    assert result["decision"] == "phase_b2_pass"
    assert len((output / "windows-raw-samples.jsonl").read_text().splitlines()) == 1_800
    assert len((output / "wsl-raw-samples.jsonl").read_text().splitlines()) == 1_800
    assert len((output / "monotonic-schedule.jsonl").read_text().splitlines()) == 1_800
    index = json.loads((output / "private-evidence-index.json").read_text())
    marker = json.loads((output / "completion-marker.json").read_text())
    assert index["acceptance_credit"] is True
    assert {entry["path"] for entry in index["files"]} == {
        "windows-raw-samples.jsonl",
        "wsl-raw-samples.jsonl",
        "monotonic-schedule.jsonl",
        "fresh-report.json",
    }
    assert marker["phase_b2_pass"] is True
    assert marker["private_evidence_index_sha256"] == result["private_index_sha256"]
    assert not (output / "failure-seal.json").exists()


def test_offline_schedule_origin_is_after_preflight_and_stop_latency() -> None:
    scheduler = SyntheticScheduler()
    initial_ns = scheduler.now_ns
    windows = RawSampler()
    wsl = RawSampler()

    def delayed(_context: fresh.FreshContext) -> dict[str, bool]:
        scheduler.now_ns += 2_000_000_000
        return {"passed": True}

    lifecycle = {
        "compose_stop": delayed,
        "desktop_stop": delayed,
        "desktop_start": lambda _context: {"passed": True},
        "compose_start": lambda _context: {"passed": True},
    }
    execution = fresh.run_fresh(
        preflight=delayed,
        lifecycle_callbacks=lifecycle,
        windows_sampler=windows,
        wsl_sampler=wsl,
        recovery=lambda _context: {"passed": True},
        invariant_probe=_all_invariants,
        monotonic_ns=scheduler.monotonic_ns,
        sleep=scheduler.sleep,
        utc_clock=lambda: "2026-08-31T18:00:00Z",
        run_uuid_factory=lambda: RUN_UUID,
    )

    assert execution.success_eligible is True
    assert execution.report.schedule.origin_monotonic_ns == initial_ns + 6_000_000_000
    assert execution.report.schedule.observed_end_monotonic_ns == (
        initial_ns + 6_000_000_000 + 180_000_000_000
    )


def test_evidence_directory_is_create_exclusive_and_not_overwritten(tmp_path: Path) -> None:
    execution, *_rest = _run()
    output = tmp_path / "fresh-once"
    fresh.write_fresh_evidence(output, execution)
    before = _hashes(output)

    with pytest.raises(fresh.FreshEvidenceExistsError, match="evidence_directory_exists"):
        fresh.write_fresh_evidence(output, execution)

    assert _hashes(output) == before


@pytest.mark.parametrize(
    ("mutation", "field"),
    [("backward", "backward_step_count"), ("bracket", "bracket_violation_count")],
)
def test_clock_metric_failure_is_zero_credit_without_success_marker(
    tmp_path: Path,
    mutation: str,
    field: str,
) -> None:
    execution, *_rest = _run(windows=RawSampler(mutation=mutation))
    output = tmp_path / f"failed-{mutation}"

    assert execution.success_eligible is False
    assert getattr(execution.report.windows, field) > 0
    result = fresh.write_fresh_evidence(output, execution)

    assert result["decision"] == "zero_credit_failure"
    assert (output / "failure-seal.json").is_file()
    assert (output / "failure-evidence-index.json").is_file()
    assert not (output / "completion-marker.json").exists()
    assert not (output / "private-evidence-index.json").exists()


def test_false_runtime_invariant_forbids_success_marker(tmp_path: Path) -> None:
    def one_false(_context: fresh.FreshContext) -> dict[str, bool]:
        values = _all_invariants(_context)
        values["api_revision_exact"] = False
        return values

    execution, *_rest = _run(invariant_probe=one_false)
    output = tmp_path / "invariant-failure"
    fresh.write_fresh_evidence(output, execution)

    assert execution.success_eligible is False
    assert "runtime_invariants_failed:api_revision_exact" in execution.report.errors
    assert "runtime_invariants_not_all_true" in execution.eligibility.reasons
    assert not (output / "completion-marker.json").exists()
    assert not (output / "private-evidence-index.json").exists()


def test_restore_report_or_mapping_cannot_be_synthesized_into_fresh_success(
    tmp_path: Path,
) -> None:
    restore_like = {
        "mode": "restore-only",
        "passed": True,
        "decision": "restore_only_pass",
        "metadata": {"samples": 1_800},
    }

    with pytest.raises(fresh.FreshEvidenceValidationError, match="fresh_execution_type_required"):
        fresh.write_fresh_evidence(tmp_path / "synthesized", restore_like)  # type: ignore[arg-type]

    assert not (tmp_path / "synthesized").exists()


def test_restore_shaped_sampler_payload_is_not_accepted_as_raw_clock_data() -> None:
    execution, _scheduler, windows, _wsl, ledger = _run(
        windows=RawSampler(mutation="restore-report")
    )

    assert execution.success_eligible is False
    assert execution.report.manual_intervention_required is True
    assert execution.report.call_counts["windows_sampler"] == 1
    assert execution.report.call_counts["wsl_sampler"] == 0
    assert len(windows.requests) == 1
    assert "desktop_start" not in ledger.calls
    assert any(error.startswith("raw_collection_failed:") for error in execution.report.errors)


def test_mutated_raw_evidence_is_rejected_before_directory_creation(tmp_path: Path) -> None:
    execution, *_rest = _run()
    mutated_windows = execution.windows_samples[:-1]
    mutated = replace(execution, windows_samples=mutated_windows)
    output = tmp_path / "raw-mutation"

    with pytest.raises(fresh.FreshEvidenceValidationError, match="raw_analysis_mismatch"):
        fresh.write_fresh_evidence(output, mutated)

    assert not output.exists()


def test_lifecycle_failure_prevents_collection_and_success_marker(tmp_path: Path) -> None:
    ledger = CallbackLedger(failed_action="desktop_stop")
    execution, _scheduler, windows, wsl, ledger = _run(ledger=ledger)
    output = tmp_path / "lifecycle-failure"
    fresh.write_fresh_evidence(output, execution)

    assert execution.success_eligible is False
    assert len(windows.requests) == 0
    assert len(wsl.requests) == 0
    assert ledger.calls == ["preflight", "compose_stop", "desktop_stop"]
    assert execution.report.call_counts["wsl_shutdown"] == 0
    assert not (output / "completion-marker.json").exists()


@pytest.mark.parametrize(
    "contract",
    [
        fresh.FreshContract(duration_seconds=179),
        fresh.FreshContract(cadence_ms=101),
        fresh.FreshContract(sample_count=1_799),
        fresh.FreshContract(required_invariants=("docker_engine",)),
    ],
)
def test_frozen_fresh_contract_rejects_mutation(contract: fresh.FreshContract) -> None:
    with pytest.raises(fresh.FreshContractError):
        _run(contract=contract)


def test_wsl_shutdown_callback_is_forbidden() -> None:
    ledger = CallbackLedger()
    lifecycle = {name: ledger.callback(name) for name in fresh.LIFECYCLE_SEQUENCE}
    lifecycle["wsl_shutdown"] = ledger.callback("wsl_shutdown")
    scheduler = SyntheticScheduler()

    with pytest.raises(fresh.FreshContractError, match="lifecycle_callbacks_must_be_exactly"):
        fresh.run_fresh(
            preflight=ledger.callback("preflight"),
            lifecycle_callbacks=lifecycle,
            windows_sampler=RawSampler(),
            wsl_sampler=RawSampler(),
            recovery=ledger.callback("recovery"),
            invariant_probe=_all_invariants,
            monotonic_ns=scheduler.monotonic_ns,
            sleep=scheduler.sleep,
        )

    assert ledger.calls == []


def test_sampler_output_is_bound_to_unique_run_and_schedule() -> None:
    execution, *_rest = _run()
    first_windows = execution.windows_samples[0]
    last_wsl = execution.wsl_samples[-1]

    assert first_windows["run_uuid"] == RUN_UUID
    assert first_windows["scheduled_monotonic_ns"] == 8_000_000_000_000
    assert last_wsl["run_uuid"] == RUN_UUID
    assert last_wsl["scheduled_monotonic_ns"] == (
        8_000_000_000_000 + 1_799 * fresh.FRESH_CADENCE_NS
    )
