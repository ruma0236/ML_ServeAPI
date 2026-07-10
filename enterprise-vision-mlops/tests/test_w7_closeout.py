from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.w7_closeout import build_closeout_matrix


def cycle_payload() -> dict:
    payload = json.loads(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    payload["dataset"]["record_count"] = 10821
    payload["dataset"]["quality_status"] = "pass"
    payload["dataset"]["storage_uri"] = "s3://validated/visa"
    payload["model_matrix"]["status"] = "pass"
    payload["ci_evidence"] = {
        "validation_id": "ci-test",
        "valid": True,
        "status": "pass",
        "workflow_run_id": "1",
        "commit_sha": "a" * 40,
        "checked_at": "2026-07-10T00:00:00Z",
        "input_digest": "b" * 64,
        "checks": {"commit": "pass"},
        "blockers": [],
        "source_uri": "https://github.com/example/actions/runs/1",
    }
    return payload


def resource_payload(job_status: str = "fail", serving_replicas: int = 0) -> dict:
    return {
        "observation_status": "live",
        "snapshot_uri": "F:/artifacts/w7/kubernetes_observer/latest.json",
        "resources": [
            {
                "resource_id": "evm-training:Job:evm-b7-training",
                "namespace": "evm-training",
                "kind": "Job",
                "name": "evm-b7-training",
                "status": job_status,
                "node_pool": "docker-desktop",
                "readiness": "blocked" if job_status == "fail" else "ready",
                "restarts": 0,
                "control_actions": ["view"],
                "pressure": job_status,
                "related_stages": [],
                "observation_source": "kubernetes_snapshot",
                "observation_status": "live",
                "reason": "DeadlineExceeded" if job_status == "fail" else "Complete",
            },
            {
                "resource_id": "evm-staging:Deployment:evm-b7-serving",
                "namespace": "evm-staging",
                "kind": "Deployment",
                "name": "evm-b7-serving",
                "status": "pass" if serving_replicas else "queued",
                "node_pool": "docker-desktop",
                "readiness": "ready" if serving_replicas else "not_requested",
                "restarts": 0,
                "control_actions": ["view"],
                "pressure": "pass" if serving_replicas else "queued",
                "related_stages": [],
                "observation_source": "kubernetes_snapshot",
                "observation_status": "live",
                "desired_replicas": serving_replicas,
                "ready_replicas": serving_replicas,
                "reason": "Available" if serving_replicas else "ScaledToZero",
            },
        ],
    }


def test_closeout_matrix_keeps_runtime_and_deployment_gaps_blocked():
    matrix = build_closeout_matrix(
        cycle_payload(),
        resource_payload(),
        source_commit="test-commit",
    )

    assert matrix.closeout_allowed is False
    assert "kubernetes_gpu_training" in matrix.blocker_claim_ids
    assert "kubernetes_serving_rollout" in matrix.blocker_claim_ids
    assert "deployment_apply" in matrix.blocker_claim_ids
    assert "deployment_rollback" in matrix.blocker_claim_ids
    assert next(item for item in matrix.claims if item.claim_id == "measured_drift_review").status == "pass"


def test_closeout_matrix_does_not_treat_live_observation_as_gpu_execution():
    matrix = build_closeout_matrix(
        cycle_payload(),
        resource_payload(job_status="fail"),
        source_commit="test-commit",
    )

    observation = next(
        item for item in matrix.claims if item.claim_id == "live_kubernetes_observation"
    )
    training = next(
        item for item in matrix.claims if item.claim_id == "kubernetes_gpu_training"
    )
    assert observation.status == "pass"
    assert training.status == "blocked"
