from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
GIT_ROOT = ROOT.parent
RUNNER = ROOT / "scripts/dev/run_s8_v4_s6bm_experiment.py"
VALIDATOR = ROOT / "scripts/dev/validate_s8_v4_s6bm.py"
REVIEW_WRITER = ROOT / "scripts/dev/write_s8_v4_s6bm_review.py"
CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml"


def load_runner():
    spec = importlib.util.spec_from_file_location("s6bm_experiment_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
            "triton_blue_success": {
                "response": {"status": "success", "data": {"result": []}}
            },
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
    runner = load_runner()
    config = SimpleNamespace(procedure={"drain_timeout_seconds": 15})
    body = {
        "schema_version": "evm.s8_v4.s6bm_predict_request.v1",
        "run_id": "s8-v4-s6bm-run-unit",
        "attempt_id": "s6bm-success-1-unit",
        "request_id": "request-unit",
        "request_nonce": "nonce-unit-00000001",
        "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        "input_values": [1.0, 2.0, 3.0, 4.0],
        "hold_ms": 1,
        "expected_model_role": "blue",
        "expected_model_name": "s6bm_blue",
        "expected_model_version": "1",
        "expected_artifact_sha256": "a" * 64,
        "expected_route_generation": 1,
        "causal_crossover": True,
    }
    observed_deadlines: list[float] = []

    monotonic_values = iter((100.0, 114.99, 115.01))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        runner,
        "find_triton_compute_start",
        lambda *_args, **_kwargs: observed_deadlines.append(1.0) or None,
    )

    with pytest.raises(runner.S6BMExperimentError, match="triton_compute_start_trace_timeout"):
        runner.wait_and_register_triton_start_receipt(
            config,
            suite_root=tmp_path,
            checkpoint={"trace_start_offset": 0},
            body=body,
            clock_chain=SimpleNamespace(),
        )

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
