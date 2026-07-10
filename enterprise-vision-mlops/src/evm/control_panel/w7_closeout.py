from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, ConfigDict, Field

from evm.control_panel.schemas import CycleRun, RuntimeResourceList


class CloseoutClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    category: str
    required: bool
    status: str
    evidence_uri: str | None = None
    reason: str
    owner_issue: str


class W7CloseoutMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "evm.w7.closeout_matrix.v1"
    status: str
    closeout_allowed: bool
    cycle_id: str
    source_commit: str
    generated_at: str
    passed_claims: int = Field(ge=0)
    blocked_claims: int = Field(ge=0)
    blocker_claim_ids: list[str]
    claims: list[CloseoutClaim]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def first_uri(payload: dict[str, Any] | None, *keys: str) -> str | None:
    if not payload:
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def claim(
    claim_id: str,
    category: str,
    passed: bool,
    reason: str,
    owner_issue: str,
    evidence_uri: str | None = None,
    required: bool = True,
) -> CloseoutClaim:
    return CloseoutClaim(
        claim_id=claim_id,
        category=category,
        required=required,
        status="pass" if passed else "blocked",
        evidence_uri=evidence_uri,
        reason=reason,
        owner_issue=owner_issue,
    )


def find_live_resource(
    resources: RuntimeResourceList,
    *,
    kind: str,
    name: str,
) -> Any | None:
    return next(
        (
            resource
            for resource in resources.resources
            if resource.kind == kind
            and resource.name == name
            and resource.observation_source == "kubernetes_snapshot"
        ),
        None,
    )


def build_closeout_matrix(
    cycle_payload: dict[str, Any],
    resource_payload: dict[str, Any],
    *,
    source_commit: str,
) -> W7CloseoutMatrix:
    cycle = CycleRun.model_validate(cycle_payload)
    resources = RuntimeResourceList.model_validate(resource_payload)
    matrix = cycle.model_matrix
    candidates = matrix.candidates if matrix else []
    selected_candidate = next(
        (
            candidate
            for candidate in candidates
            if candidate.candidate_id == "effnet-b7-img600-finetune-adamw"
        ),
        None,
    )
    drift = cycle.drift
    ci = cycle.ci_evidence
    readiness = cycle.readiness_evaluation
    policy = cycle.promotion_policy
    deployment = cycle.latest_deployment_intent
    job = find_live_resource(resources, kind="Job", name="evm-b7-training")
    serving = find_live_resource(resources, kind="Deployment", name="evm-b7-serving")

    dataset_pass = (
        cycle.dataset.record_count >= 10821
        and cycle.dataset.quality_status == "pass"
        and bool(cycle.dataset.storage_uri)
    )
    matrix_pass = bool(
        matrix
        and matrix.status == "pass"
        and len(candidates) >= 4
        and selected_candidate
        and not selected_candidate.promotion_blockers
    )
    selected_run_uri = selected_candidate.run_uri if selected_candidate else None
    drift_pass = bool(
        drift
        and drift.measurement_status == "measured"
        and drift.review_event_type in {"review_required", "within_policy"}
        and drift.automatic_retraining is False
        and drift.report_uri
    )
    ci_pass = bool(ci and ci.valid and ci.commit_sha and ci.workflow_run_id)
    observation_pass = resources.observation_status == "live"
    training_pass = bool(job and job.status in {"pass", "done"})
    serving_pass = bool(
        serving
        and serving.status == "pass"
        and (serving.desired_replicas or 0) > 0
        and serving.ready_replicas == serving.desired_replicas
    )
    readiness_pass = bool(readiness and readiness.status == "pass")
    policy_pass = bool(policy and policy.decision == "allow" and policy.status == "pass")
    apply_pass = bool(deployment and deployment.state == "applied")
    rollback_pass = bool(
        deployment
        and any(transition.to_state == "rolled_back" for transition in deployment.transitions)
    )

    claims = [
        claim(
            "real_dataset_lineage",
            "data",
            dataset_pass,
            (
                f"record_count={cycle.dataset.record_count}, "
                f"quality={cycle.dataset.quality_status}"
            ),
            "EVM-224",
            cycle.dataset.storage_uri,
        ),
        claim(
            "real_model_matrix",
            "model",
            matrix_pass,
            f"candidate_count={len(candidates)}, matrix_status={matrix.status if matrix else 'missing'}",
            "EVM-237",
            selected_run_uri,
        ),
        claim(
            "mlflow_selected_run",
            "model",
            bool(selected_run_uri),
            "selected B7 candidate has an MLflow run URI",
            "EVM-237",
            selected_run_uri,
        ),
        claim(
            "measured_drift_review",
            "ct",
            drift_pass,
            (
                f"measurement={drift.measurement_status if drift else 'missing'}, "
                f"event={drift.review_event_type if drift else 'missing'}, "
                f"auto_retraining={drift.automatic_retraining if drift else None}"
            ),
            "EVM-234",
            drift.report_uri if drift else None,
        ),
        claim(
            "immutable_ci_evidence",
            "ci",
            ci_pass,
            (
                f"valid={ci.valid if ci else False}, commit={ci.commit_sha if ci else 'missing'}"
            ),
            "EVM-235",
            first_uri(ci.model_dump() if ci else None, "source_uri", "report_uri"),
        ),
        claim(
            "live_kubernetes_observation",
            "runtime",
            observation_pass,
            f"observation_status={resources.observation_status}",
            "EVM-229",
            resources.snapshot_uri,
        ),
        claim(
            "kubernetes_gpu_training",
            "runtime",
            training_pass,
            (
                f"status={job.status if job else 'missing'}, "
                f"reason={job.reason if job else 'missing'}"
            ),
            "EVM-226",
            resources.snapshot_uri,
        ),
        claim(
            "kubernetes_serving_rollout",
            "runtime",
            serving_pass,
            (
                f"status={serving.status if serving else 'missing'}, "
                f"desired={serving.desired_replicas if serving else None}, "
                f"ready={serving.ready_replicas if serving else None}"
            ),
            "EVM-226",
            resources.snapshot_uri,
        ),
        claim(
            "artifact_readiness",
            "governance",
            readiness_pass,
            (
                f"status={readiness.status if readiness else 'missing'}, "
                f"blockers={len(readiness.blockers) if readiness else 0}"
            ),
            "EVM-236",
            readiness.report_uri if readiness else None,
        ),
        claim(
            "environment_promotion_policy",
            "governance",
            policy_pass,
            (
                f"decision={policy.decision if policy else 'missing'}, "
                f"status={policy.status if policy else 'missing'}"
            ),
            "EVM-233",
            policy.audit_uri if policy else None,
        ),
        claim(
            "deployment_apply",
            "cd",
            apply_pass,
            f"latest_state={deployment.state if deployment else 'no_intent'}",
            "EVM-235",
            deployment.audit_uri if deployment else None,
        ),
        claim(
            "deployment_rollback",
            "cd",
            rollback_pass,
            "no audited rolled_back transition exists" if not rollback_pass else "rollback recorded",
            "EVM-235",
            deployment.audit_uri if deployment else None,
        ),
        claim(
            "external_airflow_contract",
            "orchestration",
            bool(cycle.airflow and cycle.airflow.mode == "external-compose" and cycle.airflow.url),
            (
                f"mode={cycle.airflow.mode if cycle.airflow else 'missing'}, "
                f"control={cycle.airflow.control_mode if cycle.airflow else 'missing'}"
            ),
            "EVM-230",
            cycle.airflow.url if cycle.airflow else None,
            required=False,
        ),
    ]
    blockers = [item.claim_id for item in claims if item.required and item.status != "pass"]
    passed = sum(1 for item in claims if item.status == "pass")
    return W7CloseoutMatrix(
        status="pass" if not blockers else "blocked",
        closeout_allowed=not blockers,
        cycle_id=cycle.cycle_id,
        source_commit=source_commit,
        generated_at=utc_now(),
        passed_claims=passed,
        blocked_claims=len(blockers),
        blocker_claim_ids=blockers,
        claims=claims,
    )


def fetch_json(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"expected object response from {url}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a live W7 closeout claim matrix.")
    parser.add_argument(
        "--cycle-url",
        default="http://127.0.0.1:8000/control-panel/v1/cycles/latest",
    )
    parser.add_argument(
        "--resources-url",
        default="http://127.0.0.1:8000/control-panel/v1/resources",
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-closeout", action="store_true")
    args = parser.parse_args()

    matrix = build_closeout_matrix(
        fetch_json(args.cycle_url),
        fetch_json(args.resources_url),
        source_commit=args.source_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(matrix.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(matrix.model_dump(mode="json"), ensure_ascii=False))
    return 1 if args.require_closeout and not matrix.closeout_allowed else 0


if __name__ == "__main__":
    raise SystemExit(main())
