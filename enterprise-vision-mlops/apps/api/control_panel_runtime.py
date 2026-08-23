from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from evm.control_panel.api_rollout import API_DRAIN_CONTROLLER
from evm.control_panel.transactional_store import (
    ControlPlaneIdempotencyConflict,
    ControlPlaneStoreError,
    get_transactional_store,
)
from evm.observability.otel import runtime_service_version


router = APIRouter(prefix="/control-panel/v1/runtime", tags=["control-panel-runtime"])


class ApiDrainRequest(BaseModel):
    reason: str = Field(default="operator_requested", min_length=3, max_length=120)
    timeout_seconds: float = Field(default=20.0, ge=0.1, le=30.0)


class RolloutProbeRequest(BaseModel):
    logical_request_id: str = Field(
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$",
    )
    seed: int = Field(ge=1)
    processing_delay_ms: int = Field(default=50, ge=0, le=2_000)
    payload_token: str = Field(default="s6-controlled-replay", min_length=1, max_length=512)


class RolloutProbeResponse(BaseModel):
    schema_version: str
    logical_request_id: str
    effect_id: str
    request_sha256: str
    result_sha256: str
    state: str
    release_id: str
    instance_id: str
    source_revision: str
    completed_at: str
    replayed: bool = False


@router.get("/status")
def runtime_status() -> dict[str, object]:
    return API_DRAIN_CONTROLLER.snapshot().public_dict()


@router.post("/drain")
async def drain_runtime(
    payload: ApiDrainRequest,
    request: Request,
) -> dict[str, object]:
    client_host = request.client.host if request.client else ""
    remote_allowed = os.getenv("EVM_API_ALLOW_REMOTE_DRAIN", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"} and not remote_allowed:
        raise HTTPException(status_code=403, detail="drain_requires_loopback_client")
    API_DRAIN_CONTROLLER.begin_drain(payload.reason)
    snapshot, elapsed = await asyncio.to_thread(
        API_DRAIN_CONTROLLER.wait_until_drained,
        payload.timeout_seconds,
    )
    if snapshot.instance_id == "unknown" or snapshot.source_revision == "unknown":
        raise HTTPException(status_code=503, detail="drain_runtime_identity_unavailable")
    completed_at = datetime.now(UTC).isoformat()
    request_payload = {
        "instance_id": snapshot.instance_id,
        "release_id": snapshot.release_id,
        "source_revision": snapshot.source_revision,
        "reason": snapshot.reason,
        "timeout_seconds": payload.timeout_seconds,
    }
    response_payload = snapshot.public_dict() | {
        "schema_version": "evm.api_drain_event.v1",
        "drain_elapsed_seconds": round(elapsed, 6),
        "drain_completed": snapshot.in_flight == 0,
        "completed_at": completed_at,
    }
    try:
        stored, replayed = get_transactional_store().commit_idempotent_terminal_entity(
            scope="s6.api-drain",
            idempotency_key=f"{snapshot.instance_id}:{snapshot.release_id}",
            request_payload=request_payload,
            entity_kind="s6_api_drain",
            entity_id=snapshot.instance_id,
            response_payload=response_payload,
            state="completed" if snapshot.in_flight == 0 else "timed_out",
        )
    except ControlPlaneIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail="drain_event_identity_conflict") from exc
    except ControlPlaneStoreError as exc:
        raise HTTPException(status_code=503, detail="drain_event_store_unavailable") from exc
    return stored | {"replayed": replayed}


@router.post("/rollout-probes", response_model=RolloutProbeResponse)
async def execute_rollout_probe(
    payload: RolloutProbeRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RolloutProbeResponse:
    if idempotency_key and idempotency_key != payload.logical_request_id:
        raise HTTPException(status_code=409, detail="idempotency_key_identity_mismatch")
    if payload.processing_delay_ms:
        await asyncio.sleep(payload.processing_delay_ms / 1_000)

    request_payload = payload.model_dump(mode="json")
    request_sha256 = _canonical_digest(request_payload)
    effect_id = hashlib.sha256(
        f"s6-rollout:{payload.logical_request_id}:{request_sha256}".encode("ascii")
    ).hexdigest()
    result_sha256 = hashlib.sha256(
        f"{payload.seed}:{payload.payload_token}".encode("utf-8")
    ).hexdigest()
    snapshot = API_DRAIN_CONTROLLER.snapshot()
    response_payload = {
        "schema_version": "evm.api_rollout_probe.v1",
        "logical_request_id": payload.logical_request_id,
        "effect_id": effect_id,
        "request_sha256": request_sha256,
        "result_sha256": result_sha256,
        "state": "completed",
        "release_id": snapshot.release_id,
        "instance_id": snapshot.instance_id,
        "source_revision": runtime_service_version(default=snapshot.source_revision),
        "completed_at": datetime.now(UTC).isoformat(),
        "replayed": False,
    }
    try:
        stored, replayed = get_transactional_store().commit_idempotent_terminal_entity(
            scope="s6.api-rollout-probe",
            idempotency_key=payload.logical_request_id,
            request_payload=request_payload,
            entity_kind="s6_rollout_probe",
            entity_id=payload.logical_request_id,
            response_payload=response_payload,
            state="completed",
        )
    except ControlPlaneIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail="rollout_probe_identity_conflict") from exc
    except ControlPlaneStoreError as exc:
        raise HTTPException(status_code=503, detail="rollout_probe_store_unavailable") from exc
    stored["replayed"] = replayed
    return RolloutProbeResponse.model_validate(stored)


@router.get("/rollout-probes/{logical_request_id}", response_model=RolloutProbeResponse)
def rollout_probe(logical_request_id: str) -> RolloutProbeResponse:
    try:
        payload = get_transactional_store().get_entity(
            "s6_rollout_probe",
            logical_request_id,
        )
    except ControlPlaneStoreError as exc:
        raise HTTPException(status_code=503, detail="rollout_probe_store_unavailable") from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="rollout_probe_not_found")
    return RolloutProbeResponse.model_validate(payload | {"replayed": True})


def _canonical_digest(payload: object) -> str:
    import json

    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
