from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fp:
        return tomllib.load(fp)


def _check_path(path: Path, label: str) -> Check:
    if path.exists():
        return Check(label, "pass", str(path))
    return Check(label, "fail", f"missing: {path}")


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _check_dataset_metadata(path: Path) -> tuple[Check, dict[str, Any]]:
    if not path.exists():
        return Check("dataset metadata", "fail", f"missing: {path}"), {}

    payload = _read_json(path)
    required = [
        "dataset_version",
        "validated_parquet_uri",
        "record_count",
        "trace",
    ]
    missing = [key for key in required if not payload.get(key)]
    if missing:
        return Check("dataset metadata", "fail", f"missing keys: {', '.join(missing)}"), payload

    trace_id = _nested(payload, "trace", "trace_id")
    git_commit = _nested(payload, "trace", "git_commit")
    if not trace_id or not git_commit:
        return Check("dataset metadata", "fail", "trace_id or git_commit is missing"), payload

    parquet_uri = str(payload["validated_parquet_uri"])
    if not parquet_uri.startswith("s3://validated/"):
        return Check(
            "dataset metadata",
            "fail",
            f"validated_parquet_uri is not in validated bucket: {parquet_uri}",
        ), payload

    return Check(
        "dataset metadata",
        "pass",
        f"{payload['dataset_version']} records={payload['record_count']}",
    ), payload


def _check_registry_metadata(path: Path, dataset: dict[str, Any]) -> tuple[Check, dict[str, Any]]:
    if not path.exists():
        return Check("registry latest", "fail", f"missing: {path}"), {}

    payload = _read_json(path)
    required = ["model_name", "version", "stage", "source_model", "trace"]
    missing = [key for key in required if not payload.get(key)]
    if missing:
        return Check("registry latest", "fail", f"missing keys: {', '.join(missing)}"), payload

    registry_dataset_version = _nested(payload, "source_model", "dataset", "dataset_version")
    expected_dataset_version = dataset.get("dataset_version")
    if expected_dataset_version and registry_dataset_version != expected_dataset_version:
        return Check(
            "registry latest",
            "fail",
            (
                "dataset mismatch "
                f"registry={registry_dataset_version} dataset={expected_dataset_version}"
            ),
        ), payload

    registry_parquet_uri = _nested(payload, "source_model", "dataset", "validated_parquet_uri")
    expected_parquet_uri = dataset.get("validated_parquet_uri")
    if expected_parquet_uri and registry_parquet_uri != expected_parquet_uri:
        return Check(
            "registry latest",
            "fail",
            "validated_parquet_uri mismatch between registry and dataset metadata",
        ), payload

    return Check(
        "registry latest",
        "pass",
        f"{payload['model_name']} v{payload['version']} stage={payload['stage']}",
    ), payload


def _check_workers(path: Path) -> Check:
    if not path.exists():
        return Check("worker config", "fail", f"missing: {path}")

    payload = _read_toml(path)
    workers = payload.get("workers", {})
    mac = workers.get("ruma_macmini", {})
    if not mac:
        return Check("worker config", "fail", "ruma_macmini worker is not defined")

    required = ["ssh_user", "ssh_key_path", "remote_exec_probe", "roles"]
    missing = [key for key in required if not mac.get(key)]
    if missing:
        return Check("worker config", "fail", f"ruma_macmini missing: {', '.join(missing)}")

    roles = mac.get("roles", [])
    return Check("worker config", "pass", f"ruma_macmini roles={len(roles)}")


def _check_serving_gap(path: Path) -> Check:
    if not path.exists():
        return Check("serving gap", "fail", f"missing: {path}")

    source = path.read_text(encoding="utf-8")
    if "placeholder=True" in source and "@app.post(\"/predict\"" in source:
        return Check(
            "serving gap",
            "warn",
            "predict endpoint is still placeholder, which is expected before EVM-053",
        )
    return Check("serving gap", "pass", "predict endpoint no longer has placeholder=True")


def _check_w3_status(path: Path) -> Check:
    if not path.exists():
        return Check("w3 backlog state", "fail", f"missing: {path}")

    source = path.read_text(encoding="utf-8")
    planned_ids = [
        "EVM-041",
        "EVM-042",
        "EVM-044",
        "EVM-045",
        "EVM-051",
        "EVM-052",
        "EVM-053",
        "EVM-054",
        "EVM-055",
    ]
    missing = [item for item in planned_ids if item not in source]
    if missing:
        return Check("w3 backlog state", "fail", f"missing ids: {', '.join(missing)}")
    return Check("w3 backlog state", "pass", "W3 issue ids are present in issue register")


def run(root: Path) -> dict[str, Any]:
    checks: list[Check] = []

    config_path = root / "configs" / "local.toml"
    workers_path = root / "configs" / "workers.toml"
    dataset_path = root / "data" / "validated" / "dataset_version.json"
    registry_path = root / "artifacts" / "registry" / "vision-baseline" / "latest.json"
    api_path = root / "apps" / "api" / "main.py"
    issue_register_path = root / "docs" / "issues" / "issue-register.md"

    checks.append(_check_path(config_path, "local config"))
    dag_path = root / "orchestration" / "airflow" / "dags" / "enterprise_vision_mlops_daily.py"
    checks.append(_check_path(dag_path, "airflow dag"))
    dataset_check, dataset = _check_dataset_metadata(dataset_path)
    checks.append(dataset_check)
    registry_check, registry = _check_registry_metadata(registry_path, dataset)
    checks.append(registry_check)
    checks.append(_check_workers(workers_path))
    checks.append(_check_serving_gap(api_path))
    checks.append(_check_w3_status(issue_register_path))

    status_counts: dict[str, int] = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks:
        status_counts[check.status] += 1

    ready = status_counts["fail"] == 0
    return {
        "ready_for_w3_start": ready,
        "status_counts": status_counts,
        "checks": [check.__dict__ for check in checks],
        "handoff": {
            "dataset_version": dataset.get("dataset_version", ""),
            "validated_parquet_uri": dataset.get("validated_parquet_uri", ""),
            "registry_model_name": registry.get("model_name", ""),
            "registry_version": registry.get("version", ""),
            "registry_stage": registry.get("stage", ""),
        },
    }


def _format_text(result: dict[str, Any]) -> str:
    lines = [
        f"ready_for_w3_start={result['ready_for_w3_start']}",
        f"status_counts={result['status_counts']}",
        "",
        "Checks:",
    ]
    for check in result["checks"]:
        lines.append(f"- [{check['status']}] {check['name']}: {check['detail']}")
    lines.extend(["", "Handoff:"])
    for key, value in result["handoff"].items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate W3 handoff readiness without starting W3."
    )
    parser.add_argument("--root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    result = run(root)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_format_text(result))
    return 0 if result["ready_for_w3_start"] else 1


if __name__ == "__main__":
    sys.exit(main())
