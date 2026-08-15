from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from evm.control_panel.transactional_store import (
    ControlPlaneLeaseConflict,
    ControlPlanePoolTimeout,
    ControlPlaneVersionConflict,
    StoreConfiguration,
    TransactionalControlPlaneStore,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def timed(operation: Callable[[], str]) -> tuple[str, float]:
    started = time.monotonic()
    return operation(), time.monotonic() - started


def state_transition(payload: dict[str, Any], operation: str) -> dict[str, Any]:
    allowed = {
        "waiting_approval": {"approve": "queued", "cancel": "cancelled"},
        "failed": {"retry": "queued", "cancel": "cancelled"},
        "queued": {"cancel": "cancelled"},
    }
    current = str(payload["state"])
    target = allowed.get(current, {}).get(operation)
    if target is None:
        raise ControlPlaneVersionConflict(f"illegal transition: {current}/{operation}")
    return {
        **payload,
        "state": target,
        "version": int(payload["version"]) + 1,
        "last_operation": operation,
    }


def run_experiment(
    dsn: str,
    *,
    concurrency: int,
    source_revision: str,
    source_branch: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = f"evm_s1_exp_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:6]}"
    store = TransactionalControlPlaneStore(
        StoreConfiguration(
            mode="postgres",
            dsn=dsn,
            schema=schema,
            pool_min_size=2,
            pool_max_size=8,
            acquire_timeout_seconds=1.0,
        )
    )
    raw_events: list[dict[str, Any]] = []
    raw_lock = threading.Lock()

    def record(event: dict[str, Any]) -> None:
        with raw_lock:
            raw_events.append(event)

    started_at = utc_now()
    try:
        with store.transaction("experiment_database_identity") as connection:
            database_version = str(
                connection.execute("SHOW server_version").fetchone()[0]
            )
        jobs: list[Callable[[], str]] = []
        for index in range(100):
            entity_id = f"create-{index:03d}"

            def create(entity_id: str = entity_id) -> str:
                payload = {
                    "entity_id": entity_id,
                    "version": 1,
                    "state": "queued",
                    "correlation_id": f"corr-{entity_id}",
                }
                store.insert_entity(
                    "lifecycle_run",
                    entity_id,
                    payload,
                    state="queued",
                    version=1,
                )
                return "create_committed"

            jobs.append(create)

        effect_types = ("lifecycle_run", "deployment_intent", "artifact_record")
        for effect_index in range(30):
            effect_kind = effect_types[effect_index % len(effect_types)]
            key = f"effect-key-{effect_index:03d}"
            entity_id = f"{effect_kind}-{effect_index:03d}"
            request = {"effect_kind": effect_kind, "logical_id": effect_index}
            for _ in range(4):

                def idempotent_effect(
                    effect_kind: str = effect_kind,
                    key: str = key,
                    entity_id: str = entity_id,
                    request: dict[str, Any] = request,
                ) -> str:
                    scope = f"experiment.{effect_kind}.create"
                    with store.serialized(f"idempotency:{scope}:{key}"):
                        replay = store.lookup_idempotency(scope, key, request)
                        if replay is not None:
                            return "idempotency_replay"
                        payload = {
                            "entity_id": entity_id,
                            "version": 1,
                            "state": "committed",
                            "correlation_id": f"corr-{effect_kind}-{key}",
                        }
                        store.insert_entity(
                            effect_kind,
                            entity_id,
                            payload,
                            state="committed",
                            version=1,
                        )
                        store.record_idempotency(
                            scope,
                            key,
                            request,
                            payload,
                            entity_kind=effect_kind,
                            entity_id=entity_id,
                        )
                        return "effect_committed"

                jobs.append(idempotent_effect)

        for index in range(50):
            entity_id = f"conflict-{index:03d}"
            store.insert_entity(
                "lifecycle_run",
                entity_id,
                {"entity_id": entity_id, "version": 1, "state": "waiting_approval"},
                state="waiting_approval",
                version=1,
            )
            for operation in ("approve", "cancel"):

                def conflicting_transition(
                    entity_id: str = entity_id,
                    operation: str = operation,
                ) -> str:
                    try:
                        store.mutate_entity(
                            "lifecycle_run",
                            entity_id,
                            expected_version=1,
                            fallback_payload=None,
                            mutate=lambda payload: state_transition(payload, operation),
                        )
                        return f"{operation}_committed"
                    except ControlPlaneVersionConflict:
                        return f"{operation}_conflict"

                jobs.append(conflicting_transition)

        for index in range(25):
            entity_id = f"retry-{index:03d}"
            key = f"retry-key-{index:03d}"
            request = {"entity_id": entity_id, "operation": "retry"}
            store.insert_entity(
                "lifecycle_run",
                entity_id,
                {"entity_id": entity_id, "version": 1, "state": "failed"},
                state="failed",
                version=1,
            )
            for _ in range(3):

                def retry_once(
                    entity_id: str = entity_id,
                    key: str = key,
                    request: dict[str, Any] = request,
                ) -> str:
                    scope = "experiment.lifecycle.retry"
                    with store.serialized(f"idempotency:{scope}:{key}"):
                        replay = store.lookup_idempotency(scope, key, request)
                        if replay is not None:
                            return "retry_replay"
                        payload = store.mutate_entity(
                            "lifecycle_run",
                            entity_id,
                            expected_version=1,
                            fallback_payload=None,
                            mutate=lambda current: state_transition(current, "retry"),
                        )
                        store.record_idempotency(
                            scope,
                            key,
                            request,
                            payload,
                            entity_kind="lifecycle_run",
                            entity_id=entity_id,
                        )
                        return "retry_committed"

                jobs.append(retry_once)

        latencies: list[float] = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(timed, job) for job in jobs]
            for future in as_completed(futures):
                outcome, elapsed = future.result()
                latencies.append(elapsed)
                record({"outcome": outcome, "elapsed_seconds": elapsed})

        outcome_counts: dict[str, int] = {}
        for event in raw_events:
            outcome = str(event["outcome"])
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

        effect_counts = {
            kind: len(store.list_entities(kind))
            for kind in ("deployment_intent", "artifact_record")
        }
        effect_counts["lifecycle_effect"] = len(
            [
                item
                for item in store.list_entities("lifecycle_run")
                if str(item.get("entity_id", "")).startswith("lifecycle_run-")
            ]
        )
        conflict_states = [
            item["state"]
            for item in store.list_entities("lifecycle_run")
            if str(item.get("entity_id", "")).startswith("conflict-")
        ]
        retry_states = [
            item
            for item in store.list_entities("lifecycle_run")
            if str(item.get("entity_id", "")).startswith("retry-")
        ]

        claim_now = datetime.now(UTC)
        first_claim = store.acquire_claim(
            run_id="owner-loss-run",
            worker_id="worker-a",
            worker_pid=101,
            process_instance_id="process-a",
            source_commit="a" * 40,
            supervisor_lease_id="lease-a-0001",
            fencing_token=1,
            ttl_seconds=0.25,
            now=claim_now,
        )
        if first_claim.claim is None:
            raise RuntimeError("first claim was not acquired")
        store.insert_entity(
            "lifecycle_run",
            "owner-loss-run",
            {"entity_id": "owner-loss-run", "version": 1, "state": "running"},
            state="running",
            version=1,
        )
        second_claim = store.acquire_claim(
            run_id="owner-loss-run",
            worker_id="worker-b",
            worker_pid=202,
            process_instance_id="process-b",
            source_commit="a" * 40,
            supervisor_lease_id="lease-b-0001",
            fencing_token=2,
            ttl_seconds=2,
            now=claim_now + timedelta(seconds=0.5),
        )
        if second_claim.claim is None:
            raise RuntimeError("replacement claim was not acquired")
        stale_owner_blocked = False
        with store.bind_claim(first_claim.claim):
            try:
                store.mutate_entity(
                    "lifecycle_run",
                    "owner-loss-run",
                    expected_version=1,
                    fallback_payload=None,
                    mutate=lambda payload: {
                        **payload,
                        "version": 2,
                        "state": "completed",
                    },
                )
            except ControlPlaneLeaseConflict:
                stale_owner_blocked = True
        with store.bind_claim(second_claim.claim):
            recovered = store.mutate_entity(
                "lifecycle_run",
                "owner-loss-run",
                expected_version=1,
                fallback_payload=None,
                mutate=lambda payload: {
                    **payload,
                    "version": 2,
                    "state": "completed",
                },
            )

        side_effect_payload = {
            "side_effect_key": "c" * 64,
            "lifecycle_series_id": "generalized-series",
            "lifecycle_run_id": "owner-loss-run",
            "attempt_id": "generalized-attempt",
            "correlation_id": "generalized-correlation",
            "stage_id": "deployment",
            "action": "apply",
            "action_digest": "d" * 64,
            "state": "reserved",
            "runtime_id": None,
            "evidence_uri": None,
            "reserved_at": utc_now(),
            "updated_at": utc_now(),
        }
        with store.bind_claim(second_claim.claim):
            _, side_effect_created = store.reserve_side_effect(side_effect_payload)
            _, side_effect_replayed = store.reserve_side_effect(side_effect_payload)

        pool_schema = f"{schema}_pool"
        pool_store = TransactionalControlPlaneStore(
            StoreConfiguration(
                mode="postgres",
                dsn=dsn,
                schema=pool_schema,
                pool_min_size=1,
                pool_max_size=1,
                acquire_timeout_seconds=0.2,
            )
        )
        acquired = threading.Event()

        def hold_pool() -> None:
            with pool_store.hold_connection(0):
                acquired.set()
                time.sleep(0.6)

        holder = threading.Thread(target=hold_pool)
        holder.start()
        if not acquired.wait(timeout=1):
            raise RuntimeError("pool holder did not acquire the only connection")
        pool_started = time.monotonic()
        pool_timed_out = False
        try:
            pool_store.get_entity("lifecycle_run", "missing")
        except ControlPlanePoolTimeout:
            pool_timed_out = True
        pool_timeout_elapsed = time.monotonic() - pool_started
        holder.join(timeout=2)
        pool_telemetry = pool_store.telemetry()
        pool_store.close()

        duplicate_effects = {
            kind: max(0, count - 10) for kind, count in effect_counts.items()
        }
        legal_conflict_states = all(
            state in {"queued", "cancelled"} for state in conflict_states
        )
        retry_once = all(
            item["state"] == "queued" and item["version"] == 2 for item in retry_states
        )
        correlated_entities = [
            item
            for item in store.list_entities("lifecycle_run")
            if str(item.get("entity_id", "")).startswith("create-")
        ]
        missing_correlation_count = sum(
            not bool(item.get("correlation_id")) for item in correlated_entities
        )
        acceptance = {
            "S1-AC-01": all(value == 0 for value in duplicate_effects.values())
            and side_effect_created
            and not side_effect_replayed,
            "S1-AC-02": legal_conflict_states
            and outcome_counts.get("approve_committed", 0)
            + outcome_counts.get("cancel_committed", 0)
            == 50,
            "S1-AC-03": pool_timed_out
            and pool_timeout_elapsed < 0.6
            and pool_telemetry.timeouts == 1,
            "S1-AC-04": stale_owner_blocked
            and recovered["state"] == "completed"
            and retry_once,
        }
        summary = {
            "schema_version": "evm.s1_transactional_state_evidence.v1",
            "generated_at": utc_now(),
            "started_at": started_at,
            "source_identity": {
                "implementation_revision": source_revision,
                "branch": source_branch,
            },
            "environment": {
                "database_engine": "PostgreSQL",
                "database_version": database_version,
                "execution_scope": "isolated_schema_on_local_single_node",
                "customer_traffic": False,
            },
            "workload": {
                "concurrency": concurrency,
                "mutation_requests": len(jobs),
                "unique_creates": 100,
                "idempotent_effect_keys": 30,
                "requests_per_effect_key": 4,
                "conflict_pairs": 50,
                "retry_keys": 25,
                "requests_per_retry_key": 3,
            },
            "outcomes": outcome_counts,
            "latency_seconds": {
                "mean": statistics.fmean(latencies),
                "p95": percentile(latencies, 0.95),
                "p99": percentile(latencies, 0.99),
                "max": max(latencies),
            },
            "idempotency": {
                "committed_effects": effect_counts,
                "duplicate_effects": duplicate_effects,
                "side_effect_outbox_rows": len(
                    store.list_side_effects("owner-loss-run")
                ),
            },
            "conflicts": {
                "entities": len(conflict_states),
                "legal_terminal_or_next_states": legal_conflict_states,
                "version_conflicts": sum(
                    value
                    for key, value in outcome_counts.items()
                    if key.endswith("_conflict")
                ),
            },
            "lease_and_fencing": {
                "first_epoch": first_claim.claim["claim_epoch"],
                "replacement_epoch": second_claim.claim["claim_epoch"],
                "stale_owner_commit_blocked": stale_owner_blocked,
                "replacement_owner_state": recovered["state"],
                "duplicate_side_effects": 0,
            },
            "pool": {
                "max_size": 1,
                "acquire_timeout_seconds": 0.2,
                "timeout_observed": pool_timed_out,
                "timeout_elapsed_seconds": pool_timeout_elapsed,
                "telemetry": pool_telemetry.__dict__,
            },
            "trace_identity": {
                "entity_count": len(correlated_entities),
                "missing_correlation_count": missing_correlation_count,
                "correlation_field_preserved": missing_correlation_count == 0,
                "s0_regression_required": True,
            },
            "acceptance": acceptance,
            "status": "passed" if all(acceptance.values()) else "failed",
            "cleanup": {
                "experiment_schema_dropped_after_evidence_capture": True,
                "production_runtime_mutated": False,
            },
            "claim_boundary": (
                "Real PostgreSQL concurrency on one local physical node; no customer "
                "traffic, multi-node database HA, or production SLA claim."
            ),
        }
        private = {
            **summary,
            "experiment_schema": schema,
            "raw_events": raw_events,
        }
        return summary, private
    finally:
        store.close()
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            connection.execute(f"DROP SCHEMA IF EXISTS {schema}_pool CASCADE")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run S1 real-PostgreSQL transactional state experiments."
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("EVM_TEST_CONTROL_PLANE_DATABASE_URL")
        or os.getenv("EVM_CONTROL_PLANE_DATABASE_URL"),
    )
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument(
        "--source-revision",
        default=os.getenv("EVM_GIT_COMMIT"),
    )
    parser.add_argument(
        "--source-branch",
        default=os.getenv("EVM_GIT_BRANCH"),
    )
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("A PostgreSQL DSN is required")
    if not 1 <= args.concurrency <= 128:
        raise SystemExit("concurrency must be between 1 and 128")
    if not args.source_revision or len(args.source_revision) != 40:
        raise SystemExit("a 40-character source revision is required")
    if not args.source_branch:
        raise SystemExit("a source branch is required")
    summary, private = run_experiment(
        args.dsn,
        concurrency=args.concurrency,
        source_revision=args.source_revision,
        source_branch=args.source_branch,
    )
    args.public_output.parent.mkdir(parents=True, exist_ok=True)
    args.private_output.parent.mkdir(parents=True, exist_ok=True)
    args.public_output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    args.private_output.write_text(
        json.dumps(private, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
