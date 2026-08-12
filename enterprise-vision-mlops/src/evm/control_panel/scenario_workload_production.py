from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from evm.control_panel.schemas import ContractModel
from evm.control_panel.scenario_workload_control import get_preset, load_preset_catalog
from evm.control_panel.scenario_workloads import (
    ScenarioWorkloadError,
    atomic_write_json,
    canonical_workload_root,
    file_lock,
    file_sha256,
    get_workload_run,
    payload_sha256,
    utc_now,
    workload_artifact_path,
    workload_root,
)


ProductionIntentState = Literal[
    "pending_approval",
    "queued",
    "applying",
    "applied",
    "rollback_requested",
    "rolling_back",
    "rolled_back",
    "failed",
]


class ScenarioProductionRequest(ContractModel):
    actor: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=12, max_length=500)


class ScenarioProductionApprovalRequest(ContractModel):
    actor: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=12, max_length=500)


class ScenarioProductionRollbackRequest(ContractModel):
    actor: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=12, max_length=500)


class ScenarioProductionTarget(ContractModel):
    environment: Literal["local-production"] = "local-production"
    runtime: Literal["windows-host-cuda"] = "windows-host-cuda"
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(ge=1024, le=65535)
    endpoint: str
    metrics_endpoint: str
    gpu_holder_namespace: Literal["evm-production"] = "evm-production"
    gpu_holder_name: Literal["evm-b0-production"] = "evm-b0-production"


class ScenarioProductionIntent(ContractModel):
    schema_version: Literal["evm.scenario_production_intent.v1"] = (
        "evm.scenario_production_intent.v1"
    )
    intent_id: str
    version: int = Field(ge=1)
    state: ProductionIntentState
    run_id: str
    preset_id: str
    requested_by: str
    request_reason: str
    approved_by: str | None = None
    approval_reason: str | None = None
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_branch: str
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_family: Literal["vlm", "llm"]
    model_repository: str
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    model_artifact_uri: str
    model_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluation_uri: str
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_index_uri: str
    evidence_index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ci_evidence_uri: str
    ci_evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: ScenarioProductionTarget
    created_at: str
    updated_at: str
    approved_at: str | None = None
    applied_at: str | None = None
    rolled_back_at: str | None = None
    service_pid: int | None = Field(default=None, ge=1)
    service_process_started_at: str | None = None
    gpu_holder_uid: str | None = None
    evidence_uri: str | None = None
    blockers: list[str] = Field(default_factory=list)
    audit: list[dict[str, Any]] = Field(default_factory=list)


class ScenarioProductionIntentList(ContractModel):
    intents: list[ScenarioProductionIntent] = Field(default_factory=list)
    total: int = 0


def production_root() -> Path:
    return workload_root() / "_production"


def canonical_production_root() -> Path:
    return canonical_workload_root() / "_production"


def intent_path(intent_id: str) -> Path:
    return production_root() / "intents" / f"{intent_id}.json"


def canonical_intent_evidence_path(intent_id: str) -> Path:
    return canonical_production_root() / "intents" / intent_id / "deployment-evidence.json"


def production_state_path() -> Path:
    return production_root() / "current.json"


def local_ci_evidence_path() -> Path:
    configured = os.getenv("EVM_SCENARIO_WORKLOAD_CI_EVIDENCE_PATH", "").strip()
    if configured:
        return workload_artifact_path(configured)
    return production_root() / "local-ci-evidence.json"


def list_production_intents(limit: int = 100) -> ScenarioProductionIntentList:
    intents: list[ScenarioProductionIntent] = []
    for path in (production_root() / "intents").glob("*.json"):
        try:
            intents.append(
                ScenarioProductionIntent.model_validate_json(path.read_text(encoding="utf-8-sig"))
            )
        except (OSError, ValueError):
            continue
    intents.sort(key=lambda item: item.created_at, reverse=True)
    return ScenarioProductionIntentList(
        intents=intents[: max(1, min(limit, 500))],
        total=len(intents),
    )


def get_production_intent(intent_id: str) -> ScenarioProductionIntent:
    path = intent_path(intent_id)
    if not path.is_file():
        raise ScenarioWorkloadError("scenario_production_intent_not_found", intent_id, status_code=404)
    try:
        return ScenarioProductionIntent.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ScenarioWorkloadError(
            "scenario_production_intent_invalid", intent_id, status_code=500
        ) from exc


def current_production_intent() -> ScenarioProductionIntent | None:
    path = production_state_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return get_production_intent(str(payload["intent_id"]))
    except (OSError, ValueError, KeyError, ScenarioWorkloadError):
        return None


def create_production_intent(
    run_id: str,
    request: ScenarioProductionRequest,
) -> ScenarioProductionIntent:
    with file_lock(production_root() / ".intent.lock", "scenario_production_intent"):
        return _create_production_intent(run_id, request)


def _create_production_intent(
    run_id: str,
    request: ScenarioProductionRequest,
) -> ScenarioProductionIntent:
    run = get_workload_run(run_id)
    if run.state != "completed" or run.blockers:
        raise ScenarioWorkloadError(
            "scenario_production_run_not_releasable",
            f"state={run.state};blockers={','.join(run.blockers) or 'none'}",
        )
    if not run.identity.source_commit or len(run.identity.source_commit) != 40:
        raise ScenarioWorkloadError("scenario_production_source_revision_missing", run_id)
    active = [
        item.intent_id
        for item in list_production_intents(limit=500).intents
        if item.state
        in {
            "pending_approval",
            "queued",
            "applying",
            "applied",
            "rollback_requested",
            "rolling_back",
        }
    ]
    if active:
        raise ScenarioWorkloadError(
            "scenario_production_intent_already_active", ",".join(active)
        )
    preset = _preset_for_run(run)
    verified = validate_release_identity(run_id)
    target = ScenarioProductionTarget(
        port=preset.production_port,
        endpoint=f"http://127.0.0.1:{preset.production_port}",
        metrics_endpoint=f"http://127.0.0.1:{preset.production_port}/metrics",
    )
    created_at = utc_now()
    intent_id = f"scenario-deploy-{uuid4().hex[:16]}"
    material = {
        "intent_id": intent_id,
        "run_id": run.run_id,
        "source_commit": run.identity.source_commit,
        "identity_sha256": run.identity.identity_sha256,
        "model_artifact_sha256": run.model_artifact_sha256,
        "evidence_index_sha256": run.evidence_index_sha256,
        "ci_evidence_sha256": verified["ci_evidence_sha256"],
        "target": target.model_dump(mode="json"),
        "action": "deploy_exact_transformer_adapter_to_local_production",
    }
    intent = ScenarioProductionIntent(
        intent_id=intent_id,
        version=1,
        state="pending_approval",
        run_id=run.run_id,
        preset_id=preset.preset_id,
        requested_by=request.actor,
        request_reason=request.reason,
        source_commit=run.identity.source_commit,
        source_branch=str(run.identity.source_branch or "detached"),
        identity_sha256=run.identity.identity_sha256,
        model_family=run.identity.model_family,
        model_repository=run.identity.model_repository,
        model_revision=run.identity.model_revision,
        model_artifact_uri=str(run.model_artifact_uri),
        model_artifact_sha256=str(run.model_artifact_sha256),
        evaluation_uri=str(run.evaluation_uri),
        evaluation_sha256=verified["evaluation_sha256"],
        evidence_index_uri=str(run.evidence_index_uri),
        evidence_index_sha256=str(run.evidence_index_sha256),
        ci_evidence_uri=str(canonical_production_root() / "local-ci-evidence.json"),
        ci_evidence_sha256=verified["ci_evidence_sha256"],
        action_digest=payload_sha256(material),
        target=target,
        created_at=created_at,
        updated_at=created_at,
        audit=[
            _audit(
                request.actor,
                "scenario_production_intent_created",
                state="pending_approval",
                action_digest=payload_sha256(material),
            )
        ],
    )
    _write_intent(intent)
    return intent


def approve_production_intent(
    intent_id: str,
    request: ScenarioProductionApprovalRequest,
) -> ScenarioProductionIntent:
    with file_lock(production_root() / ".intent.lock", "scenario_production_intent"):
        intent = get_production_intent(intent_id)
        if intent.state != "pending_approval":
            raise ScenarioWorkloadError(
                "scenario_production_intent_not_approvable", f"state={intent.state}"
            )
        if request.actor.strip() == intent.requested_by.strip():
            raise ScenarioWorkloadError(
                "scenario_production_approver_requester_conflict",
                "Requester and production approver must be different identities.",
                status_code=422,
            )
        validate_intent_identity(intent)
        now = utc_now()
        intent.state = "queued"
        intent.approved_by = request.actor
        intent.approval_reason = request.reason
        intent.approved_at = now
        intent.updated_at = now
        intent.version += 1
        intent.audit.append(
            _audit(
                request.actor,
                "scenario_production_intent_approved",
                action_digest=intent.action_digest,
                single_use=True,
            )
        )
        _write_intent(intent)
        return intent


def request_production_rollback(
    intent_id: str,
    request: ScenarioProductionRollbackRequest,
) -> ScenarioProductionIntent:
    intent = get_production_intent(intent_id)
    if intent.state != "applied":
        raise ScenarioWorkloadError(
            "scenario_production_intent_not_rollbackable", f"state={intent.state}"
        )
    return transition_production_intent(
        intent_id,
        expected_state="applied",
        state="rollback_requested",
        actor=request.actor,
        event="scenario_production_rollback_requested",
        updates={"blockers": []},
    )


def transition_production_intent(
    intent_id: str,
    *,
    expected_state: ProductionIntentState,
    state: ProductionIntentState,
    actor: str,
    updates: dict[str, Any] | None = None,
    event: str,
) -> ScenarioProductionIntent:
    with file_lock(production_root() / ".intent.lock", "scenario_production_intent"):
        intent = get_production_intent(intent_id)
        if intent.state != expected_state:
            raise ScenarioWorkloadError(
                "scenario_production_intent_state_conflict",
                f"expected={expected_state};actual={intent.state}",
            )
        allowed = {
            "queued": {"applying", "failed"},
            "applying": {"applied", "failed"},
            "applied": {"rollback_requested"},
            "rollback_requested": {"rolling_back", "failed"},
            "rolling_back": {"rolled_back", "failed"},
        }
        if state == "failed" and expected_state in {"applying", "rolling_back"}:
            allowed.setdefault(expected_state, set()).add("failed")
        if state not in allowed.get(expected_state, set()):
            raise ScenarioWorkloadError(
                "scenario_production_transition_invalid", f"{expected_state}->{state}"
            )
        for key, value in (updates or {}).items():
            if key not in {
                "applied_at",
                "rolled_back_at",
                "service_pid",
                "service_process_started_at",
                "gpu_holder_uid",
                "evidence_uri",
                "blockers",
            }:
                raise ScenarioWorkloadError("scenario_production_update_forbidden", key)
            setattr(intent, key, value)
        intent.state = state
        intent.updated_at = utc_now()
        intent.version += 1
        intent.audit.append(_audit(actor, event, state=state))
        _write_intent(intent)
        if state == "applied":
            atomic_write_json(
                production_state_path(),
                {
                    "schema_version": "evm.scenario_production_current.v1",
                    "intent_id": intent.intent_id,
                    "run_id": intent.run_id,
                    "source_commit": intent.source_commit,
                    "model_artifact_sha256": intent.model_artifact_sha256,
                    "endpoint": intent.target.endpoint,
                    "state": "applied",
                    "updated_at": intent.updated_at,
                },
            )
        elif state in {"rolled_back", "failed"}:
            current = current_production_intent()
            if current is not None and current.intent_id == intent.intent_id:
                production_state_path().unlink(missing_ok=True)
        return intent


def validate_release_identity(run_id: str) -> dict[str, str]:
    run = get_workload_run(run_id)
    required = {
        "model_artifact_uri": run.model_artifact_uri,
        "model_artifact_sha256": run.model_artifact_sha256,
        "evaluation_uri": run.evaluation_uri,
        "evidence_index_uri": run.evidence_index_uri,
        "evidence_index_sha256": run.evidence_index_sha256,
    }
    missing = sorted(key for key, value in required.items() if not value)
    if missing:
        raise ScenarioWorkloadError(
            "scenario_production_release_identity_incomplete", ",".join(missing)
        )
    artifact = workload_artifact_path(str(run.model_artifact_uri))
    evaluation = workload_artifact_path(str(run.evaluation_uri))
    evidence_index = workload_artifact_path(str(run.evidence_index_uri))
    if file_sha256(artifact) != run.model_artifact_sha256:
        raise ScenarioWorkloadError("scenario_production_artifact_digest_mismatch", run_id)
    if file_sha256(evidence_index) != run.evidence_index_sha256:
        raise ScenarioWorkloadError("scenario_production_evidence_digest_mismatch", run_id)
    try:
        training = json.loads(
            (workload_artifact_path(run.artifact_root) / "model" / "training-result.json").read_text(
                encoding="utf-8-sig"
            )
        )
        evaluation_payload = json.loads(evaluation.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ScenarioWorkloadError("scenario_production_evaluation_invalid", run_id) from exc
    if training.get("status") != "pass" or training.get("promotion_blockers"):
        raise ScenarioWorkloadError("scenario_production_quality_gate_blocked", run_id)
    if not isinstance(evaluation_payload.get("metrics"), dict):
        raise ScenarioWorkloadError("scenario_production_metric_contract_missing", run_id)
    ci_path = local_ci_evidence_path()
    try:
        ci = json.loads(ci_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ScenarioWorkloadError(
            "scenario_production_ci_evidence_unavailable", str(ci_path), status_code=503
        ) from exc
    commands = ci.get("commands")
    if (
        ci.get("schema_version") != "evm.scenario_local_ci_evidence.v1"
        or ci.get("status") != "pass"
        or ci.get("source_commit") != run.identity.source_commit
        or not isinstance(commands, list)
        or len(commands) < 5
        or any(item.get("status") != "pass" or item.get("exit_code") != 0 for item in commands)
    ):
        raise ScenarioWorkloadError("scenario_production_ci_admission_blocked", run_id)
    return {
        "evaluation_sha256": file_sha256(evaluation),
        "ci_evidence_sha256": file_sha256(ci_path),
    }


def validate_intent_identity(intent: ScenarioProductionIntent) -> None:
    run = get_workload_run(intent.run_id)
    verified = validate_release_identity(run.run_id)
    expected = {
        "source_commit": run.identity.source_commit,
        "identity_sha256": run.identity.identity_sha256,
        "model_artifact_sha256": run.model_artifact_sha256,
        "evaluation_sha256": verified["evaluation_sha256"],
        "evidence_index_sha256": run.evidence_index_sha256,
        "ci_evidence_sha256": verified["ci_evidence_sha256"],
    }
    mismatches = [key for key, value in expected.items() if getattr(intent, key) != value]
    if mismatches:
        raise ScenarioWorkloadError(
            "scenario_production_intent_identity_mismatch", ",".join(mismatches)
        )


def _preset_for_run(run: Any):
    for preset in load_preset_catalog().presets:
        if (
            preset.scenario_id == run.identity.scenario_id
            and preset.model_family == run.identity.model_family
            and preset.model_repository == run.identity.model_repository
            and preset.model_revision == run.identity.model_revision
        ):
            return get_preset(preset.preset_id)
    raise ScenarioWorkloadError("scenario_production_preset_not_found", run.run_id)


def _write_intent(intent: ScenarioProductionIntent) -> None:
    atomic_write_json(intent_path(intent.intent_id), intent.model_dump(mode="json"))


def _audit(actor: str, event: str, **details: Any) -> dict[str, Any]:
    return {"timestamp": utc_now(), "actor": actor, "event": event, "details": details}
