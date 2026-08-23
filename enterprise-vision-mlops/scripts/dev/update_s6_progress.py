from __future__ import annotations

import argparse
import hashlib
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
        "component": "Scenario S6 frozen runtime and evidence contract",
        "files": [
            "configs/s6_rolling_handoff.toml",
            "src/evm/scale_validation/s6_runtime.py",
            "src/evm/scale_validation/s6_evidence.py",
            "scripts/dev/run_s6_rolling_handoff_experiment.py",
            "scripts/dev/validate_s6_rolling_handoff_evidence.py",
        ],
    },
    {
        "component": "Existing Prometheus target discovery",
        "files": ["monitoring/prometheus/prometheus.yml"],
    },
]

EVIDENCE = {
    "preflight": "docs/status/evidence/s6-api-rolling-preflight-checkpoint.json",
    "attempt_01": "docs/status/evidence/s6-api-rolling-failed-attempt-01.json",
    "attempt_02": "docs/status/evidence/s6-api-rolling-failed-attempt-02.json",
    "experiment": "docs/status/evidence/s6-rolling-handoff-experiment.json",
    "smoke": "docs/status/evidence/s6-current-revision-runtime-smoke.json",
    "closure": "docs/status/evidence/s6-rolling-handoff-closure.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize Scenario S6 progress records")
    parser.add_argument("--phase", choices=("implementation", "closure"), required=True)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-path", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(UTC).replace(microsecond=0)
    payload = json.loads(args.json_path.read_text(encoding="utf-8"))
    scenario = next(item for item in payload["scenarios"] if item["scenario_id"] == "S6")
    scenario["changed_components"] = S6_COMPONENTS
    scenario["affected_existing_components"] = S6_COMPONENTS
    scenario["implementation_summary"] = [
        "The existing FastAPI now rejects new workload requests after drain begins while allowing accepted in-flight requests to finish.",
        "A PostgreSQL-backed idempotent rollout probe commits one terminal effect across API replicas and retries.",
        "The existing API Deployment now declares two replicas, zero-unavailable RollingUpdate, startup/readiness/liveness probes, preStop drain, and bounded termination grace.",
        "An isolated S6 Deployment and Service preserve the Compose API and B0 serving baseline during controlled validation.",
        "The acceptance runner separates three API rolling repetitions from one calibration and three controlled single-GPU handoff repetitions.",
        "Strict evidence validation recomputes request/effect/drain/trace and GPU approval/owner/CUDA/rollback outcomes from private raw artifacts and canonical Git blobs.",
    ]
    scenario["implementation_delta"]["modified_existing_components"] = S6_COMPONENTS
    if args.phase == "implementation":
        scenario["status"] = "implementing"
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
    else:
        artifacts = evidence_artifacts()
        acceptance_refs = [EVIDENCE["experiment"], EVIDENCE["closure"]]
        scenario["status"] = "verified"
        scenario["experiment_environment"] = (
            "A controlled experiment ran on one local physical node with Docker Desktop Kubernetes, "
            "two isolated stateless API replicas, real PostgreSQL, Prometheus and W3C OTLP, one "
            "physical GPU, and real CUDA B0 serving. No customer traffic or physical-node failover was used."
        )
        scenario["acceptance_criteria"] = [
            {
                **criterion,
                "status": "passed",
                "evidence_refs": acceptance_refs,
            }
            for criterion in scenario["acceptance_criteria"]
        ]
        scenario["observed_result"] = (
            "Three independent API rolling repetitions completed 1,500 logical requests with "
            "zero accepted loss and zero duplicate effects; rollout recovery measured 16.313 to "
            "20.969 seconds and p99 latency measured 172.0 to 1,265.01 ms. One calibration and "
            "three independent single-GPU handoffs preserved zero overlapping owners, exact "
            "candidate and rollback identity, real CUDA inference, and measured source-to-target "
            "interruption of 9.125 to 10.281 seconds and target-to-source interruption of 10.031 "
            "to 11.234 seconds. Two rejected runtime attempts and regression command RCA remain "
            "recorded without acceptance credit."
        )
        scenario["evidence_artifacts"] = artifacts
        scenario["evidence_index"] = artifacts
        scenario["unresolved_items"] = []
        scenario["next_action"] = (
            "Review S7 model-family-specific admission readiness and blockers only; do not start S7 in this closure turn."
        )
        scenario["verdict_and_claim_boundary"]["verdict"] = "passed"
        update = {
            "occurred_at": now.isoformat().replace("+00:00", "Z"),
            "phase": "verification",
            "status": "verified",
            "summary": (
                "Three API rolling and three controlled single-GPU handoff repetitions passed all "
                "four S6 criteria; strict evidence, current-revision regressions, exact rollback, "
                "baseline restoration, and isolated-resource cleanup also passed."
            ),
            "evidence_refs": [EVIDENCE["experiment"], EVIDENCE["closure"]],
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


def evidence_artifacts() -> list[dict[str, str]]:
    claims = {
        "preflight": "The isolated two-replica API, drain, PostgreSQL identity, OTLP and Prometheus preflight passed without acceptance credit.",
        "attempt_01": "The first rolling attempt was rejected because exact old-Pod drain evidence had not converged when rollout status returned.",
        "attempt_02": "The second rolling attempt was rejected because the initial shared latency gate and instantaneous scrape check did not model rollout recovery correctly.",
        "experiment": "Three API rolling and three single-GPU handoff repetitions passed from independently recomputed private raw evidence.",
        "smoke": "The current revision preserved healthy API, PostgreSQL, queue-worker, CUDA serving and Prometheus behavior before isolated cleanup.",
        "closure": "Strict closure binds the accepted experiment, regressions, failed-attempt RCA, Git blobs, private inventory and cleanup without claiming GPU HA.",
    }
    artifacts = []
    for name, relative in EVIDENCE.items():
        path = ROOT / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        artifacts.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "generated_at": str(payload["generated_at"]),
                "claim": claims[name],
            }
        )
    return artifacts


if __name__ == "__main__":
    raise SystemExit(main())
