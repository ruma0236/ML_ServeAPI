ALTER TABLE evm_control_plane.task_admission_queue
    ADD COLUMN IF NOT EXISTS resource_class text NOT NULL DEFAULT 'cpu',
    ADD COLUMN IF NOT EXISTS claim_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retry_budget_scope text NOT NULL DEFAULT 's2-bounded-queue-v2',
    ADD COLUMN IF NOT EXISTS execution_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS runtime_pending_at timestamptz;

UPDATE evm_control_plane.task_admission_queue
SET resource_class = CASE
    WHEN lower(COALESCE(task_payload->>'resource_profile', '')) LIKE '%gpu%'
      OR lower(COALESCE(task_payload->'config_payload'->>'resource_class', '')) = 'gpu'
    THEN 'gpu'
    ELSE 'cpu'
END;

ALTER TABLE evm_control_plane.task_admission_queue
    DROP CONSTRAINT IF EXISTS task_admission_queue_state_check;
ALTER TABLE evm_control_plane.task_admission_queue
    ADD CONSTRAINT task_admission_queue_state_check CHECK (
        state IN ('available', 'retry_wait', 'leased', 'runtime_pending', 'completed',
                  'failed', 'dlq', 'expired', 'cancelled')
    );
ALTER TABLE evm_control_plane.task_admission_queue
    DROP CONSTRAINT IF EXISTS task_admission_queue_resource_class_check;
ALTER TABLE evm_control_plane.task_admission_queue
    ADD CONSTRAINT task_admission_queue_resource_class_check
    CHECK (resource_class IN ('cpu', 'gpu'));

CREATE INDEX IF NOT EXISTS task_admission_resource_claim_idx
    ON evm_control_plane.task_admission_queue(
        resource_class, state, available_at, priority DESC, created_at
    )
    WHERE state IN ('available', 'retry_wait');

CREATE TABLE IF NOT EXISTS evm_control_plane.task_dispatch_effects (
    effect_key char(64) PRIMARY KEY,
    queue_id text NOT NULL UNIQUE
        REFERENCES evm_control_plane.task_admission_queue(queue_id) ON DELETE CASCADE,
    task_id text NOT NULL,
    dag_id text NOT NULL,
    dag_run_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('reserved', 'submitted', 'terminal', 'failed')),
    lease_owner text NOT NULL,
    lease_epoch bigint NOT NULL CHECK (lease_epoch >= 1),
    runtime_state text,
    runtime_payload jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (task_id, dag_id, dag_run_id)
);

CREATE INDEX IF NOT EXISTS task_dispatch_effect_state_idx
    ON evm_control_plane.task_dispatch_effects(state, updated_at);

INSERT INTO evm_control_plane.schema_migrations(version)
VALUES ('003_task_queue_safety')
ON CONFLICT (version) DO NOTHING;
