CREATE TABLE IF NOT EXISTS diary_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    diary_date DATE NOT NULL,
    body_text TEXT NOT NULL,
    source_session_ids UUID[] NOT NULL DEFAULT '{}',
    source_candidate_ids UUID[] NOT NULL DEFAULT '{}',
    mood TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_diary_entries_diary_date_created_at
    ON diary_entries (diary_date DESC, created_at DESC);
