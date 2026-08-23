from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from evm.scale_validation.s4_evidence import (
    S4EvidenceValidationError,
    validate_s4_gpu_batching_closure,
    validate_s4_gpu_batching_evidence,
)
from evm.scale_validation.s4_runtime import S4RuntimeConfig


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/status/evidence/s4-gpu-batching-experiment.json"
CLOSURE = ROOT / "docs/status/evidence/s4-gpu-batching-closure.json"
CONFIG = ROOT / "configs/s4_gpu_batching_runtime.toml"
DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")


def _payload_and_config() -> tuple[dict, S4RuntimeConfig]:
    return (
        json.loads(EVIDENCE.read_text(encoding="utf-8")),
        S4RuntimeConfig.from_path(CONFIG, data_root=DATA_ROOT),
    )


def test_s4_public_evidence_recomputes_all_acceptance() -> None:
    payload, config = _payload_and_config()

    result = validate_s4_gpu_batching_evidence(
        payload,
        config=config,
        git_root=ROOT.parent,
    )

    assert result["status"] == "valid"
    assert result["point_result_count"] == 66
    assert result["acceptance"] == {
        "S4-AC-01": True,
        "S4-AC-02": True,
        "S4-AC-03": True,
        "S4-AC-04": True,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "top_level_acceptance",
        "missing_matrix_repetition",
        "trace_gap",
        "oom",
        "open_delivery",
        "stabilization",
        "cleanup",
        "capacity",
        "non_finite",
    ],
)
def test_s4_evidence_mutations_fail_closed(mutation: str) -> None:
    payload, config = _payload_and_config()
    mutated = copy.deepcopy(payload)
    if mutation == "top_level_acceptance":
        mutated["acceptance"]["S4-AC-01"] = False
    elif mutation == "missing_matrix_repetition":
        del mutated["point_results"][0]
    elif mutation == "trace_gap":
        mutated["point_results"][0]["trace"]["missing_count"] = 1
    elif mutation == "oom":
        mutated["point_results"][0]["oom_count"] = 1
    elif mutation == "open_delivery":
        mutated["point_results"][-1]["offered_rate_delivery_ratio"] = 0.5
    elif mutation == "stabilization":
        mutated["open_loop_stabilization"]["experiment_container_absent"] = False
    elif mutation == "cleanup":
        mutated["cleanup"]["lease_released"] = False
    elif mutation == "capacity":
        mutated["analysis"]["s2_capacity_recalculation"]["calculated_depth"] = 64
    elif mutation == "non_finite":
        mutated["point_results"][0]["service_rps"] = math.nan

    with pytest.raises(S4EvidenceValidationError):
        validate_s4_gpu_batching_evidence(mutated, config=config)


def test_s4_git_source_identity_fails_closed_on_unknown_revision() -> None:
    payload, config = _payload_and_config()
    payload["source_identity"]["implementation_revision"] = "0" * 40

    with pytest.raises(S4EvidenceValidationError):
        validate_s4_gpu_batching_evidence(
            payload,
            config=config,
            git_root=ROOT.parent,
        )


def test_s4_closure_recomputes_from_runtime_evidence() -> None:
    payload, config = _payload_and_config()
    closure = json.loads(CLOSURE.read_text(encoding="utf-8"))

    result = validate_s4_gpu_batching_closure(
        closure,
        experiment=payload,
        experiment_sha256="8d2b3525eee115e38dab33f19b4426b9b8ce529ecd78cdd7b86d15eaf8530a22",
        config=config,
        git_root=ROOT.parent,
    )

    assert result["status"] == "valid"
    assert result["point_result_count"] == 66
    assert all(result["acceptance"].values())


@pytest.mark.parametrize(
    "field",
    [
        "experiment_sha256",
        "selected_point",
        "capacity",
        "regression",
        "cleanup",
    ],
)
def test_s4_closure_mutations_fail_closed(field: str) -> None:
    payload, config = _payload_and_config()
    closure = json.loads(CLOSURE.read_text(encoding="utf-8"))
    if field == "experiment_sha256":
        closure["final_runtime_evidence"]["git_blob_sha256"] = "0" * 64
    elif field == "selected_point":
        closure["selected_operating_point"]["batch_size"] = 32
    elif field == "capacity":
        closure["s2_capacity_recalculation"]["applied_depth"] = 64
    elif field == "regression":
        closure["regression"]["current_revision_runtime_smoke"]["status"] = "failed"
    elif field == "cleanup":
        closure["cleanup"]["runtime_cleanup_passed"] = False

    with pytest.raises(S4EvidenceValidationError):
        validate_s4_gpu_batching_closure(
            closure,
            experiment=payload,
            experiment_sha256="8d2b3525eee115e38dab33f19b4426b9b8ce529ecd78cdd7b86d15eaf8530a22",
            config=config,
        )
