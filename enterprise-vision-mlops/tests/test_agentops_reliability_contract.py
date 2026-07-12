from pathlib import Path

from evm.agentops.contracts import AgentRun
from scripts.dev.validate_agentops_reliability import validate


def test_agentops_design_contract_requires_hitl_and_disables_automatic_release():
    run_path = Path("contracts/agentops/examples/agent-run.json")
    run = AgentRun.model_validate_json(run_path.read_text(encoding="utf-8"))
    report = validate(Path("configs/agentops_reliability.toml"), run_path)

    assert run.status == "pending_approval"
    assert run.tool_calls[-1].operation_category == "deploy"
    assert run.tool_calls[-1].approval_required is True
    assert run.automatic_deployment is False
    assert run.automatic_model_promotion is False
    assert report["status"] == "pass"
    assert report["design_only"] is True
    assert report["runtime_execution_claimed"] is False
