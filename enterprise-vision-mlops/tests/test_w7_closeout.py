from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.w7_closeout import build_closeout_matrix, canonical_runtime_uri


def cycle_payload() -> dict:
    payload = json.loads(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    payload["dataset"]["record_count"] = 10821
    payload["dataset"]["quality_status"] = "pass"
    payload["dataset"]["storage_uri"] = "s3://validated/visa"
    payload["model_matrix"]["status"] = "pass"
    selected_id = payload["readiness_evaluation"]["candidate_id"]
    for candidate in payload["model_matrix"]["candidates"]:
        if candidate["candidate_id"] == selected_id:
            candidate["status"] = "pass"
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
    assert "deployment_rollback_ready" in matrix.blocker_claim_ids
    assert "production_serving_monitoring" in matrix.blocker_claim_ids
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


def test_closeout_accepts_audited_apply_followed_by_exact_rollback():
    payload = cycle_payload()
    payload["latest_deployment_intent"] = {
        "target_environment": "staging",
        "target_namespace": "evm-staging",
        "target": {"namespace": "evm-staging", "kind": "Deployment", "name": "evm-b7-serving"},
        "actor": "ml-platform",
        "reason": "verified rollout",
        "dry_run": True,
        "intent_id": "deploy-test",
        "state": "rolled_back",
        "version": 6,
        "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-10T00:05:00Z",
        "ci_evidence": payload["ci_evidence"],
        "ci_evidence_uri": "F:/artifacts/w7/ci/latest_ci_evidence.json",
        "ci_bundle_digest": "c" * 64,
        "readiness_evaluation_id": "readiness-test",
        "promotion_policy": payload["promotion_policy"],
        "model_candidate_id": "effnet-b7-img600-finetune-adamw",
        "model_artifact_uri": "F:/artifacts/model.pt",
        "model_digest": "d" * 64,
        "image_digest": "evm-serving@sha256:" + "e" * 64,
        "config_render_digest": "f" * 64,
        "rollback_reference": "F:/artifacts/rollback.json",
        "manifest_ref": "infra/kubernetes/model-runtime/b7-serving-deployment.yaml",
        "audit_uri": "F:/artifacts/w7/deployment_intents/deploy-test/deployment_intent.json",
        "approver": "ai-infra-sre",
        "approved_at": "2026-07-10T00:02:00Z",
        "transitions": [
            {
                "from_state": "applying",
                "to_state": "applied",
                "actor": "deployment-executor",
                "timestamp": "2026-07-10T00:04:00Z",
                "environment": "staging",
                "namespace": "evm-staging",
                "artifact_digest": "c" * 64,
                "reason": "apply finished",
                "result": "applied",
            },
            {
                "from_state": "applied",
                "to_state": "rolled_back",
                "actor": "deployment-executor",
                "timestamp": "2026-07-10T00:05:00Z",
                "environment": "staging",
                "namespace": "evm-staging",
                "artifact_digest": "c" * 64,
                "reason": "rollback finished",
                "result": "rolled_back",
            },
        ],
        "execution_result": {
            "action": "rollback",
            "status": "rolled_back",
            "started_at": "2026-07-10T00:04:30Z",
            "finished_at": "2026-07-10T00:05:00Z",
            "command": ["kubectl patch deployment/evm-b7-serving"],
            "exit_code": 0,
        },
    }

    matrix = build_closeout_matrix(
        payload,
        resource_payload(job_status="done", serving_replicas=1),
        source_commit="test-commit",
    )

    applied = next(item for item in matrix.claims if item.claim_id == "deployment_apply")
    rolled_back = next(
        item for item in matrix.claims if item.claim_id == "deployment_rollback_executed"
    )
    rollback_ready = next(
        item for item in matrix.claims if item.claim_id == "deployment_rollback_ready"
    )
    assert applied.status == "pass"
    assert rolled_back.status == "pass"
    assert rollback_ready.status == "pass"
    assert applied.evidence_uri == payload["latest_deployment_intent"]["audit_uri"]


def test_closeout_matrix_canonicalizes_container_evidence_uri(monkeypatch):
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", "F:/evm")

    assert canonical_runtime_uri("/app/artifacts/w7/observer/latest.json") == (
        "F:/evm/artifacts/w7/observer/latest.json"
    )
