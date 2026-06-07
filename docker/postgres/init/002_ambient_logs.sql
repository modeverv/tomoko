CREATE TABLE IF NOT EXISTS ambient_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id TEXT NOT NULL,
    speaker TEXT,
    transcript TEXT NOT NULL,
    audio_level_db DOUBLE PRECISION NOT NULL,
    is_final BOOLEAN NOT NULL DEFAULT TRUE,
    tomoko_participated BOOLEAN NOT NULL DEFAULT FALSE,
    attention_mode TEXT NOT NULL DEFAULT 'ambient',
    attended BOOLEAN NOT NULL DEFAULT FALSE,
    participation_mode TEXT NOT NULL DEFAULT 'observer'
);

ALTER TABLE ambient_logs
    ADD COLUMN IF NOT EXISTS attention_mode TEXT NOT NULL DEFAULT 'ambient';

ALTER TABLE ambient_logs
    ADD COLUMN IF NOT EXISTS attended BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE ambient_logs
    ADD COLUMN IF NOT EXISTS participation_mode TEXT NOT NULL DEFAULT 'observer';

CREATE INDEX IF NOT EXISTS ambient_logs_recorded_at_idx
    ON ambient_logs (recorded_at DESC);

CREATE INDEX IF NOT EXISTS ambient_logs_device_recorded_at_idx
    ON ambient_logs (device_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS conversation_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id TEXT NOT NULL,
    speaker TEXT,
    role TEXT NOT NULL,
    transcript TEXT NOT NULL,
    emotion TEXT,
    participation_mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed'
);

ALTER TABLE conversation_logs
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'completed';

ALTER TABLE conversation_logs
    ADD COLUMN IF NOT EXISTS llm_prompt_content TEXT NULL;

CREATE INDEX IF NOT EXISTS conversation_logs_recorded_at_idx
    ON conversation_logs (recorded_at DESC);

CREATE INDEX IF NOT EXISTS conversation_logs_device_recorded_at_idx
    ON conversation_logs (device_id, recorded_at DESC);
