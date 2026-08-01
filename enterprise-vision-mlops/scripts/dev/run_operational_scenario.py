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
from evm.operations.reconciliation import plan_device_plugin_reconciliation  # noqa: E402
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
    reconcile.add_argument("--discovered-driver-path", action="append", required=True)
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

    resource = json.loads(args.daemonset_json.read_text(encoding="utf-8-sig"))
    plan = plan_device_plugin_reconciliation(resource, args.discovered_driver_path)
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
