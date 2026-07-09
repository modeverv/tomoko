CREATE TABLE IF NOT EXISTS v2_sense_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL CHECK (kind IN ('screenshot', 'camera_presence', 'world_search', 'calendar_lookup')),
    query text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'claimed', 'done', 'failed')),
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    requested_by text NOT NULL DEFAULT '',
    requested_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE INDEX IF NOT EXISTS idx_v2_sense_requests_pending
    ON v2_sense_requests (kind, requested_at)
    WHERE status = 'pending';
