from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from evm.control_panel.deployment_intents import (
    DeploymentTransitionRejected,
    get_intent,
    intent_root,
    mark_applying,
    mark_rolled_back,
    finish_execution,
    revalidate_queued_intent,
    transition_intent,
)
from evm.control_panel.schemas import (
    DeploymentExecutionResult,
    DeploymentIntent,
    DeploymentTransitionRequest,
)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def executor_enabled() -> bool:
    return os.getenv("EVM_DEPLOYMENT_EXECUTOR_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }


def execute_apply(
    intent_id: str,
    *,
    runner: Runner = subprocess.run,
    require_enabled: bool = True,
) -> DeploymentIntent:
    if require_enabled and not executor_enabled():
        raise DeploymentTransitionRejected("deployment_executor_disabled")
    intent = revalidate_queued_intent(intent_id)
    validate_executor_target(intent)
    applying = mark_applying(intent_id)
    commands = [
        [
            "kubectl",
            "-n",
            applying.target_namespace,
            "annotate",
            f"deployment/{applying.target.name}",
            f"evm.openai.local/deployment-intent={applying.intent_id}",
            "--overwrite",
        ],
        [
            "kubectl",
            "-n",
            applying.target_namespace,
            "set",
            "image",
            f"deployment/{applying.target.name}",
            f"serving={applying.image_digest}",
        ],
        [
            "kubectl",
            "-n",
            applying.target_namespace,
            "rollout",
            "status",
            f"deployment/{applying.target.name}",
            "--timeout=60s",
        ],
    ]
    execution = run_commands(applying, "apply", commands, runner)
    return finish_execution(intent_id, execution)


def execute_rollback(
    intent_id: str,
    *,
    runner: Runner = subprocess.run,
    require_enabled: bool = True,
) -> DeploymentIntent:
    if require_enabled and not executor_enabled():
        raise DeploymentTransitionRejected("deployment_executor_disabled")
    intent = get_intent(intent_id)
    validate_executor_target(intent)
    if intent.state not in {"applied", "failed"}:
        raise DeploymentTransitionRejected("rollback_requires_applied_or_failed_intent")
    commands = [
        [
            "kubectl",
            "-n",
            intent.target_namespace,
            "rollout",
            "undo",
            f"deployment/{intent.target.name}",
        ],
        [
            "kubectl",
            "-n",
            intent.target_namespace,
            "rollout",
            "status",
            f"deployment/{intent.target.name}",
            "--timeout=60s",
        ],
    ]
    execution = run_commands(intent, "rollback", commands, runner)
    if execution.status == "rolled_back":
        return mark_rolled_back(intent_id, execution)
    current = get_intent(intent_id)
    return transition_intent(
        intent_id,
        DeploymentTransitionRequest(
            actor="deployment-executor",
            reason="executor rollback failed",
            expected_version=current.version,
        ),
        allowed_from={"applied", "failed"},
        to_state=current.state,
        result="rollback_failed",
        mutate=lambda _: {"execution_result": execution},
    )


def run_commands(
    intent: DeploymentIntent,
    action: str,
    commands: list[list[str]],
    runner: Runner,
) -> DeploymentExecutionResult:
    started_at = utc_now()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    exit_code = 0
    executed: list[str] = []
    for command in commands:
        executed.append(" ".join(command))
        result = runner(command, capture_output=True, text=True, check=False)
        stdout_parts.append(result.stdout or "")
        stderr_parts.append(result.stderr or "")
        exit_code = int(result.returncode)
        if exit_code != 0:
            break
    output_root = intent_root() / intent.intent_id / "executor"
    output_root.mkdir(parents=True, exist_ok=True)
    stdout_path = output_root / f"{action}-stdout.log"
    stderr_path = output_root / f"{action}-stderr.log"
    stdout_path.write_text("\n".join(stdout_parts), encoding="utf-8")
    stderr_path.write_text("\n".join(stderr_parts), encoding="utf-8")
    if action == "rollback":
        status = "rolled_back" if exit_code == 0 else "failed"
    else:
        status = "applied" if exit_code == 0 else "failed"
    return DeploymentExecutionResult(
        action=action,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        finished_at=utc_now(),
        command=executed,
        exit_code=exit_code,
        stdout_uri=canonical_output_uri(stdout_path),
        stderr_uri=canonical_output_uri(stderr_path),
    )


def validate_executor_target(intent: DeploymentIntent) -> None:
    if intent.target.kind != "Deployment" or intent.target.name != "evm-b7-serving":
        raise DeploymentTransitionRejected("executor_target_not_allowed")
    if intent.target.namespace != intent.target_namespace:
        raise DeploymentTransitionRejected("executor_namespace_mismatch")
    if "@sha256:" not in intent.image_digest:
        raise DeploymentTransitionRejected("executor_image_not_immutable")


def canonical_output_uri(path: Path) -> str:
    from evm.control_panel.readiness_evaluator import canonical_evidence_uri

    return canonical_evidence_uri(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute queued deployment intents.")
    parser.add_argument("--ledger-root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("apply", "rollback"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--intent-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.ledger_root:
        os.environ["EVM_DEPLOYMENT_INTENT_ROOT"] = args.ledger_root
    intent = (
        execute_apply(args.intent_id)
        if args.command == "apply"
        else execute_rollback(args.intent_id)
    )
    print(json.dumps(intent.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if intent.state in {"applied", "rolled_back"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
