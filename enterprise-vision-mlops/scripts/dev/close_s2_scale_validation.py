from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.contracts import (  # noqa: E402
    ScenarioProgressLedger,
    render_progress_markdown,
)
from evm.scale_validation.evidence import (  # noqa: E402
    public_file_sha256,
    write_public_json,
)


RUNTIME_PATH = "docs/status/evidence/s2-bounded-queue-experiment.json"
CLOSURE_PATH = "docs/status/evidence/s2-bounded-queue-closure.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Close S2 from validated external-runtime evidence."
    )
    parser.add_argument(
        "--progress",
        type=Path,
        default=ROOT
        / "docs/status/2026-08-15-distributed-scale-scenario-progress.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=ROOT
        / "docs/status/2026-08-15-distributed-scale-scenario-progress.md",
    )
    parser.add_argument(
        "--runtime-evidence",
        type=Path,
        default=ROOT / RUNTIME_PATH,
    )
    parser.add_argument(
        "--closure-evidence",
        type=Path,
        default=ROOT / CLOSURE_PATH,
    )
    return parser.parse_args()


def validate_runtime(payload: dict[str, Any]) -> None:
    if payload.get("runtime_verdict") != "passed":
        raise ValueError("S2 runtime evidence is not passed")
    if len(payload.get("profile_results", [])) != 30:
        raise ValueError("S2 runtime evidence must contain 30 profile results")
    if set(payload.get("acceptance", {}).values()) != {True}:
        raise ValueError("S2 acceptance criteria are not all passed")
    if set(payload.get("readiness_gates", {}).values()) != {True}:
        raise ValueError("S2 readiness gates are not all passed")
    if payload.get("failed_attempts_and_rca"):
        raise ValueError("final S2 runtime evidence contains a failed profile")


def append_unique_file(component: dict[str, Any], path: str) -> None:
    if path not in component["files"]:
        component["files"].append(path)


def update_components(components: list[dict[str, Any]]) -> None:
    frozen = next(
        item for item in components if item["component"] == "Frozen runtime, migration, and telemetry"
    )
    append_unique_file(frozen, "configs/s2_experiment_matrix_v1.toml")
    verification = next(
        item for item in components if item["component"] == "Focused verification"
    )
    for path in (
        "src/evm/scale_validation/s2_runtime.py",
        "scripts/validation/run_s2_bounded_queue_experiment.py",
        "scripts/dev/close_s2_scale_validation.py",
        "tests/test_s2_runtime.py",
    ):
        append_unique_file(verification, path)


def close_s2(
    ledger_payload: dict[str, Any],
    *,
    runtime: dict[str, Any],
    closure: dict[str, Any],
    runtime_sha256: str,
    closure_sha256: str,
) -> ScenarioProgressLedger:
    generated_at = str(closure["generated_at"])
    scenario = next(
        item for item in ledger_payload["scenarios"] if item["scenario_id"] == "S2"
    )
    update_components(scenario["changed_components"])
    update_components(
        scenario["implementation_delta"]["modified_existing_components"]
    )
    scenario["implementation_summary"] = [
        item
        for item in scenario["implementation_summary"]
        if not item.startswith("At source revision 5de1c41")
        and not item.startswith("At implementation revision 31ee37c")
    ]
    scenario["implementation_summary"].append(
        "At implementation revision 31ee37c, the frozen A-J external matrix "
        "completed 30 of 30 profile repetitions with all four acceptance criteria "
        "and all 11 readiness gates passed. Forty-two focused real-PostgreSQL "
        "tests, 681 full Python tests, 59 Control Panel tests, and the production "
        "frontend build also passed."
    )
    scenario["experiment_environment"] = (
        "The accepted matrix used an isolated PostgreSQL 16 schema per runtime, "
        "an external Uvicorn TCP/HTTP API, a real dedicated queue-worker process, "
        "a deterministic Airflow-compatible HTTP dependency, Prometheus file_sd "
        "queries, per-task W3C OTLP chains, and a trusted real CUDA producer on "
        "one local physical node. No customer traffic or production endpoint was used."
    )
    evidence = [
        {
            "path": RUNTIME_PATH,
            "sha256": runtime_sha256,
            "generated_at": runtime["generated_at"],
            "claim": (
                "Thirty independent A-J profile repetitions passed the frozen external "
                "HTTP, PostgreSQL, worker, Prometheus, OTLP, recovery, and one-GPU contract."
            ),
        },
        {
            "path": CLOSURE_PATH,
            "sha256": closure_sha256,
            "generated_at": generated_at,
            "claim": (
                "S2 closure records regressions, two retained failed attempts with RCA, "
                "cleanup, residual limits, and the accepted claim boundary."
            ),
        },
    ]
    scenario["evidence_artifacts"] = [
        item
        for item in scenario["evidence_artifacts"]
        if item["path"] not in {RUNTIME_PATH, CLOSURE_PATH}
    ] + evidence
    scenario["evidence_index"] = list(scenario["evidence_artifacts"])
    evidence_refs = [RUNTIME_PATH, CLOSURE_PATH]
    for criterion in scenario["acceptance_criteria"]:
        criterion["status"] = "passed"
        criterion["evidence_refs"] = evidence_refs
    scenario["observed_result"] = (
        "A-J each ran three times through the existing external task path. All 30 "
        "profile results, S2-AC-01 through S2-AC-04, and 11 readiness gates passed; "
        "accepted work reached one terminal outcome, duplicate logical effects were "
        "zero, backpressure was explicit, worker loss recovered, and real CUDA GPU "
        "runtime concurrency remained one."
    )
    scenario["status"] = "verified"
    scenario["unresolved_items"] = [
        "The result is limited to one local physical node and controlled traffic.",
        "Multi-node availability, customer traffic, production SLA, HA, DR, and multi-GPU behavior remain outside this evidence.",
        "S3 and final S0-S8 cross-scenario validation have not started."
    ]
    scenario["next_action"] = (
        "Keep S2 as the regression boundary. Do not start S3 in this work unit."
    )
    scenario["verdict_and_claim_boundary"]["verdict"] = "passed"
    scenario["chronological_updates"] = [
        item
        for item in scenario["chronological_updates"]
        if not any(ref in {RUNTIME_PATH, CLOSURE_PATH} for ref in item["evidence_refs"])
    ]
    scenario["chronological_updates"].extend(
        [
            {
                "occurred_at": "2026-08-16T17:38:16.247374Z",
                "phase": "experiment",
                "status": "implementing",
                "summary": (
                    "The first complete 30-profile suite preserved all passing profile "
                    "assertions but failed S2-AC-01 because instantaneous sampling missed "
                    "three short-lived executor process trees. The failure and cleanup "
                    "were retained; no acceptance credit was awarded."
                ),
                "evidence_refs": [CLOSURE_PATH],
            },
            {
                "occurred_at": "2026-08-16T17:55:36.258827Z",
                "phase": "recovery",
                "status": "implementing",
                "summary": (
                    "After retained executor RSS was added, a transient Windows heartbeat "
                    "replace lock terminated the D CPU-one worker. The suite stopped before "
                    "E-J, cleanup passed, and bounded heartbeat replacement retry was added."
                ),
                "evidence_refs": [CLOSURE_PATH],
            },
            {
                "occurred_at": generated_at,
                "phase": "verification",
                "status": "verified",
                "summary": (
                    "At 31ee37c, the fresh A-J matrix passed 30 of 30 profile repetitions, "
                    "S2-AC-01 through S2-AC-04, all 11 readiness gates, 42 focused real-"
                    "PostgreSQL tests, 681 full Python tests, 59 Control Panel tests, and "
                    "the production frontend build. Canonical public evidence and the two "
                    "failed-attempt RCAs are hash-linked."
                ),
                "evidence_refs": evidence_refs,
            },
        ]
    )
    ledger_payload["generated_at"] = generated_at
    return ScenarioProgressLedger.model_validate(ledger_payload)


def main() -> int:
    args = parse_args()
    runtime = json.loads(args.runtime_evidence.read_text(encoding="utf-8"))
    closure = json.loads(args.closure_evidence.read_text(encoding="utf-8"))
    validate_runtime(runtime)
    if closure.get("verdict") != "passed":
        raise ValueError("S2 closure evidence is not passed")
    ledger_payload = json.loads(args.progress.read_text(encoding="utf-8"))
    ledger = close_s2(
        ledger_payload,
        runtime=runtime,
        closure=closure,
        runtime_sha256=public_file_sha256(args.runtime_evidence),
        closure_sha256=public_file_sha256(args.closure_evidence),
    )
    write_public_json(args.progress, ledger.model_dump(mode="json"))
    args.markdown.write_bytes(render_progress_markdown(ledger).encode("utf-8"))
    print(
        json.dumps(
            {
                "status": "valid",
                "scenario": "S2",
                "scenario_status": "verified",
                "runtime_sha256": public_file_sha256(args.runtime_evidence),
                "closure_sha256": public_file_sha256(args.closure_evidence),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
