from __future__ import annotations

from fastapi import APIRouter, HTTPException

from evm.control_panel.isolated_ct import (
    create_ct_snapshot,
    load_ct_evaluation,
    load_ct_snapshot,
)
from evm.control_panel.schemas import (
    CTDatasetSnapshot,
    CTEvaluation,
    CTSnapshotCreateRequest,
)


router = APIRouter(prefix="/control-panel/v1/ct", tags=["control-panel-ct"])


@router.get("/snapshots/latest", response_model=CTDatasetSnapshot)
def latest_ct_snapshot() -> CTDatasetSnapshot:
    snapshot = load_ct_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=404, detail={"error": "ct_snapshot_not_found"})
    return snapshot


@router.post("/snapshots", response_model=CTDatasetSnapshot, status_code=201)
def create_isolated_ct_snapshot(request: CTSnapshotCreateRequest) -> CTDatasetSnapshot:
    try:
        return create_ct_snapshot(
            request.source_shard_index_uri,
            lifecycle_run_id=request.lifecycle_run_id,
            profile_id=request.profile_id,
            profile_version=request.profile_version,
            profile_digest=request.profile_digest,
            split=request.split,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "ct_snapshot_creation_failed", "message": str(exc)},
        ) from exc


@router.get("/evaluations/latest", response_model=CTEvaluation)
def latest_ct_evaluation() -> CTEvaluation:
    evaluation = load_ct_evaluation()
    if evaluation is None:
        raise HTTPException(status_code=404, detail={"error": "ct_evaluation_not_found"})
    return evaluation
