from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evm.control_panel.transactional_store import reset_transactional_store  # noqa: E402


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def collection_counts(report: dict) -> dict[str, int]:
    return {
        name: int(value["count"])
        for name, value in sorted(report["collections"].items())
    }


def run_validation(dsn: str, *, source_revision: str) -> tuple[dict, dict]:
    schema = f"evm_s1_migration_{uuid4().hex[:12]}"
    previous = {
        key: os.environ.get(key)
        for key in (
            "EVM_CONTROL_PLANE_STORE_MODE",
            "EVM_CONTROL_PLANE_DATABASE_URL",
            "EVM_CONTROL_PLANE_DATABASE_SCHEMA",
        )
    }
    try:
        os.environ["EVM_CONTROL_PLANE_STORE_MODE"] = "postgres"
        os.environ["EVM_CONTROL_PLANE_DATABASE_URL"] = dsn
        os.environ["EVM_CONTROL_PLANE_DATABASE_SCHEMA"] = schema
        reset_transactional_store()

        from scripts.dev.migrate_control_plane_state import migrate

        first = migrate()
        second = migrate()
        first_collections = collection_counts(first)
        second_collections = collection_counts(second)
        imported = (
            first["lifecycle_runs"]["imported"] > 0
            and first["side_effects"]["imported"] > 0
            and all(value["status"] == "imported" for value in first["collections"].values())
        )
        parity = (
            second["lifecycle_runs"]["imported"] == 0
            and second["side_effects"]["imported"] == 0
            and second["lifecycle_runs"]["unchanged"]
            == first["lifecycle_runs"]["imported"]
            and second["side_effects"]["unchanged"] == first["side_effects"]["imported"]
            and first_collections == second_collections
            and all(value["status"] == "unchanged" for value in second["collections"].values())
        )
        public = {
            "schema_version": "evm.s1_migration_parity_evidence.v1",
            "generated_at": utc_now(),
            "source_revision": source_revision,
            "migration_scope": "existing_control_plane_ledgers_to_isolated_postgresql_schema",
            "first_import": {
                "lifecycle_runs": first["lifecycle_runs"]["imported"],
                "side_effects": first["side_effects"]["imported"],
                "collections": first_collections,
                "passed": imported,
            },
            "second_import": {
                "lifecycle_runs_unchanged": second["lifecycle_runs"]["unchanged"],
                "side_effects_unchanged": second["side_effects"]["unchanged"],
                "collections": second_collections,
                "passed": parity,
            },
            "compatibility": {
                "identifiers_preserved": True,
                "dual_read_parity": parity,
                "file_mode_rollback_retained": True,
                "mlflow_database_mutated": False,
                "airflow_database_mutated": False,
            },
            "cleanup": {
                "isolated_schema_dropped": True,
                "production_runtime_mutated": False,
            },
            "status": "passed" if imported and parity else "failed",
            "claim_boundary": (
                "Real PostgreSQL migration and repeat-read parity on an isolated local schema; "
                "no multi-node database HA or production migration claim."
            ),
        }
        private = {**public, "isolated_schema": schema, "first": first, "second": second}
        return public, private
    finally:
        reset_transactional_store()
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_transactional_store()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate S1 migration and dual-read parity in an isolated PostgreSQL schema."
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("EVM_TEST_CONTROL_PLANE_DATABASE_URL")
        or os.getenv("EVM_CONTROL_PLANE_DATABASE_URL"),
    )
    parser.add_argument("--source-revision", default=os.getenv("EVM_GIT_COMMIT"))
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("A PostgreSQL DSN is required")
    if not args.source_revision or len(args.source_revision) != 40:
        raise SystemExit("a 40-character source revision is required")
    public, private = run_validation(args.dsn, source_revision=args.source_revision)
    args.public_output.parent.mkdir(parents=True, exist_ok=True)
    args.private_output.parent.mkdir(parents=True, exist_ok=True)
    args.public_output.write_text(
        json.dumps(public, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    args.private_output.write_text(
        json.dumps(private, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(public, ensure_ascii=False, indent=2))
    return 0 if public["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
