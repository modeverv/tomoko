CREATE TABLE IF NOT EXISTS persona_lexicon_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_session_id UUID REFERENCES conversation_sessions(id),
    previous_version_id UUID REFERENCES persona_lexicon_versions(id),
    reason TEXT NOT NULL,
    lexicon_json JSONB NOT NULL,
    diff_json JSONB NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'completed'
);

CREATE TABLE IF NOT EXISTS persona_state_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_session_id UUID REFERENCES conversation_sessions(id),
    previous_version_id UUID REFERENCES persona_state_versions(id),
    reason TEXT NOT NULL,
    state_json JSONB NOT NULL,
    diff_json JSONB NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'completed'
);

CREATE UNIQUE INDEX IF NOT EXISTS persona_lexicon_versions_version_idx
    ON persona_lexicon_versions (version);

CREATE UNIQUE INDEX IF NOT EXISTS persona_state_versions_version_idx
    ON persona_state_versions (version);

CREATE INDEX IF NOT EXISTS persona_lexicon_versions_source_session_idx
    ON persona_lexicon_versions (source_session_id)
    WHERE source_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS persona_state_versions_source_session_idx
    ON persona_state_versions (source_session_id)
    WHERE source_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS persona_lexicon_versions_json_gin_idx
    ON persona_lexicon_versions
    USING gin (lexicon_json jsonb_path_ops);

CREATE INDEX IF NOT EXISTS persona_state_versions_json_gin_idx
    ON persona_state_versions
    USING gin (state_json jsonb_path_ops);

CREATE INDEX IF NOT EXISTS persona_lexicon_versions_diff_json_gin_idx
    ON persona_lexicon_versions
    USING gin (diff_json jsonb_path_ops);

CREATE INDEX IF NOT EXISTS persona_state_versions_diff_json_gin_idx
    ON persona_state_versions
    USING gin (diff_json jsonb_path_ops);

CREATE INDEX IF NOT EXISTS persona_lexicon_versions_created_at_idx
    ON persona_lexicon_versions (created_at DESC);

CREATE INDEX IF NOT EXISTS persona_state_versions_created_at_idx
    ON persona_state_versions (created_at DESC);
