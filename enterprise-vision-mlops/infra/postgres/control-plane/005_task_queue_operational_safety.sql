ALTER TABLE evm_control_plane.idempotency_keys
    ADD COLUMN IF NOT EXISTS compacted_at timestamptz,
    ADD COLUMN IF NOT EXISTS retain_until timestamptz;

CREATE INDEX IF NOT EXISTS idempotency_retention_idx
    ON evm_control_plane.idempotency_keys(compacted_at, retain_until, created_at)
    WHERE compacted_at IS NOT NULL;

ALTER TABLE evm_control_plane.task_admission_queue
    ADD COLUMN IF NOT EXISTS next_runtime_poll_at timestamptz,
    ADD COLUMN IF NOT EXISTS runtime_poll_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS outcome_unknown_at timestamptz;

ALTER TABLE evm_control_plane.task_admission_queue
    ALTER COLUMN retry_budget_scope SET DEFAULT 's2-bounded-queue-v3';

UPDATE evm_control_plane.task_admission_queue
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
END;

ALTER TABLE evm_control_plane.task_admission_queue
    DROP CONSTRAINT IF EXISTS task_admission_queue_state_check;
ALTER TABLE evm_control_plane.task_admission_queue
    ADD CONSTRAINT task_admission_queue_state_check CHECK (
        state IN ('available', 'retry_wait', 'leased', 'runtime_pending',
                  'outcome_unknown', 'completed', 'failed', 'dlq', 'expired',
                  'cancelled')
    );

CREATE INDEX IF NOT EXISTS task_runtime_poll_idx
    ON evm_control_plane.task_admission_queue(
        next_runtime_poll_at, runtime_pending_at, created_at, queue_id
    )
    WHERE state IN ('runtime_pending', 'outcome_unknown');

ALTER TABLE evm_control_plane.task_dispatch_effects
    DROP CONSTRAINT IF EXISTS task_dispatch_effects_state_check;
ALTER TABLE evm_control_plane.task_dispatch_effects
    ADD CONSTRAINT task_dispatch_effects_state_check CHECK (
        state IN ('reserved', 'submitting', 'submitted', 'terminal',
                  'failed', 'outcome_unknown')
    );

INSERT INTO evm_control_plane.schema_migrations(version)
VALUES ('005_task_queue_operational_safety')
ON CONFLICT (version) DO NOTHING;
