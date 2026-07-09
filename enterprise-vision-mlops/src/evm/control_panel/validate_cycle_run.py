from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evm.control_panel.schemas import CycleRun


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON: {path}")
    return payload


def openapi_component_required(openapi_path: Path, component: str) -> list[str]:
    openapi = read_json(openapi_path)
    schemas = openapi.get("components", {}).get("schemas", {})
    schema = schemas.get(component)
    if not isinstance(schema, dict):
        raise ValueError(f"OpenAPI component not found: {component}")
    required = schema.get("required", [])
    if not isinstance(required, list):
        return []
    return [str(item) for item in required]


def validate_cycle_run(payload: dict[str, Any], openapi_path: Path, component: str = "CycleRun") -> dict[str, Any]:
    required = openapi_component_required(openapi_path, component)
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError(f"{component} missing required fields: {', '.join(missing)}")
    cycle = CycleRun.model_validate(payload)
    return {
        "valid": True,
        "component": component,
        "required_fields": required,
        "cycle_id": cycle.cycle_id,
        "status": cycle.status,
        "stage_count": len(cycle.stages),
        "artifact_count": len(cycle.artifacts),
        "model_name": cycle.model.model_name,
        "model_version": cycle.model.version,
        "dataset_version": cycle.dataset.version,
    }


def default_report_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_schema_validation.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Control Panel CycleRun payload.")
    parser.add_argument("--openapi", required=True, type=Path)
    parser.add_argument("--component", default="CycleRun")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report_path = args.report or default_report_path(args.input)
    try:
        report = validate_cycle_run(read_json(args.input), args.openapi, args.component)
    except (ValueError, ValidationError) as exc:
        report = {
            "valid": False,
            "component": args.component,
            "input": str(args.input),
            "error": str(exc),
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
        return 1

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
