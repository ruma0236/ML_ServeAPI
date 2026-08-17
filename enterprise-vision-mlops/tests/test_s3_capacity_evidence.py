from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evm.scale_validation.s3_evidence import (
    S3EvidenceValidationError,
    validate_s3_capacity_evidence,
)
from evm.scale_validation.s3_runtime import S3RuntimeConfig


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/status/evidence/s3-capacity-experiment.json"
CONFIG = ROOT / "configs/s3_capacity_runtime.toml"
DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")


def _payload_and_config() -> tuple[dict, S3RuntimeConfig]:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    config = S3RuntimeConfig.from_path(CONFIG, data_root=DATA_ROOT)
    return payload, config


def test_s3_public_capacity_evidence_recomputes_all_acceptance() -> None:
    payload, config = _payload_and_config()

    result = validate_s3_capacity_evidence(payload, config=config)

    assert result["status"] == "valid"
    assert result["point_result_count"] == 111
    assert result["acceptance"] == {
        "S3-AC-01": True,
        "S3-AC-02": True,
        "S3-AC-03": True,
        "S3-AC-04": True,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "top_level_acceptance",
        "point_evidence",
        "terminal_identity",
        "trace_contract",
        "cleanup",
    ],
)
def test_s3_public_capacity_evidence_mutations_fail_closed(
    mutation: str,
) -> None:
    payload, config = _payload_and_config()
    mutated = copy.deepcopy(payload)
    point = mutated["point_results"][0]
    if mutation == "top_level_acceptance":
        mutated["acceptance"]["S3-AC-01"] = False
    elif mutation == "point_evidence":
        point["evidence_valid"] = False
    elif mutation == "terminal_identity":
        point["load"]["client_request_identity_count"] -= 1
    elif mutation == "trace_contract":
        point["trace"]["complete_trace_contract_counts"]["full"] -= 1
    elif mutation == "cleanup":
        point["cleanup"]["marker_process_count"] = 1

    with pytest.raises(S3EvidenceValidationError):
        validate_s3_capacity_evidence(mutated, config=config)
