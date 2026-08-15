from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Iterator, Mapping, Sequence
from uuid import uuid4

from prometheus_client import Counter, Histogram

from evm.control_panel.admission_queue import (
    ACTIVE_QUEUE_STATES,
    QUEUE_ADMISSION_WAIT_SECONDS,
    AdmissionQueueConfig,
    canonical_payload_size,
)


SCHEMA_VERSIONS = (
    "001_transactional_control_plane",
    "002_bounded_admission_queue",
)
CONTROL_PLANE_DB_POOL_ACQUIRE_SECONDS = Histogram(
    "evm_control_plane_db_pool_acquire_seconds",
    "Seconds spent acquiring a dedicated control-plane PostgreSQL connection.",
)
CONTROL_PLANE_DB_POOL_TIMEOUTS = Counter(
    "evm_control_plane_db_pool_timeouts_total",
    "Bounded control-plane PostgreSQL pool acquisition timeouts.",
)
CONTROL_PLANE_DB_VERSION_CONFLICTS = Counter(
    "evm_control_plane_db_version_conflicts_total",
    "Optimistic control-plane state version conflicts.",
)


class ControlPlaneStoreError(RuntimeError):
    """Base error for the transactional control-plane store."""


class ControlPlaneStoreUnavailable(ControlPlaneStoreError):
    pass


class ControlPlanePoolTimeout(ControlPlaneStoreError):
    pass


class ControlPlaneTransactionTimeout(ControlPlaneStoreError):
    pass


class ControlPlaneVersionConflict(ControlPlaneStoreError):
    pass


class ControlPlaneIdempotencyConflict(ControlPlaneStoreError):
    pass


class ControlPlaneLeaseConflict(ControlPlaneStoreError):
    pass


class ControlPlaneParityError(ControlPlaneStoreError):
    pass


class ControlPlaneItemTooLarge(ControlPlaneStoreError):
    def __init__(self, *, payload_bytes: int, max_item_bytes: int) -> None:
        super().__init__(
            f"canonical task payload is {payload_bytes} bytes; maximum is {max_item_bytes}"
        )
        self.payload_bytes = payload_bytes
        self.max_item_bytes = max_item_bytes


class ControlPlaneAdmissionRejected(ControlPlaneStoreError):
    def __init__(
        self,
        *,
        reason: str,
        retry_after_seconds: int,
        current_depth: int,
        current_bytes: int,
    ) -> None:
        super().__init__(f"task admission rejected because {reason}")
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds
        self.current_depth = current_depth
        self.current_bytes = current_bytes


@dataclass(frozen=True)
class StoreConfiguration:
    mode: str
    dsn: str | None
    schema: str
    pool_min_size: int
    pool_max_size: int
    acquire_timeout_seconds: float
    lock_timeout_seconds: float = 2.0
    statement_timeout_seconds: float = 10.0

    @property
    def enabled(self) -> bool:
        return self.mode in {"dual", "postgres"}

    @classmethod
    def from_env(cls) -> StoreConfiguration:
        mode = os.getenv("EVM_CONTROL_PLANE_STORE_MODE", "file").strip().lower()
        if mode not in {"file", "dual", "postgres"}:
            raise ControlPlaneStoreUnavailable(
                f"unsupported control-plane store mode: {mode}"
            )
        return cls(
            mode=mode,
            dsn=os.getenv("EVM_CONTROL_PLANE_DATABASE_URL") or None,
            schema=os.getenv("EVM_CONTROL_PLANE_DATABASE_SCHEMA", "evm_control_plane"),
            pool_min_size=int(os.getenv("EVM_CONTROL_PLANE_POOL_MIN_SIZE", "1")),
            pool_max_size=int(os.getenv("EVM_CONTROL_PLANE_POOL_MAX_SIZE", "8")),
            acquire_timeout_seconds=float(
                os.getenv("EVM_CONTROL_PLANE_POOL_ACQUIRE_TIMEOUT_SECONDS", "2")
            ),
            lock_timeout_seconds=float(
                os.getenv("EVM_CONTROL_PLANE_LOCK_TIMEOUT_SECONDS", "2")
            ),
            statement_timeout_seconds=float(
                os.getenv("EVM_CONTROL_PLANE_STATEMENT_TIMEOUT_SECONDS", "10")
            ),
        )


@dataclass(frozen=True)
class PoolTelemetrySnapshot:
    acquisitions: int
    timeouts: int
    wait_seconds_total: float
    wait_seconds_max: float


@dataclass(frozen=True)
class ClaimResult:
    acquired: bool
    reason: str
    claim: dict[str, Any] | None


@dataclass(frozen=True)
class TaskAdmissionResult:
    queue_id: str
    task_payload: dict[str, Any]
    payload_bytes: int
    replayed: bool


@dataclass(frozen=True)
class TaskQueueSnapshot:
    active_depth: int
    active_bytes: int
    oldest_age_seconds: float
    state_counts: dict[str, int]
    state_bytes: dict[str, int]


@dataclass(frozen=True)
class TaskQueueLease:
    queue_id: str
    task_id: str
    task_payload: dict[str, Any]
    payload_bytes: int
    attempt_count: int
    lease_owner: str
    lease_epoch: int
    lease_expires_at: str
    deadline_at: str


_BOUND_CONNECTION: ContextVar[Any | None] = ContextVar(
    "evm_control_plane_connection", default=None
)
_BOUND_CLAIM: ContextVar[dict[str, Any] | None] = ContextVar(
    "evm_control_plane_claim", default=None
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _advisory_key(scope: str) -> int:
    raw = int.from_bytes(hashlib.sha256(scope.encode("utf-8")).digest()[:8], "big")
    return raw if raw < 2**63 else raw - 2**64


def _safe_identifier(value: str) -> str:
    if not value or not value.replace("_", "").isalnum():
        raise ControlPlaneStoreUnavailable(f"invalid PostgreSQL identifier: {value!r}")
    return value


class TransactionalControlPlaneStore:
    """PostgreSQL authority for mutable control-plane state.

    The existing JSON ledgers remain outside this class as dual-write mirrors and
    rollback inputs. Every method here either joins an existing transaction scope
    or opens a bounded transaction of its own.
    """

    def __init__(self, configuration: StoreConfiguration | None = None) -> None:
        self.configuration = configuration or StoreConfiguration.from_env()
        self._pool: Any | None = None
        self._jsonb: Any | None = None
        self._pool_timeout_type: type[BaseException] = TimeoutError
        self._transaction_timeout_types: tuple[type[BaseException], ...] = ()
        self._telemetry_lock = threading.Lock()
        self._acquisitions = 0
        self._timeouts = 0
        self._wait_seconds_total = 0.0
        self._wait_seconds_max = 0.0
        if self.configuration.enabled:
            self._open()

    @property
    def enabled(self) -> bool:
        return self.configuration.enabled

    @property
    def mode(self) -> str:
        return self.configuration.mode

    def _open(self) -> None:
        if not self.configuration.dsn:
            raise ControlPlaneStoreUnavailable(
                "EVM_CONTROL_PLANE_DATABASE_URL is required in dual/postgres mode"
            )
        if self.configuration.pool_min_size < 0:
            raise ControlPlaneStoreUnavailable("pool_min_size must be non-negative")
        if self.configuration.pool_max_size < 1:
            raise ControlPlaneStoreUnavailable("pool_max_size must be positive")
        if self.configuration.pool_min_size > self.configuration.pool_max_size:
            raise ControlPlaneStoreUnavailable("pool_min_size cannot exceed pool_max_size")
        if self.configuration.acquire_timeout_seconds <= 0:
            raise ControlPlaneStoreUnavailable("pool acquire timeout must be positive")
        if self.configuration.lock_timeout_seconds <= 0:
            raise ControlPlaneStoreUnavailable("lock timeout must be positive")
        if self.configuration.statement_timeout_seconds <= 0:
            raise ControlPlaneStoreUnavailable("statement timeout must be positive")
        try:
            from psycopg.errors import LockNotAvailable, QueryCanceled
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
            from psycopg_pool import ConnectionPool, PoolTimeout
        except ImportError as exc:
            raise ControlPlaneStoreUnavailable(
                "psycopg and psycopg-pool are required in dual/postgres mode"
            ) from exc
        self._jsonb = Jsonb
        self._pool_timeout_type = PoolTimeout
        self._transaction_timeout_types = (LockNotAvailable, QueryCanceled)
        self._pool = ConnectionPool(
            conninfo=self.configuration.dsn,
            min_size=self.configuration.pool_min_size,
            max_size=self.configuration.pool_max_size,
            timeout=self.configuration.acquire_timeout_seconds,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        self.ensure_schema()

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def telemetry(self) -> PoolTelemetrySnapshot:
        with self._telemetry_lock:
            return PoolTelemetrySnapshot(
                acquisitions=self._acquisitions,
                timeouts=self._timeouts,
                wait_seconds_total=self._wait_seconds_total,
                wait_seconds_max=self._wait_seconds_max,
            )

    @contextmanager
    def _acquire(self, operation: str) -> Iterator[Any]:
        del operation
        if self._pool is None:
            raise ControlPlaneStoreUnavailable("transactional store is disabled")
        started = time.monotonic()
        try:
            with self._pool.connection(
                timeout=self.configuration.acquire_timeout_seconds
            ) as connection:
                waited = time.monotonic() - started
                with self._telemetry_lock:
                    self._acquisitions += 1
                    self._wait_seconds_total += waited
                    self._wait_seconds_max = max(self._wait_seconds_max, waited)
                CONTROL_PLANE_DB_POOL_ACQUIRE_SECONDS.observe(waited)
                yield connection
        except self._pool_timeout_type as exc:
            waited = time.monotonic() - started
            with self._telemetry_lock:
                self._timeouts += 1
                self._wait_seconds_total += waited
                self._wait_seconds_max = max(self._wait_seconds_max, waited)
            CONTROL_PLANE_DB_POOL_ACQUIRE_SECONDS.observe(waited)
            CONTROL_PLANE_DB_POOL_TIMEOUTS.inc()
            raise ControlPlanePoolTimeout(
                "control-plane database connection acquisition timed out"
            ) from exc
        except self._transaction_timeout_types as exc:
            raise ControlPlaneTransactionTimeout(
                "control-plane database transaction exceeded its bounded wait"
            ) from exc

    @contextmanager
    def transaction(self, operation: str) -> Iterator[Any]:
        existing = _BOUND_CONNECTION.get()
        if existing is not None:
            yield existing
            return
        with self._acquire(operation) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('lock_timeout', %s, true)",
                    (f"{self.configuration.lock_timeout_seconds}s",),
                )
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{self.configuration.statement_timeout_seconds}s",),
                )
                token = _BOUND_CONNECTION.set(connection)
                try:
                    yield connection
                finally:
                    _BOUND_CONNECTION.reset(token)

    @contextmanager
    def serialized(self, scope: str) -> Iterator[Any]:
        existing = _BOUND_CONNECTION.get()
        if existing is not None:
            existing.execute("SELECT pg_advisory_xact_lock(%s)", (_advisory_key(scope),))
            yield existing
            return
        with self.transaction(f"serialized:{scope}") as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_advisory_key(scope),))
            yield connection

    @contextmanager
    def bind_claim(self, claim: Mapping[str, Any]) -> Iterator[None]:
        token = _BOUND_CLAIM.set(dict(claim))
        try:
            yield
        finally:
            _BOUND_CLAIM.reset(token)

    @contextmanager
    def hold_connection(self, seconds: float) -> Iterator[None]:
        """Test hook for a real bounded-pool acquisition experiment."""
        with self._acquire("test_hold"):
            yield
            if seconds > 0:
                time.sleep(seconds)

    def ensure_schema(self) -> None:
        schema = _safe_identifier(self.configuration.schema)
        statements = _schema_statements(schema)
        with self.transaction("schema_migration") as connection:
            for statement in statements:
                connection.execute(statement)
            for version in SCHEMA_VERSIONS:
                connection.execute(
                    f"""
                    INSERT INTO {schema}.schema_migrations(version)
                    VALUES (%s)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    (version,),
                )

    def get_entity(self, entity_kind: str, entity_id: str) -> dict[str, Any] | None:
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("entity_read") as connection:
            row = connection.execute(
                f"SELECT payload FROM {schema}.entities WHERE entity_kind=%s AND entity_id=%s",
                (entity_kind, entity_id),
            ).fetchone()
        return dict(row["payload"]) if row else None

    def list_entities(self, entity_kind: str) -> list[dict[str, Any]]:
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("entity_list") as connection:
            rows = connection.execute(
                f"""
                SELECT payload FROM {schema}.entities
                WHERE entity_kind=%s
                ORDER BY created_at DESC, entity_id DESC
                """,
                (entity_kind,),
            ).fetchall()
        return [dict(row["payload"]) for row in rows]

    def insert_entity(
        self,
        entity_kind: str,
        entity_id: str,
        payload: Mapping[str, Any],
        *,
        state: str,
        version: int,
    ) -> None:
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("entity_insert") as connection:
            try:
                connection.execute(
                    f"""
                    INSERT INTO {schema}.entities
                        (entity_kind, entity_id, version, state, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (entity_kind, entity_id, version, state, self._json(payload)),
                )
            except Exception as exc:
                if getattr(exc, "sqlstate", None) == "23505":
                    raise ControlPlaneVersionConflict(
                        f"{entity_kind}/{entity_id} already exists"
                    ) from exc
                raise

    def import_entity(
        self,
        entity_kind: str,
        entity_id: str,
        payload: Mapping[str, Any],
        *,
        state: str,
        version: int,
    ) -> str:
        schema = _safe_identifier(self.configuration.schema)
        with self.serialized(f"import:{entity_kind}:{entity_id}") as connection:
            row = connection.execute(
                f"""
                SELECT payload FROM {schema}.entities
                WHERE entity_kind=%s AND entity_id=%s FOR UPDATE
                """,
                (entity_kind, entity_id),
            ).fetchone()
            if row:
                if canonical_digest(row["payload"]) != canonical_digest(payload):
                    raise ControlPlaneParityError(
                        f"import parity mismatch for {entity_kind}/{entity_id}"
                    )
                return "unchanged"
            connection.execute(
                f"""
                INSERT INTO {schema}.entities
                    (entity_kind, entity_id, version, state, payload)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (entity_kind, entity_id, version, state, self._json(payload)),
            )
            return "imported"

    def mutate_entity(
        self,
        entity_kind: str,
        entity_id: str,
        *,
        expected_version: int | None,
        fallback_payload: Mapping[str, Any] | None,
        mutate: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        with self.serialized(f"entity:{entity_kind}:{entity_id}") as connection:
            self.assert_bound_claim(entity_id, connection=connection)
            row = connection.execute(
                f"""
                SELECT version, payload FROM {schema}.entities
                WHERE entity_kind=%s AND entity_id=%s FOR UPDATE
                """,
                (entity_kind, entity_id),
            ).fetchone()
            if row is None:
                if fallback_payload is None:
                    raise KeyError(entity_id)
                fallback_version = int(fallback_payload.get("version", 1))
                connection.execute(
                    f"""
                    INSERT INTO {schema}.entities
                        (entity_kind, entity_id, version, state, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        entity_kind,
                        entity_id,
                        fallback_version,
                        str(fallback_payload.get("state", "unknown")),
                        self._json(fallback_payload),
                    ),
                )
                current_version = fallback_version
                current_payload = dict(fallback_payload)
            else:
                current_version = int(row["version"])
                current_payload = dict(row["payload"])
            if expected_version is not None and current_version != expected_version:
                CONTROL_PLANE_DB_VERSION_CONFLICTS.inc()
                raise ControlPlaneVersionConflict(
                    f"expected version {expected_version}, current version is {current_version}"
                )
            updated = mutate(current_payload)
            next_version = int(updated.get("version", 0))
            if next_version != current_version + 1:
                raise ControlPlaneVersionConflict(
                    f"mutation must advance version {current_version} to {current_version + 1}"
                )
            changed = connection.execute(
                f"""
                UPDATE {schema}.entities
                SET version=%s, state=%s, payload=%s, updated_at=clock_timestamp()
                WHERE entity_kind=%s AND entity_id=%s AND version=%s
                """,
                (
                    next_version,
                    str(updated.get("state", "unknown")),
                    self._json(updated),
                    entity_kind,
                    entity_id,
                    current_version,
                ),
            )
            if changed.rowcount != 1:
                CONTROL_PLANE_DB_VERSION_CONFLICTS.inc()
                raise ControlPlaneVersionConflict(
                    f"concurrent version conflict for {entity_kind}/{entity_id}"
                )
            return updated

    def read_collection(self, collection_name: str) -> list[dict[str, Any]] | None:
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("collection_read") as connection:
            row = connection.execute(
                f"SELECT payload FROM {schema}.collections WHERE collection_name=%s",
                (collection_name,),
            ).fetchone()
        if row is None:
            return None
        payload = row["payload"]
        return [dict(item) for item in payload] if isinstance(payload, list) else None

    def write_collection(
        self,
        collection_name: str,
        payload: list[Mapping[str, Any]],
    ) -> int:
        schema = _safe_identifier(self.configuration.schema)
        with self.serialized(f"collection:{collection_name}") as connection:
            row = connection.execute(
                f"""
                INSERT INTO {schema}.collections(collection_name, version, payload)
                VALUES (%s, 1, %s)
                ON CONFLICT (collection_name) DO UPDATE
                SET version={schema}.collections.version + 1,
                    payload=EXCLUDED.payload,
                    updated_at=clock_timestamp()
                RETURNING version
                """,
                (collection_name, self._json(payload)),
            ).fetchone()
            return int(row["version"])

    def lookup_idempotency(
        self,
        scope: str,
        key: str | None,
        request_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if not key:
            return None
        schema = _safe_identifier(self.configuration.schema)
        request_digest = canonical_digest(request_payload)
        with self.transaction("idempotency_read") as connection:
            row = connection.execute(
                f"""
                SELECT request_sha256, response_payload
                FROM {schema}.idempotency_keys
                WHERE scope=%s AND idempotency_key=%s
                """,
                (scope, key),
            ).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != request_digest:
            raise ControlPlaneIdempotencyConflict(
                f"idempotency key {key!r} was reused with a different request"
            )
        return dict(row["response_payload"])

    def record_idempotency(
        self,
        scope: str,
        key: str | None,
        request_payload: Mapping[str, Any],
        response_payload: Mapping[str, Any],
        *,
        entity_kind: str,
        entity_id: str,
    ) -> None:
        if not key:
            return
        schema = _safe_identifier(self.configuration.schema)
        request_digest = canonical_digest(request_payload)
        with self.transaction("idempotency_write") as connection:
            row = connection.execute(
                f"""
                INSERT INTO {schema}.idempotency_keys
                    (scope, idempotency_key, request_sha256, entity_kind, entity_id,
                     response_payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (scope, idempotency_key) DO NOTHING
                RETURNING request_sha256
                """,
                (
                    scope,
                    key,
                    request_digest,
                    entity_kind,
                    entity_id,
                    self._json(response_payload),
                ),
            ).fetchone()
            if row is None:
                existing = connection.execute(
                    f"""
                    SELECT request_sha256 FROM {schema}.idempotency_keys
                    WHERE scope=%s AND idempotency_key=%s
                    """,
                    (scope, key),
                ).fetchone()
                if existing is None or existing["request_sha256"] != request_digest:
                    raise ControlPlaneIdempotencyConflict(
                        f"idempotency key {key!r} conflicts with an existing request"
                    )

    def admit_task_assignment(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_payload: Mapping[str, Any],
        task_payload: Mapping[str, Any],
        priority: int,
        config: AdmissionQueueConfig,
        replace_existing: bool = False,
        now: datetime | None = None,
    ) -> TaskAdmissionResult:
        """Atomically reserve bounded capacity, task state, and idempotency identity."""
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        payload_bytes = canonical_payload_size(task_payload)
        if payload_bytes > config.max_item_bytes:
            raise ControlPlaneItemTooLarge(
                payload_bytes=payload_bytes,
                max_item_bytes=config.max_item_bytes,
            )
        request_digest = canonical_digest(request_payload)
        started = time.monotonic()
        try:
            with self.serialized("task-admission-capacity") as connection:
                existing = connection.execute(
                    f"""
                    SELECT request_sha256, response_payload, entity_id
                    FROM {schema}.idempotency_keys
                    WHERE scope=%s AND idempotency_key=%s
                    FOR UPDATE
                    """,
                    (scope, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["request_sha256"] != request_digest:
                        raise ControlPlaneIdempotencyConflict(
                            f"idempotency key {idempotency_key!r} was reused with a different request"
                        )
                    queue_row = connection.execute(
                        f"""
                        SELECT queue_id, payload_bytes
                        FROM {schema}.task_admission_queue
                        WHERE task_id=%s
                        """,
                        (existing["entity_id"],),
                    ).fetchone()
                    return TaskAdmissionResult(
                        queue_id=str(queue_row["queue_id"]) if queue_row else "not-applicable",
                        task_payload=dict(existing["response_payload"]),
                        payload_bytes=int(queue_row["payload_bytes"]) if queue_row else 0,
                        replayed=True,
                    )

                capacity = connection.execute(
                    f"""
                    SELECT count(*) AS depth, COALESCE(sum(payload_bytes), 0) AS bytes
                    FROM {schema}.task_admission_queue
                    WHERE state = ANY(%s)
                    """,
                    (list(ACTIVE_QUEUE_STATES),),
                ).fetchone()
                current_depth = int(capacity["depth"])
                current_bytes = int(capacity["bytes"])
                reason = None
                if current_depth + 1 > config.durable_max_depth:
                    reason = "durable_depth_limit"
                elif current_bytes + payload_bytes > config.durable_max_bytes:
                    reason = "durable_bytes_limit"
                if reason:
                    raise ControlPlaneAdmissionRejected(
                        reason=reason,
                        retry_after_seconds=config.retry_after_seconds,
                        current_depth=current_depth,
                        current_bytes=current_bytes,
                    )

                queue_id = f"queue-{uuid4().hex}"
                task_id = str(task_payload["task_id"])
                deadline_at = observed_at + timedelta(seconds=config.max_age_seconds)
                connection.execute(
                    f"""
                    INSERT INTO {schema}.task_admission_queue
                        (queue_id, task_id, idempotency_scope, idempotency_key,
                         request_sha256, state, priority, payload_bytes, task_payload,
                         available_at, deadline_at)
                    VALUES (%s, %s, %s, %s, %s, 'available', %s, %s, %s, %s, %s)
                    """,
                    (
                        queue_id,
                        task_id,
                        scope,
                        idempotency_key,
                        request_digest,
                        priority,
                        payload_bytes,
                        self._json(task_payload),
                        observed_at,
                        deadline_at,
                    ),
                )
                self._write_task_collection_locked(
                    connection,
                    task_payload,
                    replace_existing=replace_existing,
                )
                connection.execute(
                    f"""
                    INSERT INTO {schema}.idempotency_keys
                        (scope, idempotency_key, request_sha256, entity_kind, entity_id,
                         response_payload)
                    VALUES (%s, %s, %s, 'task_assignment', %s, %s)
                    """,
                    (
                        scope,
                        idempotency_key,
                        request_digest,
                        task_id,
                        self._json(task_payload),
                    ),
                )
                return TaskAdmissionResult(
                    queue_id=queue_id,
                    task_payload=dict(task_payload),
                    payload_bytes=payload_bytes,
                    replayed=False,
                )
        finally:
            QUEUE_ADMISSION_WAIT_SECONDS.observe(time.monotonic() - started)

    def task_queue_snapshot(self, *, now: datetime | None = None) -> TaskQueueSnapshot:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        with self.transaction("task_queue_snapshot") as connection:
            rows = connection.execute(
                f"""
                SELECT state, count(*) AS depth, COALESCE(sum(payload_bytes), 0) AS bytes
                FROM {schema}.task_admission_queue
                GROUP BY state
                """
            ).fetchall()
            oldest = connection.execute(
                f"""
                SELECT EXTRACT(EPOCH FROM (%s - min(created_at))) AS age
                FROM {schema}.task_admission_queue
                WHERE state = ANY(%s)
                """,
                (observed_at, list(ACTIVE_QUEUE_STATES)),
            ).fetchone()
        state_counts = {str(row["state"]): int(row["depth"]) for row in rows}
        state_bytes = {str(row["state"]): int(row["bytes"]) for row in rows}
        return TaskQueueSnapshot(
            active_depth=sum(state_counts.get(state, 0) for state in ACTIVE_QUEUE_STATES),
            active_bytes=sum(state_bytes.get(state, 0) for state in ACTIVE_QUEUE_STATES),
            oldest_age_seconds=max(0.0, float(oldest["age"] or 0.0)),
            state_counts=state_counts,
            state_bytes=state_bytes,
        )

    def list_task_queue_items(
        self,
        *,
        states: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("task_queue_list") as connection:
            if states:
                rows = connection.execute(
                    f"""
                    SELECT * FROM {schema}.task_admission_queue
                    WHERE state = ANY(%s)
                    ORDER BY created_at, queue_id
                    """,
                    (list(states),),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT * FROM {schema}.task_admission_queue ORDER BY created_at, queue_id"
                ).fetchall()
        return [self._queue_row(row) for row in rows]

    def reconcile_task_queue(
        self,
        *,
        max_attempts: int,
        now: datetime | None = None,
    ) -> dict[str, int]:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        outcome = {"expired": 0, "requeued": 0, "dlq": 0}
        with self.serialized("task-queue-reconciliation") as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM {schema}.task_admission_queue
                WHERE state = ANY(%s)
                  AND (deadline_at <= %s OR (state='leased' AND lease_expires_at <= %s))
                FOR UPDATE
                """,
                (list(ACTIVE_QUEUE_STATES), observed_at, observed_at),
            ).fetchall()
            for row in rows:
                queue_id = str(row["queue_id"])
                if row["deadline_at"] <= observed_at:
                    connection.execute(
                        f"""
                        UPDATE {schema}.task_admission_queue
                        SET state='expired', terminal_reason='deadline_exceeded',
                            terminal_at=%s, lease_owner=NULL, lease_expires_at=NULL,
                            updated_at=clock_timestamp()
                        WHERE queue_id=%s
                        """,
                        (observed_at, queue_id),
                    )
                    outcome["expired"] += 1
                elif int(row["attempt_count"]) >= max_attempts:
                    connection.execute(
                        f"""
                        UPDATE {schema}.task_admission_queue
                        SET state='dlq', terminal_reason='lease_lost_attempts_exhausted',
                            terminal_at=%s, lease_owner=NULL, lease_expires_at=NULL,
                            last_failure_class='owner_lost', updated_at=clock_timestamp()
                        WHERE queue_id=%s
                        """,
                        (observed_at, queue_id),
                    )
                    outcome["dlq"] += 1
                else:
                    connection.execute(
                        f"""
                        UPDATE {schema}.task_admission_queue
                        SET state='retry_wait', available_at=%s, lease_owner=NULL,
                            lease_expires_at=NULL, last_failure_class='owner_lost',
                            updated_at=clock_timestamp()
                        WHERE queue_id=%s
                        """,
                        (observed_at, queue_id),
                    )
                    outcome["requeued"] += 1
        return outcome

    def claim_task_queue_items(
        self,
        *,
        owner: str,
        max_items: int,
        max_bytes: int,
        lease_seconds: float,
        scan_limit: int,
        now: datetime | None = None,
    ) -> list[TaskQueueLease]:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        leases: list[TaskQueueLease] = []
        selected_bytes = 0
        with self.transaction("task_queue_claim") as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM {schema}.task_admission_queue
                WHERE state IN ('available', 'retry_wait')
                  AND available_at <= %s AND deadline_at > %s
                ORDER BY priority DESC, available_at, created_at, queue_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (observed_at, observed_at, scan_limit),
            ).fetchall()
            for row in rows:
                if len(leases) >= max_items:
                    break
                payload_bytes = int(row["payload_bytes"])
                if selected_bytes + payload_bytes > max_bytes:
                    continue
                lease_epoch = int(row["lease_epoch"]) + 1
                lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
                connection.execute(
                    f"""
                    UPDATE {schema}.task_admission_queue
                    SET state='leased', lease_owner=%s, lease_epoch=%s,
                        lease_expires_at=%s, attempt_count=attempt_count + 1,
                        updated_at=clock_timestamp()
                    WHERE queue_id=%s
                    """,
                    (owner, lease_epoch, lease_expires_at, row["queue_id"]),
                )
                selected_bytes += payload_bytes
                leases.append(
                    TaskQueueLease(
                        queue_id=str(row["queue_id"]),
                        task_id=str(row["task_id"]),
                        task_payload=dict(row["task_payload"]),
                        payload_bytes=payload_bytes,
                        attempt_count=int(row["attempt_count"]) + 1,
                        lease_owner=owner,
                        lease_epoch=lease_epoch,
                        lease_expires_at=lease_expires_at.isoformat(),
                        deadline_at=row["deadline_at"].isoformat(),
                    )
                )
        return leases

    def complete_task_queue_item(
        self,
        lease: TaskQueueLease,
        *,
        state: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if state not in {"completed", "failed", "dlq", "expired", "cancelled"}:
            raise ValueError(f"invalid task queue terminal state: {state}")
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        with self.transaction("task_queue_complete") as connection:
            row = connection.execute(
                f"""
                SELECT * FROM {schema}.task_admission_queue
                WHERE queue_id=%s FOR UPDATE
                """,
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(row, lease, observed_at)
            connection.execute(
                f"""
                UPDATE {schema}.task_admission_queue
                SET state=%s, terminal_reason=%s, terminal_at=%s,
                    lease_owner=NULL, lease_expires_at=NULL,
                    updated_at=clock_timestamp()
                WHERE queue_id=%s
                """,
                (state, reason, observed_at, lease.queue_id),
            )
            payload = self._queue_row(row)
            payload.update(
                {
                    "state": state,
                    "terminal_reason": reason,
                    "terminal_at": observed_at.isoformat(),
                    "lease_owner": None,
                    "lease_expires_at": None,
                }
            )
            return payload

    def release_task_queue_lease(
        self,
        lease: TaskQueueLease,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return unstarted local work to the durable queue without consuming retry budget."""
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        with self.transaction("task_queue_release") as connection:
            row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(row, lease, observed_at)
            connection.execute(
                f"""
                UPDATE {schema}.task_admission_queue
                SET state='available', available_at=%s, lease_owner=NULL,
                    lease_expires_at=NULL, last_failure_class=%s,
                    updated_at=clock_timestamp()
                WHERE queue_id=%s
                """,
                (observed_at, reason, lease.queue_id),
            )
            return {
                "queue_id": lease.queue_id,
                "state": "available",
                "release_reason": reason,
                "available_at": observed_at.isoformat(),
            }

    def reschedule_task_queue_item(
        self,
        lease: TaskQueueLease,
        *,
        failure_class: str,
        transient: bool,
        config: AdmissionQueueConfig,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        with self.serialized("task-queue-retry-budget") as connection:
            row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(row, lease, observed_at)
            if not transient:
                return self._finish_locked_queue_item(
                    connection,
                    lease.queue_id,
                    state="dlq",
                    reason=f"permanent:{failure_class}",
                    failure_class=failure_class,
                    observed_at=observed_at,
                )
            if int(row["attempt_count"]) >= config.max_attempts:
                return self._finish_locked_queue_item(
                    connection,
                    lease.queue_id,
                    state="dlq",
                    reason=f"attempts_exhausted:{failure_class}",
                    failure_class=failure_class,
                    observed_at=observed_at,
                )
            budget = connection.execute(
                f"""
                SELECT * FROM {schema}.task_retry_budget
                WHERE budget_name='task-dispatch' FOR UPDATE
                """
            ).fetchone()
            if budget is None or (
                observed_at - budget["window_started_at"]
            ).total_seconds() >= config.retry_budget_window_seconds:
                consumed = 0
                window_started_at = observed_at
            else:
                consumed = int(budget["consumed"])
                window_started_at = budget["window_started_at"]
            if consumed >= config.global_retry_budget:
                return self._finish_locked_queue_item(
                    connection,
                    lease.queue_id,
                    state="dlq",
                    reason=f"retry_budget_exhausted:{failure_class}",
                    failure_class=failure_class,
                    observed_at=observed_at,
                )
            connection.execute(
                f"""
                INSERT INTO {schema}.task_retry_budget
                    (budget_name, window_started_at, consumed)
                VALUES ('task-dispatch', %s, %s)
                ON CONFLICT (budget_name) DO UPDATE
                SET window_started_at=EXCLUDED.window_started_at,
                    consumed=EXCLUDED.consumed,
                    updated_at=clock_timestamp()
                """,
                (window_started_at, consumed + 1),
            )
            base = min(
                config.backoff_max_seconds,
                config.backoff_base_seconds * (2 ** max(0, lease.attempt_count - 1)),
            )
            digest = hashlib.sha256(
                f"{lease.queue_id}:{lease.attempt_count}".encode("utf-8")
            ).digest()
            unit = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
            jitter = (unit * 2.0 - 1.0) * config.jitter_ratio
            delay = max(0.0, base * (1.0 + jitter))
            available_at = observed_at + timedelta(seconds=delay)
            connection.execute(
                f"""
                UPDATE {schema}.task_admission_queue
                SET state='retry_wait', available_at=%s, lease_owner=NULL,
                    lease_expires_at=NULL, last_failure_class=%s,
                    updated_at=clock_timestamp()
                WHERE queue_id=%s
                """,
                (available_at, failure_class, lease.queue_id),
            )
            return {
                "queue_id": lease.queue_id,
                "state": "retry_wait",
                "failure_class": failure_class,
                "delay_seconds": delay,
                "available_at": available_at.isoformat(),
            }

    def _write_task_collection_locked(
        self,
        connection: Any,
        task_payload: Mapping[str, Any],
        *,
        replace_existing: bool,
    ) -> None:
        schema = _safe_identifier(self.configuration.schema)
        row = connection.execute(
            f"""
            SELECT version, payload FROM {schema}.collections
            WHERE collection_name='task_assignments' FOR UPDATE
            """
        ).fetchone()
        items = [dict(item) for item in row["payload"]] if row else []
        task_id = str(task_payload["task_id"])
        matching = next((index for index, item in enumerate(items) if item.get("task_id") == task_id), None)
        if matching is None:
            items.insert(0, dict(task_payload))
        elif replace_existing:
            items[matching] = dict(task_payload)
        else:
            raise ControlPlaneVersionConflict(f"task_assignment/{task_id} already exists")
        if row:
            connection.execute(
                f"""
                UPDATE {schema}.collections
                SET version=version + 1, payload=%s, updated_at=clock_timestamp()
                WHERE collection_name='task_assignments'
                """,
                (self._json(items),),
            )
        else:
            connection.execute(
                f"""
                INSERT INTO {schema}.collections(collection_name, version, payload)
                VALUES ('task_assignments', 1, %s)
                """,
                (self._json(items),),
            )

    def _assert_queue_lease(
        self,
        row: Mapping[str, Any] | None,
        lease: TaskQueueLease,
        observed_at: datetime,
    ) -> None:
        if row is None:
            raise ControlPlaneLeaseConflict("task_queue_item_missing")
        if str(row["state"]) != "leased":
            raise ControlPlaneLeaseConflict("task_queue_item_not_leased")
        if str(row["lease_owner"]) != lease.lease_owner:
            raise ControlPlaneLeaseConflict("task_queue_owner_mismatch")
        if int(row["lease_epoch"]) != lease.lease_epoch:
            raise ControlPlaneLeaseConflict("task_queue_epoch_mismatch")
        if row["lease_expires_at"] is None or row["lease_expires_at"] <= observed_at:
            raise ControlPlaneLeaseConflict("task_queue_lease_expired")

    def _finish_locked_queue_item(
        self,
        connection: Any,
        queue_id: str,
        *,
        state: str,
        reason: str,
        failure_class: str,
        observed_at: datetime,
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        connection.execute(
            f"""
            UPDATE {schema}.task_admission_queue
            SET state=%s, terminal_reason=%s, terminal_at=%s,
                lease_owner=NULL, lease_expires_at=NULL,
                last_failure_class=%s, updated_at=clock_timestamp()
            WHERE queue_id=%s
            """,
            (state, reason, observed_at, failure_class, queue_id),
        )
        return {
            "queue_id": queue_id,
            "state": state,
            "terminal_reason": reason,
            "terminal_at": observed_at.isoformat(),
            "failure_class": failure_class,
        }

    @staticmethod
    def _queue_row(row: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["task_payload"] = dict(row["task_payload"])
        for key in (
            "available_at",
            "deadline_at",
            "lease_expires_at",
            "terminal_at",
            "created_at",
            "updated_at",
        ):
            if payload.get(key) is not None:
                payload[key] = payload[key].isoformat()
        return payload

    def acquire_claim(
        self,
        *,
        run_id: str,
        worker_id: str,
        worker_pid: int,
        process_instance_id: str,
        source_commit: str,
        supervisor_lease_id: str,
        fencing_token: int,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> ClaimResult:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        with self.serialized(f"claim:{run_id}") as connection:
            row = connection.execute(
                f"SELECT claim_epoch, payload FROM {schema}.lifecycle_claims WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            current = dict(row["payload"]) if row else None
            owner_matches = bool(
                current
                and current["worker_id"] == worker_id
                and current["worker_pid"] == worker_pid
                and current["process_instance_id"] == process_instance_id
                and current["supervisor_lease_id"] == supervisor_lease_id
                and int(current["fencing_token"]) == fencing_token
            )
            if current:
                expires_at = _parse_datetime(current["expires_at"])
                released_at = current.get("released_at")
                if released_at is None and expires_at > observed_at:
                    if not owner_matches:
                        return ClaimResult(False, "active_claim_conflict", current)
                    current["renewed_at"] = observed_at.isoformat()
                    current["expires_at"] = (
                        observed_at + timedelta(seconds=ttl_seconds)
                    ).isoformat()
                    connection.execute(
                        f"""
                        UPDATE {schema}.lifecycle_claims
                        SET payload=%s, expires_at=%s, updated_at=clock_timestamp()
                        WHERE run_id=%s
                        """,
                        (self._json(current), current["expires_at"], run_id),
                    )
                    return ClaimResult(True, "claim_reused", current)
                if int(current["fencing_token"]) > fencing_token:
                    return ClaimResult(False, "stale_supervisor_fence", current)
            claim_epoch = (int(row["claim_epoch"]) + 1) if row else 1
            claim = {
                "run_id": run_id,
                "claim_id": uuid4().hex,
                "claim_epoch": claim_epoch,
                "worker_id": worker_id,
                "worker_pid": worker_pid,
                "process_instance_id": process_instance_id,
                "source_commit": source_commit,
                "supervisor_lease_id": supervisor_lease_id,
                "fencing_token": fencing_token,
                "acquired_at": observed_at.isoformat(),
                "renewed_at": observed_at.isoformat(),
                "expires_at": (observed_at + timedelta(seconds=ttl_seconds)).isoformat(),
                "released_at": None,
            }
            connection.execute(
                f"""
                INSERT INTO {schema}.lifecycle_claims
                    (run_id, claim_epoch, claim_id, expires_at, released_at, payload)
                VALUES (%s, %s, %s, %s, NULL, %s)
                ON CONFLICT (run_id) DO UPDATE
                SET claim_epoch=EXCLUDED.claim_epoch,
                    claim_id=EXCLUDED.claim_id,
                    expires_at=EXCLUDED.expires_at,
                    released_at=NULL,
                    payload=EXCLUDED.payload,
                    updated_at=clock_timestamp()
                """,
                (
                    run_id,
                    claim_epoch,
                    claim["claim_id"],
                    claim["expires_at"],
                    self._json(claim),
                ),
            )
            return ClaimResult(
                True,
                "expired_claim_replaced" if current else "claim_acquired",
                claim,
            )

    def renew_claim(
        self,
        claim: Mapping[str, Any],
        *,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed_at = now or utc_now()
        with self.serialized(f"claim:{claim['run_id']}"):
            current = self._locked_claim(claim["run_id"])
            if not _same_claim(current, claim):
                raise ControlPlaneLeaseConflict("lifecycle_claim_lost")
            if current.get("released_at") is not None:
                raise ControlPlaneLeaseConflict("lifecycle_claim_released")
            if _parse_datetime(current["expires_at"]) <= observed_at:
                raise ControlPlaneLeaseConflict("lifecycle_claim_expired")
            current["renewed_at"] = observed_at.isoformat()
            current["expires_at"] = (
                observed_at + timedelta(seconds=ttl_seconds)
            ).isoformat()
            self._write_locked_claim(current)
            return current

    def release_claim(
        self,
        claim: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed_at = now or utc_now()
        with self.serialized(f"claim:{claim['run_id']}"):
            current = self._locked_claim(claim["run_id"])
            if not _same_claim(current, claim):
                raise ControlPlaneLeaseConflict("lifecycle_claim_lost")
            current["renewed_at"] = observed_at.isoformat()
            current["expires_at"] = observed_at.isoformat()
            current["released_at"] = observed_at.isoformat()
            self._write_locked_claim(current)
            return current

    def reconcile_stale_claims(self, *, now: datetime | None = None) -> list[str]:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        reconciled: list[str] = []
        with self.serialized("claim-reconciliation") as connection:
            rows = connection.execute(
                f"""
                SELECT run_id, payload FROM {schema}.lifecycle_claims
                WHERE released_at IS NULL AND expires_at <= %s
                FOR UPDATE
                """,
                (observed_at,),
            ).fetchall()
            for row in rows:
                payload = dict(row["payload"])
                payload["renewed_at"] = observed_at.isoformat()
                payload["expires_at"] = observed_at.isoformat()
                payload["released_at"] = observed_at.isoformat()
                connection.execute(
                    f"""
                    UPDATE {schema}.lifecycle_claims
                    SET expires_at=%s, released_at=%s, payload=%s,
                        updated_at=clock_timestamp()
                    WHERE run_id=%s
                    """,
                    (
                        observed_at,
                        observed_at,
                        self._json(payload),
                        row["run_id"],
                    ),
                )
                reconciled.append(str(row["run_id"]))
        return reconciled

    def read_claim(self, run_id: str) -> dict[str, Any] | None:
        """Return the current persisted claim without changing lease state."""
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("claim_read") as connection:
            row = connection.execute(
                f"SELECT payload FROM {schema}.lifecycle_claims WHERE run_id=%s",
                (run_id,),
            ).fetchone()
        return dict(row["payload"]) if row else None

    def assert_bound_claim(self, run_id: str, *, connection: Any | None = None) -> None:
        claim = _BOUND_CLAIM.get()
        if claim is None:
            return
        if claim.get("run_id") != run_id:
            raise ControlPlaneLeaseConflict("bound_claim_run_identity_mismatch")
        schema = _safe_identifier(self.configuration.schema)
        if connection is None:
            with self.transaction("claim_assert") as active:
                self.assert_bound_claim(run_id, connection=active)
            return
        row = connection.execute(
            f"""
            SELECT payload FROM {schema}.lifecycle_claims
            WHERE run_id=%s FOR SHARE
            """,
            (run_id,),
        ).fetchone()
        if row is None or not _same_claim(dict(row["payload"]), claim):
            raise ControlPlaneLeaseConflict("bound_claim_lost")
        current = dict(row["payload"])
        if current.get("released_at") is not None:
            raise ControlPlaneLeaseConflict("bound_claim_released")
        if _parse_datetime(current["expires_at"]) <= utc_now():
            raise ControlPlaneLeaseConflict("bound_claim_expired")

    def reserve_side_effect(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        schema = _safe_identifier(self.configuration.schema)
        side_effect_key = str(payload["side_effect_key"])
        run_id = str(payload["lifecycle_run_id"])
        with self.serialized(f"side-effect:{side_effect_key}") as connection:
            self.assert_bound_claim(run_id, connection=connection)
            row = connection.execute(
                f"""
                SELECT payload FROM {schema}.side_effect_outbox
                WHERE side_effect_key=%s FOR UPDATE
                """,
                (side_effect_key,),
            ).fetchone()
            if row:
                existing = dict(row["payload"])
                immutable = (
                    "lifecycle_series_id",
                    "lifecycle_run_id",
                    "attempt_id",
                    "stage_id",
                    "action",
                    "action_digest",
                )
                if any(existing.get(key) != payload.get(key) for key in immutable):
                    raise ControlPlaneIdempotencyConflict(
                        f"side-effect key {side_effect_key} identity mismatch"
                    )
                return existing, False
            connection.execute(
                f"""
                INSERT INTO {schema}.side_effect_outbox
                    (side_effect_key, lifecycle_run_id, state, payload)
                VALUES (%s, %s, %s, %s)
                """,
                (side_effect_key, run_id, str(payload["state"]), self._json(payload)),
            )
            return dict(payload), True

    def complete_side_effect(
        self,
        side_effect_key: str,
        *,
        state: str,
        runtime_id: str | None,
        evidence_uri: str | None,
        updated_at: str,
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        allowed = {
            "reserved": {"completed", "failed", "reconciled"},
            "reconciled": {"reconciled", "completed", "failed"},
            "completed": {"completed"},
            "failed": {"failed"},
        }
        with self.serialized(f"side-effect:{side_effect_key}") as connection:
            row = connection.execute(
                f"""
                SELECT lifecycle_run_id, state, payload
                FROM {schema}.side_effect_outbox
                WHERE side_effect_key=%s FOR UPDATE
                """,
                (side_effect_key,),
            ).fetchone()
            if row is None:
                raise KeyError(side_effect_key)
            self.assert_bound_claim(str(row["lifecycle_run_id"]), connection=connection)
            current_state = str(row["state"])
            if state not in allowed[current_state]:
                raise ControlPlaneVersionConflict(
                    f"side-effect cannot transition from {current_state} to {state}"
                )
            payload = dict(row["payload"])
            payload.update(
                {
                    "state": state,
                    "runtime_id": runtime_id or payload.get("runtime_id"),
                    "evidence_uri": evidence_uri or payload.get("evidence_uri"),
                    "updated_at": updated_at,
                }
            )
            connection.execute(
                f"""
                UPDATE {schema}.side_effect_outbox
                SET state=%s, payload=%s, updated_at=clock_timestamp()
                WHERE side_effect_key=%s
                """,
                (state, self._json(payload), side_effect_key),
            )
            return payload

    def list_side_effects(self, run_id: str) -> list[dict[str, Any]]:
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("side_effect_list") as connection:
            rows = connection.execute(
                f"""
                SELECT payload FROM {schema}.side_effect_outbox
                WHERE lifecycle_run_id=%s
                ORDER BY created_at, side_effect_key
                """,
                (run_id,),
            ).fetchall()
        return [dict(row["payload"]) for row in rows]

    def _locked_claim(self, run_id: str) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        connection = _BOUND_CONNECTION.get()
        if connection is None:
            with self.serialized(f"claim:{run_id}"):
                return self._locked_claim(run_id)
        row = connection.execute(
            f"SELECT payload FROM {schema}.lifecycle_claims WHERE run_id=%s FOR UPDATE",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ControlPlaneLeaseConflict("lifecycle_claim_missing")
        return dict(row["payload"])

    def _write_locked_claim(self, claim: Mapping[str, Any]) -> None:
        schema = _safe_identifier(self.configuration.schema)
        connection = _BOUND_CONNECTION.get()
        if connection is None:
            raise ControlPlaneStoreUnavailable("claim write requires an active transaction")
        connection.execute(
            f"""
            UPDATE {schema}.lifecycle_claims
            SET expires_at=%s, released_at=%s, payload=%s, updated_at=clock_timestamp()
            WHERE run_id=%s AND claim_id=%s AND claim_epoch=%s
            """,
            (
                claim["expires_at"],
                claim.get("released_at"),
                self._json(claim),
                claim["run_id"],
                claim["claim_id"],
                claim["claim_epoch"],
            ),
        )

    def _json(self, payload: object) -> Any:
        if self._jsonb is None:
            raise ControlPlaneStoreUnavailable("JSON adapter is unavailable")
        return self._jsonb(payload)


_STORE_LOCK = threading.Lock()
_STORE: TransactionalControlPlaneStore | None = None
_STORE_KEY: tuple[object, ...] | None = None


def get_transactional_store() -> TransactionalControlPlaneStore:
    global _STORE, _STORE_KEY
    configuration = StoreConfiguration.from_env()
    key = (
        configuration.mode,
        configuration.dsn,
        configuration.schema,
        configuration.pool_min_size,
        configuration.pool_max_size,
        configuration.acquire_timeout_seconds,
        configuration.lock_timeout_seconds,
        configuration.statement_timeout_seconds,
    )
    with _STORE_LOCK:
        if _STORE is None or _STORE_KEY != key:
            if _STORE is not None:
                _STORE.close()
            _STORE = TransactionalControlPlaneStore(configuration)
            _STORE_KEY = key
        return _STORE


def reset_transactional_store() -> None:
    global _STORE, _STORE_KEY
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.close()
        _STORE = None
        _STORE_KEY = None


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _same_claim(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        left.get(field) == right.get(field)
        for field in ("run_id", "claim_id", "claim_epoch", "fencing_token")
    )


def _schema_statements(schema: str) -> tuple[str, ...]:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.schema_migrations (
            version text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entities (
            entity_kind text NOT NULL,
            entity_id text NOT NULL,
            version bigint NOT NULL CHECK (version >= 1),
            state text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (entity_kind, entity_id)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS entities_kind_state_updated_idx
        ON {schema}.entities(entity_kind, state, updated_at DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.collections (
            collection_name text PRIMARY KEY,
            version bigint NOT NULL CHECK (version >= 1),
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.idempotency_keys (
            scope text NOT NULL,
            idempotency_key text NOT NULL,
            request_sha256 char(64) NOT NULL,
            entity_kind text NOT NULL,
            entity_id text NOT NULL,
            response_payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (scope, idempotency_key)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idempotency_entity_idx
        ON {schema}.idempotency_keys(entity_kind, entity_id)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.lifecycle_claims (
            run_id text PRIMARY KEY,
            claim_epoch bigint NOT NULL CHECK (claim_epoch >= 1),
            claim_id text NOT NULL,
            expires_at timestamptz NOT NULL,
            released_at timestamptz,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS lifecycle_claim_expiry_idx
        ON {schema}.lifecycle_claims(released_at, expires_at)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.side_effect_outbox (
            side_effect_key char(64) PRIMARY KEY,
            lifecycle_run_id text NOT NULL,
            state text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS side_effect_run_state_idx
        ON {schema}.side_effect_outbox(lifecycle_run_id, state, created_at)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.task_admission_queue (
            queue_id text PRIMARY KEY,
            task_id text NOT NULL UNIQUE,
            idempotency_scope text NOT NULL,
            idempotency_key text NOT NULL,
            request_sha256 char(64) NOT NULL,
            state text NOT NULL CHECK (
                state IN ('available', 'retry_wait', 'leased', 'completed', 'failed',
                          'dlq', 'expired', 'cancelled')
            ),
            priority smallint NOT NULL,
            payload_bytes bigint NOT NULL CHECK (payload_bytes > 0),
            task_payload jsonb NOT NULL,
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            available_at timestamptz NOT NULL,
            deadline_at timestamptz NOT NULL,
            lease_owner text,
            lease_epoch bigint NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
            lease_expires_at timestamptz,
            last_failure_class text,
            terminal_reason text,
            terminal_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (idempotency_scope, idempotency_key)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS task_admission_claim_idx
        ON {schema}.task_admission_queue(state, available_at, priority DESC, created_at)
        WHERE state IN ('available', 'retry_wait')
        """,
        f"""
        CREATE INDEX IF NOT EXISTS task_admission_active_idx
        ON {schema}.task_admission_queue(state, deadline_at, lease_expires_at)
        WHERE state IN ('available', 'retry_wait', 'leased')
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.task_retry_budget (
            budget_name text PRIMARY KEY,
            window_started_at timestamptz NOT NULL,
            consumed integer NOT NULL CHECK (consumed >= 0),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """,
    )
