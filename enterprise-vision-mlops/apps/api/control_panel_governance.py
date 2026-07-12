from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from apps.api.control_panel import (
    cycle_snapshot,
    invalidate_cycle_cache,
    resources_for_cycle,
)
from evm.control_panel.cycle_catalog import find_cycle
from evm.control_panel.decision_registry import (
    DecisionNotFound,
    DecisionTransitionRejected,
    DecisionVersionConflict,
    create_decision,
    read_decisions,
    transition_decision,
)
from evm.control_panel.diagnostics import build_control_panel_diagnostics
from evm.control_panel.drift_workflow import (
    DriftWorkflowConflict,
    DriftWorkflowNotFound,
    DriftWorkflowRejected,
    load_latest_drift_workflow,
    transition_drift_review,
)
from evm.control_panel.schemas import (
    ControlPanelDiagnostics,
    DecisionRecord,
    DecisionRecordList,
    DecisionRecordRequest,
    DecisionTransitionRequest,
    DriftReviewTransitionRequest,
    DriftReviewWorkflow,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-governance"])


@router.get("/diagnostics/latest", response_model=ControlPanelDiagnostics)
def latest_diagnostics(cycle_id: str | None = None) -> ControlPanelDiagnostics:
    live_cycle = cycle_snapshot()
    cycle = find_cycle(cycle_id, live_cycle) if cycle_id else live_cycle
    if cycle is None:
        raise HTTPException(status_code=404, detail="diagnostic_cycle_not_found")
    return build_control_panel_diagnostics(
        cycle,
        resources_for_cycle(cycle),
        persist=cycle_id is None,
    )


@router.get("/drift-reviews/latest", response_model=DriftReviewWorkflow)
def latest_drift_review() -> DriftReviewWorkflow:
    try:
        return load_latest_drift_workflow()
    except DriftWorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail="drift_review_not_found") from exc


@router.post(
    "/drift-reviews/{event_id}/transition",
    response_model=DriftReviewWorkflow,
)
def transition_drift_review_route(
    event_id: str,
    request: DriftReviewTransitionRequest,
    response: Response,
) -> DriftReviewWorkflow:
    try:
        workflow = transition_drift_review(event_id, request)
    except DriftWorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail="drift_review_not_found") from exc
    except DriftWorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DriftWorkflowRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not request.dry_run:
        invalidate_cycle_cache()
    response.status_code = status.HTTP_200_OK if request.dry_run else status.HTTP_202_ACCEPTED
    return workflow


@router.get("/decisions", response_model=DecisionRecordList)
def list_decisions() -> DecisionRecordList:
    return read_decisions()


@router.post("/decisions", response_model=DecisionRecord, status_code=status.HTTP_201_CREATED)
def create_decision_route(request: DecisionRecordRequest) -> DecisionRecord:
    try:
        return create_decision(request)
    except DecisionTransitionRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/decisions/{decision_id}/transition",
    response_model=DecisionRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
def transition_decision_route(
    decision_id: str,
    request: DecisionTransitionRequest,
) -> DecisionRecord:
    try:
        return transition_decision(decision_id, request)
    except DecisionNotFound as exc:
        raise HTTPException(status_code=404, detail="decision_not_found") from exc
    except DecisionVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DecisionTransitionRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
