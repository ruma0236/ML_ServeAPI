from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from evm.scale_validation.s5_evidence import (
    S5EvidenceValidationError,
    validate_s5_regression_evidence,
    validate_s5_runtime_smoke,
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


def test_historical_s5_closure_is_rejected_by_strict_v2_contract() -> None:
    payload = _payload()
    config = S5RuntimeConfig.from_path(CONFIG, data_root=DATA_ROOT)
    closure = json.loads(CLOSURE.read_text(encoding="utf-8"))
    with pytest.raises(S5EvidenceValidationError, match="runtime_smoke_invalid"):
        validate_s5_spark_data_scale_closure(
            closure,
            experiment=payload,
            experiment_sha256=hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(),
            config=config,
            private_root=config.private_root / payload["suite_id"],
        )


def test_s5_runtime_smoke_recomputes_digest_cleanup_and_health() -> None:
    config = S5RuntimeConfig.from_path(CONFIG, data_root=DATA_ROOT)
    payload = {
        "schema_version": "evm.s5_current_revision_runtime_smoke.v2",
        "status": "passed",
        "acceptance_credit": False,
        "source_identity": {"revision": "a" * 40, "config_sha256": config.sha256},
        "engines": [
            {
                "engine": engine,
                "output_digest": "digest",
                "missing_records": 0,
                "duplicate_records": 0,
            }
            for engine in (
                "single_process_columnar",
                "spark_local",
                "spark_kubernetes_1",
            )
        ],
        "cross_engine_output_digest_equal": True,
        "cleanup": {
            "kubernetes_jobs_remaining": 0,
            "kubernetes_executor_pods_remaining": 0,
            "pvc_phase": "Bound",
            "source_dataset_unchanged": True,
        },
        "runtime_health": {
            "api_status": "ok",
            "existing_serving_desired_replicas": 1,
            "existing_serving_available_replicas": 1,
            "prometheus_targets_total": 5,
            "prometheus_targets_up": 5,
        },
    }

    assert validate_s5_runtime_smoke(payload, config=config)["status"] == "valid"

    payload["runtime_health"]["prometheus_targets_up"] = 4
    with pytest.raises(S5EvidenceValidationError, match="smoke_runtime_health"):
        validate_s5_runtime_smoke(payload, config=config)


def test_s5_regression_evidence_rehashes_logs_and_counts(tmp_path: Path) -> None:
    from evm.scale_validation.s5_evidence import REQUIRED_REGRESSION_SUITES

    suites = []
    for suite_id in REQUIRED_REGRESSION_SUITES:
        path = tmp_path / f"{suite_id}.log"
        path.write_text("passed\n", encoding="utf-8", newline="\n")
        suites.append(
            {
                "suite_id": suite_id,
                "status": "passed",
                "command": f"run {suite_id}",
                "exit_code": 0,
                "tests_passed": 0
                if suite_id in {"changed_file_lint", "frontend_production_build"}
                else 1,
                "tests_skipped": 0,
                "log_path": path.name,
                "log_bytes": path.stat().st_size,
                "log_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    payload = {
        "schema_version": "evm.s5_reclosure_regression.v1",
        "status": "passed",
        "source_identity": {"revision": "a" * 40},
        "suites": suites,
    }

    assert validate_s5_regression_evidence(
        payload, regression_root=tmp_path
    )["suite_count"] == len(REQUIRED_REGRESSION_SUITES)

    payload["suites"][1]["tests_passed"] = 0
    with pytest.raises(S5EvidenceValidationError, match="regression_test_count"):
        validate_s5_regression_evidence(payload, regression_root=tmp_path)


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
