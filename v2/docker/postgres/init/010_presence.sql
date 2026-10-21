CREATE TABLE IF NOT EXISTS presence_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    audio_level_db DOUBLE PRECISION NOT NULL,
    transcript_id UUID,
    transcript_text TEXT,
    is_speaking BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS presence_reports_observed_at_idx
    ON presence_reports (observed_at DESC);

CREATE INDEX IF NOT EXISTS presence_reports_device_observed_at_idx
    ON presence_reports (device_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS edge_status (
    device_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    role TEXT NOT NULL DEFAULT 'edge',
    detail TEXT
);

CREATE INDEX IF NOT EXISTS edge_status_last_seen_at_idx
    ON edge_status (last_seen_at DESC);
