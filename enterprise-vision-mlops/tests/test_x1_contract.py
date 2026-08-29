from __future__ import annotations

import copy
from pathlib import Path

import pytest

from evm.scale_validation.x1_contract import (
    API_REPLICAS,
    CPU_WORKERS,
    KERNEL_OVERLAP_FALLBACK,
    MODEL_IDS,
    X1Contract,
    X1ContractError,
    compute_load_freeze,
    jain_service_attainment,
    validate_kernel_overlap,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")
CONFIG = ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml"


def contract() -> X1Contract:
    return X1Contract.from_path(CONFIG, source_root=ROOT, data_root=DATA_ROOT)


def model_calibrations() -> list[dict[str, object]]:
    return [
        {
            "model_id": model_id,
            "repetition": repetition,
            "safe_service_rps": 100.0 + index,
            "gpu_seconds_per_request": 0.001 + index * 0.0001,
        }
        for index, model_id in enumerate(MODEL_IDS)
        for repetition in (1, 2, 3)
    ]


def topology_calibrations() -> list[dict[str, object]]:
    return [
        {
            "topology_id": f"r{replicas}-w{workers}",
            "repetition": repetition,
            "safe_service_rps": float(150 + replicas * 20 + workers * 5),
        }
        for replicas in API_REPLICAS
        for workers in CPU_WORKERS
        for repetition in (1, 2, 3)
    ]


def test_x1_contract_freezes_exact_models_topology_and_matrix() -> None:
    loaded = contract()
    assert len(loaded.solo_calibration_cells()) == 12
    assert len(loaded.topology_calibration_cells()) == 18
    assert len(loaded.batching_calibration_cells()) == 24
    assert len(loaded.credit_matrix()) == 78
    assert len({cell.cell_id for cell in loaded.credit_matrix()}) == 78
    assert loaded.public_snapshot()["preliminary_isolation"]["reuse_forbidden"] is True


def test_x1_load_freeze_uses_minimum_seventy_percent_capacity() -> None:
    result = compute_load_freeze(
        contract(),
        model_calibrations=model_calibrations(),
        topology_calibrations=topology_calibrations(),
    )
    assert result["selection"] == "minimum_of_70_percent_gpu_api_cpu_capacity"
    assert result["selected_total_rps"] == min(result["capacity_candidates_rps"].values())
    assert set(result["balanced_target_rps"]) == set(MODEL_IDS)
    assert set(result["hot_target_rps"]) == set(MODEL_IDS)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda rows: rows.pop(), "x1_calibration_repetition_set:model_id"),
        (
            lambda rows: rows.append(copy.deepcopy(rows[0])),
            "x1_calibration_duplicate:model_id",
        ),
        (
            lambda rows: rows[0].__setitem__("safe_service_rps", float("nan")),
            "x1_finite:safe_service_rps",
        ),
        (
            lambda rows: rows[0].__setitem__("gpu_seconds_per_request", True),
            "x1_numeric:gpu_seconds_per_request",
        ),
    ],
)
def test_x1_load_freeze_rejects_incomplete_duplicate_or_nonfinite_calibration(
    mutation: object, reason: str
) -> None:
    rows = model_calibrations()
    mutation(rows)  # type: ignore[operator]
    with pytest.raises(X1ContractError, match=reason):
        compute_load_freeze(
            contract(),
            model_calibrations=rows,
            topology_calibrations=topology_calibrations(),
        )


def test_x1_jain_uses_service_to_frozen_target_attainment() -> None:
    targets = {model_id: 10.0 for model_id in MODEL_IDS}
    assert jain_service_attainment(targets, targets) == pytest.approx(1.0)
    skewed = dict(targets)
    skewed[MODEL_IDS[0]] = 1.0
    assert jain_service_attainment(skewed, targets) < 0.90


def test_x1_profiler_requires_nonzero_interval_for_distinct_identity() -> None:
    serial = [
        {
            "model_id": MODEL_IDS[0],
            "request_id": "request-a",
            "kernel_name": "kernel-a",
            "start_ns": 100,
            "end_ns": 200,
            "device_id": 0,
            "stream_id": 1,
        },
        {
            "model_id": MODEL_IDS[1],
            "request_id": "request-b",
            "kernel_name": "kernel-b",
            "start_ns": 200,
            "end_ns": 300,
            "device_id": 0,
            "stream_id": 2,
        },
    ]
    assert validate_kernel_overlap(serial) == KERNEL_OVERLAP_FALLBACK
    overlapping = copy.deepcopy(serial)
    overlapping[1]["start_ns"] = 199
    assert validate_kernel_overlap(overlapping) == "kernel_overlap_evidenced"


def test_x1_profiler_rejects_summary_only_or_nonfinite_shape() -> None:
    with pytest.raises(X1ContractError, match="x1_profiler_record_schema"):
        validate_kernel_overlap([{"overlap": True}])
