from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from evm.scale_validation.s5_evidence import (
    S5EvidenceValidationError,
    validate_s5_spark_data_scale_closure,
    validate_s5_spark_data_scale_evidence,
)
from evm.scale_validation.s5_runtime import S5RuntimeConfig


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s5_spark_data_scale.toml"
EVIDENCE = ROOT / "docs/status/evidence/s5-spark-data-scale-experiment.json"
CLOSURE = ROOT / "docs/status/evidence/s5-spark-data-scale-closure.json"
DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")


def _payload() -> dict:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def _validate(payload: dict) -> dict:
    config = S5RuntimeConfig.from_path(CONFIG, data_root=DATA_ROOT)
    git_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return validate_s5_spark_data_scale_evidence(
        payload,
        config=config,
        git_root=git_root,
        private_root=config.private_root / payload["suite_id"],
    )


def test_s5_public_and_private_evidence_recompute() -> None:
    result = _validate(_payload())

    assert result["status"] == "valid"
    assert result["point_result_count"] == 30
    assert all(item["status"] == "passed" for item in result["acceptance"].values())
    assert result["private_evidence"]["result_projection_count"] == 30


def test_s5_closure_binds_experiment_and_regressions() -> None:
    payload = _payload()
    config = S5RuntimeConfig.from_path(CONFIG, data_root=DATA_ROOT)
    closure = json.loads(CLOSURE.read_text(encoding="utf-8"))
    result = validate_s5_spark_data_scale_closure(
        closure,
        experiment=payload,
        experiment_sha256=hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(),
        config=config,
        private_root=config.private_root / payload["suite_id"],
    )

    assert result["status"] == "valid"
    assert result["point_result_count"] == 30


def test_s5_closure_rejects_missing_regression() -> None:
    payload = _payload()
    config = S5RuntimeConfig.from_path(CONFIG, data_root=DATA_ROOT)
    closure = json.loads(CLOSURE.read_text(encoding="utf-8"))
    closure["regression"]["full_python_real_postgresql"]["status"] = "failed"

    with pytest.raises(S5EvidenceValidationError, match="closure_regression"):
        validate_s5_spark_data_scale_closure(
            closure,
            experiment=payload,
            experiment_sha256=hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(),
            config=config,
            private_root=config.private_root / payload["suite_id"],
        )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda value: value["results"].pop(), "point_result_count"),
        (
            lambda value: value["results"][0].__setitem__("duplicate_records", 1),
            "analysis_projection",
        ),
        (
            lambda value: value["results"][-1].__setitem__(
                "retry_commit_state", "committed"
            ),
            "retry_commit",
        ),
        (
            lambda value: value["source_identity"].__setitem__(
                "revision", "0" * 40
            ),
            "git_identity_unavailable",
        ),
    ],
)
def test_s5_validator_fails_closed(mutation, expected: str) -> None:
    payload = copy.deepcopy(_payload())
    mutation(payload)

    with pytest.raises(S5EvidenceValidationError, match=expected):
        _validate(payload)
