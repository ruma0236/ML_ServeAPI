from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from opentelemetry import trace as otel_trace
from pydantic import BaseModel, Field, model_validator

from evm.control_panel.scenario_workloads import (
    CapacityProbeCatalog,
    CapacityProbeRequest,
    CapacityProbeResponse,
    GpuBatchProbeDescriptor,
    GpuBatchProbeRequest,
    GpuBatchProbeResponse,
    GpuLease,
    ScenarioWorkloadError,
    ScenarioWorkloadRun,
    ScenarioWorkloadRunList,
    get_workload_run,
    list_workload_runs,
    read_active_gpu_lease,
)
from evm.control_panel.transactional_store import (
    StoreConfiguration,
    TransactionalControlPlaneStore,
    canonical_digest,
)
from evm.model_runtime.capacity_probe import (
    CapacityProbeError,
    load_capacity_probe_catalog,
)
from evm.model_runtime.capacity_executor import execute_capacity_probe_async
from evm.model_runtime.gpu_batch_probe import (
    GpuBatchProbeError,
    execute_gpu_batch_probe,
    load_gpu_batch_probe_descriptor,
)
from evm.model_runtime.triton_blue_green import (
    TritonBlueGreenCausalIdentity,
    TritonBlueGreenControlRequest,
    TritonBlueGreenError,
    TritonBlueGreenInitializeRequest,
    TritonBlueGreenPredictRequest,
    TritonBlueGreenPredictResponse,
    TritonBlueGreenResetRequest,
    TritonBlueGreenStateResponse,
    causal_start_observation,
    expected_causal_identity_for_request,
    manager as triton_blue_green_manager,
)
from evm.control_panel.scenario_workload_control import (
    ScenarioWorkloadApprovalRequest,
    ScenarioWorkloadLaunchRequest,
    ScenarioWorkloadGpuHandoffRequest,
    ScenarioWorkloadPresetCatalog,
    ScenarioWorkloadWorkerHealth,
    issue_staging_approval,
    issue_gpu_handoff_request,
    launch_workload,
    load_preset_catalog,
    read_worker_health,
)
from evm.control_panel.scenario_workload_production import (
    ScenarioProductionApprovalRequest,
    ScenarioProductionIntent,
    ScenarioProductionIntentList,
    ScenarioProductionRequest,
    ScenarioProductionRollbackRequest,
    approve_production_intent,
    create_production_intent,
    current_production_intent,
    get_production_intent,
    list_production_intents,
    request_production_rollback,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-workloads"])


_S6BM_TERMINAL_STORE_INIT_LOCK = threading.Lock()
_S6BM_TERMINAL_STORE: TransactionalControlPlaneStore | None = None


def initialize_s6bm_terminal_store() -> TransactionalControlPlaneStore:
    """Create the S6B-M store once before concurrent request handling begins."""

    global _S6BM_TERMINAL_STORE
    observed = _S6BM_TERMINAL_STORE
    if observed is not None:
        return observed
    with _S6BM_TERMINAL_STORE_INIT_LOCK:
        observed = _S6BM_TERMINAL_STORE
        if observed is not None:
            return observed
        observed = _build_s6bm_terminal_store()
        _S6BM_TERMINAL_STORE = observed
        return observed


def _build_s6bm_terminal_store() -> TransactionalControlPlaneStore:
    dsn = os.getenv("EVM_S6BM_DATABASE_URL", "").strip()
    schema = os.getenv("EVM_S6BM_DATABASE_SCHEMA", "").strip()
    if not dsn or not schema:
        raise RuntimeError("S6B-M durable terminal-effect store is not configured")
    store = TransactionalControlPlaneStore(
        StoreConfiguration(
            mode="postgres",
            dsn=dsn,
            schema=schema,
            pool_min_size=int(os.getenv("EVM_S6BM_DATABASE_POOL_MIN_SIZE", "1")),
            pool_max_size=int(os.getenv("EVM_S6BM_DATABASE_POOL_MAX_SIZE", "8")),
            acquire_timeout_seconds=float(
                os.getenv("EVM_S6BM_DATABASE_ACQUIRE_TIMEOUT_SECONDS", "2")
            ),
            lock_timeout_seconds=float(os.getenv("EVM_S6BM_DATABASE_LOCK_TIMEOUT_SECONDS", "2")),
            statement_timeout_seconds=float(
                os.getenv("EVM_S6BM_DATABASE_STATEMENT_TIMEOUT_SECONDS", "10")
            ),
            commit_timestamp_readback_max_concurrency=int(
                os.getenv("EVM_S6BM_COMMIT_READBACK_MAX_CONCURRENCY", "2")
            ),
            commit_timestamp_readback_acquire_timeout_seconds=float(
                os.getenv("EVM_S6BM_COMMIT_READBACK_ACQUIRE_TIMEOUT_SECONDS", "2")
            ),
        )
    )
    return store


def shutdown_s6bm_terminal_store() -> None:
    global _S6BM_TERMINAL_STORE
    with _S6BM_TERMINAL_STORE_INIT_LOCK:
        store = _S6BM_TERMINAL_STORE
        _S6BM_TERMINAL_STORE = None
    if store is not None:
        store.close()


def _s6bm_terminal_store() -> TransactionalControlPlaneStore:
    return initialize_s6bm_terminal_store()


atexit.register(shutdown_s6bm_terminal_store)


def _commit_s6bm_terminal_effect_sync(
    request: TritonBlueGreenPredictRequest,
    response: TritonBlueGreenPredictResponse,
) -> dict[str, Any]:
    request_payload = request.model_dump(mode="json")
    response_core = response.model_dump(mode="json", exclude={"durable_effect"})
    effect_payload = {
        "schema_version": "evm.s8_v4.s6bm_terminal_effect.v1",
        "run_id": response.run_id,
        "attempt_id": response.attempt_id,
        "request_id": response.request_id,
        "trace_id": response.trace_id,
        "effect_id": response.effect_id,
        "offered_identity": {
            "model_role": request.expected_model_role,
            "model_name": request.expected_model_name,
            "model_version": request.expected_model_version,
            "artifact_sha256": request.expected_artifact_sha256,
        },
        "served_identity": {
            "model_role": response.model_role,
            "model_name": response.model_name,
            "model_version": response.model_version,
            "artifact_sha256": response.artifact_sha256,
        },
        "route_generation": response.route_generation,
        "route_phase": response.route_phase,
        "result_sha256": response.result_sha256,
        "result_payload_sha256": canonical_digest(response_core),
        "terminal_outcome": "completed",
    }
    causal_payload = {
        "schema_version": "evm.s8_v4.s6bm_terminal_causal_event.v1",
        "attempt_id": response.attempt_id,
        "run_id": response.run_id,
        "request_id": response.request_id,
        "request_nonce": request.request_nonce,
        "trace_id": response.trace_id,
        "effect_id": response.effect_id,
        "model_role": response.model_role,
        "model_name": response.model_name,
        "model_version": response.model_version,
        "artifact_sha256": response.artifact_sha256,
        "route_generation": response.route_generation,
        "route_phase": response.route_phase,
        "result_sha256": response.result_sha256,
        "requires_switch_before_effect": request.causal_crossover,
    }
    stored, replayed, receipt = (
        _s6bm_terminal_store().commit_idempotent_terminal_entity_with_receipt(
            scope=f"s6bm.terminal-effect.{request.attempt_id}",
            idempotency_key=request.request_id,
            request_payload=request_payload,
            entity_kind="s6bm_terminal_effect",
            entity_id=response.effect_id,
            response_payload=effect_payload,
            state="completed",
            causal_payload=causal_payload,
        )
    )
    expected = {key: effect_payload[key] for key in effect_payload if key != "durable_commit"}
    if any(stored.get(key) != value for key, value in expected.items()):
        raise RuntimeError("S6B-M durable terminal-effect payload parity failed")
    if bool(receipt["replayed"]) is not replayed:
        raise RuntimeError("S6B-M durable terminal-effect replay parity failed")
    return receipt


async def _commit_s6bm_terminal_effect(
    request: TritonBlueGreenPredictRequest,
    response: TritonBlueGreenPredictResponse,
) -> dict[str, Any]:
    return await asyncio.to_thread(_commit_s6bm_terminal_effect_sync, request, response)


def _commit_s6bm_start_receipt_sync(
    stage: str,
    request: TritonBlueGreenPredictRequest,
    payload: dict[str, Any],
) -> dict[str, Any]:
    expected = expected_causal_identity_for_request(request).model_dump(mode="json")
    if any(payload.get(key) != value for key, value in expected.items()):
        raise RuntimeError("S6B-M start receipt identity differs from offered identity")
    return _s6bm_terminal_store().commit_s6bm_start_receipt(
        event_type=stage,
        payload=payload,
        actor_identity=str(payload.get("actor_identity") or stage),
    )


async def _commit_s6bm_start_receipt(
    stage: str,
    request: TritonBlueGreenPredictRequest,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _commit_s6bm_start_receipt_sync,
        stage,
        request,
        payload,
    )


def _commit_s6bm_transition_fence(
    request: TritonBlueGreenControlRequest,
    context: dict[str, Any],
) -> dict[str, Any]:
    if request.causal_crossover is None:
        raise RuntimeError("S6B-M causal crossover identity is absent")
    identity = request.causal_crossover.model_dump(mode="json")
    if request.action == "green_switched":
        return _s6bm_terminal_store().commit_s6bm_route_switch_fence(crossover_identity=identity)
    if request.action == "blue_unloaded":
        return _s6bm_terminal_store().commit_s6bm_unload_intent(
            crossover_identity=identity,
            pre_switch_blue_effects=list(context.get("pre_switch_blue_effects", [])),
        )
    raise RuntimeError(f"unsupported S6B-M causal transition: {request.action}")


class WorkloadReleaseGateSummary(BaseModel):
    status: Literal["pass", "blocked", "unavailable"] = "unavailable"
    blockers: list[str] = Field(default_factory=list)
    policy_source: str


class S6BMTritonStartReceiptRequest(BaseModel):
    schema_version: Literal[
        "evm.s8_v4.s6bm_triton_start_receipt.v1",
        "evm.s8_v4.s6bm_triton_start_receipt.v2",
    ]
    causal_identity: TritonBlueGreenCausalIdentity
    trace_event_name: Literal["COMPUTE_START"]
    actor_start_unix_ns: int = Field(gt=0)
    raw_trace_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_trace_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_trace_span_id: str = Field(min_length=1, max_length=64)
    triton_container_id: str = Field(min_length=12, max_length=128)
    triton_image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    gpu_uuid: str = Field(min_length=8, max_length=128)
    collector_observation: dict[str, Any]
    collector_process_id: int | None = Field(default=None, gt=0)
    collector_parent_process_id: int | None = Field(default=None, gt=0)
    collector_nonce: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    collector_source_identity: str | None = Field(default=None, min_length=1, max_length=512)
    collector_spec_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    backend_identity: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_v2_collector_identity(self) -> "S6BMTritonStartReceiptRequest":
        if self.schema_version.endswith(".v2") and any(
            value is None
            for value in (
                self.collector_process_id,
                self.collector_parent_process_id,
                self.collector_nonce,
                self.collector_source_identity,
                self.collector_spec_sha256,
                self.backend_identity,
            )
        ):
            raise ValueError("strict v2 Triton receipt requires collector identity")
        return self


class WorkloadEvaluationSummary(BaseModel):
    schema_version: str
    model_family: Literal["vlm", "llm"]
    quality_metrics: dict[str, float] = Field(default_factory=dict)
    operational_metrics: dict[str, float] = Field(default_factory=dict)
    release_gate: WorkloadReleaseGateSummary
    evaluated_at: str | None = None
    evidence_uri: str
    claim_boundary: str | None = None


class ScenarioWorkloadRunView(ScenarioWorkloadRun):
    evaluation_summary: WorkloadEvaluationSummary | None = None
    training_progress: dict[str, object] | None = None
    control_state: dict[str, object] = Field(default_factory=dict)


class ScenarioWorkloadRunListView(BaseModel):
    runs: list[ScenarioWorkloadRunView] = Field(default_factory=list)
    total: int = 0


@router.get("/scenario-workloads", response_model=ScenarioWorkloadRunListView)
def scenario_workload_runs(limit: int = 100) -> ScenarioWorkloadRunListView:
    listed: ScenarioWorkloadRunList = list_workload_runs(limit=limit)
    return ScenarioWorkloadRunListView(
        runs=[_workload_view(run) for run in listed.runs],
        total=listed.total,
    )


@router.get("/scenario-workloads/presets", response_model=ScenarioWorkloadPresetCatalog)
def scenario_workload_presets() -> ScenarioWorkloadPresetCatalog:
    return workload_operation(load_preset_catalog)


@router.get("/scenario-workloads/worker", response_model=ScenarioWorkloadWorkerHealth)
def scenario_workload_worker() -> ScenarioWorkloadWorkerHealth:
    return read_worker_health()


@router.post("/scenario-workloads", response_model=ScenarioWorkloadRunView, status_code=202)
def launch_scenario_workload(request: ScenarioWorkloadLaunchRequest) -> ScenarioWorkloadRunView:
    source_commit = os.getenv("GIT_COMMIT", "").strip() or os.getenv("EVM_GIT_COMMIT", "").strip()
    source_branch = os.getenv("GIT_BRANCH", "").strip() or os.getenv("EVM_GIT_BRANCH", "").strip()
    return _workload_view(
        workload_operation(
            lambda: launch_workload(
                request,
                source_commit=source_commit,
                source_branch=source_branch,
            )
        )
    )


@router.post(
    "/scenario-workloads/{run_id}/approve-gpu-handoff",
    response_model=ScenarioWorkloadRunView,
    status_code=202,
)
def approve_scenario_workload_gpu_handoff(
    run_id: str,
    request: ScenarioWorkloadGpuHandoffRequest,
) -> ScenarioWorkloadRunView:
    workload_operation(lambda: issue_gpu_handoff_request(run_id, request))
    return _workload_view(get_workload_run(run_id))


@router.post(
    "/scenario-workloads/{run_id}/approve-staging",
    response_model=ScenarioWorkloadRunView,
    status_code=202,
)
def approve_scenario_workload_staging(
    run_id: str,
    request: ScenarioWorkloadApprovalRequest,
) -> ScenarioWorkloadRunView:
    workload_operation(lambda: issue_staging_approval(run_id, request))
    return _workload_view(get_workload_run(run_id))


@router.get(
    "/scenario-workloads/production-intents",
    response_model=ScenarioProductionIntentList,
)
def scenario_production_intents(limit: int = 100) -> ScenarioProductionIntentList:
    return list_production_intents(limit=limit)


@router.get(
    "/scenario-workloads/production-intents/current",
    response_model=ScenarioProductionIntent | None,
)
def scenario_current_production_intent() -> ScenarioProductionIntent | None:
    return current_production_intent()


@router.post(
    "/scenario-workloads/{run_id}/production-intents",
    response_model=ScenarioProductionIntent,
    status_code=202,
)
def create_scenario_production_intent(
    run_id: str,
    request: ScenarioProductionRequest,
) -> ScenarioProductionIntent:
    return workload_operation(lambda: create_production_intent(run_id, request))


@router.post(
    "/scenario-workloads/production-intents/{intent_id}/approve",
    response_model=ScenarioProductionIntent,
    status_code=202,
)
def approve_scenario_production_intent(
    intent_id: str,
    request: ScenarioProductionApprovalRequest,
) -> ScenarioProductionIntent:
    return workload_operation(lambda: approve_production_intent(intent_id, request))


@router.post(
    "/scenario-workloads/production-intents/{intent_id}/rollback",
    response_model=ScenarioProductionIntent,
    status_code=202,
)
def rollback_scenario_production_intent(
    intent_id: str,
    request: ScenarioProductionRollbackRequest,
) -> ScenarioProductionIntent:
    return workload_operation(lambda: request_production_rollback(intent_id, request))


@router.get(
    "/scenario-workloads/production-intents/{intent_id}",
    response_model=ScenarioProductionIntent,
)
def scenario_production_intent(intent_id: str) -> ScenarioProductionIntent:
    return workload_operation(lambda: get_production_intent(intent_id))


@router.get("/scenario-workloads/gpu-lease", response_model=GpuLease | None)
def scenario_gpu_lease() -> GpuLease | None:
    return read_active_gpu_lease()


@router.get(
    "/scenario-workloads/capacity-probes",
    response_model=CapacityProbeCatalog,
)
def scenario_capacity_probe_catalog() -> CapacityProbeCatalog:
    return capacity_probe_operation(load_capacity_probe_catalog)


@router.post(
    "/scenario-workloads/capacity-probes/predict",
    response_model=CapacityProbeResponse,
)
async def predict_scenario_capacity_probe(
    request: CapacityProbeRequest,
) -> CapacityProbeResponse:
    try:
        return await execute_capacity_probe_async(request)
    except CapacityProbeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
            headers=exc.headers,
        ) from exc


@router.get(
    "/scenario-workloads/gpu-batch-probes",
    response_model=GpuBatchProbeDescriptor,
)
def scenario_gpu_batch_probe_descriptor() -> GpuBatchProbeDescriptor:
    try:
        return load_gpu_batch_probe_descriptor()
    except GpuBatchProbeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
            headers=exc.headers,
        ) from exc


@router.post(
    "/scenario-workloads/gpu-batch-probes/predict",
    response_model=GpuBatchProbeResponse,
)
async def predict_scenario_gpu_batch_probe(
    request: GpuBatchProbeRequest,
) -> GpuBatchProbeResponse:
    try:
        return await execute_gpu_batch_probe(request)
    except GpuBatchProbeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
            headers=exc.headers,
        ) from exc


@router.post(
    "/scenario-workloads/triton-blue-green/initialize",
    response_model=TritonBlueGreenStateResponse,
)
def initialize_triton_blue_green(
    request: TritonBlueGreenInitializeRequest,
) -> TritonBlueGreenStateResponse:
    return triton_blue_green_operation(lambda: triton_blue_green_manager.initialize(request))


@router.post(
    "/scenario-workloads/triton-blue-green/control",
    response_model=TritonBlueGreenStateResponse,
)
def control_triton_blue_green(
    request: TritonBlueGreenControlRequest,
) -> TritonBlueGreenStateResponse:
    return triton_blue_green_operation(
        lambda: triton_blue_green_manager.control(
            request,
            transition_fence_committer=_commit_s6bm_transition_fence,
        )
    )


@router.post(
    "/scenario-workloads/triton-blue-green/predict",
    response_model=TritonBlueGreenPredictResponse,
)
async def predict_triton_blue_green(
    request: TritonBlueGreenPredictRequest,
) -> TritonBlueGreenPredictResponse:
    try:
        if request.causal_crossover:
            server_payload = {
                **expected_causal_identity_for_request(request).model_dump(mode="json"),
                **causal_start_observation("fastapi-server-handler"),
                "route_phase": "offered_blue_crossover",
            }
            server_receipt = await _commit_s6bm_start_receipt(
                "api_server_handler_entry",
                request,
                server_payload,
            )
            if server_receipt.get("readback_visible") is not True:
                raise TritonBlueGreenError(
                    "causal_server_receipt_mismatch",
                    request.request_id,
                    status_code=503,
                )
        response = await triton_blue_green_manager.predict(
            request,
            terminal_effect_committer=_commit_s6bm_terminal_effect,
            start_receipt_committer=_commit_s6bm_start_receipt,
        )
        span = otel_trace.get_current_span()
        span.set_attributes(
            {
                "evm.run.id": response.run_id,
                "evm.attempt.id": response.attempt_id,
                "evm.request.id": response.request_id,
                "evm.model.role": response.model_role,
                "evm.model.name": response.model_name,
                "evm.model.version": response.model_version,
                "evm.model.artifact.sha256": response.artifact_sha256,
                "evm.effect.id": response.effect_id,
                "evm.terminal.outcome": "completed",
                "evm.request.replayed": response.replayed,
            }
        )
        return response
    except TritonBlueGreenError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc


@router.post("/scenario-workloads/triton-blue-green/causal-receipts/triton")
def record_triton_blue_green_start_receipt(
    request: S6BMTritonStartReceiptRequest,
) -> dict[str, Any]:
    identity = request.causal_identity.model_dump(mode="json")
    payload = {
        **identity,
        "schema_version": "evm.s8_v4.s6bm_triton_actor_receipt.v1",
        "actor_identity": f"triton:{request.triton_container_id[:12]}",
        "actor_start_unix_ns": request.actor_start_unix_ns,
        "trace_event_name": request.trace_event_name,
        "raw_trace_artifact_sha256": request.raw_trace_artifact_sha256,
        "raw_trace_record_sha256": request.raw_trace_record_sha256,
        "raw_trace_span_id": request.raw_trace_span_id,
        "triton_container_id": request.triton_container_id,
        "triton_image_digest": request.triton_image_digest,
        "gpu_uuid": request.gpu_uuid,
        "collector_observation": request.collector_observation,
        "collector_process_id": request.collector_process_id,
        "collector_parent_process_id": request.collector_parent_process_id,
        "collector_nonce": request.collector_nonce,
        "collector_source_identity": request.collector_source_identity,
        "collector_spec_sha256": request.collector_spec_sha256,
        "backend_identity": request.backend_identity,
    }
    return _s6bm_terminal_store().commit_s6bm_start_receipt(
        event_type="triton_backend_compute_entry",
        payload=payload,
        actor_identity=str(payload["actor_identity"]),
    )


@router.get("/scenario-workloads/triton-blue-green/effects/{attempt_id}")
def triton_blue_green_effects(attempt_id: str) -> dict[str, Any]:
    if not attempt_id or len(attempt_id) > 128:
        raise HTTPException(status_code=422, detail={"error": "attempt_identity_invalid"})
    rows = _s6bm_terminal_store().list_idempotent_terminal_entities(
        entity_kind="s6bm_terminal_effect",
        attempt_id=attempt_id,
    )
    return {
        "schema_version": "evm.s8_v4.s6bm_terminal_effect_export.v1",
        "attempt_id": attempt_id,
        "effect_count": len(rows),
        "identity_sha256": hashlib.sha256(attempt_id.encode("utf-8")).hexdigest(),
        "effects": rows,
    }


@router.get("/scenario-workloads/triton-blue-green/causal-events/{attempt_id}")
def triton_blue_green_causal_events(attempt_id: str) -> dict[str, Any]:
    if not attempt_id or len(attempt_id) > 128:
        raise HTTPException(status_code=422, detail={"error": "attempt_identity_invalid"})
    rows = _s6bm_terminal_store().list_s6bm_causal_events(attempt_id=attempt_id)
    return {
        "schema_version": "evm.s8_v4.s6bm_causal_event_export.v1",
        "attempt_id": attempt_id,
        "event_count": len(rows),
        "events": rows,
    }


@router.get(
    "/scenario-workloads/triton-blue-green/state",
    response_model=TritonBlueGreenStateResponse,
)
def triton_blue_green_state() -> TritonBlueGreenStateResponse:
    return triton_blue_green_manager.snapshot()


@router.post(
    "/scenario-workloads/triton-blue-green/reset",
)
def reset_triton_blue_green(request: TritonBlueGreenResetRequest) -> dict[str, bool]:
    triton_blue_green_operation(
        lambda: triton_blue_green_manager.reset(
            request.run_id, request.lease_id, request.fencing_token
        )
    )
    return {"reset": True}


@router.get("/scenario-workloads/{run_id}", response_model=ScenarioWorkloadRunView)
def scenario_workload_run(run_id: str) -> ScenarioWorkloadRunView:
    try:
        return _workload_view(get_workload_run(run_id))
    except ScenarioWorkloadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc


def workload_operation(operation):
    try:
        return operation()
    except ScenarioWorkloadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc


def capacity_probe_operation(operation):
    try:
        return operation()
    except CapacityProbeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
            headers=exc.headers,
        ) from exc


def triton_blue_green_operation(operation):
    try:
        return operation()
    except TritonBlueGreenError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc


def _workload_view(run: ScenarioWorkloadRun) -> ScenarioWorkloadRunView:
    return ScenarioWorkloadRunView.model_validate(
        {
            **run.model_dump(mode="json"),
            "evaluation_summary": _evaluation_summary(run),
            "training_progress": _training_progress(run),
            "control_state": _control_state(run),
        }
    )


def _control_state(run: ScenarioWorkloadRun) -> dict[str, object]:
    root = _resolve_data_path(run.artifact_root)
    handoff = _read_json(root / "gpu-handoff-request.json")
    staging = _read_json(root / "staging-approval.json")
    return {
        "gpu_handoff_state": _approval_state(handoff, consumed=True),
        "gpu_handoff_approver": handoff.get("approver") if handoff else None,
        "staging_approval_state": _approval_state(staging, consumed=False),
        "staging_approver": staging.get("approver") if staging else None,
    }


def _approval_state(payload: dict[str, object] | None, *, consumed: bool) -> str:
    if payload is None:
        return "missing"
    state = str(payload.get("state") or payload.get("decision") or "")
    if consumed and state == "consumed":
        return "consumed"
    if state in {"approved", "approved_for_staging"} or payload.get("decision") == "approved":
        return "approved"
    return "invalid"


def _training_progress(run: ScenarioWorkloadRun) -> dict[str, object] | None:
    path = _resolve_data_path(str(Path(run.artifact_root) / "model" / "training-progress.json"))
    payload = _read_json(path)
    if payload is None or payload.get("schema_version") != "evm.scenario_training_progress.v1":
        return None
    if payload.get("lifecycle_run_id") != run.run_id:
        return None
    return payload


def _evaluation_summary(run: ScenarioWorkloadRun) -> WorkloadEvaluationSummary | None:
    if not run.evaluation_uri:
        return None
    evaluation_path = _resolve_data_path(run.evaluation_uri)
    evaluation = _read_json(evaluation_path)
    if evaluation is None:
        return None
    training_path = evaluation_path.with_name("training-result.json")
    training = _read_json(training_path) or {}
    evaluation_metrics = evaluation.get("metrics")
    training_metrics = training.get("metrics")
    if not isinstance(evaluation_metrics, dict):
        return None
    if not isinstance(training_metrics, dict):
        training_metrics = {}

    if run.identity.model_family == "vlm":
        quality_metrics = _numeric_metrics(
            evaluation_metrics,
            ("accuracy", "parse_rate"),
        )
        evaluated_records = evaluation_metrics.get("record_count")
    else:
        quality_metrics = _numeric_metrics(
            evaluation_metrics,
            ("validation_loss", "mean_token_f1", "nonempty_rate"),
        )
        evaluated_records = evaluation_metrics.get("generated_record_count")

    operational_metrics = _numeric_metrics(
        evaluation_metrics,
        ("p95_latency_seconds",),
    )
    if isinstance(evaluated_records, int | float) and not isinstance(evaluated_records, bool):
        operational_metrics["evaluated_records"] = float(evaluated_records)
    operational_metrics.update(
        _numeric_metrics(
            training_metrics,
            ("peak_gpu_allocated_mib", "training_seconds"),
        )
    )

    blockers = training.get("promotion_blockers")
    gate_blockers = [str(value) for value in blockers] if isinstance(blockers, list) else []
    training_status = training.get("status")
    gate_status: Literal["pass", "blocked", "unavailable"] = (
        "pass"
        if training_status == "pass" and not gate_blockers
        else "blocked"
        if training_status in {"pass", "blocked", "failed"} or gate_blockers
        else "unavailable"
    )
    return WorkloadEvaluationSummary(
        schema_version=str(evaluation.get("schema_version") or "unknown"),
        model_family=run.identity.model_family,
        quality_metrics=quality_metrics,
        operational_metrics=operational_metrics,
        release_gate=WorkloadReleaseGateSummary(
            status=gate_status,
            blockers=gate_blockers,
            policy_source=str(training_path),
        ),
        evaluated_at=str(evaluation.get("evaluated_at"))
        if evaluation.get("evaluated_at")
        else None,
        evidence_uri=run.evaluation_uri,
        claim_boundary=str(training.get("claim_boundary"))
        if training.get("claim_boundary")
        else None,
    )


def _resolve_data_path(uri: str) -> Path:
    direct = Path(uri)
    if direct.is_file():
        return direct
    normalized = uri.replace("\\", "/")
    host_root = (
        os.getenv(
            "EVM_HOST_DATA_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
        )
        .replace("\\", "/")
        .rstrip("/")
    )
    mount_root = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data").replace("\\", "/").rstrip("/")
    if normalized.lower().startswith(host_root.lower()):
        return Path(f"{mount_root}{normalized[len(host_root) :]}")
    return Path(normalized)


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _numeric_metrics(source: dict[str, object], names: tuple[str, ...]) -> dict[str, float]:
    return {
        name: float(source[name])
        for name in names
        if isinstance(source.get(name), int | float) and not isinstance(source.get(name), bool)
    }
