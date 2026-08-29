from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.dev import diagnose_s8_v4_x1_dlrm_backend as diagnostic
from scripts.dev.run_s8_v4_x1_calibration import X1ExperimentError


def _bundle() -> dict[str, object]:
    trace = [
        "--trace-config=mode=opentelemetry",
        "--trace-config=opentelemetry,url=http://collector/v1/traces",
        "--trace-config=level=TIMESTAMPS",
        "--trace-config=rate=1",
        "--trace-config=count=-1",
    ]
    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "kind": "Deployment",
                "metadata": {"name": diagnostic.TRITON_NAME},
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "triton",
                                    "command": [
                                        "tritonserver",
                                        *[
                                            f"--load-model={model_id}"
                                            for model_id in diagnostic.base.MODEL_IDS
                                        ],
                                        *trace,
                                    ],
                                }
                            ]
                        }
                    }
                },
            },
            {"kind": "Service", "metadata": {"name": diagnostic.TRITON_NAME}},
            {"kind": "Deployment", "metadata": {"name": "evm-x1-api"}},
            {"kind": "Service", "metadata": {"name": "evm-x1-api"}},
        ],
    }


def _mode(*, passed: bool) -> dict[str, object]:
    return {
        "success_count": diagnostic.REPEATED_REQUESTS if passed else 1,
        "failure": None if passed else {"error_type": "ReadTimeout"},
    }


def _direct(*, passed: bool) -> dict[str, object]:
    return {
        "projection": {
            "request_count": diagnostic.REPEATED_REQUESTS if passed else 1,
            "correct_count": diagnostic.REPEATED_REQUESTS if passed else 1,
            "cuda_available": True,
        }
    }


def test_dlrm_diagnostic_trace_enabled_preserves_frozen_trace_args() -> None:
    source = _bundle()
    projected = diagnostic.triton_only_bundle(source, trace_enabled=True)
    assert source == _bundle()
    assert [item["kind"] for item in projected["items"]] == ["Deployment", "Service"]
    command = projected["items"][0]["spec"]["template"]["spec"]["containers"][0]["command"]
    assert len([value for value in command if value.startswith("--trace-config=")]) == 5


def test_dlrm_diagnostic_trace_disabled_removes_only_trace_args() -> None:
    enabled = diagnostic.triton_only_bundle(_bundle(), trace_enabled=True)
    disabled = diagnostic.triton_only_bundle(_bundle(), trace_enabled=False)
    enabled_command = enabled["items"][0]["spec"]["template"]["spec"]["containers"][0]["command"]
    disabled_command = disabled["items"][0]["spec"]["template"]["spec"]["containers"][0]["command"]
    assert disabled_command == [
        value for value in enabled_command if not value.startswith("--trace-config=")
    ]


def test_dlrm_diagnostic_rejects_trace_contract_drift() -> None:
    bundle = deepcopy(_bundle())
    bundle["items"][0]["spec"]["template"]["spec"]["containers"][0]["command"].pop()
    with pytest.raises(X1ExperimentError, match="x1_dlrm_diagnostic_trace_contract"):
        diagnostic.triton_only_bundle(bundle, trace_enabled=True)


def test_dlrm_diagnostic_selects_exact_loaded_model_set() -> None:
    source = diagnostic.triton_only_bundle(_bundle(), trace_enabled=False)
    all_models = diagnostic.select_loaded_models(source, model_ids=diagnostic.base.MODEL_IDS)
    dlrm_only = diagnostic.select_loaded_models(source, model_ids=(diagnostic.DLRM_MODEL_ID,))
    source_command = source["items"][0]["spec"]["template"]["spec"]["containers"][0]["command"]
    all_command = all_models["items"][0]["spec"]["template"]["spec"]["containers"][0]["command"]
    dlrm_command = dlrm_only["items"][0]["spec"]["template"]["spec"]["containers"][0]["command"]
    assert source_command == all_command
    assert [value for value in dlrm_command if value.startswith("--load-model=")] == [
        f"--load-model={diagnostic.DLRM_MODEL_ID}"
    ]


@pytest.mark.parametrize(
    "model_ids",
    [(), (diagnostic.DLRM_MODEL_ID, diagnostic.DLRM_MODEL_ID), ("unknown",)],
)
def test_dlrm_diagnostic_rejects_invalid_loaded_model_set(
    model_ids: tuple[str, ...],
) -> None:
    source = diagnostic.triton_only_bundle(_bundle(), trace_enabled=False)
    with pytest.raises(X1ExperimentError, match="x1_dlrm_diagnostic_loaded_model_set"):
        diagnostic.select_loaded_models(source, model_ids=model_ids)


def test_dlrm_diagnostic_sums_reason_labelled_failure_counters() -> None:
    labels = 'model="criteo_dlrm_lite",version="1"'
    metrics = "\n".join(
        [
            f"nv_inference_request_success{{{labels}}} 1",
            f'nv_inference_request_failure{{{labels},reason="REJECTED"}} 0',
            f'nv_inference_request_failure{{{labels},reason="OTHER"}} 0',
            f"nv_inference_count{{{labels}}} 1",
            f"nv_inference_exec_count{{{labels}}} 1",
            f"nv_inference_pending_request_count{{{labels}}} 1",
            f"nv_inference_compute_infer_duration_us{{{labels}}} 10",
            "",
        ]
    )
    assert diagnostic._model_metrics(metrics) == {
        "success": 1.0,
        "failure": 0.0,
        "inference": 1.0,
        "execution": 1.0,
        "pending": 1.0,
        "compute_us": 10.0,
    }


@pytest.mark.parametrize(
    (
        "direct_passed",
        "host_passed",
        "enabled_passed",
        "disabled_passed",
        "dlrm_only_passed",
        "expected",
    ),
    [
        (False, False, False, False, False, "host_direct_artifact_or_framework_failure"),
        (False, True, False, True, True, "triton_trace_pipeline_correlated_stall"),
        (
            True,
            True,
            False,
            False,
            True,
            "triton_four_model_coresidency_correlated_stall",
        ),
        (
            True,
            True,
            False,
            False,
            False,
            "triton_pytorch_backend_or_dlrm_artifact_interaction",
        ),
        (True, True, True, True, True, "stall_not_reproduced_in_bounded_isolation"),
        (True, True, True, False, True, "diagnostic_inconclusive"),
    ],
)
def test_dlrm_diagnostic_classification_is_bounded(
    direct_passed: bool,
    host_passed: bool,
    enabled_passed: bool,
    disabled_passed: bool,
    dlrm_only_passed: bool,
    expected: str,
) -> None:
    assert (
        diagnostic.classify_diagnostic(
            _direct(passed=direct_passed),
            _direct(passed=host_passed)["projection"],
            _mode(passed=enabled_passed),
            _mode(passed=disabled_passed),
            _mode(passed=dlrm_only_passed),
        )
        == expected
    )
