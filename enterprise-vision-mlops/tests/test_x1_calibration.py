from __future__ import annotations

import copy
from pathlib import Path

import pytest

from evm.scale_validation.x1_calibration import X1CalibrationError, project_calibration_attempt
from evm.scale_validation.x1_contract import X1Contract


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")


def contract() -> X1Contract:
    return X1Contract.from_path(
        ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml",
        source_root=ROOT,
        data_root=DATA_ROOT,
    )


def raw_attempt() -> dict[str, object]:
    attempt = "x1-solo-logistic-rep1"
    steps = []
    for step_index, offered_rps in enumerate((25, 50, 100, 200, 400, 800)):
        runtime_attempt = f"{attempt}-step-{step_index:02d}"
        requests = []
        effects = []
        trace_entries = []
        for sequence in range(4):
            request_id = f"{attempt}-s{step_index:02d}-{sequence:08d}"
            trace_id = f"{step_index:02x}{sequence:02x}" + "a" * 28
            effect_id = f"effect-{step_index}-{sequence}"
            topology = {
                "pod_uid": "pod-a",
                "pod_name": "pod-a-name",
                "service_instance_id": "pod-a",
                "worker_pid": 101,
                "worker_thread_id": 202,
                "worker_slot": "pod-a:1",
                "api_replicas_expected": 1,
                "cpu_workers_expected": 1,
            }
            requests.append(
                {
                    "suite_id": "x1-unit-suite",
                    "attempt_id": runtime_attempt,
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "model_id": "higgs_logistic_regression",
                    "admission_outcome": "accepted",
                    "rejection_reason": "",
                    "status_code": 200,
                    "terminal_outcome": "completed",
                    "enqueued_ns": 1_000_000_000 + sequence * 10_000_000,
                    "started_ns": 1_000_000_000 + sequence * 10_000_000,
                    "finished_ns": 1_005_000_000 + sequence * 10_000_000,
                    "latency_ms": 5.0,
                    "queue_wait_ms": 0.1,
                    "prediction_ms": 2.0,
                    "effect_id": effect_id,
                    "result_sha256": "c" * 64,
                    "model_version": "1",
                    "artifact_sha256": "a" * 64,
                    "config_sha256": "b" * 64,
                    "runtime_device": "cuda",
                    "triton_instance_kind": "KIND_GPU",
                    "triton_instance_count": 1,
                    "triton_gpu_device": 0,
                    "topology": topology,
                    "cpu_fallback_detected": False,
                    "oom_detected": False,
                    "outcome_unknown": False,
                }
            )
            effects.append(
                {
                    "entity_id": effect_id,
                    "state": "completed",
                    "payload": {
                        "schema_version": "evm.s8_v4.x1_terminal_effect.v1",
                        "suite_id": "x1-unit-suite",
                        "attempt_id": runtime_attempt,
                        "request_id": request_id,
                        "trace_id": trace_id,
                        "effect_id": effect_id,
                        "model_id": "higgs_logistic_regression",
                        "model_version": "1",
                        "artifact_sha256": "a" * 64,
                        "config_sha256": "b" * 64,
                        "runtime_device": "cuda",
                        "triton_instance_kind": "KIND_GPU",
                        "triton_instance_count": 1,
                        "triton_gpu_device": 0,
                        "result_sha256": "c" * 64,
                        "terminal_outcome": "completed",
                        "topology": topology,
                        "lease_id": "lease-unit",
                        "fencing_token_sha256": "d" * 64,
                    },
                    "entity_created_at": "2026-08-30T00:00:00Z",
                    "entity_updated_at": "2026-08-30T00:00:00Z",
                    "scope": f"x1.terminal-effect.{runtime_attempt}",
                    "idempotency_key": request_id,
                    "request_sha256": "e" * 64,
                    "idempotency_created_at": "2026-08-30T00:00:00Z",
                    "captured_at": "2026-08-30T00:00:01Z",
                }
            )
            trace_attributes = {
                "evm.x1.suite_id": "x1-unit-suite",
                "evm.x1.attempt_id": runtime_attempt,
                "evm.x1.request_id": request_id,
                "evm.x1.model_id": "higgs_logistic_regression",
                "evm.x1.model_version": "1",
                "evm.x1.artifact_sha256": "a" * 64,
                "evm.x1.effect_id": effect_id,
                "evm.x1.runtime_device": "cuda",
                "evm.x1.pod_uid": "pod-a",
                "evm.terminal.outcome": "completed",
            }
            trace_entries.append(
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.instance.id",
                                "value": {"stringValue": "pod-a"},
                            }
                        ]
                    },
                    "scope": {"name": "unit"},
                    "span": {
                        "traceId": trace_id,
                        "spanId": "f" * 16,
                        "name": "POST /control-panel/v1/scenario-workloads/x1/predict",
                        "attributes": [
                            {"key": key, "value": {"stringValue": value}}
                            for key, value in trace_attributes.items()
                        ],
                    },
                }
            )
        steps.append(
            {
                "offered_rps": offered_rps,
                "runtime_attempt_id": runtime_attempt,
                "measurement_window": {"start_ns": 1_000_000_000, "end_ns": 2_000_000_000},
                "requests": requests,
                "durable_effects": effects,
                "trace_export": {
                    "schema_version": "evm.s8_v4.x1_raw_otlp_export.v1",
                    "attempt_id": runtime_attempt,
                    "entries": trace_entries,
                },
                "triton_metrics": {
                    "model_ids": ["higgs_logistic_regression"],
                    "before_raw": _metric_text(10, 10, 10, 1000),
                    "after_raw": _metric_text(14, 14, 12, 3000),
                },
                "gpu_samples": [
                    {
                        "gpu_uuid": "GPU-unit",
                        "gpu_name": "RTX 4080",
                        "utilization_percent": 50.0,
                        "memory_used_mib": 1000.0,
                        "memory_total_mib": 16384.0,
                    }
                ],
            }
        )
    return {
        "attempt_id": attempt,
        "mode": "solo_calibration",
        "model_id": "higgs_logistic_regression",
        "repetition": 1,
        "topology": {"api_replicas": 1, "cpu_workers": 1},
        "topology_readback": {
            "triton_pods_ready": 1,
            "triton_gpu_limits": 1,
            "api_pods_ready": 1,
            "api_pod_count": 1,
            "terminating_api_pod_uids": [],
            "observed_api_pod_uids": ["pod-a"],
            "api_endpoint_pod_uids": ["pod-a"],
            "not_ready_api_endpoint_pod_uids": [],
            "observed_worker_slots_by_pod": {"pod-a": ["pod-a:1"]},
            "client_lanes_are_server_workers": False,
        },
        "steps": steps,
    }


def _metric_text(success: int, inference: int, execution: int, compute: int) -> str:
    labels = 'model="higgs_logistic_regression",version="1"'
    return "\n".join(
        (
            f"nv_inference_request_success{{{labels}}} {success}",
            f"nv_inference_count{{{labels}}} {inference}",
            f"nv_inference_exec_count{{{labels}}} {execution}",
            f"nv_inference_compute_infer_duration_us{{{labels}}} {compute}",
            "",
        )
    )


def test_x1_calibration_recomputes_raw_counts_latency_metrics_and_topology() -> None:
    projected = project_calibration_attempt(raw_attempt(), contract())
    assert projected["safe_service_rps"] == 4.0
    assert projected["steps"][-1]["terminal"] == 4
    assert projected["steps"][-1]["p99_ms"] == 5.0
    assert projected["steps"][-1]["formed_batch_mean"] == 2.0


def test_x1_calibration_separates_measured_cohort_from_completed_drain_tail() -> None:
    value = raw_attempt()
    for step in value["steps"]:  # type: ignore[index]
        request = step["requests"][-1]
        request["started_ns"] = 2_000_000_000
        request["finished_ns"] = 2_005_000_000

    projected = project_calibration_attempt(value, contract())

    assert projected["safe_service_rps"] == 3.0
    assert all(step["late_terminal"] == 1 for step in projected["steps"])
    assert all(step["terminal"] == step["accepted"] for step in projected["steps"])
    assert all(step["lost"] == 0 for step in projected["steps"])


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (
            lambda value: value["steps"][0]["requests"][0].__setitem__("effect_id", "other"),
            "x1_calibration_effect_join",
        ),
        (
            lambda value: value["steps"][0]["requests"][0].__setitem__("latency_ms", 4.0),
            "x1_calibration_latency_recompute",
        ),
        (
            lambda value: value["steps"][0]["gpu_samples"][0].__setitem__(
                "utilization_percent", float("nan")
            ),
            "x1_calibration_finite:utilization_percent",
        ),
        (
            lambda value: value["topology_readback"]["observed_worker_slots_by_pod"][
                "pod-a"
            ].clear(),
            "x1_runtime_worker_count",
        ),
        (
            lambda value: value["steps"][0]["triton_metrics"].__setitem__(
                "after_raw", _metric_text(9, 14, 12, 3000)
            ),
            "x1_calibration_counter_decrease:success_count",
        ),
        (
            lambda value: value["steps"][0]["durable_effects"][0].__setitem__("state", "failed"),
            "x1_calibration_effect_wrapper_identity",
        ),
        (
            lambda value: value["steps"][0]["trace_export"].__setitem__("entries", []),
            "x1_calibration_trace_request_set",
        ),
        (
            lambda value: value["steps"][0]["trace_export"]["entries"][0]["span"]["attributes"][6][
                "value"
            ].__setitem__("stringValue", "wrong-effect"),
            "x1_calibration_trace_attribute_join",
        ),
        (
            lambda value: value["steps"][0]["triton_metrics"].__setitem__(
                "after_raw",
                value["steps"][0]["triton_metrics"]["after_raw"].replace(
                    'nv_inference_count{model="higgs_logistic_regression",version="1"} 14\n',
                    "",
                ),
            ),
            "x1_calibration_metric_cardinality:higgs_logistic_regression:inference_count:0",
        ),
    ],
)
def test_x1_calibration_mutations_fail_closed(mutator: object, reason: str) -> None:
    value = copy.deepcopy(raw_attempt())
    mutator(value)  # type: ignore[operator]
    with pytest.raises((X1CalibrationError, RuntimeError), match=reason):
        project_calibration_attempt(value, contract())
