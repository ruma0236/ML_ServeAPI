from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

from evm.agentops.contracts import AgentRun


def validate(policy_path: Path, run_path: Path) -> dict[str, object]:
    with policy_path.open("rb") as fp:
        policy = tomllib.load(fp)
    run = AgentRun.model_validate_json(run_path.read_text(encoding="utf-8"))
    blockers: list[str] = []

    persistence = policy.get("persistence", {})
    hitl = policy.get("human_in_the_loop", {})
    tool_audit = policy.get("tool_audit", {})
    recovery = policy.get("recovery", {})
    safety = policy.get("safety", {})

    if persistence.get("checkpointer") in {None, "memory", "in_memory"}:
        blockers.append("persistent_checkpointer_required")
    if hitl.get("allowed_decisions") != ["approve", "edit", "reject"]:
        blockers.append("hitl_decisions_incomplete")
    if set(hitl.get("required_operation_categories", [])) != {"write", "execute", "deploy"}:
        blockers.append("side_effect_categories_incomplete")
    if tool_audit.get("store_raw_arguments") is not False:
        blockers.append("raw_tool_arguments_must_not_be_stored")
    if recovery.get("automatic_resume_idempotent_only") is not True:
        blockers.append("automatic_resume_must_be_idempotent_only")
    if safety.get("automatic_deployment") is not False or run.automatic_deployment:
        blockers.append("automatic_deployment_must_be_disabled")
    if safety.get("automatic_model_promotion") is not False or run.automatic_model_promotion:
        blockers.append("automatic_model_promotion_must_be_disabled")

    required_categories = set(hitl.get("required_operation_categories", []))
    for tool_call in run.tool_calls:
        if tool_call.operation_category in required_categories and not tool_call.approval_required:
            blockers.append(f"approval_missing:{tool_call.call_id}")

    return {
        "schema_version": "evm.agentops.policy_validation.v1",
        "status": "pass" if not blockers else "blocked",
        "design_only": True,
        "runtime_execution_claimed": False,
        "run_id": run.run_id,
        "tool_call_count": len(run.tool_calls),
        "pending_interrupt_count": sum(item.status == "pending" for item in run.interrupts),
        "failure_scenario_count": len(policy.get("failure_scenarios", [])),
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="configs/agentops_reliability.toml")
    parser.add_argument(
        "--run", default="contracts/agentops/examples/agent-run.json"
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    report = validate(Path(args.policy), Path(args.run))
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
