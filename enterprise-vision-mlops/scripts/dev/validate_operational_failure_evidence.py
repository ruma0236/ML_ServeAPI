from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from evm.operations.failure_evidence import OperationalFailureReport, validate_closure


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate operational failure evidence and its requested closure.",
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--require-closure",
        choices=("readiness", "live_proof"),
        default="live_proof",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = OperationalFailureReport.model_validate_json(
            args.report.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=True))
        return 2

    errors = validate_closure(report, args.require_closure)
    result = {
        "schema_version": report.schema_version,
        "run_id": report.run_id,
        "scenario_id": report.scenario_id,
        "required_closure": args.require_closure,
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
