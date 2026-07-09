from __future__ import annotations

from fastapi import APIRouter, HTTPException

from evm.control_panel.aggregation import build_latest_cycle
from evm.control_panel.schemas import CycleRun


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel"])


@router.get("/cycles/latest", response_model=CycleRun)
def latest_cycle() -> CycleRun:
    return build_latest_cycle()


@router.get("/cycles/{cycle_id}", response_model=CycleRun)
def get_cycle(cycle_id: str) -> CycleRun:
    cycle = build_latest_cycle()
    if cycle.cycle_id != cycle_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "cycle_not_found",
                "message": f"Only latest local cycle is available: {cycle.cycle_id}",
            },
        )
    return cycle
