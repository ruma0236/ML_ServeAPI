from __future__ import annotations

import tomllib
from pathlib import Path

from evm.operations.lifecycle_guard_a_candidate import render_integration_config


def test_render_integration_config_binds_fresh_lifecycle_identity(tmp_path: Path) -> None:
    lifecycle_root = tmp_path / "lifecycle-a"
    base = {
        "target": {
            "namespace": "evm-production",
            "deployment": "evm-b0-production",
            "pod_label": "app=evm-b0-production",
            "container": "serving",
        },
        "m0": {
            "scenario_config_path": "configs/operations/local_failure_validation.toml",
            "candidate_id": "m0",
            "dataset_version": "visa-v1",
            "model_sha256": "a" * 64,
            "image_digest": "image@sha256:" + "b" * 64,
        },
        "m1": {
            "sample_image_uri": "file:///F:/visa/anomaly.jpg",
            "expected_prediction": "anomaly",
        },
        "execution": {
            "evidence_root": "F:/evidence/a",
            "detection_budget_seconds": 30.0,
            "recovery_budget_seconds": 300.0,
            "sample_cadence_seconds": 5.0,
            "prometheus_convergence_seconds": 90.0,
            "approval_ttl_seconds": 3600,
        },
    }
    release = {
        "run_id": "lifecycle-fresh-a",
        "candidate_id": "candidate-fresh-a",
        "dataset_version": "visa-v1",
        "model_digest": "c" * 64,
        "container_image_digest": "image@sha256:" + "d" * 64,
        "evidence": {
            "ct_evaluation": {"uri": "F:/ct/fresh-a/ct_evaluation.json"}
        },
    }

    rendered = render_integration_config(
        base_config=base,
        lifecycle_root=lifecycle_root,
        release=release,
        source_commit="e" * 40,
    )
    parsed = tomllib.loads(rendered)

    assert parsed["m1"]["lifecycle_run_id"] == "lifecycle-fresh-a"
    assert parsed["m1"]["lifecycle_run_root"] == str(lifecycle_root).replace(
        "\\", "/"
    )
    assert parsed["m1"]["source_revision"] == "e" * 40
    assert parsed["m1"]["model_sha256"] == "c" * 64
    assert parsed["m0"]["candidate_id"] == "m0"
    assert parsed["execution"]["recovery_budget_seconds"] == 300.0
