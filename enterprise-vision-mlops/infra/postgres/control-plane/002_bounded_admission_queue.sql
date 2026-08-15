CREATE TABLE IF NOT EXISTS evm_control_plane.task_admission_queue (
    queue_id text PRIMARY KEY,
    task_id text NOT NULL UNIQUE,
    idempotency_scope text NOT NULL,
    idempotency_key text NOT NULL,
    request_sha256 char(64) NOT NULL,
    state text NOT NULL CHECK (
        state IN ('available', 'retry_wait', 'leased', 'completed', 'failed', 'dlq', 'expired', 'cancelled')
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
);

CREATE INDEX IF NOT EXISTS task_admission_claim_idx
    ON evm_control_plane.task_admission_queue(state, available_at, priority DESC, created_at)
    WHERE state IN ('available', 'retry_wait');

CREATE INDEX IF NOT EXISTS task_admission_active_idx
    ON evm_control_plane.task_admission_queue(state, deadline_at, lease_expires_at)
    WHERE state IN ('available', 'retry_wait', 'leased');

CREATE TABLE IF NOT EXISTS evm_control_plane.task_retry_budget (
    budget_name text PRIMARY KEY,
    window_started_at timestamptz NOT NULL,
    consumed integer NOT NULL CHECK (consumed >= 0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO evm_control_plane.schema_migrations(version)
VALUES ('002_bounded_admission_queue')
ON CONFLICT (version) DO NOTHING;
