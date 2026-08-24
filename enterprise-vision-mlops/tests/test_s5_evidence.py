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


def _smoke_payload(tmp_path: Path, config: S5RuntimeConfig) -> tuple[dict, Path]:
    private_root = tmp_path / "s5-smoke-suite"
    private_root.mkdir()
    digest = "d" * 64
    engine_contracts = (
        ("smoke-01", "single_process_columnar", "preparation_smoke"),
        ("smoke-02", "spark_local", "spark_local_stage"),
        ("smoke-03", "spark_kubernetes_1", "kubernetes_scale"),
    )
    engines = []
    entries = []
    for index, (point_id, engine, profile) in enumerate(engine_contracts, start=1):
        item = {
            "point_id": point_id,
            "engine": engine,
            "stage": "small",
            "repetition": 1,
            "profile": profile,
            "semantic_row_count": 766_864,
            "effective_row_count": 766_864,
            "repeat_factor": 1,
            "generated_io_only": False,
            "output_digest": digest,
            "missing_records": 0,
            "duplicate_records": 0,
            "commit_state": "committed",
        }
        if engine.startswith("spark_"):
            item.update({"task_count": 2, "failed_task_count": 0, "executors_added": 1})
        engines.append(item)
        raw_item = {key: value for key, value in item.items() if key != "point_id"}
        target = private_root / f"engine-{index:02d}-result.json"
        target.write_bytes(
            (json.dumps(raw_item, indent=2, ensure_ascii=True) + "\n").encode()
        )
        entries.append(
            {
                "path": target.name,
                "bytes": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    index_payload = {
        "schema_version": "evm.s5_private_evidence_index.v1",
        "artifact_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "entries": entries,
    }
    index_path = private_root / "private-evidence-index.json"
    index_path.write_bytes(
        (json.dumps(index_payload, indent=2, ensure_ascii=True) + "\n").encode()
    )
    payload = {
        "schema_version": "evm.s5_current_revision_runtime_smoke.v2",
        "status": "passed",
        "acceptance_credit": False,
        "suite_id": private_root.name,
        "source_identity": {"revision": "a" * 40, "config_sha256": config.sha256},
        "engines": engines,
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
        "private_evidence": {
            "artifact_count": len(entries),
            "total_bytes": sum(item["bytes"] for item in entries),
            "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
        },
    }

    return payload, private_root


def test_s5_runtime_smoke_recomputes_digest_cleanup_and_health(
    tmp_path: Path,
) -> None:
    config = S5RuntimeConfig.from_path(CONFIG, data_root=DATA_ROOT)
    payload, private_root = _smoke_payload(tmp_path, config)

    assert validate_s5_runtime_smoke(
        payload, config=config, private_root=private_root
    )["status"] == "valid"

    payload["runtime_health"]["prometheus_targets_up"] = 4
    with pytest.raises(S5EvidenceValidationError, match="smoke_runtime_health"):
        validate_s5_runtime_smoke(payload, config=config, private_root=private_root)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda value: value["engines"][0].__setitem__(
                "output_digest", "not-a-sha"
            ),
            "smoke_cross_engine_digest",
        ),
        (
            lambda value: value["engines"].append(copy.deepcopy(value["engines"][0])),
            "smoke_engines",
        ),
        (
            lambda value: value["engines"][1].__setitem__("duplicate_records", 1),
            "smoke_integrity",
        ),
    ],
)
def test_s5_runtime_smoke_rejects_summary_mutations(
    tmp_path: Path, mutation, expected: str
) -> None:
    config = S5RuntimeConfig.from_path(CONFIG, data_root=DATA_ROOT)
    payload, private_root = _smoke_payload(tmp_path, config)
    mutation(payload)

    with pytest.raises(S5EvidenceValidationError, match=expected):
        validate_s5_runtime_smoke(payload, config=config, private_root=private_root)


def test_s5_regression_evidence_rehashes_logs_and_counts(tmp_path: Path) -> None:
    from evm.scale_validation.s5_evidence import REQUIRED_REGRESSION_SUITES

    suites = []
    for suite_id in REQUIRED_REGRESSION_SUITES:
        path = tmp_path / f"{suite_id}.log"
        if suite_id == "changed_file_lint":
            log_text = "All checks passed!\n"
        elif suite_id == "frontend_production_build":
            log_text = "built in 1.00s\n"
        else:
            log_text = "1 passed in 0.01s\n"
        path.write_text(log_text, encoding="utf-8", newline="\n")
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
