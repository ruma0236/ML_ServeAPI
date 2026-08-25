from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evm.scale_validation import s6bm_trace_receipt_collector as trace_collector


ROOT = Path(__file__).resolve().parents[1]
GIT_ROOT = ROOT.parent
RUNNER = ROOT / "scripts/dev/run_s8_v4_s6bm_experiment.py"
VALIDATOR = ROOT / "scripts/dev/validate_s8_v4_s6bm.py"
REVIEW_WRITER = ROOT / "scripts/dev/write_s8_v4_s6bm_review.py"
CONTINUITY_VALIDATOR = ROOT / "scripts/dev/validate_s8_v4_s6bm_continuity_qualification.py"
CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml"


def load_runner():
    spec = importlib.util.spec_from_file_location("s6bm_experiment_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_continuity_validator():
    spec = importlib.util.spec_from_file_location("s6bm_continuity_validator", CONTINUITY_VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_continuity_mutation_contract_is_exact_and_frozen() -> None:
    validator = load_continuity_validator()
    assert len(validator.CASE_CONTRACT) == len(validator.MUTATIONS) == 19
    assert len({case_id for case_id, _reason in validator.CASE_CONTRACT}) == 19
    assert validator.canonical_sha256(validator.CASE_CONTRACT) == (
        "230ff21035dace5eb498d649d69a1bb063c377c95808d8d9bcae5903edd13a6e"
    )
    assert validator.canonical_sha256(validator.HISTORICAL_CASE_CONTRACT) == (
        "c42a6245d1e48152d06c6f1bd31c7fca8de58f201129c99bb7c59abe9356d4d7"
    )


def load_validator():
    spec = importlib.util.spec_from_file_location("s6bm_evidence_validator", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_review_writer():
    spec = importlib.util.spec_from_file_location("s6bm_review_writer", REVIEW_WRITER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_git_blob_hash_uses_parent_repository_root() -> None:
    runner = load_runner()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    repository_path = CONFIG.relative_to(GIT_ROOT).as_posix()
    blob = subprocess.run(
        ["git", "show", f"{revision}:{repository_path}"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
    ).stdout

    assert runner.git_blob_sha256(revision, CONFIG) == hashlib.sha256(blob).hexdigest()


def test_validator_reads_config_blob_from_parent_repository_root() -> None:
    validator = load_validator()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    repository_path = CONFIG.relative_to(GIT_ROOT).as_posix()
    blob = subprocess.run(
        ["git", "show", f"{revision}:{repository_path}"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
    ).stdout

    assert validator.git_bytes(revision, CONFIG) == blob


def test_review_writer_separates_python_and_ui_pass_counts() -> None:
    writer = load_review_writer()
    log = "77 passed in 10.0s\nTest Files 17 passed\nTests 59 passed (59)\n"

    assert writer.pytest_counts(log, occurrence=0) == (77, 0)
    assert writer.pytest_counts(log) == (59, 0)


def test_send_batch_reuses_bounded_per_worker_sessions(monkeypatch) -> None:
    runner = load_runner()
    created = []
    seen: list[int] = []
    seen_lock = threading.Lock()

    class FakeSession:
        def __init__(self) -> None:
            self.closed = False
            created.append(self)

        def mount(self, _prefix: str, _adapter: object) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    def fake_send(_config, body, *, session=None):
        time.sleep(0.002)
        with seen_lock:
            seen.append(id(session))
        return {"request_id": body["request_id"]}

    monkeypatch.setattr(runner.requests, "Session", FakeSession)
    monkeypatch.setattr(runner, "send_request", fake_send)

    class Model:
        model_name = "s6bm_blue"
        model_version = "1"
        artifact_sha256 = "a" * 64

    class Config:
        blue = Model()
        green = Model()

    records, bodies = runner.send_batch(
        Config(),
        "run-id",
        "attempt-id",
        "batch",
        24,
        3,
        expected_model_role="blue",
    )

    assert len(records) == len(bodies) == 24
    assert 1 <= len(created) <= 3
    assert set(seen) == {id(session) for session in created}
    assert all(session.closed for session in created)


def test_fixed_bridge_producer_preserves_schedule_and_bounded_capacity(monkeypatch) -> None:
    runner = load_runner()
    actor_receipts_ready = threading.Event()
    switch_released = threading.Event()
    observed_request_ids: set[str] = set()
    observed_lock = threading.Lock()

    class FakeSession:
        def mount(self, _prefix: str, _adapter: object) -> None:
            return None

        def close(self) -> None:
            return None

    def fake_send(_config, body, *, session=None):
        assert session is not None
        attempted = time.perf_counter()
        with observed_lock:
            observed_request_ids.add(body["request_id"])
            if len(observed_request_ids) == 3:
                actor_receipts_ready.set()
        if body.get("causal_crossover"):
            assert switch_released.wait(timeout=2)
        time.sleep(0.003)
        return {
            "request_id": body["request_id"],
            "model_role": "blue",
            "attempted_monotonic": attempted,
            "completed_monotonic": time.perf_counter(),
            "outcome": "completed",
            "status_code": 200,
        }

    monkeypatch.setattr(runner.requests, "Session", FakeSession)
    monkeypatch.setattr(runner, "send_request", fake_send)
    config = SimpleNamespace(
        continuity={
            "producer_workers": 2,
            "max_in_flight_requests": 2,
            "max_request_payload_bytes": 4096,
            "max_in_flight_payload_bytes": 8192,
        }
    )
    schedule = [
        {"request_id": f"bridge-{index}", "scheduled_offset_ms": index * 5, "hold_ms": 0}
        for index in range(3)
    ]
    bodies = [
        {
            "request_id": item["request_id"],
            "hold_ms": 0,
            "value": index,
            "causal_crossover": index == 0,
        }
        for index, item in enumerate(schedule)
    ]

    def collect_receipts():
        assert actor_receipts_ready.wait(timeout=2)
        return {"receipt": "actor"}

    def transition(receipt_proof, terminal_gate):
        assert receipt_proof == {"receipt": "actor"}
        assert terminal_gate["expected_terminal_request_ids"] == [
            "bridge-1",
            "bridge-2",
        ]
        switch_released.set()
        return {
            "transition_receipt_observed_monotonic": time.perf_counter(),
            "receipt": "actor",
            "observed_request_ids": sorted(observed_request_ids),
        }

    records, evidence, receipt = runner.run_fixed_bridge_producer(
        config, bodies, schedule, collect_receipts, transition
    )

    assert [item["request_id"] for item in records] == [item["request_id"] for item in schedule]
    assert receipt["receipt"] == "actor"
    assert receipt["observed_request_ids"] == ["bridge-0", "bridge-1", "bridge-2"]
    assert evidence["adaptive_pacing"] is False
    assert evidence["switch_gate_basis"] == (
        "all40_schedule_plus_exact39_blue_terminal_plus_exact4x3_receipts_"
        "plus_exact2_pending_crossovers"
    )
    assert evidence["pre_switch_terminal_gate"]["observed_terminal_count"] == 2
    assert evidence["max_reserved_requests_observed"] <= 2
    assert evidence["max_reserved_payload_bytes_observed"] <= 8192
    assert evidence["reserved_requests_at_finish"] == 0
    assert evidence["reserved_payload_bytes_at_finish"] == 0
    assert (
        evidence["producer_finished_monotonic"] >= evidence["transition_receipt_observed_monotonic"]
    )


def test_bridge_switch_waits_for_every_non_crossover_terminal_after_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = load_runner()
    all_started = threading.Event()
    release_last = threading.Event()
    release_crossover = threading.Event()
    switch_called = threading.Event()
    started: set[str] = set()
    lock = threading.Lock()

    class FakeSession:
        def mount(self, _prefix: str, _adapter: object) -> None:
            return None

        def close(self) -> None:
            return None

    def fake_send(_config, body, *, session=None):
        assert session is not None
        attempted = time.perf_counter()
        with lock:
            started.add(body["request_id"])
            if len(started) == 4:
                all_started.set()
        if body["request_id"] == "bridge-3":
            assert release_last.wait(timeout=2)
        if body.get("causal_crossover"):
            assert release_crossover.wait(timeout=2)
        return {
            "request_id": body["request_id"],
            "model_role": "blue",
            "attempted_monotonic": attempted,
            "completed_monotonic": time.perf_counter(),
            "outcome": "completed",
            "status_code": 200,
        }

    monkeypatch.setattr(runner.requests, "Session", FakeSession)
    monkeypatch.setattr(runner, "send_request", fake_send)
    config = SimpleNamespace(
        continuity={
            "producer_workers": 4,
            "max_in_flight_requests": 4,
            "max_request_payload_bytes": 4096,
            "max_in_flight_payload_bytes": 16384,
        }
    )
    schedule = [
        {"request_id": f"bridge-{index}", "scheduled_offset_ms": index, "hold_ms": 0}
        for index in range(4)
    ]
    bodies = [
        {
            "request_id": item["request_id"],
            "hold_ms": 0,
            "causal_crossover": index == 0,
        }
        for index, item in enumerate(schedule)
    ]

    def switch(_receipts, terminal_gate):
        assert terminal_gate["observed_terminal_request_ids"] == [
            "bridge-1",
            "bridge-2",
            "bridge-3",
        ]
        switch_called.set()
        release_crossover.set()
        return {"transition_receipt_observed_monotonic": time.perf_counter()}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            runner.run_fixed_bridge_producer,
            config,
            bodies,
            schedule,
            lambda: {"receipts": "ready"},
            switch,
        )
        assert all_started.wait(timeout=2)
        assert not switch_called.wait(timeout=0.05)
        release_last.set()
        records, evidence, _receipt = future.result(timeout=3)

    assert switch_called.is_set()
    assert len(records) == 4
    assert evidence["pre_switch_terminal_gate"]["observed_terminal_count"] == 3


def test_bridge_receipt_gate_preserves_exact_pre_switch_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = load_runner()
    attempt_id = "s6bm-success-bridge-readback"
    request_ids = [f"bridge-{index}" for index in range(4)]
    stages = (
        "api_server_handler_entry",
        "controller_entry",
        "triton_backend_compute_entry",
    )
    rows = []
    for sequence, (request_id, stage) in enumerate(
        ((request_id, stage) for request_id in request_ids for stage in stages),
        start=1,
    ):
        payload = {
            "attempt_id": attempt_id,
            "run_id": "run-bridge-readback",
            "request_id": request_id,
            "request_nonce": hashlib.sha256(request_id.encode("ascii")).hexdigest()[:32],
            "trace_id": f"{sequence:032x}",
            "effect_id": f"{sequence:064x}",
            "model_role": "blue",
            "model_name": "s6bm_blue",
            "model_version": "1",
            "artifact_sha256": "a" * 64,
            "route_generation": 2,
        }
        rows.append(
            {
                "causal_sequence": sequence,
                "event_type": stage,
                **payload,
                "actor_identity": f"actor:{stage}",
                "payload_sha256": runner.canonical_sha256(payload),
                "payload": payload,
                "transaction_id": str(10_000 + sequence),
                "database_recorded_at": f"2026-08-25T00:00:00.{sequence:06d}Z",
                "captured_at": "2026-08-25T00:00:01.000000Z",
            }
        )
    export = {
        "schema_version": "evm.s8_v4.s6bm_causal_event_export.v1",
        "attempt_id": attempt_id,
        "event_count": len(rows),
        "events": rows,
    }

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return export

    monkeypatch.setattr(runner.requests, "get", lambda *_args, **_kwargs: Response())
    gate = runner.read_required_bridge_start_receipts(
        SimpleNamespace(ports={"api": 1}),
        suite_root=tmp_path,
        attempt_id=attempt_id,
        required_request_ids=request_ids,
        route_generation=2,
    )

    readback_path = tmp_path / gate["raw_readback_export"]["path"]
    assert json.loads(readback_path.read_text(encoding="utf-8")) == export
    assert gate["visible_event_count"] == 12
    assert gate["selected_event_set_sha256"] == runner.canonical_sha256(gate["events"])
    assert all(item["readback_visible"] is True for item in gate["events"])
    assert all(item["readback_at"] == rows[0]["captured_at"] for item in gate["events"])


def test_expected_attempt_trace_counts_include_exact_controller_only_replay() -> None:
    runner = load_runner()
    attempt = {
        "attempt_id": "s6bm-success-1-unit",
        "request_records": [
            {"request_id": "request-0001"},
            {"request_id": "request-0002"},
        ],
        "idempotent_replay": {
            "request_id": "request-0001",
            "replayed": True,
            "unique_count_before": 2,
            "unique_count_after": 2,
            "record": {"request_id": "request-0001", "replayed": True},
        },
    }

    assert runner.expected_attempt_trace_counts(attempt) == {
        "controller": 3,
        "inference": 2,
    }

    attempt["idempotent_replay"]["unique_count_after"] = 3
    with pytest.raises(runner.S6BMExperimentError, match="otlp_replay_identity"):
        runner.expected_attempt_trace_counts(attempt)


def test_expected_attempt_trace_counts_support_non_replay_qualification() -> None:
    runner = load_runner()
    attempt = {
        "attempt_id": "s6bm-causal-qualification-unit",
        "request_records": [{"request_id": "request-0001"}],
    }

    assert runner.expected_attempt_trace_counts(attempt) == {
        "controller": 1,
        "inference": 1,
    }

    attempt["request_records"].append({"request_id": "request-0001"})
    with pytest.raises(runner.S6BMExperimentError, match="otlp_replay_identity"):
        runner.expected_attempt_trace_counts(attempt)


def test_prometheus_direct_comparison_reports_exact_series_failure() -> None:
    runner = load_runner()
    config = SimpleNamespace(
        blue=SimpleNamespace(model_name="s6bm_blue", model_version="1"),
        green=SimpleNamespace(model_name="s6bm_green", model_version="1"),
    )
    api_metrics = "\n".join(
        [
            'evm_s6bm_requests_total{model_name="s6bm_blue",model_role="blue",model_version="1",outcome="completed"} 10',
            'evm_s6bm_terminal_effects_total{model_name="s6bm_blue",model_role="blue",model_version="1",outcome="committed"} 10',
            'evm_s6bm_requests_total{model_name="s6bm_green",model_role="green",model_version="1",outcome="completed"} 0',
            'evm_s6bm_terminal_effects_total{model_name="s6bm_green",model_role="green",model_version="1",outcome="committed"} 0',
        ]
    )
    triton_metrics = "\n".join(
        [
            'nv_inference_request_success{model="s6bm_blue",version="1"} 10',
            'nv_inference_request_success{model="s6bm_green",version="1"} 0',
            'nv_inference_request_success{gpu_uuid="GPU-unit",model="s6bm_green",version="1"} 0',
        ]
    )
    common = {"scenario": "s8-v4-s6bm", "attempt_id": "attempt-unit"}

    def query(metric: dict[str, str], value: int) -> dict[str, object]:
        return {
            "response": {
                "status": "success",
                "data": {"result": [{"metric": {**common, **metric}, "value": [1, str(value)]}]},
            }
        }

    snapshot = {
        "queries": {
            "api_blue_completed": query(
                {
                    "model_name": "s6bm_blue",
                    "model_role": "blue",
                    "model_version": "1",
                    "outcome": "completed",
                },
                10,
            ),
            "api_blue_effect": query(
                {
                    "model_name": "s6bm_blue",
                    "model_role": "blue",
                    "model_version": "1",
                    "outcome": "committed",
                },
                10,
            ),
            "triton_blue_success": query({"model": "s6bm_blue", "version": "1"}, 10),
            "api_green_completed": query(
                {
                    "model_name": "s6bm_green",
                    "model_role": "green",
                    "model_version": "1",
                    "outcome": "completed",
                },
                0,
            ),
            "api_green_effect": query(
                {
                    "model_name": "s6bm_green",
                    "model_role": "green",
                    "model_version": "1",
                    "outcome": "committed",
                },
                0,
            ),
            "triton_green_success": {
                "response": {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "metric": {
                                    **common,
                                    "model": "s6bm_green",
                                    "version": "1",
                                },
                                "value": [1, "0"],
                            },
                            {
                                "metric": {
                                    **common,
                                    "model": "s6bm_green",
                                    "version": "1",
                                    "gpu_uuid": "GPU-unit",
                                },
                                "value": [1, "0"],
                            },
                        ]
                    },
                }
            },
        }
    }

    assert runner.prometheus_direct_comparison(
        config, snapshot, api_metrics, triton_metrics, "attempt-unit"
    )["passed"]

    missing_green = triton_metrics.splitlines()[0]
    failed = runner.prometheus_direct_comparison(
        config, snapshot, api_metrics, missing_green, "attempt-unit"
    )
    assert failed["passed"] is False
    assert any("s6bm_direct_metric_aggregate" in error for error in failed["errors"])


def test_prometheus_direct_comparison_accepts_only_explicit_unloaded_blue() -> None:
    runner = load_runner()
    config = SimpleNamespace(
        blue=SimpleNamespace(model_name="s6bm_blue", model_version="1"),
        green=SimpleNamespace(model_name="s6bm_green", model_version="1"),
    )
    api_metrics = "\n".join(
        [
            'evm_s6bm_requests_total{model_name="s6bm_blue",model_role="blue",model_version="1",outcome="completed"} 1',
            'evm_s6bm_terminal_effects_total{model_name="s6bm_blue",model_role="blue",model_version="1",outcome="committed"} 1',
            'evm_s6bm_requests_total{model_name="s6bm_green",model_role="green",model_version="1",outcome="completed"} 0',
            'evm_s6bm_terminal_effects_total{model_name="s6bm_green",model_role="green",model_version="1",outcome="committed"} 0',
        ]
    )
    triton_metrics = 'nv_inference_request_success{model="s6bm_green",version="1"} 20'
    common = {"scenario": "s8-v4-s6bm", "attempt_id": "attempt-unloaded"}

    def query(metric: dict[str, str], value: int) -> dict[str, object]:
        return {
            "response": {
                "status": "success",
                "data": {"result": [{"metric": {**common, **metric}, "value": [1, str(value)]}]},
            }
        }

    snapshot = {
        "queries": {
            "api_blue_completed": query(
                {
                    "model_name": "s6bm_blue",
                    "model_role": "blue",
                    "model_version": "1",
                    "outcome": "completed",
                },
                1,
            ),
            "api_blue_effect": query(
                {
                    "model_name": "s6bm_blue",
                    "model_role": "blue",
                    "model_version": "1",
                    "outcome": "committed",
                },
                1,
            ),
            "triton_blue_success": {"response": {"status": "success", "data": {"result": []}}},
            "api_green_completed": query(
                {
                    "model_name": "s6bm_green",
                    "model_role": "green",
                    "model_version": "1",
                    "outcome": "completed",
                },
                0,
            ),
            "api_green_effect": query(
                {
                    "model_name": "s6bm_green",
                    "model_role": "green",
                    "model_version": "1",
                    "outcome": "committed",
                },
                0,
            ),
            "triton_green_success": query(
                {"model": "s6bm_green", "version": "1"},
                20,
            ),
        }
    }

    passed = runner.prometheus_direct_comparison(
        config,
        snapshot,
        api_metrics,
        triton_metrics,
        "attempt-unloaded",
        expected_absent_triton_roles=frozenset({"blue"}),
    )
    assert passed["passed"] is True

    contaminated = triton_metrics + (
        '\nnv_inference_request_success{model="s6bm_blue",version="1"} 1'
    )
    failed = runner.prometheus_direct_comparison(
        config,
        snapshot,
        api_metrics,
        contaminated,
        "attempt-unloaded",
        expected_absent_triton_roles=frozenset({"blue"}),
    )
    assert failed["passed"] is False
    assert "s6bm_triton_expected_absent:blue" in failed["errors"]


def test_triton_receipt_wait_uses_frozen_drain_bound(monkeypatch, tmp_path: Path) -> None:
    spec = {
        "schema_version": "evm.s8_v4.s6bm_trace_collector_spec.v1",
        "runner_process_id": os.getppid(),
        "timeout_seconds": 15,
        "otel_trace_path": str(tmp_path / "traces.json"),
        "trace_start_offset": 0,
        "source_revision": "a" * 40,
        "suite_id": "suite-unit",
        "attempt_id": "s6bm-success-1-unit",
        "request_id": "request-unit",
        "request_nonce": "nonce-unit-00000001",
        "trace_id": "1" * 32,
        "model_name": "s6bm_blue",
        "model_version": "1",
    }
    spec_path = tmp_path / "collector-spec.json"
    spec_path.write_text(
        json.dumps(spec, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    observed_deadlines: list[float] = []

    monotonic_values = iter((100.0, 114.99, 115.01))
    monkeypatch.setattr(trace_collector.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(trace_collector.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        trace_collector,
        "find_triton_compute_start",
        lambda *_args, **_kwargs: observed_deadlines.append(1.0) or None,
    )

    with pytest.raises(
        trace_collector.S6BMTraceCollectorError,
        match="triton_compute_start_trace_timeout",
    ):
        trace_collector.collect(spec_path)

    assert len(observed_deadlines) == 1


def test_telemetry_snapshot_waits_for_exact_attempt_targets(monkeypatch) -> None:
    runner = load_runner()
    config = SimpleNamespace(
        ports={"triton_metrics": 18002},
        telemetry={
            "prometheus_job_api": "evm-s8-v4-s6bm-api",
            "prometheus_job_triton": "evm-s8-v4-s6bm-triton",
        },
    )

    def targets(attempt_id: str) -> list[dict[str, object]]:
        return [
            {
                "health": "up",
                "labels": {
                    "job": job,
                    "scenario": "s8-v4-s6bm",
                    "suite_id": "suite-unit",
                    "attempt_id": attempt_id,
                },
            }
            for job in ("evm-s8-v4-s6bm-api", "evm-s8-v4-s6bm-triton")
        ]

    snapshots = [targets("stale-attempt"), targets("current-attempt")]
    monkeypatch.setattr(
        runner,
        "prometheus_targets",
        lambda: snapshots.pop(0) if snapshots else targets("current-attempt"),
    )
    monkeypatch.setattr(runner, "prometheus_query", lambda _query: 1.0)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        runner.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(text="nv_inference_request_success 1"),
    )

    snapshot = runner.telemetry_snapshot(
        config,
        suite_id="suite-unit",
        attempt_id="current-attempt",
        timeout=1,
    )

    assert snapshot["target_count"] == 2
    assert snapshot["attempt_id"] == "current-attempt"
    assert {item["attempt_id"] for item in snapshot["target_labels"]} == {"current-attempt"}
