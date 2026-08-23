from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evm.control_panel.admission_queue import load_admission_queue_config
from evm.control_panel.operations import (
    sync_task_json_mirror_from_store,
    verify_task_json_mirror_parity,
)
from evm.control_panel.transactional_store import get_transactional_store


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an explicit UTC offset")
    return parsed.astimezone(UTC)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "evm.task_queue_stranded_snapshot.v1":
        raise ValueError("unsupported stranded task snapshot schema")
    return payload


def public_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "status",
            "snapshot_sha256",
            "candidate_count",
            "eligible_count",
            "blocked_count",
            "reconciled_count",
            "restored_count",
            "mirror_version",
        )
        if key in payload
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or reconcile effect-free pre-durable Airflow tasks."
    )
    parser.add_argument(
        "action",
        choices=("snapshot", "dry-run", "apply", "verify", "rollback"),
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--apply-report", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--cutoff", default="2026-08-03T00:00:00Z")
    parser.add_argument("--actor", default="task-queue-reconciliation")
    parser.add_argument(
        "--reason",
        default="historical_pre_durable_task_cancelled_without_external_effect",
    )
    parser.add_argument("--skip-file-mirror", action="store_true")
    args = parser.parse_args()

    store = get_transactional_store()
    if not store.enabled:
        raise RuntimeError("PostgreSQL control-plane mode is required")
    cutoff = parse_utc(args.cutoff)
    try:
        if args.action == "snapshot":
            result = store.inspect_stranded_task_queue(cutoff=cutoff)
            result["captured_at"] = utc_now()
            result["status"] = "snapshot_captured"
            write_json(args.snapshot, result)
        else:
            snapshot = read_snapshot(args.snapshot)
            if args.action in {"dry-run", "apply"}:
                result = store.reconcile_stranded_task_queue(
                    task_ids=[str(item["task_id"]) for item in snapshot["items"]],
                    cutoff=cutoff,
                    expected_snapshot_sha256=str(snapshot["snapshot_sha256"]),
                    actor=args.actor,
                    reason=args.reason,
                    dry_run=args.action == "dry-run",
                )
            elif args.action == "rollback":
                result = store.rollback_stranded_task_queue(
                    snapshot=snapshot,
                    actor=args.actor,
                    reason=args.reason,
                )
            else:
                if not args.apply_report:
                    raise ValueError("--apply-report is required for verification")
                applied = json.loads(args.apply_report.read_text(encoding="utf-8"))
                expected_count = len(snapshot["items"])
                if (
                    applied.get("status") != "applied"
                    or applied.get("snapshot_sha256") != snapshot["snapshot_sha256"]
                    or int(applied.get("reconciled_count", -1)) != expected_count
                ):
                    raise ValueError("apply report does not bind the exact snapshot")
                retained = [
                    store.get_entity("task_assignment", str(item["task_id"]))
                    for item in snapshot["items"]
                ]
                retained_cancelled = sum(
                    bool(item and item.get("status") == "cancelled") for item in retained
                )
                history = store.task_queue_history_snapshot()
                compacted_tasks = int(history.compacted_rows.get("task", 0))
                if retained_cancelled + compacted_tasks < expected_count:
                    raise ValueError("reconciled tasks are missing from authority and history")
                result = {
                    "status": "verification_passed",
                    "snapshot_sha256": snapshot["snapshot_sha256"],
                    "candidate_count": expected_count,
                    "reconciled_count": expected_count,
                    "retained_cancelled_count": retained_cancelled,
                    "compacted_task_count": compacted_tasks,
                    "history": asdict(history),
                }
            if args.action in {"apply", "rollback"} and not args.skip_file_mirror:
                sync_task_json_mirror_from_store()
                result["file_mirror_parity"] = verify_task_json_mirror_parity()
            if args.action == "dry-run":
                result["cutover"] = {
                    "status": "blocked_as_expected_before_apply",
                    "stranded_depth": int(result["candidate_count"]),
                }
            else:
                result["cutover"] = store.verify_task_queue_cutover(
                    mode="durable",
                    config=load_admission_queue_config(),
                )
            result["completed_at"] = utc_now()
            if args.report:
                write_json(args.report, result)
        print(json.dumps(public_summary(result), ensure_ascii=False, sort_keys=True))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
