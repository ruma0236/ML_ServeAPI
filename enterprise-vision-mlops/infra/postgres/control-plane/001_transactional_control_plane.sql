CREATE SCHEMA IF NOT EXISTS evm_control_plane;

CREATE TABLE IF NOT EXISTS evm_control_plane.schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS evm_control_plane.entities (
    entity_kind text NOT NULL,
    entity_id text NOT NULL,
    version bigint NOT NULL CHECK (version >= 1),
    state text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (entity_kind, entity_id)
);

CREATE INDEX IF NOT EXISTS entities_kind_state_updated_idx
    ON evm_control_plane.entities(entity_kind, state, updated_at DESC);

CREATE TABLE IF NOT EXISTS evm_control_plane.collections (
    collection_name text PRIMARY KEY,
    version bigint NOT NULL CHECK (version >= 1),
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS evm_control_plane.idempotency_keys (
    scope text NOT NULL,
    idempotency_key text NOT NULL,
    request_sha256 char(64) NOT NULL,
    entity_kind text NOT NULL,
    entity_id text NOT NULL,
    response_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (scope, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idempotency_entity_idx
    ON evm_control_plane.idempotency_keys(entity_kind, entity_id);

CREATE TABLE IF NOT EXISTS evm_control_plane.lifecycle_claims (
    run_id text PRIMARY KEY,
    claim_epoch bigint NOT NULL CHECK (claim_epoch >= 1),
    claim_id text NOT NULL,
    expires_at timestamptz NOT NULL,
    released_at timestamptz,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS lifecycle_claim_expiry_idx
    ON evm_control_plane.lifecycle_claims(released_at, expires_at);

CREATE TABLE IF NOT EXISTS evm_control_plane.side_effect_outbox (
    side_effect_key char(64) PRIMARY KEY,
    lifecycle_run_id text NOT NULL,
    state text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS side_effect_run_state_idx
    ON evm_control_plane.side_effect_outbox(lifecycle_run_id, state, created_at);

INSERT INTO evm_control_plane.schema_migrations(version)
VALUES ('001_transactional_control_plane')
ON CONFLICT (version) DO NOTHING;
