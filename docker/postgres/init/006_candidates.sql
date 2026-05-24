CREATE TABLE IF NOT EXISTS utterance_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seed TEXT NOT NULL,
    generated_text TEXT,
    generated_audio BYTEA,
    priority DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    urgent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    spoken_at TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ,
    maturity INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    context_tags TEXT[] NOT NULL DEFAULT '{}',
    CONSTRAINT utterance_candidates_maturity_check
        CHECK (maturity IN (0, 1, 2)),
    CONSTRAINT utterance_candidates_maturity2_payload_check
        CHECK (maturity <> 2 OR (generated_text IS NOT NULL AND generated_audio IS NOT NULL)),
    CONSTRAINT utterance_candidates_terminal_once_check
        CHECK (spoken_at IS NULL OR dismissed_at IS NULL)
);

CREATE INDEX IF NOT EXISTS utterance_candidates_active_idx
    ON utterance_candidates (priority DESC, created_at ASC)
    WHERE spoken_at IS NULL AND dismissed_at IS NULL;

CREATE INDEX IF NOT EXISTS utterance_candidates_expires_at_idx
    ON utterance_candidates (expires_at)
    WHERE spoken_at IS NULL AND dismissed_at IS NULL;

CREATE INDEX IF NOT EXISTS utterance_candidates_source_idx
    ON utterance_candidates (source, created_at DESC);

CREATE INDEX IF NOT EXISTS utterance_candidates_context_tags_gin_idx
    ON utterance_candidates
    USING gin (context_tags);

CREATE TABLE IF NOT EXISTS arrival_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until TIMESTAMPTZ NOT NULL,
    context_snapshot JSONB NOT NULL,
    behavior TEXT NOT NULL,
    utterance_text TEXT,
    utterance_audio BYTEA,
    used_at TIMESTAMPTZ,
    CONSTRAINT arrival_candidates_behavior_check
        CHECK (behavior IN ('speak_first', 'wait_silent', 'subtle_react'))
);

CREATE INDEX IF NOT EXISTS arrival_candidates_fresh_idx
    ON arrival_candidates (device_id, computed_at DESC)
    WHERE used_at IS NULL;

CREATE INDEX IF NOT EXISTS arrival_candidates_valid_until_idx
    ON arrival_candidates (valid_until)
    WHERE used_at IS NULL;

CREATE INDEX IF NOT EXISTS arrival_candidates_context_snapshot_gin_idx
    ON arrival_candidates
    USING gin (context_snapshot jsonb_path_ops);
