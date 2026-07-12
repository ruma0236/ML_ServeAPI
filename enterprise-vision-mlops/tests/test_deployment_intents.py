from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

import pytest

from evm.control_panel.cdct import load_ci_evidence, with_ci_bundle_digest
from evm.control_panel.deployment_executor import (
    ModelTarget,
    execute_apply,
    execute_rollback,
    load_rollback_target,
    manifest_apply_command,
    model_mount_path,
    validate_executor_target,
    verified_model_target,
)
from evm.control_panel.deployment_intents import (
    DeploymentIntentBlocked,
    DeploymentTransitionRejected,
    DeploymentVersionConflict,
    approve_intent,
    create_deployment_intent,
    deployment_target_allowed,
    queue_intent,
    read_intents,
    request_approval,
)
from evm.control_panel.schemas import (
    ArtifactReadinessEvaluation,
    CycleRun,
    DeploymentIntentRequest,
    DeploymentTransitionRequest,
    ReadinessEvidenceCheck,
    ResourceRef,
)


COMMIT_SHA = "a" * 40
MODEL_SHA = "b" * 64
IMAGE_DIGEST = "enterprise-vision-mlops-efficientnet-serving@sha256:" + "c" * 64


def write_ci_evidence(path: Path, *, commit_sha: str = COMMIT_SHA) -> None:
    bundle = with_ci_bundle_digest(
        {
            "repository": "ruma0236/ML_ServeAPI",
            "workflow_name": "Enterprise Vision MLOps CI",
            "workflow_run_id": "123456",
            "workflow_run_attempt": 1,
            "commit_sha": commit_sha,
            "ref": "refs/heads/codex/mac-mini-worker",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "python_test_result": "pass",
            "frontend_test_result": "pass",
            "evidence_validator_result": "pass",
            "compose_config_result": "pass",
            "kustomize_render_result": "pass",
            "image_digest": IMAGE_DIGEST,
            "config_render_digest": "d" * 64,
            "contract_digest": "e" * 64,
            "source_uri": "https://github.com/ruma0236/ML_ServeAPI/actions/runs/123456",
            "generated_at": "2026-07-10T10:00:00Z",
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2), encoding="utf-8")


def ready_cycle() -> CycleRun:
    cycle = CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    checks = [
        ReadinessEvidenceCheck(
            check_id="model_artifact",
            category="model",
            status="pass",
            evidence_uri="F:/artifacts/model.pt",
            evidence_digest=MODEL_SHA,
            observed={"actual_sha256": MODEL_SHA},
        ),
        ReadinessEvidenceCheck(
            check_id="kubernetes_runtime",
            category="runtime",
            status="pass",
            evidence_uri="F:/artifacts/kubernetes.json",
            observed={"serving_image_digest": IMAGE_DIGEST},
        ),
        ReadinessEvidenceCheck(
            check_id="rollback_reference",
            category="runtime",
            status="pass",
            evidence_uri="F:/artifacts/rollback.json",
            observed={"rollback_ready": True},
        ),
    ]
    readiness = ArtifactReadinessEvaluation(
        evaluation_id="readiness-ready-for-deployment",
        decision="ready",
        status="pass",
        data_status="pass",
        model_status="pass",
        runtime_status="pass",
        candidate_id="effnet-b7-img600-finetune-adamw",
        dataset_version=cycle.dataset.version,
        evaluated_at="2026-07-10T10:00:00Z",
        input_digest="f" * 64,
        checks=checks,
        blockers=[],
        report_uri="F:/artifacts/readiness.json",
    )
    cdct = cycle.cdct_gate.model_copy(
        update={
            "status": "pass",
            "ci_status": "pass",
            "cd_status": "pass",
            "ct_status": "pass",
            "failed_checks": [],
            "promotion_blockers": [],
            "promotion_decision": "allow",
            "block_reason": None,
        }
    )
    environment = cycle.environment.model_copy(
        update={"release_ref": COMMIT_SHA, "promotion_blockers": []}
    )
    return cycle.model_copy(
        update={
            "readiness_evaluation": readiness,
            "cdct_gate": cdct,
            "environment": environment,
            "promotion_policy": None,
        }
    )


def deployment_request(environment: str = "staging") -> DeploymentIntentRequest:
    namespace = "evm-production" if environment == "production" else "evm-staging"
    return DeploymentIntentRequest(
        target_environment=environment,  # type: ignore[arg-type]
        target_namespace=namespace,
        target=ResourceRef(namespace=namespace, kind="Deployment", name="evm-b7-serving"),
        actor="ml-platform",
        reason="EVM-235 guarded deployment",
        dry_run=True,
    )


def configure_paths(tmp_path: Path, monkeypatch) -> Path:
    ci_path = tmp_path / "ci" / "latest_ci_evidence.json"
    write_ci_evidence(ci_path)
    monkeypatch.setenv("EVM_CI_EVIDENCE_PATH", str(ci_path))
    monkeypatch.setenv("EVM_EXPECTED_CI_COMMIT", COMMIT_SHA)
    monkeypatch.setenv("EVM_DEPLOYMENT_INTENT_ROOT", str(tmp_path / "intents"))
    monkeypatch.setenv("EVM_PROMOTION_POLICY_EVIDENCE_ROOT", str(tmp_path / "policy"))
    return ci_path


def transition(actor: str, version: int, reason: str) -> DeploymentTransitionRequest:
    return DeploymentTransitionRequest(actor=actor, reason=reason, expected_version=version)


def successful_runner(command, **_kwargs):
    return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")


def failed_runner(command, **_kwargs):
    return subprocess.CompletedProcess(command, 1, stdout="", stderr="kubectl failed")


def configure_executor_targets(monkeypatch) -> None:
    current = ModelTarget(
        candidate_id="effnet-b7-img600-finetune-adamw",
        artifact_uri="F:/artifacts/model.pt",
        mount_path="/mnt/evm-data/artifacts/model.pt",
        digest=MODEL_SHA,
    )
    rollback = ModelTarget(
        candidate_id="effnet-b7-img600-finetune-adamw",
        artifact_uri="F:/artifacts/rollback-model.pt",
        mount_path="/mnt/evm-data/artifacts/rollback-model.pt",
        digest="9" * 64,
    )
    monkeypatch.setattr(
        "evm.control_panel.deployment_executor.verified_model_target",
        lambda *_args, **_kwargs: current,
    )
    monkeypatch.setattr(
        "evm.control_panel.deployment_executor.load_rollback_target",
        lambda *_args, **_kwargs: rollback,
    )


def test_ci_evidence_is_immutable_and_fails_closed(tmp_path: Path):
    path = tmp_path / "ci.json"
    write_ci_evidence(path)
    valid = load_ci_evidence(path, expected_commit=COMMIT_SHA)
    assert valid.valid is True
    assert all(status == "pass" for status in valid.checks.values())

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["python_test_result"] = "fail"
    path.write_text(json.dumps(payload), encoding="utf-8")
    invalid = load_ci_evidence(path, expected_commit=COMMIT_SHA)
    assert invalid.valid is False
    assert "ci_python_tests_failed" in invalid.blockers
    assert "ci_bundle_digest_failed" in invalid.blockers


def test_create_intent_requires_ci_readiness_policy_and_expected_commit(
    tmp_path: Path, monkeypatch
):
    configure_paths(tmp_path, monkeypatch)
    monkeypatch.delenv("EVM_EXPECTED_CI_COMMIT")
    cycle = ready_cycle().model_copy(
        update={"environment": ready_cycle().environment.model_copy(update={"release_ref": ""})}
    )

    with pytest.raises(DeploymentIntentBlocked) as exc:
        create_deployment_intent(deployment_request(), cycle=cycle)

    assert "expected_ci_commit_not_configured" in exc.value.blockers


def test_staging_intent_runs_dry_run_approval_queue_apply_and_rollback(
    tmp_path: Path, monkeypatch
):
    mutable_ci_path = configure_paths(tmp_path, monkeypatch)
    configure_executor_targets(monkeypatch)
    cycle = ready_cycle()
    monkeypatch.setattr("evm.control_panel.deployment_intents.current_cycle", lambda: cycle)

    intent = create_deployment_intent(deployment_request(), cycle=cycle)
    assert intent.state == "dry_run"
    assert intent.promotion_policy.decision == "allow"
    immutable_ci_path = Path(intent.ci_evidence_uri)
    assert immutable_ci_path == tmp_path / "intents" / intent.intent_id / "ci_evidence.json"
    assert immutable_ci_path.is_file()
    mutable_ci_path.write_text("{}\n", encoding="utf-8")

    intent = request_approval(
        intent.intent_id,
        transition("ml-platform", intent.version, "request deployment approval"),
    )
    intent = approve_intent(
        intent.intent_id,
        transition("ai-infra-sre", intent.version, "approve staging deployment"),
    )
    intent = queue_intent(
        intent.intent_id,
        transition("ai-infra-sre", intent.version, "queue verified deployment"),
        cycle=cycle,
    )
    assert intent.state == "queued"
    assert [item.to_state for item in intent.transitions] == [
        "dry_run",
        "pending_approval",
        "pending_approval",
        "queued",
    ]

    applied = execute_apply(intent.intent_id, runner=successful_runner, require_enabled=False)
    assert applied.state == "applied"
    assert applied.execution_result.exit_code == 0
    assert applied.execution_result.stdout_uri.endswith("apply-stdout.log")
    assert applied.execution_result.command[0].startswith("kubectl apply -f ")
    assert "patch deployment/evm-b7-serving" in applied.execution_result.command[1]
    assert f'"EVM_MODEL_SHA256","value":"{MODEL_SHA}"' in applied.execution_result.command[1]

    rolled_back = execute_rollback(
        intent.intent_id, runner=successful_runner, require_enabled=False
    )
    assert rolled_back.state == "rolled_back"
    assert rolled_back.transitions[-1].result == "rolled_back"
    assert all("rollout undo" not in command for command in rolled_back.execution_result.command)
    assert '"EVM_MODEL_SHA256","value":"' + "9" * 64 in rolled_back.execution_result.command[0]


def test_verified_model_target_maps_host_artifact_to_container_mount(
    tmp_path: Path, monkeypatch
):
    host_root = tmp_path / "evm-data"
    artifact = host_root / "artifacts" / "model.pt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"immutable-model")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(host_root))
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")

    target = verified_model_target(
        str(artifact),
        digest,
        "effnet-b7-img600-finetune-adamw",
    )

    assert target.mount_path == "/mnt/evm-data/artifacts/model.pt"
    assert target.digest == digest
    with pytest.raises(DeploymentTransitionRejected, match="model_artifact_outside_data_root"):
        model_mount_path(f"{host_root}-other/artifacts/model.pt")


def test_rollback_target_reads_approved_reference_and_rejects_current_model(
    tmp_path: Path, monkeypatch
):
    configure_paths(tmp_path, monkeypatch)
    intent = create_deployment_intent(deployment_request(), cycle=ready_cycle())
    host_root = tmp_path / "evm-data"
    artifact = host_root / "artifacts" / "rollback-model.pt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"approved-rollback-model")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    reference = host_root / "artifacts" / "rollback.json"
    reference.write_text(
        json.dumps(
            {
                "schema_version": "evm.model_rollback_reference.v1",
                "candidate_id": "effnet-b0-previous-production",
                "status": "approved",
                "rollback_ready": True,
                "model_digest": digest,
                "model_artifact": str(artifact),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(host_root))
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    intent = intent.model_copy(update={"rollback_reference": str(reference)})

    target = load_rollback_target(intent)

    assert target.digest == digest
    assert target.mount_path == "/mnt/evm-data/artifacts/rollback-model.pt"
    with pytest.raises(DeploymentTransitionRejected, match="rollback_reuses_current_model"):
        load_rollback_target(intent.model_copy(update={"model_digest": digest}))


def test_generated_lifecycle_manifest_is_allowlisted_only_inside_configured_root(
    tmp_path: Path,
    monkeypatch,
):
    configure_paths(tmp_path, monkeypatch)
    generated_root = tmp_path / "lifecycle-runs"
    manifest_dir = generated_root / "run-1" / "kubernetes" / "serving"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "kustomization.yaml").write_text("resources: []\n", encoding="utf-8")
    monkeypatch.setenv("EVM_KUBERNETES_GENERATED_MANIFEST_ROOT", str(generated_root))
    intent = create_deployment_intent(deployment_request(), cycle=ready_cycle())
    dynamic = intent.model_copy(
        update={
            "target": ResourceRef(
                namespace="evm-staging",
                kind="Deployment",
                name="evm-b0-staging",
            ),
            "manifest_ref": str(manifest_dir),
        }
    )

    assert deployment_target_allowed(dynamic.target.name, dynamic.manifest_ref) is True
    validate_executor_target(dynamic)
    assert manifest_apply_command(dynamic) == ["kubectl", "apply", "-k", str(manifest_dir)]
    assert deployment_target_allowed(
        "evm-b0-staging",
        str(tmp_path / "outside"),
    ) is False


def test_production_requires_allowed_separate_approver_before_queue(
    tmp_path: Path, monkeypatch
):
    configure_paths(tmp_path, monkeypatch)
    cycle = ready_cycle()
    intent = create_deployment_intent(deployment_request("production"), cycle=cycle)
    assert intent.promotion_policy.decision == "pending_approval"

    with pytest.raises(DeploymentTransitionRejected, match="approval_requester_mismatch"):
        request_approval(
            intent.intent_id,
            transition("viewer", intent.version, "unauthorized approval request"),
        )
    intent = request_approval(
        intent.intent_id,
        transition("ml-platform", intent.version, "request production approval"),
    )
    with pytest.raises(DeploymentTransitionRejected, match="requester_approver_conflict"):
        approve_intent(
            intent.intent_id,
            transition("ml-platform", intent.version, "self approval"),
        )
    with pytest.raises(DeploymentTransitionRejected, match="approver_not_allowed"):
        approve_intent(
            intent.intent_id,
            transition("viewer", intent.version, "invalid approver"),
        )

    intent = approve_intent(
        intent.intent_id,
        transition("release-manager", intent.version, "approve production deployment"),
    )
    with pytest.raises(DeploymentTransitionRejected, match="queue_actor_not_authorized"):
        queue_intent(
            intent.intent_id,
            transition("viewer", intent.version, "unauthorized queue"),
            cycle=cycle,
        )
    queued = queue_intent(
        intent.intent_id,
        transition("release-manager", intent.version, "queue production deployment"),
        cycle=cycle,
    )
    assert queued.state == "queued"
    assert queued.promotion_policy.decision == "allow"
    assert queued.promotion_policy.approver == "release-manager"


def test_rollback_rejects_invalid_state_before_running_kubectl(tmp_path: Path, monkeypatch):
    configure_paths(tmp_path, monkeypatch)
    cycle = ready_cycle()
    intent = create_deployment_intent(deployment_request(), cycle=cycle)
    calls = 0

    def runner(command, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    with pytest.raises(
        DeploymentTransitionRejected,
        match="rollback_requires_applied_or_failed_intent",
    ):
        execute_rollback(intent.intent_id, runner=runner, require_enabled=False)

    assert calls == 0


def test_stale_version_corrupt_ledger_and_executor_failure_are_audited(
    tmp_path: Path, monkeypatch
):
    configure_paths(tmp_path, monkeypatch)
    configure_executor_targets(monkeypatch)
    cycle = ready_cycle()
    monkeypatch.setattr("evm.control_panel.deployment_intents.current_cycle", lambda: cycle)
    intent = create_deployment_intent(deployment_request(), cycle=cycle)

    with pytest.raises(DeploymentVersionConflict):
        request_approval(intent.intent_id, transition("ml-platform", 999, "stale client"))

    pending = request_approval(
        intent.intent_id,
        transition("ml-platform", intent.version, "request approval"),
    )
    approved = approve_intent(
        intent.intent_id,
        transition("ai-infra-sre", pending.version, "approve"),
    )
    queued = queue_intent(
        intent.intent_id,
        transition("ai-infra-sre", approved.version, "queue"),
        cycle=cycle,
    )
    failed = execute_apply(queued.intent_id, runner=failed_runner, require_enabled=False)
    assert failed.state == "failed"
    assert failed.execution_result.exit_code == 1
    assert failed.transitions[-1].result == "failed"

    ledger_path = tmp_path / "intents" / "deployment_intents.json"
    ledger_path.write_text("{malformed", encoding="utf-8")
    ledger = read_intents()
    assert ledger.status == "blocked"
    assert ledger.blockers == ["deployment_ledger_malformed"]
    with pytest.raises(DeploymentIntentBlocked, match="deployment_ledger_malformed"):
        create_deployment_intent(deployment_request(), cycle=cycle)
