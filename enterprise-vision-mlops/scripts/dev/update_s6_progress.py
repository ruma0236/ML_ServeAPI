from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from evm.scale_validation.contracts import (
    ScenarioProgressLedger,
    render_progress_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.json"
DEFAULT_MARKDOWN = ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.md"

S6_COMPONENTS = [
    {
        "component": "Existing API rollout admission and drain",
        "files": [
            "apps/api/main.py",
            "apps/api/control_panel_runtime.py",
            "src/evm/control_panel/api_rollout.py",
            "src/evm/control_panel/transactional_store.py",
        ],
    },
    {
        "component": "Existing Kubernetes API deployment",
        "files": [
            "infra/kubernetes/local/api.yaml",
            "infra/kubernetes/scale-validation/s6/api-rolling.yaml",
        ],
    },
    {
        "component": "Scenario S6 frozen runtime contract",
        "files": ["configs/s6_rolling_handoff.toml"],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize Scenario S6 progress records")
    parser.add_argument("--phase", choices=("implementation",), required=True)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-path", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(UTC).replace(microsecond=0)
    payload = json.loads(args.json_path.read_text(encoding="utf-8"))
    scenario = next(item for item in payload["scenarios"] if item["scenario_id"] == "S6")
    scenario["status"] = "implementing"
    scenario["changed_components"] = S6_COMPONENTS
    scenario["affected_existing_components"] = S6_COMPONENTS
    scenario["implementation_summary"] = [
        "The existing FastAPI now rejects new workload requests after drain begins while allowing accepted in-flight requests to finish.",
        "A PostgreSQL-backed idempotent rollout probe commits one terminal effect across API replicas and retries.",
        "The existing API Deployment now declares two replicas, zero-unavailable RollingUpdate, startup/readiness/liveness probes, preStop drain, and bounded termination grace.",
        "An isolated S6 Deployment and Service preserve the Compose API and B0 serving baseline during controlled validation.",
    ]
    scenario["implementation_delta"]["modified_existing_components"] = S6_COMPONENTS
    scenario["next_action"] = (
        "Build immutable old/new API images from the implementation revision, run the isolated "
        "preflight smoke, and keep all S6 acceptance criteria pending until three API rolling "
        "and three GPU handoff repetitions close."
    )
    update = {
        "occurred_at": now.isoformat().replace("+00:00", "Z"),
        "phase": "implementation",
        "status": "implementing",
        "summary": (
            "Drain-aware API admission, transactional rollout-probe identity, and isolated "
            "zero-unavailable Kubernetes contracts were implemented; acceptance experiments remain pending."
        ),
        "evidence_refs": [],
    }
    if not any(
        item.get("phase") == update["phase"] and item.get("summary") == update["summary"]
        for item in scenario["chronological_updates"]
    ):
        scenario["chronological_updates"].append(update)
    payload["generated_at"] = update["occurred_at"]
    ledger = ScenarioProgressLedger.model_validate(payload)
    args.json_path.write_text(ledger.model_dump_json(indent=2) + "\n", encoding="utf-8")
    args.markdown_path.write_text(
        render_progress_markdown(ledger),
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
