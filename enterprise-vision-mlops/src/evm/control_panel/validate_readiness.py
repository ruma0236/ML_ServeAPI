from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evm.control_panel.readiness_evaluator import category_status, readiness_input_digest
from evm.control_panel.schemas import ArtifactReadinessEvaluation, CycleRun
from evm.core.http import request_json


def read_cycle(source: str) -> dict[str, Any]:
    if source.startswith(("http://", "https://")):
        status, payload = request_json("GET", source, timeout=10)
        if status != 200 or not isinstance(payload, dict):
            raise ValueError(f"cycle endpoint returned HTTP {status}: {source}")
        return payload
    payload = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"cycle payload must be an object: {source}")
    return payload


def validate_evaluation(evaluation: ArtifactReadinessEvaluation) -> dict[str, Any]:
    input_digest = readiness_input_digest(evaluation.checks)
    blockers = sorted({blocker for check in evaluation.checks for blocker in check.blockers})
    decision = "ready" if not blockers else "blocked"
    expected_status = "pass" if decision == "ready" else "blocked"
    expected_id = f"readiness-{input_digest[:16]}"
    violations: list[str] = []
    if evaluation.input_digest != input_digest:
        violations.append("input_digest_mismatch")
    if evaluation.evaluation_id != expected_id:
        violations.append("evaluation_id_mismatch")
    if evaluation.blockers != blockers:
        violations.append("blocker_set_mismatch")
    if evaluation.decision != decision:
        violations.append("decision_mismatch")
    if evaluation.status != expected_status:
        violations.append("status_mismatch")
    for category, reported in (
        ("data", evaluation.data_status),
        ("model", evaluation.model_status),
        ("runtime", evaluation.runtime_status),
    ):
        if reported != category_status(evaluation.checks, category):
            violations.append(f"{category}_status_mismatch")
    if violations:
        raise ValueError(",".join(violations))
    return {
        "valid": True,
        "schema_version": evaluation.schema_version,
        "evaluation_id": evaluation.evaluation_id,
        "decision": evaluation.decision,
        "candidate_id": evaluation.candidate_id,
        "dataset_version": evaluation.dataset_version,
        "input_digest": evaluation.input_digest,
        "check_count": len(evaluation.checks),
        "blocker_count": len(evaluation.blockers),
        "blockers": evaluation.blockers,
        "evaluation": evaluation.model_dump(mode="json"),
    }


def validate_cycle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cycle = CycleRun.model_validate(payload)
    if cycle.readiness_evaluation is None:
        raise ValueError("CycleRun.readiness_evaluation is missing")
    report = validate_evaluation(cycle.readiness_evaluation)
    report["cycle_id"] = cycle.cycle_id
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate EVM-236 artifact readiness evidence.")
    parser.add_argument("--cycle", required=True, help="CycleRun JSON file or HTTP endpoint")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()

    exit_code = 0
    try:
        report = validate_cycle_payload(read_cycle(args.cycle))
        if args.require_ready and report["decision"] != "ready":
            report["valid"] = False
            report["error"] = "readiness decision is blocked"
            exit_code = 2
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        report = {"valid": False, "cycle": args.cycle, "error": str(exc)}
        exit_code = 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    stream = sys.stdout if exit_code == 0 else sys.stderr
    print(json.dumps(report, ensure_ascii=False), file=stream)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
