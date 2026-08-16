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
    task_resource_class,
)


SCHEMA_VERSIONS = (
    "001_transactional_control_plane",
    "002_bounded_admission_queue",
    "003_task_queue_safety",
    "004_task_entity_storage",
    "005_task_queue_operational_safety",
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


class ControlPlaneDeadlineExceeded(ControlPlaneLeaseConflict):
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


class ControlPlaneTaskValidationError(ControlPlaneStoreError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


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
    resource_state_counts: dict[str, dict[str, int]]
    resource_state_bytes: dict[str, dict[str, int]]

    def dispatchable_depth(self, resource_class: str) -> int:
        counts = self.resource_state_counts.get(resource_class, {})
        return sum(counts.get(state, 0) for state in ("available", "retry_wait"))

    def downstream_outstanding(self, resource_class: str) -> int:
        counts = self.resource_state_counts.get(resource_class, {})
        return sum(
            counts.get(state, 0)
            for state in ("leased", "runtime_pending", "outcome_unknown")
        )


@dataclass(frozen=True)
class TaskQueueHistorySnapshot:
    queue_rows: int
    queue_bytes: int
    effect_rows: int
    effect_bytes: int
    task_rows: int
    task_bytes: int
    mirror_rows: int
    mirror_bytes: int
    idempotency_rows: int
    idempotency_bytes: int
    compacted_rows: dict[str, int]
    compacted_bytes: dict[str, int]


@dataclass(frozen=True)
class TaskQueueLease:
    queue_id: str
    task_id: str
    task_payload: dict[str, Any]
    payload_bytes: int
    resource_class: str
    claim_count: int
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
_BOUND_TASK_QUEUE_LEASE: ContextVar[TaskQueueLease | None] = ContextVar(
    "evm_task_queue_lease", default=None
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
        if os.getenv("EVM_CONTROL_PLANE_AUTO_MIGRATE", "true").lower() in {
            "1",
            "true",
            "yes",
        }:
            self.ensure_schema()
        else:
            self.verify_schema()

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
    def serialized(
        self,
        scope: str,
        *,
        wait_seconds: float | None = None,
    ) -> Iterator[Any]:
        existing = _BOUND_CONNECTION.get()
        if existing is not None:
            try:
                if wait_seconds is not None:
                    existing.execute(
                        "SELECT set_config('lock_timeout', %s, true)",
                        (f"{max(1, int(wait_seconds * 1000))}ms",),
                    )
                existing.execute("SELECT pg_advisory_xact_lock(%s)", (_advisory_key(scope),))
                if wait_seconds is not None:
                    existing.execute(
                        "SELECT set_config('lock_timeout', %s, true)",
                        (f"{self.configuration.lock_timeout_seconds}s",),
                    )
                yield existing
            except self._transaction_timeout_types as exc:
                raise ControlPlaneTransactionTimeout(
                    f"control-plane lock {scope!r} exceeded its bounded wait"
                ) from exc
            return
        with self.transaction(f"serialized:{scope}") as connection:
            try:
                if wait_seconds is not None:
                    connection.execute(
                        "SELECT set_config('lock_timeout', %s, true)",
                        (f"{max(1, int(wait_seconds * 1000))}ms",),
                    )
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (_advisory_key(scope),)
                )
                if wait_seconds is not None:
                    connection.execute(
                        "SELECT set_config('lock_timeout', %s, true)",
                        (f"{self.configuration.lock_timeout_seconds}s",),
                    )
                yield connection
            except self._transaction_timeout_types as exc:
                raise ControlPlaneTransactionTimeout(
                    f"control-plane lock {scope!r} exceeded its bounded wait"
                ) from exc

    @contextmanager
    def bind_claim(self, claim: Mapping[str, Any]) -> Iterator[None]:
        token = _BOUND_CLAIM.set(dict(claim))
        try:
            yield
        finally:
            _BOUND_CLAIM.reset(token)

    @contextmanager
    def bind_task_queue_lease(self, lease: TaskQueueLease) -> Iterator[None]:
        token = _BOUND_TASK_QUEUE_LEASE.set(lease)
        try:
            yield
        finally:
            _BOUND_TASK_QUEUE_LEASE.reset(token)

    def bound_task_queue_lease(self) -> TaskQueueLease | None:
        return _BOUND_TASK_QUEUE_LEASE.get()

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
            connection.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (_advisory_key(f"schema-migration:{schema}"),),
            )
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

    def verify_schema(self) -> None:
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("schema_verify") as connection:
            rows = connection.execute(
                f"SELECT version FROM {schema}.schema_migrations WHERE version = ANY(%s)",
                (list(SCHEMA_VERSIONS),),
            ).fetchall()
        observed = {str(row["version"]) for row in rows}
        missing = set(SCHEMA_VERSIONS) - observed
        if missing:
            raise ControlPlaneStoreUnavailable(
                f"control-plane schema is missing required migrations: {sorted(missing)}"
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

    def replace_task_mirror(self, payload: list[Mapping[str, Any]]) -> int:
        """Refresh the bounded rollback mirror without making it authoritative."""
        return self.write_collection("task_assignments", payload)

    def refresh_task_mirror_from_authority(self) -> int:
        """Refresh the PostgreSQL rollback mirror in one database transaction."""
        with self.transaction("task-mirror-refresh") as connection:
            return self._refresh_task_collection_locked(connection)

    def replace_task_entities(self, payload: list[Mapping[str, Any]]) -> None:
        """Compatibility path for legacy bulk task mutations.

        Durable queue admission and execution use row-level methods instead. This
        bounded snapshot path exists for older task controls that still mutate a
        complete list under the legacy operations lock.
        """
        schema = _safe_identifier(self.configuration.schema)
        with self.serialized("task-entity-snapshot") as connection:
            for item in payload:
                task_id = str(item["task_id"])
                version = max(1, int(item.get("version", 1)))
                state = str(item.get("status", "unknown"))
                connection.execute(
                    f"""
                    INSERT INTO {schema}.entities
                        (entity_kind, entity_id, version, state, payload)
                    VALUES ('task_assignment', %s, %s, %s, %s)
                    ON CONFLICT (entity_kind, entity_id) DO UPDATE
                    SET version=EXCLUDED.version, state=EXCLUDED.state,
                        payload=EXCLUDED.payload, updated_at=clock_timestamp()
                    WHERE {schema}.entities.version <= EXCLUDED.version
                    """,
                    (task_id, version, state, self._json(item)),
                )
            self._refresh_task_collection_locked(connection)

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
                    str(updated.get("state", updated.get("status", "unknown"))),
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
            with self.serialized(
                "task-admission-capacity",
                wait_seconds=config.admission_wait_seconds,
            ) as connection:
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

                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_advisory_key("task-idempotency-capacity"),),
                )
                idempotency_depth = connection.execute(
                    f"""
                    SELECT count(*) AS depth
                    FROM {schema}.idempotency_keys
                    WHERE entity_kind='task_assignment'
                    """
                ).fetchone()
                if int(idempotency_depth["depth"]) >= config.idempotency_tombstone_max_rows:
                    raise ControlPlaneAdmissionRejected(
                        reason="idempotency_capacity_limit",
                        retry_after_seconds=config.retry_after_seconds,
                        current_depth=int(idempotency_depth["depth"]),
                        current_bytes=0,
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
                deadline_seconds = config.max_age_seconds
                config_payload = task_payload.get("config_payload")
                requested_deadline = (
                    config_payload.get("queue_deadline_seconds")
                    if isinstance(config_payload, Mapping)
                    else None
                )
                if requested_deadline is not None:
                    if isinstance(requested_deadline, bool) or not isinstance(
                        requested_deadline,
                        int | float,
                    ):
                        raise ControlPlaneTaskValidationError(
                            "queue_deadline_invalid",
                            "queue_deadline_seconds must be numeric.",
                        )
                    if not 0 < float(requested_deadline) <= config.max_age_seconds:
                        raise ControlPlaneTaskValidationError(
                            "queue_deadline_out_of_bounds",
                            "queue_deadline_seconds must be positive and no greater "
                            "than the frozen queue max age.",
                        )
                    deadline_seconds = float(requested_deadline)
                deadline_at = observed_at + timedelta(seconds=deadline_seconds)
                self._write_task_entity_locked(
                    connection,
                    task_payload,
                    replace_existing=replace_existing,
                )
                connection.execute(
                    f"""
                    INSERT INTO {schema}.task_admission_queue
                        (queue_id, task_id, idempotency_scope, idempotency_key,
                         request_sha256, state, priority, payload_bytes, task_payload,
                         resource_class, retry_budget_scope, available_at, deadline_at)
                    VALUES (%s, %s, %s, %s, %s, 'available', %s, %s, %s, %s, %s, %s, %s)
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
                        task_resource_class(task_payload),
                        config.retry_budget_scope,
                        observed_at,
                        deadline_at,
                    ),
                )
                connection.execute(
                    f"""
                    INSERT INTO {schema}.idempotency_keys
                        (scope, idempotency_key, request_sha256, entity_kind, entity_id,
                         response_payload, retain_until)
                    VALUES (%s, %s, %s, 'task_assignment', %s, %s, %s)
                    """,
                    (
                        scope,
                        idempotency_key,
                        request_digest,
                        task_id,
                        self._json(task_payload),
                        observed_at
                        + timedelta(
                            seconds=config.idempotency_tombstone_retention_seconds
                        ),
                    ),
                )
                return TaskAdmissionResult(
                    queue_id=queue_id,
                    task_payload=dict(task_payload),
                    payload_bytes=payload_bytes,
                    replayed=False,
                )
        except ControlPlaneTransactionTimeout as exc:
            raise ControlPlaneAdmissionRejected(
                reason="admission_lock_timeout",
                retry_after_seconds=config.retry_after_seconds,
                current_depth=-1,
                current_bytes=-1,
            ) from exc
        finally:
            QUEUE_ADMISSION_WAIT_SECONDS.observe(time.monotonic() - started)

    def admit_pending_task_assignment(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_payload: Mapping[str, Any],
        task_payload: Mapping[str, Any],
        config: AdmissionQueueConfig,
        now: datetime | None = None,
    ) -> TaskAdmissionResult:
        """Bound manual approval state without reserving runnable queue capacity."""
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
            with self.serialized(
                "task-pending-capacity",
                wait_seconds=config.admission_wait_seconds,
            ) as connection:
                existing = connection.execute(
                    f"""
                    SELECT request_sha256, response_payload
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
                    return TaskAdmissionResult(
                        queue_id="pending-approval",
                        task_payload=dict(existing["response_payload"]),
                        payload_bytes=0,
                        replayed=True,
                    )

                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_advisory_key("task-idempotency-capacity"),),
                )
                idempotency_depth = connection.execute(
                    f"""
                    SELECT count(*) AS depth
                    FROM {schema}.idempotency_keys
                    WHERE entity_kind='task_assignment'
                    """
                ).fetchone()
                if int(idempotency_depth["depth"]) >= config.idempotency_tombstone_max_rows:
                    raise ControlPlaneAdmissionRejected(
                        reason="idempotency_capacity_limit",
                        retry_after_seconds=config.retry_after_seconds,
                        current_depth=int(idempotency_depth["depth"]),
                        current_bytes=0,
                    )

                stale = connection.execute(
                    f"""
                    SELECT entity_id FROM {schema}.entities
                    WHERE entity_kind='task_assignment'
                      AND state='pending_confirmation'
                      AND updated_at <= %s
                    FOR UPDATE
                    """,
                    (
                        observed_at
                        - timedelta(seconds=config.pending_max_age_seconds),
                    ),
                ).fetchall()
                for row in stale:
                    self._update_task_runtime_locked(
                        connection,
                        task_id=str(row["entity_id"]),
                        status="blocked",
                        runtime_state="pending_approval_expired",
                        failure_reason="pending_approval_expired",
                        event="task_pending_approval_expired",
                        observed_at=observed_at,
                    )

                pending = connection.execute(
                    f"""
                    SELECT count(*) AS depth,
                           COALESCE(sum(pg_column_size(payload)), 0) AS bytes
                    FROM {schema}.entities
                    WHERE entity_kind='task_assignment'
                      AND state='pending_confirmation'
                    """
                ).fetchone()
                current_depth = int(pending["depth"])
                current_bytes = int(pending["bytes"])
                reason = None
                if current_depth + 1 > config.pending_max_depth:
                    reason = "pending_depth_limit"
                elif current_bytes + payload_bytes > config.pending_max_bytes:
                    reason = "pending_bytes_limit"
                if reason:
                    raise ControlPlaneAdmissionRejected(
                        reason=reason,
                        retry_after_seconds=config.retry_after_seconds,
                        current_depth=current_depth,
                        current_bytes=current_bytes,
                    )

                self._write_task_entity_locked(
                    connection,
                    task_payload,
                    replace_existing=False,
                )
                connection.execute(
                    f"""
                    INSERT INTO {schema}.idempotency_keys
                        (scope, idempotency_key, request_sha256, entity_kind, entity_id,
                         response_payload, retain_until)
                    VALUES (%s, %s, %s, 'task_assignment', %s, %s, %s)
                    """,
                    (
                        scope,
                        idempotency_key,
                        request_digest,
                        str(task_payload["task_id"]),
                        self._json(task_payload),
                        observed_at
                        + timedelta(
                            seconds=config.idempotency_tombstone_retention_seconds
                        ),
                    ),
                )
                return TaskAdmissionResult(
                    queue_id="pending-approval",
                    task_payload=dict(task_payload),
                    payload_bytes=payload_bytes,
                    replayed=False,
                )
        except ControlPlaneTransactionTimeout as exc:
            raise ControlPlaneAdmissionRejected(
                reason="admission_lock_timeout",
                retry_after_seconds=config.retry_after_seconds,
                current_depth=-1,
                current_bytes=-1,
            ) from exc
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
            resource_rows = connection.execute(
                f"""
                SELECT resource_class, state, count(*) AS depth,
                       COALESCE(sum(payload_bytes), 0) AS bytes
                FROM {schema}.task_admission_queue
                GROUP BY resource_class, state
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
        resource_state_counts: dict[str, dict[str, int]] = {}
        resource_state_bytes: dict[str, dict[str, int]] = {}
        for row in resource_rows:
            resource = str(row["resource_class"])
            state = str(row["state"])
            resource_state_counts.setdefault(resource, {})[state] = int(row["depth"])
            resource_state_bytes.setdefault(resource, {})[state] = int(row["bytes"])
        return TaskQueueSnapshot(
            active_depth=sum(state_counts.get(state, 0) for state in ACTIVE_QUEUE_STATES),
            active_bytes=sum(state_bytes.get(state, 0) for state in ACTIVE_QUEUE_STATES),
            oldest_age_seconds=max(0.0, float(oldest["age"] or 0.0)),
            state_counts=state_counts,
            state_bytes=state_bytes,
            resource_state_counts=resource_state_counts,
            resource_state_bytes=resource_state_bytes,
        )

    def verify_task_queue_cutover(
        self,
        *,
        mode: str,
        config: AdmissionQueueConfig,
    ) -> dict[str, int]:
        """Fail closed when durable/legacy ownership cannot be proven exclusive."""
        if mode not in {"durable", "legacy"}:
            raise ValueError(f"unsupported task queue ownership mode: {mode}")
        schema = _safe_identifier(self.configuration.schema)
        with self.serialized("task-queue-cutover") as connection:
            active = connection.execute(
                f"""
                SELECT count(*) AS depth
                FROM {schema}.task_admission_queue
                WHERE state = ANY(%s)
                """,
                (list(ACTIVE_QUEUE_STATES),),
            ).fetchone()
            active_depth = int(active["depth"])
            if mode == "legacy" and active_depth:
                raise ControlPlaneParityError(
                    "legacy task ownership cannot start while durable queue work is active"
                )
            stranded = connection.execute(
                f"""
                SELECT count(*) AS depth
                FROM {schema}.entities entity
                WHERE entity.entity_kind='task_assignment'
                  AND entity.state IN ('queued', 'running')
                  AND entity.payload->>'task_type'='airflow_dag_run'
                  AND NOT EXISTS (
                    SELECT 1 FROM {schema}.task_admission_queue queue
                    WHERE queue.task_id=entity.entity_id
                      AND queue.state = ANY(%s)
                  )
                """,
                (list(ACTIVE_QUEUE_STATES),),
            ).fetchone()
            stranded_depth = int(stranded["depth"])
            if mode == "durable" and stranded_depth:
                raise ControlPlaneParityError(
                    "durable task ownership found queued Airflow entities without queue rows"
                )
            pending = connection.execute(
                f"""
                SELECT count(*) AS depth,
                       COALESCE(sum(pg_column_size(payload)), 0) AS bytes
                FROM {schema}.entities
                WHERE entity_kind='task_assignment'
                  AND state='pending_confirmation'
                """
            ).fetchone()
            pending_depth = int(pending["depth"])
            pending_bytes = int(pending["bytes"])
            if mode == "durable" and (
                pending_depth > config.pending_max_depth
                or pending_bytes > config.pending_max_bytes
            ):
                raise ControlPlaneParityError(
                    "pending approval state exceeds the frozen durable cutover bounds"
                )
        return {
            "active_depth": active_depth,
            "stranded_depth": stranded_depth,
            "pending_depth": pending_depth,
            "pending_bytes": pending_bytes,
        }

    def task_mirror_parity(self) -> dict[str, Any]:
        """Compare PostgreSQL task authority with its bounded rollback mirror."""
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("task-mirror-parity") as connection:
            row = connection.execute(
                f"""
                SELECT
                    COALESCE(
                        (
                            SELECT jsonb_agg(payload ORDER BY entity_id)
                            FROM {schema}.entities
                            WHERE entity_kind='task_assignment'
                        ),
                        '[]'::jsonb
                    ) AS authority,
                    COALESCE(
                        (
                            SELECT payload
                            FROM {schema}.collections
                            WHERE collection_name='task_assignments'
                        ),
                        '[]'::jsonb
                    ) AS mirror
                """
            ).fetchone()
        entities = [dict(item) for item in row["authority"]]
        mirror_payload = row["mirror"]
        mirror = sorted(
            [dict(item) for item in mirror_payload if isinstance(item, Mapping)],
            key=lambda item: str(item.get("task_id", "")),
        )
        authority_digest = canonical_digest(entities)
        mirror_digest = canonical_digest(mirror)
        return {
            "authority_count": len(entities),
            "mirror_count": len(mirror),
            "authority_sha256": authority_digest,
            "mirror_sha256": mirror_digest,
            "matches": authority_digest == mirror_digest,
        }

    def task_queue_history_snapshot(self) -> TaskQueueHistorySnapshot:
        schema = _safe_identifier(self.configuration.schema)
        with self.transaction("task_queue_history_snapshot") as connection:
            queue = connection.execute(
                f"""
                SELECT count(*) AS rows,
                       COALESCE(sum(pg_column_size(task_admission_queue)), 0) AS bytes
                FROM {schema}.task_admission_queue
                """
            ).fetchone()
            effects = connection.execute(
                f"""
                SELECT count(*) AS rows,
                       COALESCE(sum(pg_column_size(task_dispatch_effects)), 0) AS bytes
                FROM {schema}.task_dispatch_effects
                """
            ).fetchone()
            tasks = connection.execute(
                f"""
                SELECT count(*) AS rows,
                       COALESCE(sum(pg_column_size(payload)), 0) AS bytes
                FROM {schema}.entities
                WHERE entity_kind='task_assignment'
                """
            ).fetchone()
            mirror = connection.execute(
                f"""
                SELECT COALESCE(jsonb_array_length(payload), 0) AS rows,
                       COALESCE(pg_column_size(payload), 0) AS bytes
                FROM {schema}.collections
                WHERE collection_name='task_assignments'
                """
            ).fetchone()
            idempotency = connection.execute(
                f"""
                SELECT count(*) AS rows,
                       COALESCE(sum(pg_column_size(idempotency_keys)), 0) AS bytes
                FROM {schema}.idempotency_keys
                WHERE entity_kind='task_assignment'
                """
            ).fetchone()
            rollups = connection.execute(
                f"""
                SELECT history_class, COALESCE(sum(item_count), 0) AS rows,
                       COALESCE(sum(payload_bytes), 0) AS bytes
                FROM {schema}.task_history_rollups
                GROUP BY history_class
                """
            ).fetchall()
        compacted_rows = {
            str(row["history_class"]): int(row["rows"]) for row in rollups
        }
        compacted_bytes = {
            str(row["history_class"]): int(row["bytes"]) for row in rollups
        }
        return TaskQueueHistorySnapshot(
            queue_rows=int(queue["rows"]),
            queue_bytes=int(queue["bytes"]),
            effect_rows=int(effects["rows"]),
            effect_bytes=int(effects["bytes"]),
            task_rows=int(tasks["rows"]) if tasks else 0,
            task_bytes=int(tasks["bytes"]) if tasks else 0,
            mirror_rows=int(mirror["rows"]) if mirror else 0,
            mirror_bytes=int(mirror["bytes"]) if mirror else 0,
            idempotency_rows=int(idempotency["rows"]) if idempotency else 0,
            idempotency_bytes=int(idempotency["bytes"]) if idempotency else 0,
            compacted_rows=compacted_rows,
            compacted_bytes=compacted_bytes,
        )

    def compact_task_queue_history(
        self,
        *,
        config: AdmissionQueueConfig,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Bound terminal queue/effect/task history while preserving active work."""
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        cutoff = observed_at - timedelta(seconds=config.terminal_queue_max_age_seconds)
        with self.serialized("task-queue-history-compaction") as connection:
            rows = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT queue_id, task_id, terminal_at,
                           row_number() OVER (
                               ORDER BY terminal_at DESC NULLS LAST, created_at DESC, queue_id
                           ) AS terminal_rank
                    FROM {schema}.task_admission_queue
                    WHERE state IN ('completed', 'failed', 'dlq', 'expired', 'cancelled')
                )
                SELECT queue_id, task_id
                FROM ranked
                WHERE terminal_rank > %s OR terminal_at < %s
                ORDER BY terminal_at NULLS FIRST, queue_id
                LIMIT %s
                """,
                (
                    min(
                        config.terminal_queue_max_rows,
                        config.task_history_max_terminal_rows,
                    ),
                    cutoff,
                    config.compaction_batch_size,
                ),
            ).fetchall()
            queue_ids = [str(row["queue_id"]) for row in rows]
            task_ids = [str(row["task_id"]) for row in rows]
            effect_rows = 0
            task_rows = 0
            if queue_ids:
                rolled_up = self._rollup_task_history_locked(
                    connection,
                    queue_ids=queue_ids,
                    task_ids=task_ids,
                )
                effect_rows = rolled_up["effect_rows"]
                task_rows = rolled_up["task_rows"]
                connection.execute(
                    f"DELETE FROM {schema}.task_admission_queue WHERE queue_id = ANY(%s)",
                    (queue_ids,),
                )
                connection.execute(
                    f"""
                    UPDATE {schema}.idempotency_keys
                    SET compacted_at=%s,
                        retain_until=GREATEST(
                            COALESCE(retain_until, %s),
                            %s
                        )
                    WHERE entity_kind='task_assignment' AND entity_id = ANY(%s)
                    """,
                    (
                        observed_at,
                        observed_at,
                        observed_at
                        + timedelta(
                            seconds=config.idempotency_tombstone_retention_seconds
                        ),
                        task_ids,
                    ),
                )
                connection.execute(
                    f"""
                    DELETE FROM {schema}.entities
                    WHERE entity_kind='task_assignment' AND entity_id = ANY(%s)
                    """,
                    (task_ids,),
                )

            stale_tasks = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT entity_id, updated_at,
                           row_number() OVER (
                               ORDER BY updated_at DESC, entity_id DESC
                           ) AS terminal_rank
                    FROM {schema}.entities entity
                    WHERE entity_kind='task_assignment'
                      AND state IN ('dry_run', 'done', 'failed', 'cancelled', 'blocked')
                      AND NOT EXISTS (
                          SELECT 1 FROM {schema}.task_admission_queue queue
                          WHERE queue.task_id=entity.entity_id
                            AND queue.state = ANY(%s)
                      )
                )
                SELECT entity_id FROM ranked
                WHERE terminal_rank > %s OR updated_at < %s
                ORDER BY updated_at, entity_id
                LIMIT %s
                """,
                (
                    list(ACTIVE_QUEUE_STATES),
                    config.task_history_max_terminal_rows,
                    cutoff,
                    config.compaction_batch_size,
                ),
            ).fetchall()
            stale_task_ids = [str(row["entity_id"]) for row in stale_tasks]
            if stale_task_ids:
                rolled_up = self._rollup_task_history_locked(
                    connection,
                    queue_ids=[],
                    task_ids=stale_task_ids,
                )
                task_rows += rolled_up["task_rows"]
                connection.execute(
                    f"""
                    DELETE FROM {schema}.entities
                    WHERE entity_kind='task_assignment' AND entity_id = ANY(%s)
                    """,
                    (stale_task_ids,),
                )
                connection.execute(
                    f"""
                    UPDATE {schema}.idempotency_keys
                    SET compacted_at=%s,
                        retain_until=GREATEST(
                            COALESCE(retain_until, %s),
                            %s
                        )
                    WHERE entity_kind='task_assignment' AND entity_id = ANY(%s)
                    """,
                    (
                        observed_at,
                        observed_at,
                        observed_at
                        + timedelta(
                            seconds=config.idempotency_tombstone_retention_seconds
                        ),
                        stale_task_ids,
                    ),
                )
            removed_idempotency = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT scope, idempotency_key, retain_until,
                           row_number() OVER (
                               ORDER BY compacted_at DESC NULLS LAST,
                                        created_at DESC, scope, idempotency_key
                           ) AS tombstone_rank
                    FROM {schema}.idempotency_keys
                    WHERE entity_kind='task_assignment'
                      AND compacted_at IS NOT NULL
                ), removable AS (
                    SELECT scope, idempotency_key
                    FROM ranked
                    WHERE retain_until <= %s
                    ORDER BY retain_until NULLS FIRST, scope, idempotency_key
                    LIMIT %s
                )
                DELETE FROM {schema}.idempotency_keys target
                USING removable
                WHERE target.scope=removable.scope
                  AND target.idempotency_key=removable.idempotency_key
                """,
                (
                    observed_at,
                    config.compaction_batch_size,
                ),
            ).rowcount
        return {
            "queue_rows": len(queue_ids),
            "effect_rows": effect_rows,
            "task_rows": task_rows,
            "idempotency_rows": int(removed_idempotency),
        }

    def _rollup_task_history_locked(
        self,
        connection: Any,
        *,
        queue_ids: Sequence[str],
        task_ids: Sequence[str],
    ) -> dict[str, int]:
        schema = _safe_identifier(self.configuration.schema)
        totals = {"queue_rows": 0, "effect_rows": 0, "task_rows": 0}
        groups: list[tuple[str, str, int, int]] = []
        if queue_ids:
            queue_groups = connection.execute(
                f"""
                SELECT state, count(*) AS rows,
                       COALESCE(sum(payload_bytes), 0) AS bytes
                FROM {schema}.task_admission_queue
                WHERE queue_id = ANY(%s)
                GROUP BY state
                """,
                (list(queue_ids),),
            ).fetchall()
            effect_groups = connection.execute(
                f"""
                SELECT state, count(*) AS rows,
                       COALESCE(sum(pg_column_size(task_dispatch_effects)), 0) AS bytes
                FROM {schema}.task_dispatch_effects
                WHERE queue_id = ANY(%s)
                GROUP BY state
                """,
                (list(queue_ids),),
            ).fetchall()
            for row in queue_groups:
                count = int(row["rows"])
                totals["queue_rows"] += count
                groups.append(("queue", str(row["state"]), count, int(row["bytes"])))
            for row in effect_groups:
                count = int(row["rows"])
                totals["effect_rows"] += count
                groups.append(("effect", str(row["state"]), count, int(row["bytes"])))
        if task_ids:
            task_groups = connection.execute(
                f"""
                SELECT state, count(*) AS rows,
                       COALESCE(sum(pg_column_size(payload)), 0) AS bytes
                FROM {schema}.entities
                WHERE entity_kind='task_assignment' AND entity_id = ANY(%s)
                GROUP BY state
                """,
                (list(task_ids),),
            ).fetchall()
            for row in task_groups:
                count = int(row["rows"])
                totals["task_rows"] += count
                groups.append(("task", str(row["state"]), count, int(row["bytes"])))
        for history_class, terminal_state, count, payload_bytes in groups:
            connection.execute(
                f"""
                INSERT INTO {schema}.task_history_rollups
                    (history_class, terminal_state, item_count, payload_bytes)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (history_class, terminal_state) DO UPDATE
                SET item_count={schema}.task_history_rollups.item_count + EXCLUDED.item_count,
                    payload_bytes={schema}.task_history_rollups.payload_bytes + EXCLUDED.payload_bytes,
                    updated_at=clock_timestamp()
                """,
                (history_class, terminal_state, count, payload_bytes),
            )
        return totals

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

    def get_task_queue_item(
        self,
        *,
        queue_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any] | None:
        if (queue_id is None) == (task_id is None):
            raise ValueError("exactly one queue identity is required")
        schema = _safe_identifier(self.configuration.schema)
        field = "queue_id" if queue_id is not None else "task_id"
        value = queue_id if queue_id is not None else task_id
        with self.transaction("task_queue_get") as connection:
            row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE {field}=%s",
                (value,),
            ).fetchone()
        return self._queue_row(row) if row is not None else None

    def get_task_dispatch_effect(
        self,
        *,
        queue_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any] | None:
        if (queue_id is None) == (task_id is None):
            raise ValueError("exactly one task dispatch effect identity is required")
        schema = _safe_identifier(self.configuration.schema)
        field = "queue_id" if queue_id is not None else "task_id"
        value = queue_id if queue_id is not None else task_id
        with self.transaction("task_dispatch_effect_get") as connection:
            row = connection.execute(
                f"SELECT * FROM {schema}.task_dispatch_effects WHERE {field}=%s",
                (value,),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        if payload.get("runtime_payload") is not None:
            payload["runtime_payload"] = dict(payload["runtime_payload"])
        for key in ("created_at", "updated_at"):
            if payload.get(key) is not None:
                payload[key] = payload[key].isoformat()
        return payload

    def reconcile_task_queue(
        self,
        *,
        config: AdmissionQueueConfig,
        include_transitions: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        outcome = {"expired": 0, "requeued": 0, "dlq": 0, "outcome_unknown": 0}
        transitions: list[dict[str, str]] = []
        with self.serialized("task-queue-reconciliation") as connection:
            rows = connection.execute(
                f"""
                SELECT queue.*, effect.state AS effect_state
                FROM {schema}.task_admission_queue queue
                LEFT JOIN {schema}.task_dispatch_effects effect
                  ON effect.queue_id=queue.queue_id
                WHERE queue.state = ANY(%s)
                  AND (
                    (
                      queue.state IN ('available', 'retry_wait', 'leased')
                      AND queue.deadline_at <= %s
                    )
                    OR (
                      queue.state='leased'
                      AND queue.lease_expires_at <= %s
                    )
                    OR (
                      queue.state='runtime_pending'
                      AND queue.runtime_pending_at IS NOT NULL
                      AND queue.runtime_pending_at + (%s * interval '1 second') <= %s
                    )
                  )
                FOR UPDATE OF queue
                """,
                (
                    list(ACTIVE_QUEUE_STATES),
                    observed_at,
                    observed_at,
                    config.runtime_terminal_timeout_seconds,
                    observed_at,
                ),
            ).fetchall()
            for row in rows:
                queue_id = str(row["queue_id"])
                effect_state = str(row["effect_state"] or "")
                external_effect_may_exist = effect_state in {
                    "submitting",
                    "submitted",
                    "outcome_unknown",
                }
                if str(row["state"]) == "runtime_pending" or (
                    row["deadline_at"] <= observed_at and external_effect_may_exist
                ):
                    connection.execute(
                        f"""
                        UPDATE {schema}.task_admission_queue
                        SET state='outcome_unknown',
                            terminal_reason=NULL, terminal_at=NULL,
                            outcome_unknown_at=COALESCE(outcome_unknown_at, %s),
                            next_runtime_poll_at=%s,
                            lease_owner=NULL, lease_expires_at=NULL,
                            updated_at=clock_timestamp()
                        WHERE queue_id=%s
                        """,
                        (observed_at, observed_at, queue_id),
                    )
                    if effect_state:
                        connection.execute(
                            f"""
                            UPDATE {schema}.task_dispatch_effects
                            SET state='outcome_unknown', updated_at=clock_timestamp()
                            WHERE queue_id=%s AND state <> 'terminal'
                            """,
                            (queue_id,),
                        )
                    outcome["outcome_unknown"] += 1
                    self._update_task_runtime_locked(
                        connection,
                        task_id=str(row["task_id"]),
                        status="running",
                        runtime_state="outcome_unknown",
                        failure_reason="runtime_terminal_timeout",
                        event="task_runtime_outcome_unknown",
                        observed_at=observed_at,
                    )
                    transitions.append(
                        {
                            "queue_id": queue_id,
                            "task_id": str(row["task_id"]),
                            "state": "outcome_unknown",
                            "reason": "runtime_terminal_timeout",
                        }
                    )
                elif row["deadline_at"] <= observed_at:
                    if effect_state == "reserved":
                        connection.execute(
                            f"""
                            UPDATE {schema}.task_dispatch_effects
                            SET state='failed', runtime_state='deadline_exceeded',
                                updated_at=clock_timestamp()
                            WHERE queue_id=%s
                            """,
                            (queue_id,),
                        )
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
                    self._update_task_runtime_locked(
                        connection,
                        task_id=str(row["task_id"]),
                        status="failed",
                        runtime_state="expired",
                        failure_reason="deadline_exceeded",
                        event="task_queue_expired",
                        observed_at=observed_at,
                    )
                    transitions.append(
                        {
                            "queue_id": queue_id,
                            "task_id": str(row["task_id"]),
                            "state": "expired",
                            "reason": "deadline_exceeded",
                        }
                    )
                else:
                    retry = self._reschedule_locked_queue_item(
                        connection,
                        row,
                        failure_class="owner_lost",
                        transient=True,
                        config=config,
                        observed_at=observed_at,
                    )
                    state = str(retry["state"])
                    if state == "retry_wait":
                        outcome["requeued"] += 1
                    elif state == "dlq":
                        outcome["dlq"] += 1
                    transitions.append(
                        {
                            "queue_id": queue_id,
                            "task_id": str(row["task_id"]),
                            "state": state,
                            "reason": str(retry.get("terminal_reason") or "owner_lost"),
                        }
                    )
        if include_transitions:
            return {**outcome, "transitions": transitions}
        return outcome

    def claim_task_queue_items(
        self,
        *,
        owner: str,
        max_items: int,
        max_bytes: int,
        lease_seconds: float,
        scan_limit: int,
        resource_class: str | None = None,
        max_outstanding: int | None = None,
        now: datetime | None = None,
    ) -> list[TaskQueueLease]:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        leases: list[TaskQueueLease] = []
        selected_bytes = 0
        resource_filter = ""
        parameters: list[Any] = [observed_at, observed_at]
        if resource_class is not None:
            if resource_class not in {"cpu", "gpu"}:
                raise ValueError(f"unsupported queue resource class: {resource_class}")
            resource_filter = "AND resource_class=%s"
            parameters.append(resource_class)
        parameters.append(scan_limit)
        with self.transaction("task_queue_claim") as connection:
            if resource_class is not None and max_outstanding is not None:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_advisory_key(f"task-queue-outstanding:{resource_class}"),),
                )
                outstanding = connection.execute(
                    f"""
                    SELECT COUNT(*) AS depth
                    FROM {schema}.task_admission_queue
                    WHERE resource_class=%s
                      AND state IN ('leased', 'runtime_pending', 'outcome_unknown')
                    """,
                    (resource_class,),
                ).fetchone()
                max_items = min(
                    max_items,
                    max(0, max_outstanding - int(outstanding["depth"])),
                )
                if max_items <= 0:
                    return []
            rows = connection.execute(
                f"""
                SELECT * FROM {schema}.task_admission_queue
                WHERE state IN ('available', 'retry_wait')
                  AND available_at <= %s AND deadline_at > %s
                  {resource_filter}
                ORDER BY priority DESC, available_at, created_at, queue_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                tuple(parameters),
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
                        lease_expires_at=%s, claim_count=claim_count + 1,
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
                        resource_class=str(row["resource_class"]),
                        claim_count=int(row["claim_count"]) + 1,
                        attempt_count=int(row["attempt_count"]),
                        lease_owner=owner,
                        lease_epoch=lease_epoch,
                        lease_expires_at=lease_expires_at.isoformat(),
                        deadline_at=row["deadline_at"].isoformat(),
                    )
                )
        return leases

    def begin_task_queue_attempt(
        self,
        lease: TaskQueueLease,
        *,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> TaskQueueLease:
        """Count an execution only when a claimed item actually starts."""
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        expires_at = observed_at + timedelta(seconds=lease_seconds)
        with self.transaction("task_queue_begin_attempt") as connection:
            row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(row, lease, observed_at)
            updated = connection.execute(
                f"""
                UPDATE {schema}.task_admission_queue
                SET attempt_count=attempt_count + 1, execution_started_at=%s,
                    lease_expires_at=%s, updated_at=clock_timestamp()
                WHERE queue_id=%s
                RETURNING attempt_count
                """,
                (observed_at, expires_at, lease.queue_id),
            ).fetchone()
        return TaskQueueLease(
            queue_id=lease.queue_id,
            task_id=lease.task_id,
            task_payload=lease.task_payload,
            payload_bytes=lease.payload_bytes,
            resource_class=lease.resource_class,
            claim_count=lease.claim_count,
            attempt_count=int(updated["attempt_count"]),
            lease_owner=lease.lease_owner,
            lease_epoch=lease.lease_epoch,
            lease_expires_at=expires_at.isoformat(),
            deadline_at=lease.deadline_at,
        )

    def renew_task_queue_lease(
        self,
        lease: TaskQueueLease,
        *,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> TaskQueueLease:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        expires_at = observed_at + timedelta(seconds=lease_seconds)
        with self.transaction("task_queue_lease_renew") as connection:
            row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(row, lease, observed_at)
            connection.execute(
                f"""
                UPDATE {schema}.task_admission_queue
                SET lease_expires_at=%s, updated_at=clock_timestamp()
                WHERE queue_id=%s
                """,
                (expires_at, lease.queue_id),
            )
        return TaskQueueLease(
            queue_id=lease.queue_id,
            task_id=lease.task_id,
            task_payload=lease.task_payload,
            payload_bytes=lease.payload_bytes,
            resource_class=lease.resource_class,
            claim_count=lease.claim_count,
            attempt_count=lease.attempt_count,
            lease_owner=lease.lease_owner,
            lease_epoch=lease.lease_epoch,
            lease_expires_at=expires_at.isoformat(),
            deadline_at=lease.deadline_at,
        )

    def assert_task_queue_lease(
        self,
        lease: TaskQueueLease,
        *,
        now: datetime | None = None,
    ) -> None:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        with self.transaction("task_queue_lease_assert") as connection:
            row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(row, lease, observed_at)

    def load_task_queue_lease(
        self,
        *,
        queue_id: str,
        task_id: str,
        lease_owner: str,
        lease_epoch: int,
        now: datetime | None = None,
    ) -> TaskQueueLease:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        with self.transaction("task_queue_lease_load") as connection:
            row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (queue_id,),
            ).fetchone()
            if row is None:
                raise ControlPlaneLeaseConflict("task_queue_item_missing")
            lease = TaskQueueLease(
                queue_id=str(row["queue_id"]),
                task_id=str(row["task_id"]),
                task_payload=dict(row["task_payload"]),
                payload_bytes=int(row["payload_bytes"]),
                resource_class=str(row["resource_class"]),
                claim_count=int(row["claim_count"]),
                attempt_count=int(row["attempt_count"]),
                lease_owner=str(row["lease_owner"] or ""),
                lease_epoch=int(row["lease_epoch"]),
                lease_expires_at=(
                    row["lease_expires_at"].isoformat()
                    if row["lease_expires_at"] is not None
                    else ""
                ),
                deadline_at=row["deadline_at"].isoformat(),
            )
            if (
                lease.task_id != task_id
                or lease.lease_owner != lease_owner
                or lease.lease_epoch != lease_epoch
            ):
                raise ControlPlaneLeaseConflict("task_queue_lease_identity_mismatch")
            self._assert_queue_lease(row, lease, observed_at)
            return lease

    def reserve_task_dispatch_effect(
        self,
        lease: TaskQueueLease,
        *,
        dag_id: str,
        dag_run_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        effect_key = canonical_digest(
            {
                "effect": "airflow_dag_run",
                "task_id": lease.task_id,
                "dag_id": dag_id,
                "dag_run_id": dag_run_id,
            }
        )
        with self.transaction("task_dispatch_effect_reserve") as connection:
            queue_row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(queue_row, lease, observed_at)
            row = connection.execute(
                f"""
                SELECT * FROM {schema}.task_dispatch_effects
                WHERE effect_key=%s OR queue_id=%s
                FOR UPDATE
                """,
                (effect_key, lease.queue_id),
            ).fetchone()
            if row is not None:
                if (
                    str(row["effect_key"]) != effect_key
                    or str(row["task_id"]) != lease.task_id
                    or str(row["dag_id"]) != dag_id
                    or str(row["dag_run_id"]) != dag_run_id
                ):
                    raise ControlPlaneLeaseConflict(
                        "task_dispatch_effect_identity_mismatch"
                    )
                replayed = str(row["state"]) in {"submitted", "terminal"}
                if not replayed:
                    connection.execute(
                        f"""
                        UPDATE {schema}.task_dispatch_effects
                        SET lease_owner=%s, lease_epoch=%s,
                            updated_at=clock_timestamp()
                        WHERE effect_key=%s
                        """,
                        (lease.lease_owner, lease.lease_epoch, effect_key),
                    )
                return {
                    "effect_key": effect_key,
                    "state": str(row["state"]),
                    "replayed": replayed,
                    "dag_id": dag_id,
                    "dag_run_id": dag_run_id,
                }
            connection.execute(
                f"""
                INSERT INTO {schema}.task_dispatch_effects
                    (effect_key, queue_id, task_id, dag_id, dag_run_id, state,
                     lease_owner, lease_epoch)
                VALUES (%s, %s, %s, %s, %s, 'reserved', %s, %s)
                """,
                (
                    effect_key,
                    lease.queue_id,
                    lease.task_id,
                    dag_id,
                    dag_run_id,
                    lease.lease_owner,
                    lease.lease_epoch,
                ),
            )
        return {
            "effect_key": effect_key,
            "state": "reserved",
            "replayed": False,
            "dag_id": dag_id,
            "dag_run_id": dag_run_id,
        }

    def mark_task_dispatch_effect_submitting(
        self,
        lease: TaskQueueLease,
        *,
        effect_key: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Fence the last local step before the external Airflow mutation."""
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        with self.transaction("task_dispatch_effect_submitting") as connection:
            queue_row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(queue_row, lease, observed_at)
            effect_row = connection.execute(
                f"""
                SELECT * FROM {schema}.task_dispatch_effects
                WHERE effect_key=%s FOR UPDATE
                """,
                (effect_key,),
            ).fetchone()
            if effect_row is None:
                raise ControlPlaneLeaseConflict("task_dispatch_effect_missing")
            if (
                str(effect_row["queue_id"]) != lease.queue_id
                or str(effect_row["lease_owner"]) != lease.lease_owner
                or int(effect_row["lease_epoch"]) != lease.lease_epoch
            ):
                raise ControlPlaneLeaseConflict("task_dispatch_effect_fence_mismatch")
            state = str(effect_row["state"])
            if state in {"submitted", "terminal"}:
                return {"effect_key": effect_key, "state": state, "replayed": True}
            if state in {"failed", "outcome_unknown"}:
                raise ControlPlaneLeaseConflict(
                    f"task_dispatch_effect_not_submittable:{state}"
                )
            connection.execute(
                f"""
                UPDATE {schema}.task_dispatch_effects
                SET state='submitting', updated_at=clock_timestamp()
                WHERE effect_key=%s
                """,
                (effect_key,),
            )
        return {"effect_key": effect_key, "state": "submitting", "replayed": False}

    def commit_task_dispatch_effect(
        self,
        lease: TaskQueueLease,
        *,
        effect_key: str,
        runtime_state: str,
        runtime_payload: Mapping[str, Any],
        task_payload: Mapping[str, Any],
        terminal: bool,
        succeeded: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        queue_state = (
            "completed" if succeeded else "failed"
        ) if terminal else "runtime_pending"
        reason = (
            f"runtime_terminal:{runtime_state}"
            if terminal
            else "runtime_dispatch_submitted"
        )
        effect_state = "terminal" if terminal else "submitted"
        with self.transaction("task_dispatch_effect_commit") as connection:
            queue_row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(queue_row, lease, observed_at)
            effect_row = connection.execute(
                f"""
                SELECT * FROM {schema}.task_dispatch_effects
                WHERE effect_key=%s FOR UPDATE
                """,
                (effect_key,),
            ).fetchone()
            if effect_row is None:
                raise ControlPlaneLeaseConflict("task_dispatch_effect_missing")
            if (
                str(effect_row["lease_owner"]) != lease.lease_owner
                or int(effect_row["lease_epoch"]) != lease.lease_epoch
                or str(effect_row["queue_id"]) != lease.queue_id
            ):
                raise ControlPlaneLeaseConflict("task_dispatch_effect_fence_mismatch")
            connection.execute(
                f"""
                UPDATE {schema}.task_dispatch_effects
                SET state=%s, runtime_state=%s, runtime_payload=%s,
                    updated_at=clock_timestamp()
                WHERE effect_key=%s
                """,
                (effect_state, runtime_state, self._json(runtime_payload), effect_key),
            )
            connection.execute(
                f"""
                UPDATE {schema}.task_admission_queue
                SET state=%s,
                    terminal_reason=CASE WHEN %s THEN %s ELSE NULL END,
                    terminal_at=CASE WHEN %s THEN %s ELSE NULL END,
                    runtime_pending_at=CASE WHEN %s THEN NULL ELSE %s END,
                    next_runtime_poll_at=CASE WHEN %s THEN NULL ELSE %s END,
                    outcome_unknown_at=NULL,
                    lease_owner=NULL, lease_expires_at=NULL,
                    updated_at=clock_timestamp()
                WHERE queue_id=%s
                """,
                (
                    queue_state,
                    terminal,
                    reason,
                    terminal,
                    observed_at,
                    terminal,
                    observed_at,
                    terminal,
                    observed_at,
                    lease.queue_id,
                ),
            )
            self._write_task_entity_locked(
                connection,
                task_payload,
                replace_existing=True,
            )
        return {
            "queue_id": lease.queue_id,
            "effect_key": effect_key,
            "state": queue_state,
            "terminal_reason": reason,
            "runtime_state": runtime_state,
        }

    def claim_runtime_pending_for_poll(
        self,
        *,
        max_items: int,
        poll_interval_seconds: float,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Reserve a fair bounded poll batch without holding locks across HTTP."""
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        next_poll = observed_at + timedelta(seconds=poll_interval_seconds)
        with self.transaction("task_runtime_poll_claim") as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM {schema}.task_admission_queue
                WHERE state IN ('runtime_pending', 'outcome_unknown')
                  AND COALESCE(next_runtime_poll_at, runtime_pending_at, created_at) <= %s
                ORDER BY COALESCE(next_runtime_poll_at, runtime_pending_at, created_at),
                         runtime_poll_count, queue_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (observed_at, max_items),
            ).fetchall()
            if rows:
                connection.execute(
                    f"""
                    UPDATE {schema}.task_admission_queue
                    SET next_runtime_poll_at=%s,
                        runtime_poll_count=runtime_poll_count + 1,
                        updated_at=clock_timestamp()
                    WHERE queue_id = ANY(%s)
                    """,
                    (next_poll, [str(row["queue_id"]) for row in rows]),
                )
        return [self._queue_row(row) for row in rows]

    def complete_runtime_pending_task(
        self,
        *,
        queue_id: str,
        task_id: str,
        runtime_state: str,
        succeeded: bool,
        task_payload: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        queue_state = "completed" if succeeded else "failed"
        with self.transaction("task_runtime_pending_complete") as connection:
            row = connection.execute(
                f"""
                SELECT * FROM {schema}.task_admission_queue
                WHERE queue_id=%s FOR UPDATE
                """,
                (queue_id,),
            ).fetchone()
            if row is None or str(row["task_id"]) != task_id:
                raise ControlPlaneLeaseConflict("runtime_pending_identity_mismatch")
            if str(row["state"]) not in {"runtime_pending", "outcome_unknown"}:
                raise ControlPlaneLeaseConflict("task_queue_item_not_runtime_pending")
            connection.execute(
                f"""
                UPDATE {schema}.task_admission_queue
                SET state=%s, terminal_reason=%s, terminal_at=%s,
                    next_runtime_poll_at=NULL, outcome_unknown_at=NULL,
                    updated_at=clock_timestamp()
                WHERE queue_id=%s
                """,
                (
                    queue_state,
                    f"runtime_terminal:{runtime_state}",
                    observed_at,
                    queue_id,
                ),
            )
            connection.execute(
                f"""
                UPDATE {schema}.task_dispatch_effects
                SET state='terminal', runtime_state=%s, updated_at=clock_timestamp()
                WHERE queue_id=%s
                """,
                (runtime_state, queue_id),
            )
            if task_payload is not None:
                if str(task_payload.get("task_id")) != task_id:
                    raise ControlPlaneLeaseConflict(
                        "runtime_pending_task_payload_identity_mismatch"
                    )
                self._write_task_entity_locked(
                    connection,
                    task_payload,
                    replace_existing=True,
                )
            else:
                self._update_task_runtime_locked(
                    connection,
                    task_id=task_id,
                    status="done" if succeeded else "failed",
                    runtime_state=runtime_state,
                    failure_reason=None if succeeded else runtime_state,
                    event="task_runtime_terminal",
                    observed_at=observed_at,
                )
        return {
            "queue_id": queue_id,
            "task_id": task_id,
            "state": queue_state,
            "runtime_state": runtime_state,
        }

    def resolve_missing_outcome_unknown(
        self,
        *,
        queue_id: str,
        task_id: str,
        timeout_seconds: float,
        minimum_polls: int = 3,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Close a local unknown only after a bounded, reachable 404 observation window."""
        schema = _safe_identifier(self.configuration.schema)
        observed_at = now or utc_now()
        with self.transaction("task_runtime_missing_resolution") as connection:
            row = connection.execute(
                f"""
                SELECT * FROM {schema}.task_admission_queue
                WHERE queue_id=%s FOR UPDATE
                """,
                (queue_id,),
            ).fetchone()
            if row is None or str(row["task_id"]) != task_id:
                raise ControlPlaneLeaseConflict("outcome_unknown_identity_mismatch")
            if str(row["state"]) != "outcome_unknown":
                return {"queue_id": queue_id, "state": str(row["state"])}
            unknown_at = row["outcome_unknown_at"] or row["updated_at"]
            elapsed = (observed_at - unknown_at).total_seconds()
            if elapsed < timeout_seconds or int(row["runtime_poll_count"]) < minimum_polls:
                return {
                    "queue_id": queue_id,
                    "state": "outcome_unknown",
                    "elapsed_seconds": max(0.0, elapsed),
                    "poll_count": int(row["runtime_poll_count"]),
                }
            reason = "external_effect_not_found_after_timeout"
            connection.execute(
                f"""
                UPDATE {schema}.task_admission_queue
                SET state='failed', terminal_reason=%s, terminal_at=%s,
                    next_runtime_poll_at=NULL, updated_at=clock_timestamp()
                WHERE queue_id=%s
                """,
                (reason, observed_at, queue_id),
            )
            connection.execute(
                f"""
                UPDATE {schema}.task_dispatch_effects
                SET state='failed', runtime_state=%s, updated_at=clock_timestamp()
                WHERE queue_id=%s AND state <> 'terminal'
                """,
                (reason, queue_id),
            )
            self._update_task_runtime_locked(
                connection,
                task_id=task_id,
                status="failed",
                runtime_state="failed",
                failure_reason=reason,
                event="task_runtime_missing_terminal",
                observed_at=observed_at,
            )
        return {
            "queue_id": queue_id,
            "task_id": task_id,
            "state": "failed",
            "terminal_reason": reason,
        }

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
            self._update_task_runtime_locked(
                connection,
                task_id=lease.task_id,
                status="done" if state == "completed" else "failed",
                runtime_state=state,
                failure_reason=None if state == "completed" else reason,
                event="task_queue_terminal",
                observed_at=observed_at,
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
        with self.serialized(f"task-queue-retry-budget:{config.retry_budget_scope}") as connection:
            row = connection.execute(
                f"SELECT * FROM {schema}.task_admission_queue WHERE queue_id=%s FOR UPDATE",
                (lease.queue_id,),
            ).fetchone()
            self._assert_queue_lease(row, lease, observed_at)
            return self._reschedule_locked_queue_item(
                connection,
                row,
                failure_class=failure_class,
                transient=transient,
                config=config,
                observed_at=observed_at,
            )

    def _reschedule_locked_queue_item(
        self,
        connection: Any,
        row: Mapping[str, Any],
        *,
        failure_class: str,
        transient: bool,
        config: AdmissionQueueConfig,
        observed_at: datetime,
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        queue_id = str(row["queue_id"])
        if str(row["retry_budget_scope"]) != config.retry_budget_scope:
            raise ControlPlaneStoreError(
                "task queue retry-budget scope differs from the frozen runtime config"
            )
        effect = connection.execute(
            f"""
            SELECT state FROM {schema}.task_dispatch_effects
            WHERE queue_id=%s FOR UPDATE
            """,
            (queue_id,),
        ).fetchone()
        effect_may_exist = effect is not None and str(effect["state"]) in {
            "submitting",
            "submitted",
            "outcome_unknown",
        }
        if not transient:
            return self._finish_locked_queue_item(
                connection,
                queue_id,
                state="dlq",
                reason=f"permanent:{failure_class}",
                failure_class=failure_class,
                observed_at=observed_at,
            )
        if int(row["attempt_count"]) >= config.max_attempts:
            if effect_may_exist:
                return self._mark_locked_outcome_unknown(
                    connection,
                    row,
                    reason=f"attempts_exhausted:{failure_class}",
                    observed_at=observed_at,
                )
            return self._finish_locked_queue_item(
                connection,
                queue_id,
                state="dlq",
                reason=f"attempts_exhausted:{failure_class}",
                failure_class=failure_class,
                observed_at=observed_at,
            )
        budget_name = f"task-dispatch:{config.retry_budget_scope}"
        connection.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (_advisory_key(f"task-queue-retry-budget:{config.retry_budget_scope}"),),
        )
        budget = connection.execute(
            f"""
            SELECT * FROM {schema}.task_retry_budget
            WHERE budget_name=%s FOR UPDATE
            """,
            (budget_name,),
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
            if effect_may_exist:
                return self._mark_locked_outcome_unknown(
                    connection,
                    row,
                    reason=f"retry_budget_exhausted:{failure_class}",
                    observed_at=observed_at,
                )
            return self._finish_locked_queue_item(
                connection,
                queue_id,
                state="dlq",
                reason=f"retry_budget_exhausted:{failure_class}",
                failure_class=failure_class,
                observed_at=observed_at,
            )
        connection.execute(
            f"""
            INSERT INTO {schema}.task_retry_budget
                (budget_name, window_started_at, consumed)
            VALUES (%s, %s, %s)
            ON CONFLICT (budget_name) DO UPDATE
            SET window_started_at=EXCLUDED.window_started_at,
                consumed=EXCLUDED.consumed,
                updated_at=clock_timestamp()
            """,
            (budget_name, window_started_at, consumed + 1),
        )
        attempt_count = int(row["attempt_count"])
        base = min(
            config.backoff_max_seconds,
            config.backoff_base_seconds * (2 ** max(0, attempt_count - 1)),
        )
        digest = hashlib.sha256(
            f"{queue_id}:{attempt_count}:{config.retry_budget_scope}".encode("utf-8")
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
            (available_at, failure_class, queue_id),
        )
        self._update_task_runtime_locked(
            connection,
            task_id=str(row["task_id"]),
            status="queued",
            runtime_state="retry_wait",
            failure_reason=failure_class,
            event="task_queue_retry_scheduled",
            observed_at=observed_at,
        )
        return {
            "queue_id": queue_id,
            "state": "retry_wait",
            "failure_class": failure_class,
            "delay_seconds": delay,
            "available_at": available_at.isoformat(),
        }

    def _mark_locked_outcome_unknown(
        self,
        connection: Any,
        row: Mapping[str, Any],
        *,
        reason: str,
        observed_at: datetime,
    ) -> dict[str, Any]:
        schema = _safe_identifier(self.configuration.schema)
        queue_id = str(row["queue_id"])
        connection.execute(
            f"""
            UPDATE {schema}.task_admission_queue
            SET state='outcome_unknown', outcome_unknown_at=%s,
                next_runtime_poll_at=%s, terminal_reason=NULL, terminal_at=NULL,
                lease_owner=NULL, lease_expires_at=NULL,
                last_failure_class=%s, updated_at=clock_timestamp()
            WHERE queue_id=%s
            """,
            (observed_at, observed_at, reason, queue_id),
        )
        connection.execute(
            f"""
            UPDATE {schema}.task_dispatch_effects
            SET state='outcome_unknown', runtime_state=%s,
                updated_at=clock_timestamp()
            WHERE queue_id=%s AND state <> 'terminal'
            """,
            (reason, queue_id),
        )
        self._update_task_runtime_locked(
            connection,
            task_id=str(row["task_id"]),
            status="running",
            runtime_state="outcome_unknown",
            failure_reason=reason,
            event="task_runtime_outcome_unknown",
            observed_at=observed_at,
        )
        return {
            "queue_id": queue_id,
            "state": "outcome_unknown",
            "failure_class": reason,
        }

    def _write_task_entity_locked(
        self,
        connection: Any,
        task_payload: Mapping[str, Any],
        *,
        replace_existing: bool,
    ) -> None:
        schema = _safe_identifier(self.configuration.schema)
        task_id = str(task_payload["task_id"])
        row = connection.execute(
            f"""
            SELECT version, payload FROM {schema}.entities
            WHERE entity_kind='task_assignment' AND entity_id=%s FOR UPDATE
            """,
            (task_id,),
        ).fetchone()
        incoming_version = int(task_payload.get("version", 1))
        incoming_state = str(task_payload.get("status", "unknown"))
        if row is None:
            connection.execute(
                f"""
                INSERT INTO {schema}.entities
                    (entity_kind, entity_id, version, state, payload)
                VALUES ('task_assignment', %s, %s, %s, %s)
                """,
                (task_id, incoming_version, incoming_state, self._json(task_payload)),
            )
            return
        if not replace_existing:
            raise ControlPlaneVersionConflict(f"task_assignment/{task_id} already exists")
        current_version = int(row["version"])
        if incoming_version != current_version + 1:
            raise ControlPlaneVersionConflict(
                f"task_assignment/{task_id} expected version {current_version + 1}, "
                f"received {incoming_version}"
            )
        changed = connection.execute(
            f"""
            UPDATE {schema}.entities
            SET version=%s, state=%s, payload=%s, updated_at=clock_timestamp()
            WHERE entity_kind='task_assignment' AND entity_id=%s AND version=%s
            """,
            (
                incoming_version,
                incoming_state,
                self._json(task_payload),
                task_id,
                current_version,
            ),
        )
        if changed.rowcount != 1:
            raise ControlPlaneVersionConflict(
                f"concurrent task_assignment version conflict for {task_id}"
            )

    def _update_task_runtime_locked(
        self,
        connection: Any,
        *,
        task_id: str,
        status: str,
        runtime_state: str,
        failure_reason: str | None,
        event: str,
        observed_at: datetime,
    ) -> bool:
        schema = _safe_identifier(self.configuration.schema)
        row = connection.execute(
            f"""
            SELECT version, payload FROM {schema}.entities
            WHERE entity_kind='task_assignment' AND entity_id=%s FOR UPDATE
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return False
        item = dict(row["payload"])
        current_version = int(row["version"])
        item["status"] = status
        item["runtime_state"] = runtime_state
        item["failure_reason"] = failure_reason
        item["version"] = current_version + 1
        if status in {"done", "failed", "cancelled"}:
            item["finished_at"] = observed_at.isoformat().replace("+00:00", "Z")
        audit_log = list(item.get("audit") or [])
        audit_log.append(
            {
                "timestamp": observed_at.isoformat().replace("+00:00", "Z"),
                "actor": "task-queue-reconciler",
                "event": event,
                "details": {
                    "status": status,
                    "runtime_state": runtime_state,
                    "failure_reason": failure_reason,
                },
            }
        )
        item["audit"] = audit_log
        changed = connection.execute(
            f"""
            UPDATE {schema}.entities
            SET version=%s, state=%s, payload=%s, updated_at=clock_timestamp()
            WHERE entity_kind='task_assignment' AND entity_id=%s AND version=%s
            """,
            (
                current_version + 1,
                status,
                self._json(item),
                task_id,
                current_version,
            ),
        )
        if changed.rowcount != 1:
            raise ControlPlaneVersionConflict(
                f"concurrent task_assignment runtime conflict for {task_id}"
            )
        return True

    def _refresh_task_collection_locked(self, connection: Any) -> int:
        """Keep the PostgreSQL rollback collection atomic with task authority writes."""
        schema = _safe_identifier(self.configuration.schema)
        row = connection.execute(
            f"""
            INSERT INTO {schema}.collections(collection_name, version, payload)
            SELECT 'task_assignments', 1,
                   COALESCE(
                       jsonb_agg(payload ORDER BY entity_id),
                       '[]'::jsonb
                   )
            FROM {schema}.entities
            WHERE entity_kind='task_assignment'
            ON CONFLICT (collection_name) DO UPDATE
            SET version={schema}.collections.version + 1,
                payload=EXCLUDED.payload,
                updated_at=clock_timestamp()
            RETURNING version
            """
        ).fetchone()
        return int(row["version"])

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
        if row["deadline_at"] <= observed_at:
            raise ControlPlaneDeadlineExceeded("task_queue_deadline_exceeded")

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
        row = connection.execute(
            f"SELECT task_id FROM {schema}.task_admission_queue WHERE queue_id=%s",
            (queue_id,),
        ).fetchone()
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
        if row is not None:
            self._update_task_runtime_locked(
                connection,
                task_id=str(row["task_id"]),
                status="failed",
                runtime_state=state,
                failure_reason=reason,
                event="task_queue_terminal",
                observed_at=observed_at,
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
            "execution_started_at",
            "runtime_pending_at",
            "next_runtime_poll_at",
            "outcome_unknown_at",
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
        INSERT INTO {schema}.entities
            (entity_kind, entity_id, version, state, payload, created_at, updated_at)
        SELECT 'task_assignment', item->>'task_id',
               GREATEST(1, COALESCE((item->>'version')::bigint, 1)),
               COALESCE(item->>'status', 'unknown'), item,
               COALESCE((item->>'created_at')::timestamptz, clock_timestamp()),
               COALESCE(
                   (item->>'finished_at')::timestamptz,
                   (item->>'dispatched_at')::timestamptz,
                   (item->>'queued_at')::timestamptz,
                   (item->>'created_at')::timestamptz,
                   clock_timestamp()
               )
        FROM {schema}.collections collection
        CROSS JOIN LATERAL jsonb_array_elements(collection.payload) item
        WHERE collection.collection_name='task_assignments'
          AND item ? 'task_id'
          AND NOT EXISTS (
              SELECT 1 FROM {schema}.schema_migrations
              WHERE version='004_task_entity_storage'
          )
        ON CONFLICT (entity_kind, entity_id) DO NOTHING
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.idempotency_keys (
            scope text NOT NULL,
            idempotency_key text NOT NULL,
            request_sha256 char(64) NOT NULL,
            entity_kind text NOT NULL,
            entity_id text NOT NULL,
            response_payload jsonb NOT NULL,
            compacted_at timestamptz,
            retain_until timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (scope, idempotency_key)
        )
        """,
        f"""
        ALTER TABLE {schema}.idempotency_keys
            ADD COLUMN IF NOT EXISTS compacted_at timestamptz,
            ADD COLUMN IF NOT EXISTS retain_until timestamptz
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idempotency_entity_idx
        ON {schema}.idempotency_keys(entity_kind, entity_id)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idempotency_retention_idx
        ON {schema}.idempotency_keys(compacted_at, retain_until, created_at)
        WHERE compacted_at IS NOT NULL
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
                state IN ('available', 'retry_wait', 'leased', 'runtime_pending',
                          'completed', 'failed', 'dlq', 'expired', 'cancelled')
            ),
            priority smallint NOT NULL,
            payload_bytes bigint NOT NULL CHECK (payload_bytes > 0),
            task_payload jsonb NOT NULL,
            resource_class text NOT NULL DEFAULT 'cpu'
                CHECK (resource_class IN ('cpu', 'gpu')),
            claim_count integer NOT NULL DEFAULT 0 CHECK (claim_count >= 0),
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            retry_budget_scope text NOT NULL DEFAULT 's2-bounded-queue-v3',
            available_at timestamptz NOT NULL,
            deadline_at timestamptz NOT NULL,
            lease_owner text,
            lease_epoch bigint NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
            lease_expires_at timestamptz,
            execution_started_at timestamptz,
            runtime_pending_at timestamptz,
            next_runtime_poll_at timestamptz,
            runtime_poll_count integer NOT NULL DEFAULT 0 CHECK (runtime_poll_count >= 0),
            outcome_unknown_at timestamptz,
            last_failure_class text,
            terminal_reason text,
            terminal_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (idempotency_scope, idempotency_key)
        )
        """,
        f"""
        ALTER TABLE {schema}.task_admission_queue
            ADD COLUMN IF NOT EXISTS resource_class text NOT NULL DEFAULT 'cpu',
            ADD COLUMN IF NOT EXISTS claim_count integer NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS retry_budget_scope text NOT NULL
                DEFAULT 's2-bounded-queue-v3',
            ADD COLUMN IF NOT EXISTS execution_started_at timestamptz,
            ADD COLUMN IF NOT EXISTS runtime_pending_at timestamptz,
            ADD COLUMN IF NOT EXISTS next_runtime_poll_at timestamptz,
            ADD COLUMN IF NOT EXISTS runtime_poll_count integer NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS outcome_unknown_at timestamptz
        """,
        f"""
        ALTER TABLE {schema}.task_admission_queue
            ALTER COLUMN retry_budget_scope SET DEFAULT 's2-bounded-queue-v3'
        """,
        f"""
        UPDATE {schema}.task_admission_queue
        SET resource_class = CASE
            WHEN (
                 lower(COALESCE(task_payload->>'resource_profile', '')) LIKE '%gpu%'
              OR lower(COALESCE(task_payload->>'resource_profile', '')) LIKE '%cuda%'
              OR lower(COALESCE(task_payload->>'resource_profile', '')) LIKE '%rtx%'
              OR lower(COALESCE(task_payload->>'resource_profile', '')) LIKE '%accelerator%'
            )
              OR lower(COALESCE(task_payload->'config_payload'->>'resource_class', '')) = 'gpu'
            THEN 'gpu'
            ELSE 'cpu'
        END
        WHERE resource_class IS DISTINCT FROM CASE
            WHEN (
                 lower(COALESCE(task_payload->>'resource_profile', '')) LIKE '%gpu%'
              OR lower(COALESCE(task_payload->>'resource_profile', '')) LIKE '%cuda%'
              OR lower(COALESCE(task_payload->>'resource_profile', '')) LIKE '%rtx%'
              OR lower(COALESCE(task_payload->>'resource_profile', '')) LIKE '%accelerator%'
            )
              OR lower(COALESCE(task_payload->'config_payload'->>'resource_class', '')) = 'gpu'
            THEN 'gpu'
            ELSE 'cpu'
        END
        """,
        f"""
        ALTER TABLE {schema}.task_admission_queue
            DROP CONSTRAINT IF EXISTS task_admission_queue_state_check
        """,
        f"""
        ALTER TABLE {schema}.task_admission_queue
            ADD CONSTRAINT task_admission_queue_state_check CHECK (
                state IN ('available', 'retry_wait', 'leased', 'runtime_pending',
                          'outcome_unknown', 'completed', 'failed', 'dlq', 'expired',
                          'cancelled')
            )
        """,
        f"""
        ALTER TABLE {schema}.task_admission_queue
            DROP CONSTRAINT IF EXISTS task_admission_queue_resource_class_check
        """,
        f"""
        ALTER TABLE {schema}.task_admission_queue
            ADD CONSTRAINT task_admission_queue_resource_class_check
            CHECK (resource_class IN ('cpu', 'gpu'))
        """,
        f"""
        CREATE INDEX IF NOT EXISTS task_admission_claim_idx
        ON {schema}.task_admission_queue(state, available_at, priority DESC, created_at)
        WHERE state IN ('available', 'retry_wait')
        """,
        f"""
        CREATE INDEX IF NOT EXISTS task_admission_active_idx
        ON {schema}.task_admission_queue(state, deadline_at, lease_expires_at)
        WHERE state IN ('available', 'retry_wait', 'leased', 'runtime_pending',
                        'outcome_unknown')
        """,
        f"""
        CREATE INDEX IF NOT EXISTS task_admission_resource_claim_idx
        ON {schema}.task_admission_queue(
            resource_class, state, available_at, priority DESC, created_at
        )
        WHERE state IN ('available', 'retry_wait')
        """,
        f"""
        CREATE INDEX IF NOT EXISTS task_runtime_poll_idx
        ON {schema}.task_admission_queue(
            next_runtime_poll_at, runtime_pending_at, created_at, queue_id
        )
        WHERE state IN ('runtime_pending', 'outcome_unknown')
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.task_dispatch_effects (
            effect_key char(64) PRIMARY KEY,
            queue_id text NOT NULL UNIQUE
                REFERENCES {schema}.task_admission_queue(queue_id) ON DELETE CASCADE,
            task_id text NOT NULL,
            dag_id text NOT NULL,
            dag_run_id text NOT NULL,
            state text NOT NULL CHECK (
                state IN ('reserved', 'submitting', 'submitted', 'terminal',
                          'failed', 'outcome_unknown')
            ),
            lease_owner text NOT NULL,
            lease_epoch bigint NOT NULL CHECK (lease_epoch >= 1),
            runtime_state text,
            runtime_payload jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (task_id, dag_id, dag_run_id)
        )
        """,
        f"""
        ALTER TABLE {schema}.task_dispatch_effects
            DROP CONSTRAINT IF EXISTS task_dispatch_effects_state_check
        """,
        f"""
        ALTER TABLE {schema}.task_dispatch_effects
            ADD CONSTRAINT task_dispatch_effects_state_check CHECK (
                state IN ('reserved', 'submitting', 'submitted', 'terminal',
                          'failed', 'outcome_unknown')
            )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS task_dispatch_effect_state_idx
        ON {schema}.task_dispatch_effects(state, updated_at)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.task_retry_budget (
            budget_name text PRIMARY KEY,
            window_started_at timestamptz NOT NULL,
            consumed integer NOT NULL CHECK (consumed >= 0),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.task_history_rollups (
            history_class text NOT NULL,
            terminal_state text NOT NULL,
            item_count bigint NOT NULL DEFAULT 0 CHECK (item_count >= 0),
            payload_bytes bigint NOT NULL DEFAULT 0 CHECK (payload_bytes >= 0),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (history_class, terminal_state)
        )
        """,
    )
