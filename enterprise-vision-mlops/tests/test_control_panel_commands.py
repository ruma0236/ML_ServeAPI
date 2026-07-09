from __future__ import annotations

import pytest

from apps.api.control_panel_commands import cancel_command, confirm_command, create_command, list_command_intents
from evm.control_panel.schemas import CommandIntentRequest, ResourceRef


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
