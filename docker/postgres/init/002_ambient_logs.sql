CREATE TABLE IF NOT EXISTS ambient_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id TEXT NOT NULL,
    speaker TEXT,
    transcript TEXT NOT NULL,
    audio_level_db DOUBLE PRECISION NOT NULL,
    is_final BOOLEAN NOT NULL DEFAULT TRUE,
    tomoko_participated BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ambient_logs_recorded_at_idx
    ON ambient_logs (recorded_at DESC);

CREATE INDEX IF NOT EXISTS ambient_logs_device_recorded_at_idx
    ON ambient_logs (device_id, recorded_at DESC);
