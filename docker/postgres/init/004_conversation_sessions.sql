CREATE TABLE IF NOT EXISTS conversation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    start_reason TEXT NOT NULL,
    end_reason TEXT,
    device_id TEXT NOT NULL,
    summary_text TEXT,
    summary_status TEXT NOT NULL DEFAULT 'not_ready',
    summary_model TEXT,
    summary_generated_at TIMESTAMPTZ,
    summary_embedding vector(384),
    summary_embedding_model TEXT,
    summary_embedded_at TIMESTAMPTZ,
    summary_error TEXT
);

ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS summary_text TEXT;

ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS summary_status TEXT NOT NULL DEFAULT 'not_ready';

ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS summary_model TEXT;

ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS summary_generated_at TIMESTAMPTZ;

ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS summary_embedding vector(384);

ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS summary_embedding_model TEXT;

ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS summary_embedded_at TIMESTAMPTZ;

ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS summary_error TEXT;

ALTER TABLE conversation_logs
    ADD COLUMN IF NOT EXISTS conversation_session_id UUID NULL;

ALTER TABLE conversation_logs
    DROP CONSTRAINT IF EXISTS conversation_logs_conversation_session_id_fkey;

ALTER TABLE conversation_logs
    ADD CONSTRAINT conversation_logs_conversation_session_id_fkey
    FOREIGN KEY (conversation_session_id)
    REFERENCES conversation_sessions(id);

CREATE INDEX IF NOT EXISTS conversation_sessions_device_started_at_idx
    ON conversation_sessions (device_id, started_at DESC);

CREATE INDEX IF NOT EXISTS conversation_sessions_summary_status_idx
    ON conversation_sessions (summary_status, ended_at)
    WHERE ended_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS conversation_logs_session_recorded_at_idx
    ON conversation_logs (conversation_session_id, recorded_at DESC)
    WHERE conversation_session_id IS NOT NULL;
