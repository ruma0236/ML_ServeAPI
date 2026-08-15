INSERT INTO evm_control_plane.entities
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
FROM evm_control_plane.collections collection
CROSS JOIN LATERAL jsonb_array_elements(collection.payload) item
WHERE collection.collection_name='task_assignments'
  AND item ? 'task_id'
ON CONFLICT (entity_kind, entity_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS evm_control_plane.task_history_rollups (
    history_class text NOT NULL,
    terminal_state text NOT NULL,
    item_count bigint NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    payload_bytes bigint NOT NULL DEFAULT 0 CHECK (payload_bytes >= 0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (history_class, terminal_state)
);

INSERT INTO evm_control_plane.schema_migrations(version)
VALUES ('004_task_entity_storage')
ON CONFLICT (version) DO NOTHING;
