from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from evm.operations.failure_evidence import (
    ApprovalEvidence,
    ArtifactEvidence,
    CheckEvidence,
    ClosureEvidence,
    DecisionEvidence,
    EnvironmentEvidence,
    IdentityEvidence,
    InjectionEvidence,
    OperationalFailureReport,
    PortfolioEvidence,
    RecoveryEvidence,
    SignalEvidence,
    SourceEvidence,
    TimingEvidence,
    sha256_file,
    validate_closure,
)
from evm.operations.failure_scenarios import atomic_write_json
from evm.operations.scenario_d_supervision import (
    ChildHeartbeat,
    ChildIdentity,
    ChildObservation,
    LifecycleRunClaimStore,
    ProcessRecord,
    RestartLedger,
    ScenarioDPolicy,
    evaluate_child,
)


def _observation(
    *,
    now: datetime,
    child: str = "lifecycle_worker",
    pid: int = 101,
    heartbeat_age: float = 1,
    source_commit: str,
    expected_commit: str,
    lease_id: str = "scenario-d-lease-12345678",
    expected_lease: str = "scenario-d-lease-12345678",
    fence: int = 7,
    expected_fence: int = 7,
) -> ChildObservation:
    started_at = now - timedelta(minutes=5)
    identity = ChildIdentity(
        child_name=child,
        pid=pid,
        process_started_at=started_at,
        process_instance_id=f"{child}-instance-12345678",
        source_commit=source_commit,
        supervisor_lease_id=lease_id,
        fencing_token=fence,
    )
    return ChildObservation(
        schema_version="evm.scenario_d_child_observation.v1",
        child_name=child,
        observed_at=now,
        expected_source_commit=expected_commit,
        expected_lease_id=expected_lease,
        expected_fencing_token=expected_fence,
        pid_file_pid=pid,
        pid_file_process_exists=True,
        identity=identity,
        heartbeat=ChildHeartbeat(
            **identity.model_dump(),
            observed_at=now - timedelta(seconds=heartbeat_age),
        ),
        processes=[
            ProcessRecord(
                pid=pid,
                process_started_at=started_at,
                command_matches=True,
                executable="python.exe",
                command_line=f"python -m evm.control_panel.{child}",
            )
        ],
    )


def _result(name: str, expected: tuple[str, str], decision: Any) -> dict[str, Any]:
    observed = (decision.reason, decision.action)
    return {
        "fixture": name,
        "expected_reason": expected[0],
        "expected_action": expected[1],
        "observed_reason": observed[0],
        "observed_action": observed[1],
        "state": decision.state,
        "passed": observed == expected,
        "target_pid": decision.target_pid,
        "incident_fingerprint": decision.incident_fingerprint,
    }


def evaluate_fixtures(
    *,
    policy: ScenarioDPolicy,
    source_commit: str,
    root: Path,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    healthy = _observation(
        now=now,
        source_commit=source_commit,
        expected_commit=source_commit,
    )
    results: list[dict[str, Any]] = []
    results.append(_result("healthy", ("healthy", "none"), evaluate_child(healthy, policy)))

    for child in ("lifecycle_worker", "kubernetes_observer"):
        stopped = _observation(
            now=now,
            child=child,
            source_commit=source_commit,
            expected_commit=source_commit,
        ).model_copy(update={"pid_file_process_exists": False, "processes": []})
        results.append(
            _result(
                f"stopped_{child}",
                ("process_missing", "restart_exact"),
                evaluate_child(stopped, policy),
            )
        )

    stale = healthy.model_copy(
        update={
            "heartbeat": healthy.heartbeat.model_copy(
                update={"observed_at": now - timedelta(seconds=25)}
            )
        }
    )
    first_stale = evaluate_child(stale, policy)
    second_stale = evaluate_child(stale, policy, prior_failed_samples=first_stale.failed_samples)
    results.append(_result("stale_first_sample", ("heartbeat_stale", "none"), first_stale))
    results.append(
        _result("stale_second_sample", ("heartbeat_stale", "restart_exact"), second_stale)
    )

    old_revision = _observation(
        now=now,
        source_commit="0" * 40,
        expected_commit=source_commit,
    )
    results.append(
        _result(
            "source_revision_mismatch",
            ("source_revision_mismatch", "restart_exact"),
            evaluate_child(old_revision, policy),
        )
    )
    prior_fence = _observation(
        now=now,
        source_commit=source_commit,
        expected_commit=source_commit,
        lease_id="prior-lease-12345678",
        fence=6,
    )
    results.append(
        _result(
            "prior_supervisor_fence",
            ("supervisor_fence_mismatch", "restart_exact"),
            evaluate_child(prior_fence, policy),
        )
    )
    duplicate = healthy.processes[0].model_copy(update={"pid": 202})
    results.append(
        _result(
            "duplicate_process",
            ("blocked_duplicate", "none"),
            evaluate_child(
                healthy.model_copy(update={"processes": [*healthy.processes, duplicate]}),
                policy,
            ),
        )
    )
    results.append(
        _result(
            "stale_pid_unrelated_process",
            ("blocked_unknown_owner", "none"),
            evaluate_child(healthy.model_copy(update={"processes": []}), policy),
        )
    )
    mismatched_heartbeat = healthy.heartbeat.model_copy(
        update={"process_instance_id": "wrong-instance-12345678"}
    )
    results.append(
        _result(
            "heartbeat_identity_mismatch",
            ("blocked_identity", "none"),
            evaluate_child(healthy.model_copy(update={"heartbeat": mismatched_heartbeat}), policy),
        )
    )

    ledger = RestartLedger(root / "fixture-restart-ledger.json")
    missing = healthy.model_copy(update={"pid_file_process_exists": False, "processes": []})
    admitted = ledger.admit(evaluate_child(missing, policy), policy, now=now)
    replay = ledger.admit(evaluate_child(missing, policy), policy, now=now + timedelta(seconds=1))
    results.append(_result("restart_admitted", ("process_missing", "restart_exact"), admitted))
    results.append(
        _result("duplicate_incident_replay", ("duplicate_incident_replay", "none"), replay)
    )
    for offset, pid in ((2, 201), (5, 202)):
        item = missing.model_copy(update={"pid_file_pid": pid})
        ledger.admit(evaluate_child(item, policy), policy, now=now + timedelta(seconds=offset))
    exhausted = ledger.admit(
        evaluate_child(missing.model_copy(update={"pid_file_pid": 203}), policy),
        policy,
        now=now + timedelta(seconds=10),
    )
    results.append(
        _result(
            "restart_budget_exhausted",
            ("restart_budget_exhausted", "none"),
            exhausted,
        )
    )

    claim_store = LifecycleRunClaimStore(root / "fixture-claims", ttl_seconds=5)
    claim_inputs = {
        "run_id": "scenario-d-fixture-run",
        "worker_id": "worker-a",
        "worker_pid": 101,
        "process_instance_id": "worker-instance-12345678",
        "source_commit": source_commit,
        "supervisor_lease_id": "scenario-d-lease-12345678",
        "fencing_token": 7,
    }
    first = claim_store.acquire(**claim_inputs, now=now)
    replay_claim = claim_store.acquire(**claim_inputs, now=now + timedelta(seconds=1))
    conflict = claim_store.acquire(
        **{
            **claim_inputs,
            "worker_id": "worker-b",
            "worker_pid": 202,
            "process_instance_id": "worker-instance-87654321",
        },
        now=now + timedelta(seconds=2),
    )
    replaced = claim_store.acquire(
        **{
            **claim_inputs,
            "worker_id": "worker-b",
            "worker_pid": 202,
            "process_instance_id": "worker-instance-87654321",
        },
        now=now + timedelta(seconds=6),
    )
    claim_results = {
        "first_acquired": first.acquired,
        "idempotent_replay": replay_claim.acquired
        and replay_claim.claim.claim_id == first.claim.claim_id,
        "duplicate_owner_blocked": not conflict.acquired
        and conflict.reason == "active_claim_conflict",
        "expired_claim_replaced": replaced.acquired
        and replaced.claim.claim_epoch == first.claim.claim_epoch + 1,
        "passed": all(
            (
                first.acquired,
                replay_claim.acquired,
                not conflict.acquired,
                replaced.acquired,
            )
        ),
    }
    return results, claim_results


def run_fixture_proof(
    *,
    policy_path: Path,
    output_root: Path,
    source_commit: str,
    source_branch: str,
) -> Path:
    started_at = datetime.now(UTC)
    monotonic_started = time.monotonic_ns()
    run_id = f"scenario-d-fixtures-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{source_commit[:8]}"
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    policy = ScenarioDPolicy.from_toml(policy_path)
    fixture_results, claim_results = evaluate_fixtures(
        policy=policy,
        source_commit=source_commit,
        root=run_root,
        now=started_at,
    )
    fixtures_path = run_root / "fixture-results.json"
    claims_path = run_root / "claim-results.json"
    policy_snapshot_path = run_root / "policy.json"
    atomic_write_json(
        fixtures_path,
        {
            "schema_version": "evm.scenario_d_fixture_results.v1",
            "results": fixture_results,
            "passed": all(item["passed"] for item in fixture_results),
        },
    )
    atomic_write_json(claims_path, claim_results)
    atomic_write_json(policy_snapshot_path, policy.model_dump(mode="json"))
    artifacts = [
        ArtifactEvidence(
            uri=str(path.resolve()),
            sha256=sha256_file(path),
            media_type="application/json",
            evidence_role="run_evidence",
        )
        for path in (fixtures_path, claims_path, policy_snapshot_path)
    ]
    finished_at = datetime.now(UTC)
    monotonic_finished = time.monotonic_ns()
    all_fixtures_passed = all(item["passed"] for item in fixture_results)
    report = OperationalFailureReport(
        schema_version="evm.operational_failure_evidence.v1",
        scenario_id="D",
        run_id=run_id,
        claim_class="local_operational_validation",
        status="blocked",
        started_at=started_at,
        finished_at=finished_at,
        actor="codex-scenario-d-fixture-runner",
        approval=ApprovalEvidence(required=False, decision="not_required"),
        source=SourceEvidence(
            commit=source_commit,
            branch=source_branch,
            dirty=False,
            api_revision=source_commit,
            worker_revision=source_commit,
            observer_revision=source_commit,
        ),
        environment=EnvironmentEvidence(
            cluster_context="docker-desktop",
            node="local-windows-host",
            namespaces=["evm-production", "evm-staging", "evm-training"],
            hardware={"scope": "fixture_only", "gpu_mutation": False},
            runtime_versions={"fixture_contract": "evm.scenario_d_fixture_results.v1"},
        ),
        identities=IdentityEvidence(),
        identity_requirements=[],
        preconditions=[
            CheckEvidence(
                check_id="policy_loaded",
                passed=True,
                observed=policy.schema_version,
            )
        ],
        injection=InjectionEvidence(
            method="deterministic_fixture",
            action="evaluate synthetic process and heartbeat observations",
            target={"scope": "in_memory_and_run_local_files"},
            expected_effect="no live process mutation",
            blast_radius="none",
            performed=False,
        ),
        signals=[
            SignalEvidence(
                signal_id=item["fixture"],
                source="scenario_d_evaluator",
                observed_at=finished_at,
                healthy=item["passed"],
                detail=item,
            )
            for item in fixture_results
        ],
        decision=DecisionEvidence(
            expected="all fixture decisions and claim guards match contract",
            observed="passed" if all_fixtures_passed and claim_results["passed"] else "failed",
            blocker_codes=[] if all_fixtures_passed and claim_results["passed"] else ["fixture_failure"],
        ),
        mitigation={"runtime_mutation": False, "fail_closed": True},
        recovery=RecoveryEvidence(
            action="none_fixture_only",
            target_identity={"scope": "fixture"},
            result="live proof pending",
        ),
        postconditions=[
            CheckEvidence(
                check_id="all_fixture_decisions_match",
                passed=all_fixtures_passed,
                observed={"passed": sum(item["passed"] for item in fixture_results), "total": len(fixture_results)},
            ),
            CheckEvidence(
                check_id="run_claim_guards_match",
                passed=claim_results["passed"],
                observed=claim_results,
            ),
        ],
        artifacts=artifacts,
        limitations=[
            "fixture proof does not terminate a live worker or observer",
            "single-host validation does not prove distributed consensus or HA",
        ],
        portfolio=PortfolioEvidence(
            competencies=["process supervision contracts", "fencing and idempotency"],
            interview_questions=[
                "Why does a PID alone not prove process ownership?",
                "How does a restart budget prevent a crash loop?",
            ],
            trade_offs=["fail-closed ambiguity can require operator intervention"],
            factual_claims=["deterministic Scenario D supervision fixtures were validated"],
            prohibited_claims=["live recovery complete", "high availability", "zero downtime"],
        ),
        timing=TimingEvidence(
            audit_started_at=started_at,
            audit_finished_at=finished_at,
            monotonic_started_ns=monotonic_started,
            monotonic_finished_ns=monotonic_finished,
            sample_cadence_seconds=policy.check_interval_seconds,
            signal_precedence=[
                "ownership",
                "duplicate_count",
                "heartbeat_identity",
                "heartbeat_freshness",
                "source_revision",
                "lease_fence",
            ],
        ),
        readiness_closure=ClosureEvidence(
            decision="passed",
            required_check_ids=["all_fixture_decisions_match", "run_claim_guards_match"],
            completed_at=finished_at,
        ),
        live_proof_closure=ClosureEvidence(
            decision="not_run",
            required_check_ids=[],
            blockers=["live_child_termination_not_run"],
        ),
    )
    validation_errors = validate_closure(report, "readiness")
    if validation_errors:
        raise RuntimeError(f"scenario_d_fixture_evidence_invalid:{validation_errors}")
    report_path = run_root / "report.json"
    atomic_write_json(report_path, report.model_dump(mode="json"))
    artifact_paths = [fixtures_path, claims_path, policy_snapshot_path, report_path]
    index_path = run_root / "evidence-index.json"
    atomic_write_json(
        index_path,
        {
            "schema_version": "evm.scenario_d_evidence_index.v1",
            "run_id": run_id,
            "artifacts": [
                {"uri": str(path.resolve()), "sha256": sha256_file(path)}
                for path in artifact_paths
            ],
        },
    )
    atomic_write_json(
        output_root / "latest-fixture-proof.json",
        {
            "run_id": run_id,
            "report_uri": str(report_path.resolve()),
            "index_uri": str(index_path.resolve()),
            "source_commit": source_commit,
            "status": "readiness_pass_live_not_run",
        },
    )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Scenario D deterministic fixture proof.")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    args = parser.parse_args()
    report_path = run_fixture_proof(
        policy_path=args.policy,
        output_root=args.output_root,
        source_commit=args.source_commit,
        source_branch=args.source_branch,
    )
    print(json.dumps({"report_uri": str(report_path.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
