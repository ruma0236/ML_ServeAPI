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
from evm.control_panel.admission_queue import (  # noqa: E402
    AdmissionQueueConfig,
)
from evm.scale_validation.s2_runtime import (  # noqa: E402
    recompute_s2_acceptance,
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
    if len(payload.get("profile_results", [])) != 30:
        raise ValueError("S2 runtime evidence must contain 30 profile results")
    queue_config = AdmissionQueueConfig.from_path(
        ROOT / "configs" / "s2_bounded_queue_v1.toml"
    )
    expected_contract = {**queue_config.public_dict(), "sha256": queue_config.sha256}
    if payload.get("queue_contract") != expected_contract:
        raise ValueError("S2 evidence queue contract differs from the frozen config")
    acceptance, readiness, details = recompute_s2_acceptance(
        payload["profile_results"], queue_config
    )
    repeatability = dict(payload.get("deterministic_input_repeatability", {}))
    readiness["RG-11-fixed-seed-input-repeatability"] = (
        bool(repeatability) and all(value is True for value in repeatability.values())
    )
    if payload.get("acceptance") != acceptance:
        raise ValueError("S2 acceptance differs from raw-derived recalculation")
    if payload.get("readiness_gates") != readiness:
        raise ValueError("S2 readiness differs from raw-derived recalculation")
    if payload.get("strict_recalculation") != details:
        raise ValueError("S2 strict recalculation projection is stale or mutated")
    if not all(acceptance.values()):
        raise ValueError("S2 raw-derived acceptance criteria are not all passed")
    if not all(readiness.values()):
        raise ValueError("S2 raw-derived readiness gates are not all passed")
    if payload.get("runtime_verdict") != "passed":
        raise ValueError("S2 runtime evidence is not passed")
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
    revision = str(runtime["source_identity"]["implementation_revision"])
    short_revision = revision[:7]
    regression = dict(closure.get("regression", {}))
    failed_attempt_count = len(closure.get("failed_attempts_and_rca", []))
    readiness_count = sum(
        value is True for value in runtime.get("readiness_gates", {}).values()
    )
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
        and not item.startswith("The strict-evidence checkpoint")
    ]
    scenario["implementation_summary"].append(
        f"At strict-evidence revision {short_revision}, the frozen A-J external matrix "
        "completed 30 of 30 profile repetitions with all four acceptance criteria "
        f"and all {readiness_count} readiness gates passed. "
        f"{regression.get('focused_real_postgresql_tests')} focused real-PostgreSQL "
        f"tests, {regression.get('full_python_tests')} full Python tests, "
        f"{regression.get('control_panel_tests')} Control Panel tests, and the "
        "production frontend build also passed. All acceptance values were "
        "recomputed from persisted numeric evidence rather than stored pass flags."
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
                f"S2 closure records regressions, {failed_attempt_count} retained failed "
                "attempts with RCA, "
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
        f"profile results, S2-AC-01 through S2-AC-04, and {readiness_count} readiness gates passed; "
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
        "Keep S2 as the strict regression boundary and evaluate the S3 start gate."
    )
    scenario["verdict_and_claim_boundary"]["verdict"] = "passed"
    scenario["chronological_updates"] = [
        item
        for item in scenario["chronological_updates"]
        if not (
            item.get("phase") == "verification"
            and any(
                ref in {RUNTIME_PATH, CLOSURE_PATH}
                for ref in item["evidence_refs"]
            )
        )
    ]
    scenario["chronological_updates"].extend(
        [
            {
                "occurred_at": generated_at,
                "phase": "verification",
                "status": "verified",
                "summary": (
                    f"At {short_revision}, the strict-evidence A-J matrix passed 30 of 30 "
                    f"profile repetitions, S2-AC-01 through S2-AC-04, all {readiness_count} "
                    "readiness gates, regressions, and the production frontend build. "
                    "Canonical public evidence and retained failed-attempt RCAs are hash-linked."
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
    if closure.get("source_identity") != runtime.get("source_identity"):
        raise ValueError("S2 closure source identity differs from runtime evidence")
    runtime_sha256 = public_file_sha256(args.runtime_evidence)
    final_runtime = dict(closure.get("final_runtime_evidence", {}))
    if final_runtime.get("path") != RUNTIME_PATH:
        raise ValueError("S2 closure points to an unexpected runtime evidence path")
    if final_runtime.get("sha256") != runtime_sha256:
        raise ValueError("S2 closure runtime evidence hash is stale")
    if final_runtime.get("acceptance") != runtime.get("acceptance"):
        raise ValueError("S2 closure acceptance projection differs from runtime")
    ledger_payload = json.loads(args.progress.read_text(encoding="utf-8"))
    ledger = close_s2(
        ledger_payload,
        runtime=runtime,
        closure=closure,
        runtime_sha256=runtime_sha256,
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
