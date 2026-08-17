from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from evm.scale_validation.s3_higgs import file_sha256
from evm.scale_validation.s3_runtime import (
    PROBE_FAMILIES,
    REQUIRED_TRACE_SPANS,
    ReplayPayloadFactory,
    S3LoadPoint,
    S3RuntimeConfig,
    S3RuntimeError,
    evaluate_point_assertions,
    otlp_trace_summary,
    recalculate_s2_capacity,
    run_load_phase,
    summarize_load_phase,
    verify_runtime_identity,
    wait_for_otlp_trace_summary,
)


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CONFIG = ROOT / "configs" / "s3_capacity_runtime.toml"


def _runtime_fixture(tmp_path: Path) -> tuple[S3RuntimeConfig, dict[str, str]]:
    data_root = tmp_path / "data"
    replay_path = (
        data_root
        / "artifacts"
        / "scale_validation"
        / "s3"
        / "higgs-uci-2014-seed-20260817-v1"
        / "splits"
        / "replay"
        / "features.npy"
    )
    replay_path.parent.mkdir(parents=True)
    features = np.arange(28 * 64, dtype=np.float32).reshape(64, 28)
    np.save(replay_path, features, allow_pickle=False)
    split_manifest_path = replay_path.parents[2] / "split-manifest.json"
    split_manifest_path.write_text(
        json.dumps(
            {
                "samples": {
                    "replay": {"features_sha256": file_sha256(replay_path)}
                }
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    registry_path = (
        data_root / "artifacts" / "scale_validation" / "s3" / "capacity-registry.json"
    )
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "evm.s3_capacity_registry.v1",
                "experiment_config_sha256": (
                    "3d9869aa69033cab06d64c0d83c118a524d2291b0c2c624ecac03da465c2f468"
                ),
                "dataset_version": "uci-higgs-2014-s3-v1",
                "dataset_identity_sha256": "d" * 64,
                "split_manifest_sha256": file_sha256(split_manifest_path),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    config = S3RuntimeConfig.from_path(RUNTIME_CONFIG, data_root=data_root)
    return config, {
        "replay": file_sha256(replay_path),
        "manifest": file_sha256(split_manifest_path),
    }


def test_runtime_contract_builds_frozen_baseline_and_topology_matrix(
    tmp_path: Path,
) -> None:
    config, _ = _runtime_fixture(tmp_path)

    points = config.points()

    assert config.repetitions == 3
    assert len([point for point in points if point.matrix_scope == "baseline"]) == 45
    assert len([point for point in points if point.matrix_scope == "topology"]) == 11
    assert len(points) == 56
    assert {point.probe_family for point in points} == set(PROBE_FAMILIES)


def test_runtime_identity_fails_closed_on_registry_digest_mismatch(
    tmp_path: Path,
) -> None:
    config, expected = _runtime_fixture(tmp_path)
    observed = verify_runtime_identity(config)
    assert observed["replay_features_sha256"] == expected["replay"]
    assert observed["split_manifest_sha256"] == expected["manifest"]

    registry = json.loads(config.registry_path.read_text(encoding="utf-8"))
    registry["experiment_config_sha256"] = "0" * 64
    config.registry_path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(S3RuntimeError, match="preparation_config_registry_mismatch"):
        verify_runtime_identity(config)


def test_replay_payload_sequence_is_seeded_and_family_specific(
    tmp_path: Path,
) -> None:
    config, _ = _runtime_fixture(tmp_path)
    first = ReplayPayloadFactory(
        features_path=config.replay_features_path,
        dataset_identity_sha256="d" * 64,
        family="logistic",
        seed=config.seed,
        cache_size=32,
    )
    second = ReplayPayloadFactory(
        features_path=config.replay_features_path,
        dataset_identity_sha256="d" * 64,
        family="logistic",
        seed=config.seed,
        cache_size=32,
    )
    other = ReplayPayloadFactory(
        features_path=config.replay_features_path,
        dataset_identity_sha256="d" * 64,
        family="branch-heavy",
        seed=config.seed,
        cache_size=32,
    )

    assert first.sequence_sha256 == second.sequence_sha256
    assert first.body(0) == second.body(0)
    assert first.body(0) != other.body(0)


def test_load_summary_uses_fixed_measurement_window() -> None:
    phase = {
        "declared_duration_seconds": 10,
        "observed_elapsed_seconds": 11,
        "observations": [
            {
                "status_code": 200,
                "completed_offset_seconds": 9.9,
                "latency_ms": 10,
                "response_trace_id": "a",
                "trace_identity_matches": True,
                "server_timings": {},
                "runtime": {},
            },
            {
                "status_code": 200,
                "completed_offset_seconds": 10.1,
                "latency_ms": 20,
                "response_trace_id": "b",
                "trace_identity_matches": True,
                "server_timings": {},
                "runtime": {},
            },
        ],
    }

    summary = summarize_load_phase(phase)

    assert summary["successful_count"] == 2
    assert summary["successful_within_window"] == 1
    assert summary["service_rate_per_second"] == pytest.approx(0.1)


def test_transport_error_is_accounted_without_forging_server_trace() -> None:
    payload = {
        "measurement": {
            "declared_duration_seconds": 10,
            "observed_elapsed_seconds": 10,
            "stopped": False,
            "observations": [
                {
                    "request_index": 0,
                    "trace_id": "a" * 32,
                    "status_code": 200,
                    "transport_error": None,
                    "completed_offset_seconds": 1,
                    "latency_ms": 10,
                    "response_trace_id": "a" * 32,
                    "trace_identity_matches": True,
                    "server_timings": {},
                    "runtime": {},
                },
                {
                    "request_index": 1,
                    "trace_id": "b" * 32,
                    "status_code": 0,
                    "transport_error": "ReadTimeout",
                    "completed_offset_seconds": 2,
                    "latency_ms": 1000,
                    "response_trace_id": None,
                    "trace_identity_matches": False,
                    "server_timings": {},
                    "runtime": {},
                },
            ],
        },
        "trace": {
            "expected_sampled_trace_count": 1,
            "complete_sampled_trace_count": 1,
        },
        "cleanup": {
            "lingering_pid_count": 0,
            "marker_process_count": 0,
            "prometheus_container_absent": True,
        },
        "resource_samples": [
            {
                "api_process_tree_rss_bytes": 1,
                "load_generator_rss_bytes": 1,
            }
        ],
        "prometheus_targets": {"api": 1},
        "terminal_gauges": {"in_flight": 0},
    }

    assertions = evaluate_point_assertions(payload)

    assert assertions["transport_errors_accounted"]
    assert assertions["client_request_identity_complete"]
    assert assertions["response_trace_identity_complete"]


def test_load_summary_fails_identity_when_request_metadata_is_missing() -> None:
    phase = {
        "declared_duration_seconds": 10,
        "observed_elapsed_seconds": 10,
        "observations": [
            {
                "request_index": 0,
                "trace_id": None,
                "status_code": 200,
                "transport_error": None,
                "completed_offset_seconds": 1,
                "latency_ms": 10,
                "response_trace_id": None,
                "trace_identity_matches": False,
                "server_timings": {},
                "runtime": {},
            }
        ],
    }

    summary = summarize_load_phase(phase)

    assert summary["client_request_identity_count"] == 0
    assert summary["server_response_count"] == 1
    assert summary["trace_identity_match_count"] == 0


def test_trace_flush_polls_until_expected_chain_is_complete(
    monkeypatch,
    tmp_path: Path,
) -> None:
    observed = iter(
        [
            {
                "missing_sampled_trace_count": 1,
                "complete_sampled_trace_count": 0,
            },
            {
                "missing_sampled_trace_count": 0,
                "complete_sampled_trace_count": 1,
            },
        ]
    )
    monkeypatch.setattr(
        "evm.scale_validation.s3_runtime.otlp_trace_summary",
        lambda *args, **kwargs: next(observed),
    )
    monkeypatch.setattr(
        "evm.scale_validation.s3_runtime.time.sleep",
        lambda seconds: None,
    )

    summary = wait_for_otlp_trace_summary(
        tmp_path / "traces.jsonl",
        offset=0,
        expected_trace_ids=["a" * 32],
        timeout_seconds=1,
        poll_interval_seconds=0.01,
    )

    assert summary["flush_completed"]
    assert summary["flush_poll_count"] == 2


def test_open_arrival_records_each_task_once_and_bounds_pending(
    monkeypatch,
) -> None:
    active = 0
    peak = 0

    async def fake_send(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        index = kwargs["request_index"]
        return {
            "request_index": index,
            "status_code": 200,
            "transport_error": None,
            "started_offset_seconds": 0,
            "completed_offset_seconds": 0.01,
            "latency_ms": 10,
            "start_lag_ms": 0,
            "load_generator_permit_wait_ms": 0,
            "retry_after": None,
            "trace_id": f"{index:032x}",
            "trace_sampled": index == 0,
            "response_trace_id": f"{index:032x}",
            "trace_identity_matches": True,
            "server_timings": {},
            "runtime": {},
            "error_code": None,
        }

    monkeypatch.setattr(
        "evm.scale_validation.s3_runtime._send_capacity_request",
        fake_send,
    )

    class Payloads:
        @staticmethod
        def body(index: int) -> bytes:
            return str(index).encode("ascii")

    phase = asyncio.run(
        run_load_phase(
            point=S3LoadPoint(
                mode="open",
                probe_family="logistic",
                load=100.0,
                api_replicas=1,
                cpu_workers=1,
                matrix_scope="baseline",
            ),
            replicas=[type("Replica", (), {"base_url": "http://unused"})()],
            payloads=Payloads(),
            run_id="test-open",
            duration_seconds=0.1,
            client_max_in_flight=2,
            request_timeout_seconds=1,
            stop=type("Stop", (), {"event": asyncio.Event(), "reason": None})(),
            capture=True,
        )
    )

    assert phase["planned_request_count"] == 10
    assert (
        phase["request_count"] + phase["unscheduled_request_count"]
    ) == phase["planned_request_count"]
    assert len(phase["observations"]) == phase["request_count"]
    assert len({item["request_index"] for item in phase["observations"]}) == phase[
        "request_count"
    ]
    assert peak <= 2


def test_otlp_trace_summary_requires_full_cross_thread_chain(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.json"
    trace_id = "a" * 32
    spans = [{"traceId": trace_id, "name": name} for name in REQUIRED_TRACE_SPANS]
    trace_path.write_text(
        json.dumps(
            {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}
        )
        + "\n",
        encoding="utf-8",
    )

    summary = otlp_trace_summary(
        trace_path,
        offset=0,
        expected_trace_ids=[trace_id],
    )

    assert summary["complete_sampled_trace_count"] == 1
    assert summary["missing_sampled_trace_count"] == 0


def test_otlp_trace_summary_applies_outcome_specific_contracts(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "traces.json"
    success_trace = "a" * 32
    rejected_trace = "b" * 32
    transport_trace = "c" * 32
    spans = [
        {"traceId": success_trace, "name": name}
        for name in REQUIRED_TRACE_SPANS
    ] + [
        {"traceId": rejected_trace, "name": name}
        for name in {
            "POST /control-panel/v1/scenario-workloads/capacity-probes/predict",
            "s3.capacity.admission",
        }
    ]
    trace_path.write_text(
        json.dumps(
            {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}
        )
        + "\n",
        encoding="utf-8",
    )

    summary = otlp_trace_summary(
        trace_path,
        offset=0,
        expected_trace_contracts={
            success_trace: "full",
            rejected_trace: "admission",
            transport_trace: "client_only",
        },
    )

    assert summary["expected_sampled_trace_count"] == 3
    assert summary["expected_server_sampled_trace_count"] == 2
    assert summary["observed_sampled_trace_count"] == 2
    assert summary["complete_sampled_trace_count"] == 3
    assert summary["missing_sampled_trace_count"] == 0
    assert summary["complete_trace_contract_counts"] == {
        "admission": 1,
        "client_only": 1,
        "full": 1,
    }


def test_capacity_recalculation_is_conservative_without_auto_increase(
    tmp_path: Path,
) -> None:
    config, _ = _runtime_fixture(tmp_path)
    sustainable = {
        f"{family}:open": {
            "selected": True,
            "service_rate_per_second": 100 + index,
        }
        for index, family in enumerate(PROBE_FAMILIES)
    }

    result = recalculate_s2_capacity(sustainable, config)

    assert result["calculated_depth"] == 140
    assert result["selected_depth"] == config.prior_depth == 64
    assert result["rollback_depth"] == 64


def test_s3_runner_help_bootstraps_without_pythonpath() -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "scripts/dev/run_s3_capacity_experiment.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "S3 HIGGS CPU/API capacity matrix" in result.stdout
