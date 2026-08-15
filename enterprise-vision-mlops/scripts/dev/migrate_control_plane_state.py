from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evm.control_panel.transactional_store import (
    canonical_digest,
    get_transactional_store,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def lifecycle_root() -> Path:
    return Path(
        os.getenv(
            "EVM_LIFECYCLE_RUN_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_runs",
        )
    )


def operations_root() -> Path:
    return Path(
        os.getenv(
            "EVM_CONTROL_PANEL_LEDGER_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/operations",
        )
    )


def deployment_root() -> Path:
    return Path(
        os.getenv(
            "EVM_DEPLOYMENT_INTENT_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/deployment_intents",
        )
    )


def migrate_collection(name: str, path: Path, report: dict[str, Any]) -> None:
    if not path.is_file():
        report["collections"][name] = {"status": "missing", "count": 0}
        return
    payload = read_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"collection {name} must be a JSON array")
    store = get_transactional_store()
    current = store.read_collection(name)
    if current is not None and canonical_digest(current) != canonical_digest(payload):
        raise ValueError(f"collection parity mismatch: {name}")
    status = "unchanged" if current is not None else "imported"
    if current is None:
        store.write_collection(name, payload)
    report["collections"][name] = {"status": status, "count": len(payload)}


def migrate() -> dict[str, Any]:
    store = get_transactional_store()
    if not store.enabled:
        raise RuntimeError("EVM_CONTROL_PLANE_STORE_MODE must be dual or postgres")
    report: dict[str, Any] = {
        "schema_version": "evm.control_plane_migration_report.v1",
        "generated_at": utc_now(),
        "mode": store.mode,
        "schema": store.configuration.schema,
        "lifecycle_runs": {"imported": 0, "unchanged": 0},
        "side_effects": {"imported": 0, "unchanged": 0},
        "collections": {},
        "claim_policy": "Legacy file claims are not imported; workers acquire fresh database leases.",
    }
    root = lifecycle_root()
    for path in sorted(root.glob("lifecycle-*/lifecycle_run.json")):
        payload = read_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"LifecycleRun is not a JSON object: {path.name}")
        run_id = str(payload["run_id"])
        status = store.import_entity(
            "lifecycle_run",
            run_id,
            payload,
            state=str(payload["state"]),
            version=int(payload["version"]),
        )
        report["lifecycle_runs"][status] += 1
        ledger_path = path.parent / "side_effect_ledger.json"
        if not ledger_path.is_file():
            continue
        ledger = read_json(ledger_path)
        entries = ledger.get("entries", []) if isinstance(ledger, dict) else []
        for entry in entries:
            persisted, created = store.reserve_side_effect(entry)
            if canonical_digest(persisted) != canonical_digest(entry):
                raise ValueError(f"side-effect parity mismatch: {entry['side_effect_key']}")
            report["side_effects"]["imported" if created else "unchanged"] += 1
    migrate_collection(
        "task_assignments",
        operations_root() / "task_assignments.json",
        report,
    )
    migrate_collection(
        "command_intents",
        operations_root() / "command_intents.json",
        report,
    )
    migrate_collection(
        "deployment_intents",
        deployment_root() / "deployment_intents.json",
        report,
    )
    report["pool_telemetry"] = store.telemetry().__dict__
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import existing control-plane JSON ledgers into PostgreSQL."
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = migrate()
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
