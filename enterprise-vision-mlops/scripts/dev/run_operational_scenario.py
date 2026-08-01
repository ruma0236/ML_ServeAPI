from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.operations.failure_scenarios import (  # noqa: E402
    ScenarioStateStore,
    atomic_write_json,
)
from evm.operations.reconciliation import (  # noqa: E402
    discover_wsl_driver_paths,
    plan_device_plugin_reconciliation,
)
from evm.operations.scenario_a_live import run_scenario_a_live  # noqa: E402
from evm.operations.scenario_a_preflight import (  # noqa: E402
    issue_scenario_a_approval,
    prepare_scenario_a_preflight,
)
from evm.operations.scenario_a_runner import (  # noqa: E402
    load_scenario_a_config,
    run_read_only_baseline,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded operational reliability stages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    baseline = subparsers.add_parser("baseline", help="Capture a read-only Scenario A baseline.")
    baseline.add_argument("--config", type=Path, required=True)
    baseline.add_argument("--run-id")

    reconcile = subparsers.add_parser(
        "reconcile-plan",
        help="Plan a device-plugin WSL driver reconciliation without mutation.",
    )
    reconcile.add_argument("--config", type=Path, required=True)
    reconcile.add_argument("--run-id", required=True)
    reconcile.add_argument("--daemonset-json", type=Path, required=True)
    reconcile.add_argument("--discovered-driver-path", action="append")

    preflight = subparsers.add_parser(
        "preflight",
        help="Capture the immutable identity and rollback package before approval.",
    )
    preflight.add_argument("--config", type=Path, required=True)
    preflight.add_argument("--run-id", required=True)

    approve = subparsers.add_parser(
        "approve",
        help="Issue one exact, expiring, single-use maintenance approval.",
    )
    approve.add_argument("--config", type=Path, required=True)
    approve.add_argument("--run-id", required=True)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--ttl-seconds", type=int, default=900)
    approve.add_argument("--maintenance-approved", action="store_true")

    live = subparsers.add_parser(
        "live",
        help="Consume approval and restart exactly one UID-bound production B0 Pod.",
    )
    live.add_argument("--config", type=Path, required=True)
    live.add_argument("--run-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_scenario_a_config(args.config.resolve())
    if args.command == "baseline":
        result = run_read_only_baseline(
            config=config,
            project_root=ROOT,
            run_id=args.run_id,
        )
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        return 0 if result.decision == "passed" else 1

    if args.command == "preflight":
        result = prepare_scenario_a_preflight(
            config=config,
            project_root=ROOT,
            run_id=args.run_id,
        )
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        return 0 if result.decision == "passed" else 1

    if args.command == "approve":
        if not args.maintenance_approved:
            print(json.dumps({"status": "blocked", "reason": "maintenance_approval_flag_missing"}))
            return 2
        result = issue_scenario_a_approval(
            config=config,
            run_id=args.run_id,
            approver=args.approver,
            ttl_seconds=args.ttl_seconds,
        )
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        return 0

    if args.command == "live":
        result = run_scenario_a_live(
            config=config,
            project_root=ROOT,
            run_id=args.run_id,
        )
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        return 0

    resource = json.loads(args.daemonset_json.read_text(encoding="utf-8-sig"))
    discovered_paths = args.discovered_driver_path or discover_wsl_driver_paths()
    plan = plan_device_plugin_reconciliation(resource, discovered_paths)
    run_root = config.execution.evidence_root / "A" / args.run_id
    plan_path = run_root / "device-plugin-reconciliation-plan.json"
    atomic_write_json(plan_path, plan.model_dump(mode="json"))
    store = ScenarioStateStore(config.execution.evidence_root / "A")
    state = store.load(args.run_id)
    next_state = "non_disruptive_validated" if plan.decision != "blocked" else "blocked"
    updated = store.transition(
        args.run_id,
        next_state=next_state,
        expected_revision=state.revision,
        reason=f"device_plugin_reconciliation_{plan.decision}",
        now=datetime.now(timezone.utc),
    )
    print(
        json.dumps(
            {
                "decision": plan.decision,
                "mutation_performed": plan.mutation_performed,
                "plan_path": str(plan_path),
                "run_id": args.run_id,
                "state": updated.state,
                "state_revision": updated.revision,
            },
            sort_keys=True,
        )
    )
    return 0 if plan.decision != "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
