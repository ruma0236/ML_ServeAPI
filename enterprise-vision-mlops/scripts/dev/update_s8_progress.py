from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.contracts import (  # noqa: E402
    ScenarioProgressLedger,
    render_progress_markdown,
)


DEFAULT_JSON = ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.json"
DEFAULT_MARKDOWN = ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize Scenario S8 progress")
    parser.add_argument(
        "--phase",
        choices=("implementation", "failure", "calibration", "verification"),
        default="implementation",
    )
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-path", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--attempt", type=int, default=1)
    return parser.parse_args()


def implementation_components() -> list[dict[str, object]]:
    return [
        {
            "component": "Existing durable queue retry and dependency boundary",
            "files": [
                "src/evm/control_panel/admission_queue.py",
                "src/evm/control_panel/task_queue_worker.py",
                "src/evm/control_panel/transactional_store.py",
            ],
        },
        {
            "component": "Existing capacity runtime resource sampler",
            "files": ["src/evm/scale_validation/s3_runtime.py"],
        },
        {
            "component": "S8 external runtime and independent evidence validation",
            "files": [
                "src/evm/scale_validation/s8_runtime.py",
                "src/evm/scale_validation/s8_evidence.py",
                "scripts/dev/run_s8_dependency_soak_experiment.py",
                "scripts/dev/validate_s8_dependency_soak_evidence.py",
                "tests/test_s8_runtime.py",
                "tests/test_s8_evidence.py",
            ],
        },
        {
            "component": "S8 frozen execution and evidence contract",
            "files": [
                "configs/s8_dependency_soak_v4.toml",
                "configs/s8_soak_capacity_runtime.toml",
                "docs/status/2026-08-24-s8-design-reconciliation.md",
            ],
        },
    ]


def main() -> int:
    args = parse_args()
    if args.phase == "failure":
        return record_failed_attempt(args)
    if args.phase == "calibration":
        return record_calibration(args)
    if args.phase != "implementation":
        raise SystemExit("verification updates are produced by the S8 closure script")
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = json.loads(args.json_path.read_text(encoding="utf-8"))
    scenario = next(item for item in payload["scenarios"] if item["scenario_id"] == "S8")
    components = implementation_components()
    scenario["changed_components"] = components
    scenario["implementation_delta"]["modified_existing_components"] = components
    scenario["implementation_summary"] = [
        "The existing PostgreSQL durable queue now has an opt-in transient-dependency circuit with closed, open, and one-probe half-open states; the S2 profile remains disabled by default.",
        "The existing capacity runtime now samples process-tree open handles, queue gauges, and private artifact growth together with CPU and RSS.",
        "A frozen S8 profile binds 35 RPS to 70 percent of the accepted S3 sustainable rate, three 30-minute soak repetitions, isolated dependency faults, bounded retry, and deterministic cleanup.",
        "The S8 runner reuses the existing external task API, durable queue worker, HIGGS capacity API, PostgreSQL, Prometheus, and OTLP paths; the independent validator reprojects fault and soak outcomes from private raw artifacts and Git blob identities.",
    ]
    scenario["experiment_environment"] = (
        "Implementation checkpoint only. The accepted experiment will use external TCP/HTTP, "
        "isolated PostgreSQL schemas, the real bounded queue worker, a deterministic Airflow-compatible "
        "dependency, Prometheus, W3C OTLP, and the existing HIGGS capacity route on one local physical node."
    )
    scenario["observed_result"] = None
    scenario["evidence_artifacts"] = []
    scenario["evidence_index"] = []
    scenario["status"] = "implementing"
    scenario["claim_boundary"] = (
        "Controlled traffic on one local physical node and one CUDA device only. S8 does not prove "
        "customer production SLA, multi-node or multi-zone HA/DR, multi-GPU behavior, or simultaneous "
        "residency and execution of multiple GPU model families."
    )
    scenario["verdict_and_claim_boundary"]["verdict"] = "not_run"
    scenario["verdict_and_claim_boundary"]["claim_boundary"] = scenario["claim_boundary"]
    scenario["unresolved_items"] = [
        criterion["description"] for criterion in scenario["acceptance_criteria"]
    ]
    scenario["next_action"] = (
        "Run the isolated dependency-fault repetitions, then the 35 RPS 30-minute soak repetitions, "
        "recompute S8-AC-01..04 from raw evidence, and close only after regression and Git-blob validation."
    )
    update = {
        "occurred_at": now,
        "phase": "implementation",
        "status": "implementing",
        "summary": (
            "S8 implementation started at the S0-S7 verified gate with an opt-in dependency circuit, "
            "frozen 35 RPS soak contract, expanded resource sampling, external runner, independent "
            "mutation validator, and no acceptance credit yet."
        ),
        "evidence_refs": ["docs/status/2026-08-24-s8-design-reconciliation.md"],
    }
    scenario["chronological_updates"].append(update)
    payload["generated_at"] = now
    ledger = ScenarioProgressLedger.model_validate(payload)
    args.json_path.write_bytes((ledger.model_dump_json(indent=2) + "\n").encode("utf-8"))
    args.markdown_path.write_text(
        render_progress_markdown(ledger), encoding="utf-8", newline="\n"
    )
    return 0


def record_failed_attempt(args: argparse.Namespace) -> int:
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if args.attempt not in {1, 2, 3}:
        raise SystemExit("only retained S8 failed attempts 1 through 3 are supported")
    evidence_path = ROOT / f"docs/status/evidence/s8-dependency-soak-attempt-{args.attempt:02d}.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    payload = json.loads(args.json_path.read_text(encoding="utf-8"))
    scenario = next(item for item in payload["scenarios"] if item["scenario_id"] == "S8")
    components = implementation_components()
    scenario["changed_components"] = components
    scenario["implementation_delta"]["modified_existing_components"] = components
    claims = {
        1: (
            "Rejected infrastructure attempt: control repetitions 1 and 2 passed, "
            "repetition 3 hit a Docker host-port allocation race before admission; "
            "cleanup passed and no acceptance credit was awarded."
        ),
        2: (
            "Rejected contract/runtime attempt: nine fresh control, latency, and "
            "transient repetitions passed; retry-budget repetition 1 expired five "
            "intended DLQ items after runner/config drift, cleanup passed, soak did "
            "not start, and no acceptance credit was awarded."
        ),
        3: (
            "Rejected timeout-contract attempt: fifteen fault repetitions passed, "
            "then timeout repetition 1 retained two outcome-unknown items because "
            "the worker and HTTP timeouts shared a 10-second boundary; cleanup "
            "passed, soak did not start, and no acceptance credit was awarded."
        ),
    }
    claim = claims[args.attempt]
    artifact = {
        "path": evidence_path.relative_to(ROOT).as_posix(),
        "sha256": digest,
        "generated_at": str(evidence["generated_at"]),
        "claim": claim,
    }
    existing = [
        item
        for item in scenario["evidence_artifacts"]
        if item.get("path") != artifact["path"]
    ]
    scenario["evidence_artifacts"] = [*existing, artifact]
    scenario["evidence_index"] = [*existing, artifact]
    scenario["observed_result"] = None
    scenario["status"] = "implementing"
    scenario["verdict_and_claim_boundary"]["verdict"] = "not_run"
    scenario["next_action"] = (
        "Rerun the complete 21-repetition fault matrix from the v4 fail-closed timeout "
        "revision; begin soak only after every fresh fault repetition passes."
    )
    scenario["chronological_updates"].append(
        {
            "occurred_at": now,
            "phase": "experiment",
            "status": "implementing",
            "summary": (
                f"Attempt {args.attempt:02d} was rejected with zero acceptance credit. "
                + {
                    1: (
                        "An isolated Prometheus port-bind race occurred before control "
                        "repetition 3 admission; two controls passed and cleanup completed."
                    ),
                    2: (
                        "Nine control/latency/transient repetitions passed, then "
                        "retry-budget runner/config drift allowed five expiry outcomes; "
                        "cleanup completed and soak did not start."
                    ),
                    3: (
                        "Fifteen repetitions passed, then timeout repetition 1 retained "
                        "two outcome-unknown items at an equal worker/HTTP timeout boundary; "
                        "cleanup completed and soak did not start."
                    ),
                }[args.attempt]
            ),
            "evidence_refs": [
                artifact["path"],
                f"docs/status/2026-08-24-s8-attempt-{args.attempt:02d}-rca.md",
            ],
        }
    )
    payload["generated_at"] = now
    ledger = ScenarioProgressLedger.model_validate(payload)
    args.json_path.write_bytes((ledger.model_dump_json(indent=2) + "\n").encode("utf-8"))
    args.markdown_path.write_text(
        render_progress_markdown(ledger), encoding="utf-8", newline="\n"
    )
    return 0


def record_calibration(args: argparse.Namespace) -> int:
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    evidence_path = ROOT / "docs/status/evidence/s8-retry-budget-v2-calibration.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    artifact = {
        "path": evidence_path.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "generated_at": str(evidence["generated_at"]),
        "claim": (
            "Zero-credit retry-budget calibration closed 17/17 tasks with 12 DLQ, "
            "5 completed, duplicate effects zero, and cleanup complete; its 40.109-second "
            "fault recovery justified freezing the pre-acceptance v3 MTTR bound at 60 seconds."
        ),
    }
    payload = json.loads(args.json_path.read_text(encoding="utf-8"))
    scenario = next(item for item in payload["scenarios"] if item["scenario_id"] == "S8")
    existing = [
        item
        for item in scenario["evidence_artifacts"]
        if item.get("path") != artifact["path"]
    ]
    scenario["evidence_artifacts"] = [*existing, artifact]
    scenario["evidence_index"] = [*existing, artifact]
    scenario["observed_result"] = None
    scenario["status"] = "implementing"
    scenario["verdict_and_claim_boundary"]["verdict"] = "not_run"
    scenario["next_action"] = (
        "Run all 21 fault repetitions from the frozen v3 revision with no pilot reuse; "
        "begin the three 30-minute soak repetitions only after that gate passes."
    )
    scenario["chronological_updates"].append(
        {
            "occurred_at": now,
            "phase": "implementation",
            "status": "implementing",
            "summary": (
                "A zero-credit v2 retry-budget calibration passed terminal/effect/cleanup "
                "invariants and measured 40.109 seconds to recovery. The v3 profile freezes "
                "a 60-second MTTR bound before any acceptance run."
            ),
            "evidence_refs": [artifact["path"], "docs/status/2026-08-24-s8-design-reconciliation.md"],
        }
    )
    payload["generated_at"] = now
    ledger = ScenarioProgressLedger.model_validate(payload)
    args.json_path.write_bytes((ledger.model_dump_json(indent=2) + "\n").encode("utf-8"))
    args.markdown_path.write_text(
        render_progress_markdown(ledger), encoding="utf-8", newline="\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
