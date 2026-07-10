from __future__ import annotations

from fastapi import APIRouter, HTTPException

from evm.control_panel.operations import (
    cancel_command_intent,
    confirm_command_intent,
    create_command_intent,
    read_commands,
)
from evm.control_panel.promotion_policy import PromotionPolicyDenied
from evm.control_panel.schemas import CommandIntent, CommandIntentList, CommandIntentRequest


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-commands"])


@router.get("/commands", response_model=CommandIntentList)
def list_command_intents() -> CommandIntentList:
    return read_commands()


@router.post("/commands", response_model=CommandIntent, status_code=202)
def create_command(request: CommandIntentRequest) -> CommandIntent:
    try:
        return create_command_intent(request)
    except PromotionPolicyDenied as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "promotion_policy_denied",
                "decision": exc.decision.decision,
                "decision_id": exc.decision.decision_id,
                "reason_codes": exc.decision.reason_codes,
            },
        ) from exc


@router.post("/commands/{command_id}/confirm", response_model=CommandIntent, status_code=202)
def confirm_command(command_id: str) -> CommandIntent:
    command = confirm_command_intent(command_id)
    if command is None:
        raise HTTPException(status_code=404, detail={"error": "command_not_found", "command_id": command_id})
    return command


@router.post("/commands/{command_id}/cancel", response_model=CommandIntent, status_code=202)
def cancel_command(command_id: str) -> CommandIntent:
    command = cancel_command_intent(command_id)
    if command is None:
        raise HTTPException(status_code=404, detail={"error": "command_not_found", "command_id": command_id})
    return command
