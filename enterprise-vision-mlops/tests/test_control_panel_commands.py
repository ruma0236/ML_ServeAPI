from __future__ import annotations

from pathlib import Path

import pytest

from apps.api.control_panel_commands import (
    cancel_command,
    confirm_command,
    create_command,
    list_command_intents,
)
from evm.control_panel.schemas import CommandIntentRequest, CycleRun, ResourceRef


def command_request(dry_run: bool = True) -> CommandIntentRequest:
    return CommandIntentRequest(
        action="restart_deployment",
        target=ResourceRef(namespace="evm-platform", kind="Deployment", name="evm-api"),
        actor="ai-infra-sre",
        dry_run=dry_run,
        reason="W7 guarded command intent verification",
        parameters={"replicas": 1},
    )


def test_command_intent_dry_run_confirm_cancel_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path))

    command = create_command(command_request(dry_run=True))
    assert command.status == "dry_run"
    assert command.audit[0].event == "command_intent_created"

    confirmed = confirm_command(command.command_id)
    assert confirmed.status == "pending_confirmation"
    assert confirmed.confirmed_at is not None
    assert confirmed.applied_at is None
    assert confirmed.audit[-1].details["mutation_applied"] is False

    cancelled = cancel_command(command.command_id)
    assert cancelled.status == "cancelled"
    assert cancelled.audit[-1].event == "command_cancelled"
    assert list_command_intents().commands[0].command_id == command.command_id


def test_command_intent_unknown_id_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path))

    with pytest.raises(Exception) as exc:
        confirm_command("cmd-missing")

    assert getattr(exc.value, "status_code", None) == 404


def test_promotion_command_dry_run_records_server_policy_and_apply_is_denied(
    tmp_path, monkeypatch
):
    cycle = CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path / "operations"))
    monkeypatch.setenv("EVM_PROMOTION_POLICY_EVIDENCE_ROOT", str(tmp_path / "policy"))
    monkeypatch.setattr("evm.control_panel.operations.build_latest_cycle", lambda: cycle)
    request = CommandIntentRequest(
        action="promote_model",
        target=ResourceRef(namespace="evm-staging", kind="Deployment", name="evm-b7-serving"),
        actor="ml-platform",
        dry_run=True,
        reason="EVM-233 guarded promotion verification",
        parameters={
            "target_environment": "staging",
            "target_namespace": "evm-production",
            "requester": "spoofed-requester",
            "approver": "spoofed-approver",
        },
    )

    dry_run = create_command(request)

    assert dry_run.status == "dry_run"
    assert dry_run.promotion_policy is not None
    assert dry_run.promotion_policy.decision == "blocked"
    assert dry_run.promotion_policy.target_namespace == "evm-staging"
    assert dry_run.promotion_policy.requester == "ml-platform"
    assert dry_run.promotion_policy.approver is None
    assert dry_run.audit[0].details["promotion_decision_id"]

    request.dry_run = False
    with pytest.raises(Exception) as exc:
        create_command(request)

    assert getattr(exc.value, "status_code", None) == 409
    assert exc.value.detail["error"] == "promotion_policy_denied"
