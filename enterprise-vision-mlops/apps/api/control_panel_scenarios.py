from __future__ import annotations

from fastapi import APIRouter, HTTPException

from evm.control_panel.operations import TaskDispatchError
from evm.control_panel.schemas import TaskAssignment
from evm.control_panel.scenarios import (
    EnterpriseScenarioCatalog,
    ScenarioCatalogError,
    ScenarioIntakeLaunchRequest,
    launch_scenario_intake,
    read_scenario_catalog,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-scenarios"])


@router.get("/scenarios", response_model=EnterpriseScenarioCatalog)
def list_enterprise_scenarios() -> EnterpriseScenarioCatalog:
    try:
        return read_scenario_catalog()
    except (OSError, ValueError, ScenarioCatalogError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "scenario_catalog_invalid", "message": str(exc)},
        ) from exc


@router.post(
    "/scenarios/{scenario_id}/intake",
    response_model=TaskAssignment,
    status_code=202,
)
def start_scenario_intake(
    scenario_id: str,
    request: ScenarioIntakeLaunchRequest,
) -> TaskAssignment:
    try:
        return launch_scenario_intake(scenario_id, request)
    except ScenarioCatalogError as exc:
        status_code = 404 if str(exc) == "scenario_not_found" else 409
        raise HTTPException(
            status_code=status_code,
            detail={"error": str(exc), "scenario_id": scenario_id},
        ) from exc
    except TaskDispatchError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc), "scenario_id": scenario_id},
        ) from exc
