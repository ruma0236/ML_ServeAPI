from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from evm.control_panel.schemas import ContractModel
from evm.control_panel.scenario_workloads import (
    ScenarioWorkloadError,
    ScenarioWorkloadRequest,
    ScenarioWorkloadRun,
    atomic_write_json,
    create_workload_run,
    file_sha256,
    get_workload_run,
    list_workload_runs,
    payload_sha256,
    workload_artifact_path,
)
from evm.core.config import map_runtime_data_path


class ScenarioWorkloadPreset(ContractModel):
    preset_id: str
    label: str
    model_family: Literal["vlm", "llm"]
    scenario_id: str
    model_repository: str
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    model_dir: str
    data_view_uri: str
    quality_disposition_uri: str | None = None
    adaptation_method: Literal["lora", "qlora"]
    quantization_requested: Literal["none", "int8", "int4_nf4"]
    max_steps: int = Field(ge=2, le=256)
    staging_port: int = Field(ge=1024, le=65535)
    production_port: int = Field(ge=1024, le=65535)
    record_counts: dict[str, int]
    quality_metrics: list[str]
    claim_boundary: str


class ScenarioWorkloadPresetCatalog(ContractModel):
    schema_version: Literal["evm.scenario_workload_preset_catalog.v1"] = (
        "evm.scenario_workload_preset_catalog.v1"
    )
    presets: list[ScenarioWorkloadPreset]


class ScenarioWorkloadLaunchRequest(ContractModel):
    preset_id: str
    actor: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=12, max_length=500)


class ScenarioWorkloadApprovalRequest(ContractModel):
    actor: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=12, max_length=500)


class ScenarioWorkloadGpuHandoffRequest(ContractModel):
    actor: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=12, max_length=500)


class ScenarioWorkloadExecutionRequest(ContractModel):
    schema_version: Literal["evm.scenario_workload_execution_request.v1"] = (
        "evm.scenario_workload_execution_request.v1"
    )
    run_id: str
    preset: ScenarioWorkloadPreset
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_branch: str
    requested_by: str
    reason: str
    requested_at: str
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


EXECUTION_REQUEST_SIGNED_FIELDS = (
    "run_id",
    "preset",
    "source_commit",
    "source_branch",
    "requested_by",
    "reason",
    "requested_at",
)


def execution_request_digest_material(
    request: ScenarioWorkloadExecutionRequest | dict[str, object],
) -> dict[str, object]:
    payload = request.model_dump(mode="json") if isinstance(request, ContractModel) else request
    return {field: payload[field] for field in EXECUTION_REQUEST_SIGNED_FIELDS}


class ScenarioWorkloadWorkerHealth(ContractModel):
    schema_version: Literal["evm.scenario_workload_worker.v1"] = (
        "evm.scenario_workload_worker.v1"
    )
    status: Literal["online", "busy", "stale", "offline"]
    worker_id: str | None = None
    pid: int | None = None
    source_commit: str | None = None
    source_branch: str | None = None
    started_at: str | None = None
    last_seen_at: str | None = None
    heartbeat_age_seconds: float | None = None
    current_run_id: str | None = None
    current_intent_id: str | None = None
    message: str | None = None


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def preset_catalog_path() -> Path:
    configured = os.getenv("EVM_SCENARIO_WORKLOAD_PRESETS", "").strip()
    if configured:
        candidate = Path(configured)
    else:
        candidate = Path(__file__).resolve().parents[3] / "configs" / "scenario_workloads" / "live-presets.json"
    return map_runtime_data_path(candidate)


def load_preset_catalog() -> ScenarioWorkloadPresetCatalog:
    path = preset_catalog_path()
    try:
        return ScenarioWorkloadPresetCatalog.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ScenarioWorkloadError("workload_preset_catalog_invalid", str(exc), status_code=503) from exc


def get_preset(preset_id: str) -> ScenarioWorkloadPreset:
    preset = next(
        (item for item in load_preset_catalog().presets if item.preset_id == preset_id),
        None,
    )
    if preset is None:
        raise ScenarioWorkloadError("workload_preset_not_found", preset_id, status_code=404)
    return preset


def launch_workload(
    request: ScenarioWorkloadLaunchRequest,
    *,
    source_commit: str,
    source_branch: str,
) -> ScenarioWorkloadRun:
    if len(source_commit) != 40:
        raise ScenarioWorkloadError(
            "workload_source_revision_missing",
            "An exact 40-character control-plane revision is required.",
            status_code=503,
        )
    active = [
        run.run_id
        for run in list_workload_runs(limit=500).runs
        if run.state in {"queued", "running", "waiting_approval"}
    ]
    if active:
        raise ScenarioWorkloadError(
            "workload_execution_already_active",
            ",".join(active),
            status_code=409,
        )
    from evm.control_panel.scenario_workload_production import list_production_intents

    production = next(
        (
            item
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
        ),
        None,
    )
    if production is not None:
        raise ScenarioWorkloadError(
            "workload_gpu_reserved_by_local_production",
            f"intent={production.intent_id};run={production.run_id}",
            status_code=409,
        )
    preset = get_preset(request.preset_id)
    run = create_workload_run(
        ScenarioWorkloadRequest(
            scenario_id=preset.scenario_id,
            model_family=preset.model_family,
            model_repository=preset.model_repository,
            model_revision=preset.model_revision,
            adaptation_method=preset.adaptation_method,
            quantization_requested=preset.quantization_requested,
            actor=request.actor,
            reason=request.reason,
            dry_run=False,
            source_commit=source_commit,
            source_branch=source_branch,
            dirty_worktree=False,
            quality_disposition_uri=preset.quality_disposition_uri,
            data_view_uri=preset.data_view_uri,
        )
    )
    material = {
        "run_id": run.run_id,
        "preset": preset.model_dump(mode="json"),
        "source_commit": source_commit,
        "source_branch": source_branch,
        "requested_by": request.actor,
        "reason": request.reason,
        "requested_at": utc_now(),
    }
    execution = ScenarioWorkloadExecutionRequest(
        **material,
        request_sha256=payload_sha256(execution_request_digest_material(material)),
    )
    atomic_write_json(
        workload_artifact_path(run.artifact_root) / "execution-request.json",
        execution.model_dump(mode="json"),
    )
    return run


def execution_request_path(run: ScenarioWorkloadRun) -> Path:
    return workload_artifact_path(run.artifact_root) / "execution-request.json"


def load_execution_request(run_id: str) -> ScenarioWorkloadExecutionRequest:
    run = get_workload_run(run_id)
    path = execution_request_path(run)
    try:
        request = ScenarioWorkloadExecutionRequest.model_validate_json(
            path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError) as exc:
        raise ScenarioWorkloadError("workload_execution_request_invalid", str(exc)) from exc
    material = execution_request_digest_material(request)
    if payload_sha256(material) != request.request_sha256:
        raise ScenarioWorkloadError("workload_execution_request_digest_mismatch", run_id)
    if request.run_id != run.run_id or request.source_commit != run.identity.source_commit:
        raise ScenarioWorkloadError("workload_execution_request_identity_mismatch", run_id)
    if request.preset.model_family != run.identity.model_family:
        raise ScenarioWorkloadError("workload_execution_request_family_mismatch", run_id)
    return request


def approval_path(run: ScenarioWorkloadRun) -> Path:
    return workload_artifact_path(run.artifact_root) / "staging-approval.json"


def gpu_handoff_request_path(run: ScenarioWorkloadRun) -> Path:
    return workload_artifact_path(run.artifact_root) / "gpu-handoff-request.json"


def issue_gpu_handoff_request(
    run_id: str,
    request: ScenarioWorkloadGpuHandoffRequest,
) -> dict[str, object]:
    run = get_workload_run(run_id)
    if run.state not in {"queued", "running"}:
        raise ScenarioWorkloadError(
            "workload_gpu_handoff_not_requestable", f"state={run.state}"
        )
    if request.actor.strip() == run.actor.strip():
        raise ScenarioWorkloadError(
            "workload_gpu_handoff_approver_requester_conflict",
            "Requester and maintenance approver must be different identities.",
            status_code=422,
        )
    material = {
        "run_id": run.run_id,
        "identity_sha256": run.identity.identity_sha256,
        "source_commit": run.identity.source_commit,
        "target": {
            "kind": "Deployment",
            "namespace": "evm-production",
            "name": "evm-b0-production",
        },
        "action": "release_exact_single_gpu_holder_for_scenario_workload",
    }
    payload: dict[str, object] = {
        "schema_version": "evm.scenario_workload_gpu_handoff_request.v1",
        **material,
        "request_digest": payload_sha256(material),
        "approver": request.actor,
        "reason": request.reason,
        "requested_at": utc_now(),
        "single_use": True,
        "state": "approved",
    }
    path = gpu_handoff_request_path(run)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
        stable_keys = (
            "run_id",
            "identity_sha256",
            "source_commit",
            "target",
            "action",
            "request_digest",
            "approver",
        )
        if any(existing.get(key) != payload.get(key) for key in stable_keys):
            raise ScenarioWorkloadError("workload_gpu_handoff_request_conflict", run_id)
        return existing
    atomic_write_json(path, payload)
    return payload


def issue_staging_approval(
    run_id: str,
    request: ScenarioWorkloadApprovalRequest,
) -> dict[str, object]:
    run = get_workload_run(run_id)
    if run.state != "waiting_approval" or run.current_stage != "approval":
        raise ScenarioWorkloadError(
            "workload_not_waiting_for_approval",
            f"state={run.state};stage={run.current_stage or 'none'}",
        )
    if request.actor.strip() == run.actor.strip():
        raise ScenarioWorkloadError(
            "workload_approver_requester_conflict",
            "Requester and approver must be different identities.",
            status_code=422,
        )
    artifact_uri = str(run.model_artifact_uri or "")
    artifact_sha256 = str(run.model_artifact_sha256 or "")
    if not artifact_uri or not artifact_sha256:
        raise ScenarioWorkloadError("workload_approval_artifact_missing", run_id)
    if file_sha256(map_runtime_data_path(artifact_uri)) != artifact_sha256:
        raise ScenarioWorkloadError("workload_approval_artifact_digest_mismatch", run_id)
    material = {
        "run_id": run.run_id,
        "identity_sha256": run.identity.identity_sha256,
        "artifact_sha256": artifact_sha256,
        "source_commit": run.identity.source_commit,
        "target_environment": "local-staging",
        "action": "start_bounded_validation_server",
    }
    payload: dict[str, object] = {
        "schema_version": "evm.scenario_staging_approval.v1",
        "decision": "approved",
        **material,
        "action_digest": payload_sha256(material),
        "approver": request.actor,
        "reason": request.reason,
        "production_promotion": False,
        "approved_at": utc_now(),
        "claim_boundary": "Independent approval for controlled local staging only.",
    }
    path = approval_path(run)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
        if existing != payload:
            raise ScenarioWorkloadError("workload_approval_already_recorded", run_id)
        return existing
    atomic_write_json(path, payload)
    return payload


def read_staging_approval(run_id: str) -> dict[str, object] | None:
    run = get_workload_run(run_id)
    path = approval_path(run)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ScenarioWorkloadError("workload_approval_invalid", str(exc)) from exc
    if not isinstance(payload, dict):
        raise ScenarioWorkloadError("workload_approval_invalid", run_id)
    material = {
        "run_id": run.run_id,
        "identity_sha256": run.identity.identity_sha256,
        "artifact_sha256": run.model_artifact_sha256,
        "source_commit": run.identity.source_commit,
        "target_environment": "local-staging",
        "action": "start_bounded_validation_server",
    }
    expected = {
        **material,
        "action_digest": payload_sha256(material),
        "decision": "approved",
        "production_promotion": False,
    }
    mismatches = [key for key, value in expected.items() if payload.get(key) != value]
    if mismatches:
        raise ScenarioWorkloadError(
            "workload_approval_identity_mismatch", ",".join(sorted(mismatches))
        )
    if str(payload.get("approver") or "").strip() == run.actor.strip():
        raise ScenarioWorkloadError("workload_approver_requester_conflict", run_id)
    return payload


def worker_state_path() -> Path:
    return Path(
        os.getenv(
            "EVM_SCENARIO_WORKLOAD_WORKER_PATH",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scenario_workloads/_worker.json",
        )
    )


def read_worker_health(*, stale_after_seconds: float = 15.0) -> ScenarioWorkloadWorkerHealth:
    path = map_runtime_data_path(worker_state_path())
    if not path.is_file():
        return ScenarioWorkloadWorkerHealth(status="offline", message="worker heartbeat missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        observed = datetime.fromisoformat(str(payload["last_seen_at"]).replace("Z", "+00:00"))
        age = max(0.0, (datetime.now(UTC) - observed.astimezone(UTC)).total_seconds())
        status = str(payload.get("status") or "offline")
        if age > stale_after_seconds:
            status = "stale"
        return ScenarioWorkloadWorkerHealth.model_validate(
            {
                **payload,
                "status": status,
                "heartbeat_age_seconds": age,
            }
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return ScenarioWorkloadWorkerHealth(status="offline", message=f"invalid heartbeat: {exc}")
