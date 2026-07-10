from __future__ import annotations

from fastapi import APIRouter, HTTPException

from apps.api.control_panel import invalidate_cycle_cache
from evm.control_panel.deployment_intents import (
    DeploymentIntentBlocked,
    DeploymentIntentNotFound,
    DeploymentTransitionRejected,
    DeploymentVersionConflict,
    approve_intent,
    create_deployment_intent,
    queue_intent,
    read_intents,
    request_approval,
)
from evm.control_panel.schemas import (
    DeploymentIntent,
    DeploymentIntentList,
    DeploymentIntentRequest,
    DeploymentTransitionRequest,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-deployments"])


@router.get("/deployment-intents", response_model=DeploymentIntentList)
def list_deployment_intents() -> DeploymentIntentList:
    return read_intents()


@router.post("/deployment-intents", response_model=DeploymentIntent, status_code=202)
def create_intent(request: DeploymentIntentRequest) -> DeploymentIntent:
    try:
        intent = create_deployment_intent(request)
        invalidate_cycle_cache()
        return intent
    except DeploymentIntentBlocked as exc:
        raise blocked_http(exc) from exc


@router.post(
    "/deployment-intents/{intent_id}/request-approval",
    response_model=DeploymentIntent,
    status_code=202,
)
def request_intent_approval(
    intent_id: str,
    request: DeploymentTransitionRequest,
) -> DeploymentIntent:
    return run_transition(lambda: request_approval(intent_id, request))


@router.post(
    "/deployment-intents/{intent_id}/approve",
    response_model=DeploymentIntent,
    status_code=202,
)
def approve_deployment_intent(
    intent_id: str,
    request: DeploymentTransitionRequest,
) -> DeploymentIntent:
    return run_transition(lambda: approve_intent(intent_id, request))


@router.post(
    "/deployment-intents/{intent_id}/queue",
    response_model=DeploymentIntent,
    status_code=202,
)
def queue_deployment_intent(
    intent_id: str,
    request: DeploymentTransitionRequest,
) -> DeploymentIntent:
    return run_transition(lambda: queue_intent(intent_id, request))


def run_transition(operation) -> DeploymentIntent:
    try:
        intent = operation()
        invalidate_cycle_cache()
        return intent
    except DeploymentIntentBlocked as exc:
        raise blocked_http(exc) from exc
    except DeploymentIntentNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "deployment_intent_not_found", "intent_id": str(exc)},
        ) from exc
    except DeploymentVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "deployment_intent_version_conflict", "message": str(exc)},
        ) from exc
    except DeploymentTransitionRejected as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "deployment_transition_rejected", "message": str(exc)},
        ) from exc


def blocked_http(exc: DeploymentIntentBlocked) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": "deployment_intent_blocked",
            "blockers": exc.blockers,
            "ci_validation_id": (
                exc.ci_evidence.validation_id if exc.ci_evidence else None
            ),
        },
    )
