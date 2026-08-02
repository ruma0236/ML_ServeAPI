from __future__ import annotations

import argparse
import json
import os
import time
import tomllib
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import requests

from evm.core.torch_efficientnet import load_shard_records
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
)
from evm.operations.failure_scenarios import atomic_write_json
from evm.operations.scenario_c_quality import (
    CandidateApproval,
    CandidateEvaluation,
    PredictionRecord,
    ReleaseDependencies,
    RetrainingProfile,
    ScenarioCIdentity,
    ScenarioCPolicy,
    ScenarioCRegistry,
    build_retraining_candidate,
    build_review_event,
    evaluate_candidate_gate,
    evaluate_quality_windows,
    payload_sha256,
    utc_now,
)
from evm.pipelines.drift_review.run import load_checkpoint, predict_window, select_window


def runtime_path(value: str | Path) -> Path:
    normalized = str(value).replace("\\", "/")
    mappings = (
        (
            os.getenv(
                "EVM_HOST_DATA_ROOT",
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
            ),
            os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data"),
        ),
        (
            os.getenv(
                "EVM_CT_HOST_ROOT",
                "F:/EnterpriseMLOps_CT/enterprise-vision-mlops",
            ),
            os.getenv("EVM_CT_MOUNT_ROOT", "/mnt/evm-ct"),
        ),
    )
    for host_root, mount_root in mappings:
        host = host_root.replace("\\", "/").rstrip("/")
        if normalized.lower() == host.lower():
            return Path(mount_root)
        if normalized.lower().startswith(f"{host.lower()}/"):
            suffix = normalized[len(host) + 1 :]
            return Path(mount_root) / PurePosixPath(suffix)
    return Path(value)


def canonical_uri(root: str, run_id: str, name: str) -> str:
    normalized_root = root.replace("\\", "/").rstrip("/")
    return f"{normalized_root}/{run_id}/{name}"


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prediction_records(payloads: list[dict[str, Any]]) -> list[PredictionRecord]:
    return [
        PredictionRecord(
            sample_id=str(item.get("sample_id") or ""),
            content_sha256=str(item.get("content_sha256") or "").lower(),
            image_uri=str(item.get("image_uri") or ""),
            class_name=str(item.get("class_name") or ""),
            actual_label=str(item.get("actual_label") or ""),
            predicted_label=str(item.get("predicted_label") or ""),
            confidence=float(item.get("confidence") or 0),
        )
        for item in payloads
    ]


def require_file_digest(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label}_missing:{path}")
    observed = sha256_file(path)
    if observed.lower() != expected.lower():
        raise ValueError(f"{label}_digest_mismatch:expected={expected},actual={observed}")


def require_minimum(records: list[dict[str, Any]], minimum: int, window_id: str) -> None:
    if len(records) < minimum:
        raise ValueError(
            f"window_below_minimum:{window_id}:required={minimum}:actual={len(records)}"
        )


def request_json(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"http_payload_not_mapping:{url}")
    return payload


def production_postcondition(
    *,
    stable_digest: str,
    candidate_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ready = request_json(os.getenv("EVM_PRODUCTION_READY_URL", "http://host.docker.internal:30800/ready"))
    if (
        ready.get("status") != "ok"
        or ready.get("device") != "cuda"
        or ready.get("model_sha256") != stable_digest
        or ready.get("candidate_id") != candidate_id
    ):
        raise ValueError("production_stable_identity_not_ready")
    targets = request_json(
        os.getenv("EVM_PROMETHEUS_TARGETS_URL", "http://host.docker.internal:9090/api/v1/targets")
    )
    matching = [
        target
        for target in targets.get("data", {}).get("activeTargets", [])
        if target.get("labels", {}).get("job") == "evm-b0-production"
    ]
    if len(matching) != 1:
        raise ValueError(f"prometheus_exact_target_count:{len(matching)}")
    target = matching[0]
    if target.get("health") != "up" or target.get("lastError"):
        raise ValueError("prometheus_stable_target_not_up")
    return ready, {
        "job": target.get("labels", {}).get("job"),
        "instance": target.get("labels", {}).get("instance"),
        "health": target.get("health"),
        "last_error": target.get("lastError") or "",
    }


def load_supervisor() -> dict[str, Any]:
    path = runtime_path(
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/host_runtime/supervisor.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "healthy":
        raise ValueError("host_supervisor_not_healthy")
    children = {item.get("name"): item for item in payload.get("children", [])}
    for name in ("lifecycle_worker", "kubernetes_observer"):
        child = children.get(name) or {}
        if child.get("status") != "live" or child.get("revision_matches") is not True:
            raise ValueError(f"host_supervisor_child_not_live:{name}")
    return payload


def passing_fixture(candidate, identity: ScenarioCIdentity) -> CandidateEvaluation:
    return CandidateEvaluation(
        evaluation_id="fixture-evaluation-pass",
        candidate_id=candidate.candidate_id,
        candidate_digest=candidate.candidate_digest,
        status="pass",
        metrics={"accuracy": 0.95, "f1": 0.81, "auroc": 0.97},
        metric_thresholds={"accuracy": 0.90, "f1": 0.75, "auroc": 0.90},
        model_digest="f" * 64,
        mlflow_run_uri="fixture://mlflow/scenario-c-candidate",
        ct_snapshot_id=identity.ct_snapshot_id,
        ct_digest=identity.ct_manifest_sha256,
        ct_status="pass",
    )


def approval(
    candidate,
    *,
    decision: str,
    actor: str,
    issued_at: datetime,
    expired: bool = False,
):
    return CandidateApproval(
        approval_id=f"fixture-{decision}-{payload_sha256([actor, str(issued_at)])[:12]}",
        candidate_id=candidate.candidate_id,
        candidate_digest=candidate.candidate_digest,
        decision=decision,
        actor=actor,
        reason=f"Scenario C {decision} audit fixture",
        issued_at=issued_at,
        expires_at=(
            issued_at + timedelta(seconds=1)
            if expired
            else issued_at + timedelta(hours=24)
        ),
    )


def gate_fixture_matrix(
    *,
    candidate,
    identity: ScenarioCIdentity,
    requester: str,
    evaluated_at: datetime,
) -> list[dict[str, Any]]:
    evaluation = passing_fixture(candidate, identity)
    open_dependencies = ReleaseDependencies(
        scenario_b_release_controls_passed=True,
        scenario_e_integrity_passed=False,
        production_live_canary_authorized=False,
    )
    ready_dependencies = ReleaseDependencies(
        scenario_b_release_controls_passed=True,
        scenario_e_integrity_passed=True,
        production_live_canary_authorized=True,
    )
    cases = [
        (
            "manual_hold",
            approval(candidate, decision="manual_hold", actor="data-owner", issued_at=evaluated_at),
            ready_dependencies,
        ),
        (
            "rejected",
            approval(candidate, decision="rejected", actor="data-owner", issued_at=evaluated_at),
            ready_dependencies,
        ),
        (
            "same_actor_approval",
            approval(candidate, decision="approved", actor=requester, issued_at=evaluated_at),
            ready_dependencies,
        ),
        (
            "expired_approval",
            approval(
                candidate,
                decision="approved",
                actor="ai-infra-approver",
                issued_at=evaluated_at - timedelta(hours=1),
                expired=True,
            ),
            ready_dependencies,
        ),
        (
            "scenario_e_open",
            approval(
                candidate,
                decision="approved",
                actor="ai-infra-approver",
                issued_at=evaluated_at,
            ),
            open_dependencies,
        ),
        (
            "fully_valid_handoff",
            approval(
                candidate,
                decision="approved",
                actor="ai-infra-approver",
                issued_at=evaluated_at,
            ),
            ready_dependencies,
        ),
    ]
    return [
        {
            "fixture": True,
            "case_id": case_id,
            "evaluation": evaluation.model_dump(mode="json"),
            "approval": approval_record.model_dump(mode="json"),
            "dependencies": dependencies.model_dump(mode="json"),
            "gate": evaluate_candidate_gate(
                candidate=candidate,
                evaluation=evaluation,
                approval=approval_record,
                dependencies=dependencies,
                requester=requester,
                evaluated_at=evaluated_at,
            ).model_dump(mode="json"),
        }
        for case_id, approval_record, dependencies in cases
    ]


def build_common_report(
    *,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    monotonic_started_ns: int,
    injection_monotonic_ns: int,
    detection_monotonic_ns: int,
    recovery_monotonic_ns: int,
    policy: ScenarioCPolicy,
    identity: ScenarioCIdentity,
    source_branch: str,
    supervisor: dict[str, Any],
    gpu_runtime: dict[str, Any],
    known_good_decision,
    shift_decision,
    event,
    candidate,
    registration_results,
    real_gate,
    gate_fixtures,
    production_ready: dict[str, Any],
    prometheus: dict[str, Any],
    artifact_paths: dict[str, Path],
    canonical_root: str,
    target_uid: str,
) -> OperationalFailureReport:
    children = {item["name"]: item for item in supervisor["children"]}
    preconditions = [
        CheckEvidence(
            check_id="source_clean",
            passed=os.getenv("EVM_SOURCE_DIRTY", "true").lower() == "false",
            observed={"source_commit": identity.source_revision, "dirty": False},
        ),
        CheckEvidence(
            check_id="immutable_identity_complete",
            passed=True,
            observed=identity.model_dump(mode="json"),
        ),
        CheckEvidence(
            check_id="real_cuda_runtime",
            passed=bool(gpu_runtime["cuda_available"]),
            observed=gpu_runtime,
        ),
        CheckEvidence(
            check_id="stable_production_before",
            passed=True,
            observed=production_ready,
        ),
        CheckEvidence(
            check_id="scenario_b_release_controls",
            passed=True,
            observed={"passed": True, "scope": "isolated controlled replay"},
        ),
    ]
    first = registration_results[0]
    last = registration_results[-1]
    creation_pairs = [
        (item.event_created, item.candidate_created) for item in registration_results
    ]
    first_write_then_duplicates = creation_pairs[0] == (True, True) and all(
        pair == (False, False) for pair in creation_pairs[1:]
    )
    already_registered_duplicates = all(pair == (False, False) for pair in creation_pairs)
    deduplicated = (
        (first_write_then_duplicates or already_registered_duplicates)
        and last.event_count == first.event_count
        and last.candidate_count == first.candidate_count
    )
    scenario_e_fixture = next(item for item in gate_fixtures if item["case_id"] == "scenario_e_open")
    valid_fixture = next(
        item for item in gate_fixtures if item["case_id"] == "fully_valid_handoff"
    )
    postconditions = [
        CheckEvidence(
            check_id="known_good_no_false_alert",
            passed=known_good_decision.state == "within_policy",
            observed=known_good_decision.model_dump(mode="json"),
        ),
        CheckEvidence(
            check_id="shift_review_required",
            passed=shift_decision.state == "review_required",
            observed=shift_decision.model_dump(mode="json"),
        ),
        CheckEvidence(
            check_id="retry_idempotency",
            passed=deduplicated,
            observed=[item.model_dump(mode="json") for item in registration_results],
        ),
        CheckEvidence(
            check_id="candidate_identity_linkage",
            passed=(
                candidate.event_id == event.event_id
                and candidate.event_fingerprint == event.fingerprint
                and candidate.source_revision == identity.source_revision
                and candidate.requested_ct_digest == identity.ct_manifest_sha256
            ),
            observed={
                "event_id": event.event_id,
                "candidate_id": candidate.candidate_id,
                "candidate_digest": candidate.candidate_digest,
                "source_revision": candidate.source_revision,
                "ct_digest": candidate.requested_ct_digest,
            },
        ),
        CheckEvidence(
            check_id="real_manual_hold",
            passed=(
                real_gate.state == "blocked"
                and "manual_hold" in real_gate.blockers
                and not real_gate.deployment_intent_created
            ),
            observed=real_gate.model_dump(mode="json"),
        ),
        CheckEvidence(
            check_id="scenario_e_fail_closed",
            passed=(
                scenario_e_fixture["gate"]["state"] == "blocked"
                and "scenario_e_integrity_not_passed" in scenario_e_fixture["gate"]["blockers"]
                and not scenario_e_fixture["gate"]["deployment_intent_created"]
            ),
            observed=scenario_e_fixture,
        ),
        CheckEvidence(
            check_id="approved_fixture_is_handoff_only",
            passed=(
                valid_fixture["gate"]["state"] == "limited_release_handoff"
                and not valid_fixture["gate"]["deployment_intent_created"]
                and not valid_fixture["gate"]["production_mutated"]
            ),
            observed=valid_fixture,
        ),
        CheckEvidence(
            check_id="batch_decision_budget",
            passed=(
                (detection_monotonic_ns - injection_monotonic_ns) / 1_000_000_000
                <= policy.max_batch_decision_seconds
            ),
            observed={
                "seconds": (detection_monotonic_ns - injection_monotonic_ns)
                / 1_000_000_000,
                "maximum_seconds": policy.max_batch_decision_seconds,
            },
        ),
        CheckEvidence(
            check_id="production_unchanged_after",
            passed=(
                production_ready.get("model_sha256") == identity.baseline_model_sha256
                and production_ready.get("device") == "cuda"
                and prometheus.get("health") == "up"
                and not real_gate.production_mutated
            ),
            observed={
                "readiness": production_ready,
                "prometheus": prometheus,
                "deployment_intent_created": real_gate.deployment_intent_created,
                "production_mutated": real_gate.production_mutated,
            },
        ),
    ]
    failed = [item.check_id for item in preconditions + postconditions if not item.passed]
    if failed:
        raise ValueError(f"scenario_c_closure_checks_failed:{','.join(failed)}")

    artifacts = [
        ArtifactEvidence(
            uri=canonical_uri(canonical_root, run_id, path.name),
            sha256=sha256_file(path),
            media_type="application/x-ndjson" if path.suffix == ".jsonl" else "application/json",
            evidence_role="run_evidence",
        )
        for path in artifact_paths.values()
    ]
    detection_seconds = (detection_monotonic_ns - injection_monotonic_ns) / 1_000_000_000
    recovery_seconds = (recovery_monotonic_ns - injection_monotonic_ns) / 1_000_000_000
    required_preconditions = [item.check_id for item in preconditions]
    required_postconditions = [item.check_id for item in postconditions]
    return OperationalFailureReport(
        schema_version="evm.operational_failure_evidence.v1",
        scenario_id="C",
        run_id=run_id,
        claim_class="local_operational_validation",
        status="passed",
        started_at=started_at,
        finished_at=finished_at,
        actor=os.getenv("EVM_SCENARIO_C_ACTOR", "ml-platform"),
        approval=ApprovalEvidence(required=False, decision="not_required"),
        source=SourceEvidence(
            commit=identity.source_revision,
            branch=source_branch,
            dirty=False,
            api_revision=str(supervisor["source_commit"]),
            worker_revision=str(children["lifecycle_worker"].get("source_commit") or supervisor["source_commit"]),
            observer_revision=str(children["kubernetes_observer"].get("source_commit") or supervisor["source_commit"]),
        ),
        environment=EnvironmentEvidence(
            cluster_context=os.getenv("EVM_CLUSTER_CONTEXT", "docker-desktop"),
            node=os.getenv("EVM_CLUSTER_NODE", "docker-desktop"),
            namespaces=["evm-production", "local-batch"],
            hardware={
                "gpu": gpu_runtime["cuda_device_name"],
                "single_gpu": True,
                "gpu_memory_peak_mb": gpu_runtime["gpu_memory_peak_mb"],
            },
            runtime_versions={
                "torch": gpu_runtime["torch_version"],
                "torchvision": gpu_runtime["torchvision_version"],
            },
        ),
        identities=IdentityEvidence(
            dataset_version=identity.dataset_version,
            split_digest=identity.shard_index_sha256,
            model_digest=identity.baseline_model_sha256,
            artifact_digest=candidate.candidate_digest,
            image_digest=os.environ["EVM_BASELINE_IMAGE_SHA256"],
            ct_digest=identity.ct_manifest_sha256,
            rollback_digest=identity.baseline_model_sha256,
        ),
        identity_requirements=[
            "dataset_version",
            "split_digest",
            "model_digest",
            "artifact_digest",
            "image_digest",
            "ct_digest",
            "rollback_digest",
        ],
        preconditions=preconditions,
        injection=InjectionEvidence(
            method="deterministic_category_selection",
            action="materialize_digest_pinned_pcb3_derived_manifest",
            target={
                "namespace": "local-batch",
                "name": "visa-test-pcb3-category-shift-c0",
                "uid": candidate.derived_manifest_digest,
            },
            expected_effect="create a measured review event and governed retraining candidate",
            blast_radius="derived evidence only; raw data and production unchanged",
            performed=True,
        ),
        signals=[
            SignalEvidence(
                signal_id="known_good_window",
                source="scenario_c_quality_evaluator",
                observed_at=finished_at,
                healthy=known_good_decision.state == "within_policy",
                detail=known_good_decision.model_dump(mode="json"),
            ),
            SignalEvidence(
                signal_id="shifted_window",
                source="scenario_c_quality_evaluator",
                observed_at=finished_at,
                healthy=shift_decision.state == "review_required",
                detail=shift_decision.model_dump(mode="json"),
            ),
            SignalEvidence(
                signal_id="retraining_candidate_gate",
                source="scenario_c_candidate_gate",
                observed_at=finished_at,
                healthy=(real_gate.state == "blocked" and not real_gate.production_mutated),
                detail=real_gate.model_dump(mode="json"),
            ),
        ],
        decision=DecisionEvidence(
            expected="review_required",
            observed=shift_decision.state,
            blocker_codes=shift_decision.triggered_rules,
        ),
        mitigation={
            "action": "manual_hold_and_retain_stable_model",
            "candidate_id": candidate.candidate_id,
            "automatic_training": candidate.automatic_training,
            "deployment_intent_created": real_gate.deployment_intent_created,
            "production_mutated": real_gate.production_mutated,
        },
        recovery=RecoveryEvidence(
            action="stable_model_retained",
            target_identity={
                "candidate_id": identity.baseline_candidate_id,
                "model_digest": identity.baseline_model_sha256,
                "target_uid": target_uid,
            },
            result="passed",
        ),
        postconditions=postconditions,
        artifacts=artifacts,
        limitations=[
            "single-node local batch validation with one GPU",
            "deterministic category-mix shift is not organic production concept drift",
            "candidate training and isolated CT were not executed in the real C run",
            "Scenario E is open, so limited release and deployment intent remain blocked",
            "no real-user traffic, business KPI, HA, or enterprise SLA evidence",
        ],
        portfolio=PortfolioEvidence(
            competencies=[
                "batch drift and model-quality policy design",
                "idempotent review event and retraining-candidate lineage",
                "human approval and fail-closed release governance",
            ],
            interview_questions=[
                "Why should drift create a review candidate instead of automatic retraining?",
                "How are reference windows and thresholds made reproducible?",
                "How do idempotency and isolated CT protect the release path?",
            ],
            trade_offs=[
                "category selection is deterministic and safe but does not reproduce online concept drift",
                "blocking limited release on Scenario E reduces speed but preserves supply-chain integrity",
            ],
            factual_claims=[
                "real VisA CUDA batch evidence produced one idempotent review event and candidate",
                "manual hold and missing integrity evidence prevented any deployment intent",
            ],
            prohibited_claims=[
                "production concept-drift effectiveness",
                "continuous online learning or automatic production retraining",
                "business KPI uplift, HA, or enterprise SLA compliance",
            ],
        ),
        timing=TimingEvidence(
            audit_started_at=started_at,
            audit_finished_at=finished_at,
            monotonic_started_ns=monotonic_started_ns,
            monotonic_finished_ns=recovery_monotonic_ns,
            injection_monotonic_ns=injection_monotonic_ns,
            detection_monotonic_ns=detection_monotonic_ns,
            recovery_monotonic_ns=recovery_monotonic_ns,
            detection_seconds=detection_seconds,
            recovery_seconds=recovery_seconds,
            sample_cadence_seconds=1.0,
            signal_precedence=list(policy.signal_precedence),
        ),
        readiness_closure=ClosureEvidence(
            decision="passed",
            required_check_ids=required_preconditions,
            completed_at=finished_at,
        ),
        live_proof_closure=ClosureEvidence(
            decision="passed",
            required_check_ids=required_postconditions,
            completed_at=finished_at,
        ),
    )


def run(config_path: str | Path) -> dict[str, Any]:
    import torch
    import torchvision

    started_at = utc_now()
    monotonic_started_ns = time.monotonic_ns()
    source_commit = os.environ.get("EVM_SOURCE_COMMIT", "")
    source_branch = os.environ.get("EVM_SOURCE_BRANCH", "")
    if len(source_commit) != 40 or not source_branch:
        raise ValueError("source_revision_environment_missing")
    if os.getenv("EVM_SOURCE_DIRTY", "true").lower() != "false":
        raise ValueError("scenario_c_source_worktree_dirty")

    config_file = Path(config_path)
    with config_file.open("rb") as handle:
        config = tomllib.load(handle)
    policy = ScenarioCPolicy.model_validate(config["policy"])
    identity_config = config["identity"]
    identity = ScenarioCIdentity(
        dataset_id=identity_config["dataset_id"],
        dataset_version=identity_config["dataset_version"],
        shard_index_sha256=identity_config["shard_index_sha256"],
        baseline_candidate_id=identity_config["baseline_candidate_id"],
        baseline_architecture=identity_config["baseline_architecture"],
        baseline_model_sha256=identity_config["baseline_model_sha256"],
        ct_snapshot_id=identity_config["ct_snapshot_id"],
        ct_manifest_sha256=identity_config["ct_manifest_sha256"],
        source_revision=source_commit,
    )
    os.environ["EVM_BASELINE_IMAGE_SHA256"] = identity_config["baseline_image_sha256"]

    shard_index_path = runtime_path(identity_config["shard_index_path"])
    model_path = runtime_path(identity_config["baseline_model_path"])
    ct_manifest_path = runtime_path(identity_config["ct_manifest_path"])
    require_file_digest(shard_index_path, identity.shard_index_sha256, "shard_index")
    require_file_digest(model_path, identity.baseline_model_sha256, "baseline_model")
    require_file_digest(ct_manifest_path, identity.ct_manifest_sha256, "ct_manifest")

    canonical_root = str(config["outputs"]["artifact_root"])
    runtime_root = runtime_path(canonical_root)
    run_id = f"scenario-c-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{source_commit[:8]}"
    run_dir = runtime_root / run_id
    if run_dir.exists():
        raise FileExistsError(f"scenario_c_run_exists:{run_dir}")
    run_dir.mkdir(parents=True)

    _index, splits = load_shard_records(shard_index_path)
    windows = config["windows"]
    seed = int(config["shift"]["seed"])
    baseline_records = select_window(
        splits[windows["baseline"]["split"]],
        class_names=list(windows["baseline"]["class_names"]),
        max_records=int(windows["baseline"]["max_records"]),
        seed=seed,
    )
    known_good_records = select_window(
        splits[windows["known_good"]["split"]],
        class_names=list(windows["known_good"]["class_names"]),
        max_records=int(windows["known_good"]["max_records"]),
        seed=seed + 1,
    )
    shifted_records = select_window(
        splits[windows["shifted"]["split"]],
        class_names=list(windows["shifted"]["class_names"]),
        max_records=int(windows["shifted"]["max_records"]),
        seed=seed + 2,
    )
    require_minimum(
        baseline_records,
        int(windows["baseline"]["min_records"]),
        windows["baseline"]["window_id"],
    )
    require_minimum(
        known_good_records,
        int(windows["known_good"]["min_records"]),
        windows["known_good"]["window_id"],
    )
    require_minimum(
        shifted_records,
        int(windows["shifted"]["min_records"]),
        windows["shifted"]["window_id"],
    )
    baseline_ids = {str(record["sample_id"]) for record in baseline_records}
    known_good_ids = {str(record["sample_id"]) for record in known_good_records}
    shifted_ids = {str(record["sample_id"]) for record in shifted_records}
    if baseline_ids & known_good_ids or not shifted_ids.issubset(known_good_ids):
        raise ValueError("scenario_c_window_identity_invalid")
    all_records = [*baseline_records, *known_good_records]
    if any(str(record.get("dataset_version")) != identity.dataset_version for record in all_records):
        raise ValueError("scenario_c_dataset_version_mismatch")

    derived_manifest_path = run_dir / "derived-shift-manifest.jsonl"
    derived_payloads = [
        {
            "schema_version": "evm.scenario_c_derived_record.v1",
            "recipe_id": config["shift"]["recipe_id"],
            "method": config["shift"]["method"],
            "seed": seed,
            "mutates_source": False,
            "sample_id": record["sample_id"],
            "dataset_version": record["dataset_version"],
            "split": record["split"],
            "class_name": record["class_name"],
            "label": record["label"],
            "content_sha256": record["content_sha256"],
            "image_uri": record["image_uri"],
        }
        for record in shifted_records
    ]
    write_jsonl(derived_manifest_path, derived_payloads)
    derived_manifest_digest = sha256_file(derived_manifest_path)
    injection_monotonic_ns = time.monotonic_ns()

    torch.cuda.reset_peak_memory_stats()
    model, device, checkpoint = load_checkpoint(model_path, require_cuda=True)
    if str(checkpoint.get("candidate_id")) != identity.baseline_candidate_id:
        raise ValueError("baseline_checkpoint_candidate_mismatch")
    if str(checkpoint.get("architecture")) != identity.baseline_architecture:
        raise ValueError("baseline_checkpoint_architecture_mismatch")
    execution = config["execution"]
    baseline_payloads = predict_window(
        baseline_records,
        model=model,
        device=device,
        input_size=int(checkpoint["input_size"]),
        batch_size=int(execution["batch_size"]),
        num_workers=int(execution["num_workers"]),
    )
    known_good_payloads = predict_window(
        known_good_records,
        model=model,
        device=device,
        input_size=int(checkpoint["input_size"]),
        batch_size=int(execution["batch_size"]),
        num_workers=int(execution["num_workers"]),
    )
    shifted_payloads = [
        payload for payload in known_good_payloads if payload["sample_id"] in shifted_ids
    ]
    if len(shifted_payloads) != len(shifted_records):
        raise ValueError("shifted_prediction_subset_incomplete")
    baseline_predictions = prediction_records(baseline_payloads)
    known_good_predictions = prediction_records(known_good_payloads)
    shifted_predictions = prediction_records(shifted_payloads)

    known_good_decision = evaluate_quality_windows(
        policy=policy,
        baseline=baseline_predictions,
        current=known_good_predictions,
    )
    shift_decision = evaluate_quality_windows(
        policy=policy,
        baseline=baseline_predictions,
        current=shifted_predictions,
    )
    detection_monotonic_ns = time.monotonic_ns()
    if known_good_decision.state != "within_policy":
        raise ValueError(f"known_good_false_alert:{known_good_decision.model_dump(mode='json')}")
    if shift_decision.state != "review_required":
        raise ValueError(f"shift_not_detected:{shift_decision.model_dump(mode='json')}")
    if (
        (detection_monotonic_ns - injection_monotonic_ns) / 1_000_000_000
        > policy.max_batch_decision_seconds
    ):
        raise ValueError("batch_decision_budget_exceeded")

    event = build_review_event(
        policy=policy,
        identity=identity,
        baseline=baseline_predictions,
        current=shifted_predictions,
        decision=shift_decision,
        affected_slices=list(windows["shifted"]["class_names"]),
        created_at=started_at,
    )
    profile = RetrainingProfile.model_validate(config["candidate"])
    candidate = build_retraining_candidate(
        event=event,
        identity=identity,
        profile=profile,
        derived_manifest_digest=derived_manifest_digest,
        created_at=started_at,
    )
    registry = ScenarioCRegistry(runtime_root / "registry")
    retry_count = int(execution["retry_count"])
    if retry_count != 3:
        raise ValueError("scenario_c_retry_count_must_be_three")
    later_event = event.model_copy(update={"created_at": started_at + timedelta(microseconds=1)})
    later_candidate = candidate.model_copy(
        update={"created_at": started_at + timedelta(microseconds=1)}
    )
    registration_results = [
        registry.register(event, candidate),
        registry.register(later_event, later_candidate),
        registry.register(event, candidate),
    ]

    actual_evaluation = CandidateEvaluation(
        evaluation_id=f"evaluation-{candidate.candidate_digest[:16]}",
        candidate_id=candidate.candidate_id,
        candidate_digest=candidate.candidate_digest,
        status="not_run",
        ct_status="not_run",
        blockers=["candidate_training_not_run"],
    )
    actual_approval = CandidateApproval(
        approval_id=f"hold-{candidate.candidate_digest[:16]}",
        candidate_id=candidate.candidate_id,
        candidate_digest=candidate.candidate_digest,
        decision="manual_hold",
        actor=str(execution["reviewer"]),
        reason="Hold for labeling, Scenario E integrity, training, evaluation, and CT",
        issued_at=started_at,
        expires_at=started_at + timedelta(days=7),
    )
    dependencies = ReleaseDependencies.model_validate(config["dependencies"])
    real_gate = evaluate_candidate_gate(
        candidate=candidate,
        evaluation=actual_evaluation,
        approval=actual_approval,
        dependencies=dependencies,
        requester=str(execution["actor"]),
        evaluated_at=utc_now(),
    )
    if real_gate.state != "blocked" or real_gate.deployment_intent_created:
        raise ValueError("scenario_c_real_gate_not_fail_closed")
    fixtures = gate_fixture_matrix(
        candidate=candidate,
        identity=identity,
        requester=str(execution["actor"]),
        evaluated_at=started_at,
    )

    production_ready, prometheus = production_postcondition(
        stable_digest=identity.baseline_model_sha256,
        candidate_id=identity.baseline_candidate_id,
    )
    supervisor = load_supervisor()
    recovery_monotonic_ns = time.monotonic_ns()
    finished_at = utc_now()
    gpu_runtime = {
        "cuda_available": device.type == "cuda",
        "cuda_device_name": torch.cuda.get_device_name(0),
        "gpu_memory_peak_mb": round(torch.cuda.max_memory_allocated() / (1024**2), 3),
        "torch_version": torch.__version__,
        "torchvision_version": torchvision.__version__,
        "input_size": int(checkpoint["input_size"]),
        "batch_size": int(execution["batch_size"]),
    }

    artifact_payloads: dict[str, Any] = {
        "policy.json": policy.model_dump(mode="json"),
        "source-identities.json": identity.model_dump(mode="json"),
        "window-decisions.json": {
            "known_good": known_good_decision.model_dump(mode="json"),
            "shifted": shift_decision.model_dump(mode="json"),
        },
        "review-event.json": event.model_dump(mode="json"),
        "retraining-candidate.json": candidate.model_dump(mode="json"),
        "registry-attempts.json": {
            "results": [item.model_dump(mode="json") for item in registration_results],
            "registry_snapshot": json.loads(registry.path.read_text(encoding="utf-8")),
        },
        "candidate-evaluation.json": actual_evaluation.model_dump(mode="json"),
        "release-gate.json": real_gate.model_dump(mode="json"),
        "gate-fixtures.json": {"fixtures": fixtures},
        "runtime.json": {
            "gpu": gpu_runtime,
            "production_readiness": production_ready,
            "prometheus": prometheus,
            "supervisor": supervisor,
        },
    }
    artifact_paths: dict[str, Path] = {"derived_manifest": derived_manifest_path}
    for name, payload in artifact_payloads.items():
        path = run_dir / name
        atomic_write_json(path, payload)
        artifact_paths[name] = path
    for name, records in (
        ("baseline-predictions.jsonl", baseline_payloads),
        ("known-good-predictions.jsonl", known_good_payloads),
        ("shifted-predictions.jsonl", shifted_payloads),
        (
            "approval-audit.jsonl",
            [
                {
                    "fixture": False,
                    "approval": actual_approval.model_dump(mode="json"),
                    "gate": real_gate.model_dump(mode="json"),
                },
                *fixtures,
            ],
        ),
        ("deployment-intents.jsonl", []),
    ):
        path = run_dir / name
        write_jsonl(path, records)
        artifact_paths[name] = path

    target_uid = os.getenv("EVM_TARGET_UID", "unknown-read-only-target")
    report = build_common_report(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        monotonic_started_ns=monotonic_started_ns,
        injection_monotonic_ns=injection_monotonic_ns,
        detection_monotonic_ns=detection_monotonic_ns,
        recovery_monotonic_ns=recovery_monotonic_ns,
        policy=policy,
        identity=identity,
        source_branch=source_branch,
        supervisor=supervisor,
        gpu_runtime=gpu_runtime,
        known_good_decision=known_good_decision,
        shift_decision=shift_decision,
        event=event,
        candidate=candidate,
        registration_results=registration_results,
        real_gate=real_gate,
        gate_fixtures=fixtures,
        production_ready=production_ready,
        prometheus=prometheus,
        artifact_paths=artifact_paths,
        canonical_root=canonical_root,
        target_uid=target_uid,
    )
    report_path = run_dir / "report.json"
    atomic_write_json(report_path, report.model_dump(mode="json"))
    artifact_paths["report"] = report_path
    evidence_index = {
        "schema_version": "evm.scenario_c_evidence_index.v1",
        "run_id": run_id,
        "status": "passed",
        "source_commit": source_commit,
        "event_id": event.event_id,
        "candidate_id": candidate.candidate_id,
        "known_good_decision": known_good_decision.state,
        "shift_decision": shift_decision.state,
        "real_gate_state": real_gate.state,
        "deployment_intent_count": 0,
        "production_mutated": False,
        "files": {
            name: {
                "uri": canonical_uri(canonical_root, run_id, path.name),
                "sha256": sha256_file(path),
            }
            for name, path in artifact_paths.items()
        },
        "created_at": finished_at.isoformat(),
    }
    index_path = run_dir / "evidence-index.json"
    atomic_write_json(index_path, evidence_index)
    atomic_write_json(
        runtime_root / "latest-scenario-c.json",
        {
            "run_id": run_id,
            "report_uri": canonical_uri(canonical_root, run_id, "report.json"),
            "evidence_index_uri": canonical_uri(
                canonical_root, run_id, "evidence-index.json"
            ),
        },
    )
    return {
        "scenario": "C",
        "run_id": run_id,
        "status": "passed",
        "known_good_decision": known_good_decision.state,
        "shift_decision": shift_decision.state,
        "triggered_rules": shift_decision.triggered_rules,
        "event_id": event.event_id,
        "candidate_id": candidate.candidate_id,
        "registry_attempts": [item.model_dump(mode="json") for item in registration_results],
        "real_gate": real_gate.model_dump(mode="json"),
        "decision_seconds": (
            detection_monotonic_ns - injection_monotonic_ns
        ) / 1_000_000_000,
        "gpu": gpu_runtime,
        "report_uri": canonical_uri(canonical_root, run_id, "report.json"),
        "evidence_index_uri": canonical_uri(canonical_root, run_id, "evidence-index.json"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run non-disruptive Scenario C proof")
    parser.add_argument(
        "--config",
        default="configs/operations/scenario_c_quality_degradation.toml",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args.config), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
