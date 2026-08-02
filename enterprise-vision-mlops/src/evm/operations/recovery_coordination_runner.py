from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from evm.operations.correlation import (
    CorrelationStore,
    KubernetesSubject,
    NormalizedEvent,
    StrictModel,
    load_policy,
    stable_digest,
)
from evm.operations.failure_scenarios import atomic_write_json
from evm.operations.recovery_coordination import (
    IncidentTiming,
    LeaseAcquireRequest,
    RecoveryActionRequest,
    RecoveryCoordinationStore,
    action_digest,
    build_incident_plane_snapshot,
    exact_target,
    load_recovery_policy,
    recovery_approval,
    write_incident_plane_snapshot,
)


class RecoveryCoordinationSeriesResult(StrictModel):
    schema_version: Literal["evm.recovery_coordination_series.v1"] = (
        "evm.recovery_coordination_series.v1"
    )
    series_id: str
    acquired_owner_count: int = Field(ge=0)
    conflict_block_count: int = Field(ge=0)
    higher_fence_count: int = Field(ge=0)
    authorized_recommendation_count: int = Field(ge=0)
    deduped_recommendation_count: int = Field(ge=0)
    stale_owner_block_count: int = Field(ge=0)
    approval_mismatch_block_count: int = Field(ge=0)
    mutation_intent_count: int = Field(ge=0)
    coordinator_restart_count: int = Field(ge=0)
    passed: bool


class RecoveryCoordinationProof(StrictModel):
    schema_version: Literal["evm.recovery_coordination_proof.v1"] = (
        "evm.recovery_coordination_proof.v1"
    )
    source_revision: str
    policy_version: str
    series: list[RecoveryCoordinationSeriesResult]
    total_series: int = Field(ge=1)
    total_authorized_recommendations: int = Field(ge=0)
    total_mutation_intents: int = Field(ge=0)
    production_mutation_count: int = Field(ge=0)
    passed: bool
    generated_at_utc: datetime


def _event(
    *,
    series_id: str,
    source_revision: str,
    policy_version: str,
    observed_at: datetime,
) -> tuple[NormalizedEvent, dict[str, Any]]:
    decision_inputs = {
        "signal": "production_serving_unhealthy",
        "deployment_uid": "cfdab424-dcc5-4d5f-a46f-ae7530441ef4",
        "pod_uid": f"pod-{series_id}-current",
    }
    raw_evidence = {
        **decision_inputs,
        "observed_at_utc": observed_at.isoformat(),
        "ready_replicas": 0,
        "expected_replicas": 1,
    }
    subject = KubernetesSubject(
        lifecycle_series_id=f"lifecycle-{series_id}",
        lifecycle_run_id=f"lifecycle-run-{series_id}",
        attempt_id=f"attempt-{series_id}",
        bindings={
            "deployment_uid": decision_inputs["deployment_uid"],
            "pod_uid": decision_inputs["pod_uid"],
        },
        cluster="docker-desktop",
        namespace="evm-production",
        resource_kind="Deployment",
        name="evm-b0-production",
        uid=decision_inputs["deployment_uid"],
        pod_uid=decision_inputs["pod_uid"],
        container_name="efficientnet-serving",
        image_digest="sha256:" + "a" * 64,
        expected_replica_identity="1/1",
    )
    return (
        NormalizedEvent(
            schema_version="evm.cross_scenario_event.v1",
            event_id=f"evt-{series_id}-root-0001",
            scenario_id="A",
            event_type="serving_health",
            cause_code="pod_not_ready",
            severity="critical",
            observed_at_utc=observed_at,
            monotonic_elapsed_ms=5000,
            collector_cadence_ms=5000,
            fresh_until_utc=observed_at + timedelta(seconds=20),
            producer_boot_id=f"boot-{series_id}-0001",
            producer_sequence=1,
            source_component="scenario-a-controller",
            source_revision=source_revision,
            policy_version=policy_version,
            evidence_digest=stable_digest(raw_evidence),
            semantic_identity_digest=stable_digest(decision_inputs),
            decision_inputs=decision_inputs,
            subject_identity=subject,
            target_match_count=1,
            actor_or_controller="scenario-a-controller",
            recommended_action="review",
        ),
        raw_evidence,
    )


def _artifact_index(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "artifact-index.json":
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return {
        "schema_version": "evm.recovery_coordination_artifact_index.v1",
        "file_count": len(files),
        "files": files,
    }


def run_series(
    *,
    root: Path,
    series_index: int,
    source_revision: str,
    recovery_policy_path: Path,
    correlation_policy_path: Path,
) -> RecoveryCoordinationSeriesResult:
    series_id = f"recovery-coordination-{series_index}"
    start = datetime(2026, 8, 2, 15, series_index, tzinfo=UTC)
    correlation_policy = load_policy(correlation_policy_path, revision=source_revision)
    recovery_policy = load_recovery_policy(
        recovery_policy_path,
        source_revision=source_revision,
    )
    correlation_root = root / "correlation"
    correlation = CorrelationStore(correlation_root, correlation_policy)
    event, raw_evidence = _event(
        series_id=series_id,
        source_revision=source_revision,
        policy_version=correlation_policy.policy_version,
        observed_at=start,
    )
    correlation_decision = correlation.ingest(
        event,
        raw_evidence=raw_evidence,
        ingested_at=start + timedelta(milliseconds=31),
        coordinator_monotonic_elapsed_ms=31,
    )
    correlation.set_incident_state(
        correlation_decision.incident_id,
        state="recovery_pending",
        now=start + timedelta(milliseconds=32),
    )

    target = exact_target(
        "production-b0",
        {
            "cluster": "docker-desktop",
            "namespace": "evm-production",
            "deployment": "evm-b0-production",
            "deployment_uid": "cfdab424-dcc5-4d5f-a46f-ae7530441ef4",
            "pod_uid": f"pod-{series_id}-current",
            "image_digest": "sha256:" + "a" * 64,
        },
    )
    coordination_root = root / "coordination"
    store = RecoveryCoordinationStore(coordination_root, recovery_policy)
    request = LeaseAcquireRequest(
        incident_id=correlation_decision.incident_id,
        correlation_id=correlation_decision.correlation_id,
        target=target,
        owner_id="scenario-a-controller",
        source_revision=source_revision,
        policy_version=recovery_policy.policy_version,
        observed_at_utc=start + timedelta(seconds=1),
        evidence_fresh_until_utc=start + timedelta(seconds=20),
    )
    acquired = store.acquire(request)
    assert acquired.lease is not None
    conflict = store.acquire(
        request.model_copy(
            update={
                "owner_id": "scenario-b-controller",
                "observed_at_utc": start + timedelta(seconds=2),
            }
        )
    )
    parameters = {"pod_uid": target.identity["pod_uid"]}
    digest = action_digest("recommend-exact-restart", parameters)
    approval = recovery_approval(
        approval_id=f"approval-{series_id}-0001",
        incident_id=correlation_decision.incident_id,
        correlation_id=correlation_decision.correlation_id,
        target_identity_digest=target.identity_digest,
        action_digest=digest,
        source_revision=source_revision,
        policy_version=recovery_policy.policy_version,
        actor="maintenance-approver",
        nonce=f"nonce-{series_id}-0001",
        issued_at_utc=start + timedelta(seconds=2),
        expires_at_utc=start + timedelta(seconds=19),
    )
    store.record_approval(approval, observed_at_utc=start + timedelta(seconds=2))
    action = RecoveryActionRequest(
        incident_id=correlation_decision.incident_id,
        correlation_id=correlation_decision.correlation_id,
        target=target,
        owner_id=acquired.lease.owner_id,
        lease_id=acquired.lease.lease_id,
        fencing_token=acquired.lease.fencing_token,
        action="recommend-exact-restart",
        parameters=parameters,
        action_digest=digest,
        approval_id=approval.approval_id,
        source_revision=source_revision,
        policy_version=recovery_policy.policy_version,
        observed_at_utc=start + timedelta(seconds=3),
    )
    authorized = store.authorize(action)
    restarted = RecoveryCoordinationStore(coordination_root, recovery_policy)
    deduped = restarted.authorize(
        action.model_copy(update={"observed_at_utc": start + timedelta(seconds=4)})
    )
    second = restarted.acquire(
        request.model_copy(
            update={
                "owner_id": "scenario-b-controller",
                "observed_at_utc": start + timedelta(seconds=22),
                "evidence_fresh_until_utc": start + timedelta(seconds=40),
            }
        )
    )
    assert second.lease is not None
    stale_parameters: dict[str, str] = {}
    stale_digest = action_digest("recommend-rollback", stale_parameters)
    stale_approval = recovery_approval(
        approval_id=f"approval-{series_id}-stale",
        incident_id=correlation_decision.incident_id,
        correlation_id=correlation_decision.correlation_id,
        target_identity_digest=target.identity_digest,
        action_digest=stale_digest,
        source_revision=source_revision,
        policy_version=recovery_policy.policy_version,
        actor="maintenance-approver",
        nonce=f"nonce-{series_id}-stale",
        issued_at_utc=start + timedelta(seconds=22),
        expires_at_utc=start + timedelta(seconds=40),
    )
    restarted.record_approval(stale_approval, observed_at_utc=start + timedelta(seconds=22))
    stale_owner = restarted.authorize(
        RecoveryActionRequest(
            incident_id=correlation_decision.incident_id,
            correlation_id=correlation_decision.correlation_id,
            target=target,
            owner_id=acquired.lease.owner_id,
            lease_id=acquired.lease.lease_id,
            fencing_token=acquired.lease.fencing_token,
            action="recommend-rollback",
            parameters=stale_parameters,
            action_digest=stale_digest,
            approval_id=stale_approval.approval_id,
            source_revision=source_revision,
            policy_version=recovery_policy.policy_version,
            observed_at_utc=start + timedelta(seconds=23),
        )
    )
    wrong_target = exact_target(
        "production-b0",
        {**target.identity, "pod_uid": f"pod-{series_id}-wrong"},
    )
    wrong_approval = recovery_approval(
        approval_id=f"approval-{series_id}-wrong",
        incident_id=correlation_decision.incident_id,
        correlation_id=correlation_decision.correlation_id,
        target_identity_digest=wrong_target.identity_digest,
        action_digest=stale_digest,
        source_revision=source_revision,
        policy_version=recovery_policy.policy_version,
        actor="maintenance-approver",
        nonce=f"nonce-{series_id}-wrong",
        issued_at_utc=start + timedelta(seconds=23),
        expires_at_utc=start + timedelta(seconds=40),
    )
    restarted.record_approval(wrong_approval, observed_at_utc=start + timedelta(seconds=23))
    approval_mismatch = restarted.authorize(
        RecoveryActionRequest(
            incident_id=correlation_decision.incident_id,
            correlation_id=correlation_decision.correlation_id,
            target=target,
            owner_id=second.lease.owner_id,
            lease_id=second.lease.lease_id,
            fencing_token=second.lease.fencing_token,
            action="recommend-rollback",
            parameters=stale_parameters,
            action_digest=stale_digest,
            approval_id=wrong_approval.approval_id,
            source_revision=source_revision,
            policy_version=recovery_policy.policy_version,
            observed_at_utc=start + timedelta(seconds=24),
        )
    )
    correlation.set_incident_state(
        correlation_decision.incident_id,
        state="recovery_owned",
        now=start + timedelta(seconds=24),
    )

    snapshot = build_incident_plane_snapshot(
        correlation_root=correlation_root,
        coordination_store=restarted,
        generated_at_utc=start + timedelta(seconds=24),
        evidence_root=root.as_posix(),
        timing_by_incident={
            correlation_decision.incident_id: IncidentTiming(
                collection_delay_ms=5000,
                correlation_overhead_ms=31,
                containment_seconds=0.2,
                recovery_seconds=10.1,
            )
        },
        child_evidence_by_incident={
            correlation_decision.incident_id: [
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/lifecycle_guard_validation/correlation-proof-20260802T144140Z/validation-report.json"
            ]
        },
    )
    write_incident_plane_snapshot(root / "incident-plane.json", snapshot)

    result = RecoveryCoordinationSeriesResult(
        series_id=series_id,
        acquired_owner_count=1 if acquired.admitted else 0,
        conflict_block_count=1 if conflict.result == "blocked" else 0,
        higher_fence_count=(
            1
            if second.lease.fencing_token == acquired.lease.fencing_token + 1
            else 0
        ),
        authorized_recommendation_count=1 if authorized.result == "authorized" else 0,
        deduped_recommendation_count=1 if deduped.result == "deduped" else 0,
        stale_owner_block_count=1 if stale_owner.result == "blocked" else 0,
        approval_mismatch_block_count=(
            1 if "approval_target_mismatch" in approval_mismatch.blockers else 0
        ),
        mutation_intent_count=(
            authorized.mutation_intent_count
            + deduped.mutation_intent_count
            + stale_owner.mutation_intent_count
            + approval_mismatch.mutation_intent_count
        ),
        coordinator_restart_count=1,
        passed=False,
    )
    return result.model_copy(
        update={
            "passed": (
                result.acquired_owner_count == 1
                and result.conflict_block_count == 1
                and result.higher_fence_count == 1
                and result.authorized_recommendation_count == 1
                and result.deduped_recommendation_count == 1
                and result.stale_owner_block_count == 1
                and result.approval_mismatch_block_count == 1
                and result.mutation_intent_count == 0
            )
        }
    )


def run_proof(
    *,
    output: Path,
    source_revision: str,
    recovery_policy_path: Path,
    correlation_policy_path: Path,
    series_count: int = 3,
) -> RecoveryCoordinationProof:
    output.mkdir(parents=True, exist_ok=True)
    recovery_policy = load_recovery_policy(
        recovery_policy_path,
        source_revision=source_revision,
    )
    series = [
        run_series(
            root=output / f"series-{index}",
            series_index=index,
            source_revision=source_revision,
            recovery_policy_path=recovery_policy_path,
            correlation_policy_path=correlation_policy_path,
        )
        for index in range(1, series_count + 1)
    ]
    proof = RecoveryCoordinationProof(
        source_revision=source_revision,
        policy_version=recovery_policy.policy_version,
        series=series,
        total_series=len(series),
        total_authorized_recommendations=sum(
            item.authorized_recommendation_count for item in series
        ),
        total_mutation_intents=sum(item.mutation_intent_count for item in series),
        production_mutation_count=0,
        passed=all(item.passed for item in series),
        generated_at_utc=datetime.now(UTC),
    )
    atomic_write_json(output / "validation-report.json", proof.model_dump(mode="json"))
    atomic_write_json(
        output / "policy-decision.json",
        {
            **recovery_policy.model_dump(mode="json"),
            "external_mutation_endpoint": False,
            "proof_result": "passed" if proof.passed else "failed",
        },
    )
    atomic_write_json(output / "artifact-index.json", _artifact_index(output))
    return proof


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fail-closed recovery coordination proof.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument(
        "--recovery-policy",
        type=Path,
        default=Path("configs/operations/recovery_coordination.toml"),
    )
    parser.add_argument(
        "--correlation-policy",
        type=Path,
        default=Path("configs/operations/cross_scenario_correlation.toml"),
    )
    parser.add_argument("--series-count", type=int, default=3)
    args = parser.parse_args()
    proof = run_proof(
        output=args.output,
        source_revision=args.source_revision,
        recovery_policy_path=args.recovery_policy,
        correlation_policy_path=args.correlation_policy,
        series_count=args.series_count,
    )
    print(json.dumps(proof.model_dump(mode="json"), indent=2, default=str))
    raise SystemExit(0 if proof.passed else 1)


if __name__ == "__main__":
    main()
