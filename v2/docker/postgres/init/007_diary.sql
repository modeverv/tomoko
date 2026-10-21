CREATE TABLE IF NOT EXISTS diary_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    diary_date DATE NOT NULL,
    body_text TEXT NOT NULL,
    diary_version INTEGER NOT NULL DEFAULT 1,
    source_session_ids UUID[] NOT NULL DEFAULT '{}',
    source_candidate_ids UUID[] NOT NULL DEFAULT '{}',
    mood TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE diary_entries
    ADD COLUMN IF NOT EXISTS diary_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE diary_entries
    ADD COLUMN IF NOT EXISTS source_world_observation_interpretation_ids UUID[]
    NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_diary_entries_diary_date_version
    ON diary_entries (diary_date, diary_version);

CREATE INDEX IF NOT EXISTS idx_diary_entries_diary_date_created_at
    ON diary_entries (diary_date DESC, created_at DESC);
