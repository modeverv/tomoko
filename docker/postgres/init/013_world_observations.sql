CREATE TABLE IF NOT EXISTS world_observation_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_file_path TEXT NOT NULL,
    sha256_checksum TEXT NOT NULL UNIQUE,
    generated_by TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'pending',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    parse_issues_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT world_observation_documents_status_check
        CHECK (status IN ('pending', 'normalizing', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS world_observation_documents_status_idx
    ON world_observation_documents (status, imported_at ASC);

CREATE INDEX IF NOT EXISTS world_observation_documents_observed_at_idx
    ON world_observation_documents (observed_at DESC);

CREATE INDEX IF NOT EXISTS world_observation_documents_metadata_gin_idx
    ON world_observation_documents
    USING GIN (metadata_json);

CREATE TABLE IF NOT EXISTS world_observation_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES world_observation_documents(id)
        ON DELETE CASCADE,
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_hint TEXT NOT NULL,
    freshness TEXT NOT NULL DEFAULT 'unknown',
    confidence DOUBLE PRECISION NOT NULL,
    item_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_excerpt TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT world_observation_items_freshness_check
        CHECK (freshness IN ('breaking', 'fresh', 'recent', 'stale', 'unknown')),
    CONSTRAINT world_observation_items_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE INDEX IF NOT EXISTS world_observation_items_document_idx
    ON world_observation_items (document_id, created_at ASC);

CREATE INDEX IF NOT EXISTS world_observation_items_topic_idx
    ON world_observation_items (topic, created_at DESC);

CREATE INDEX IF NOT EXISTS world_observation_items_item_json_gin_idx
    ON world_observation_items
    USING GIN (item_json);

CREATE TABLE IF NOT EXISTS world_observation_interpretations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL UNIQUE REFERENCES world_observation_items(id)
        ON DELETE CASCADE,
    persona_state_version_id UUID NULL REFERENCES persona_state_versions(id),
    persona_lexicon_version_id UUID NULL REFERENCES persona_lexicon_versions(id),
    relevance_to_user DOUBLE PRECISION NOT NULL,
    tomoko_interest DOUBLE PRECISION NOT NULL,
    emotional_tone TEXT NOT NULL DEFAULT 'neutral',
    memory_value DOUBLE PRECISION NOT NULL,
    speakability_hint TEXT NOT NULL,
    interpretation_text TEXT NOT NULL,
    reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT world_observation_interpretations_tone_check
        CHECK (emotional_tone IN (
            'neutral',
            'hopeful',
            'concerned',
            'curious',
            'playful',
            'sad'
        )),
    CONSTRAINT world_observation_interpretations_relevance_check
        CHECK (relevance_to_user >= 0.0 AND relevance_to_user <= 1.0),
    CONSTRAINT world_observation_interpretations_interest_check
        CHECK (tomoko_interest >= 0.0 AND tomoko_interest <= 1.0),
    CONSTRAINT world_observation_interpretations_memory_check
        CHECK (memory_value >= 0.0 AND memory_value <= 1.0)
);

CREATE INDEX IF NOT EXISTS world_observation_interpretations_interest_idx
    ON world_observation_interpretations (
        tomoko_interest DESC,
        relevance_to_user DESC,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS world_observation_interpretations_reason_gin_idx
    ON world_observation_interpretations
    USING GIN (reason_json);

ALTER TABLE diary_entries
    ADD COLUMN IF NOT EXISTS source_world_observation_interpretation_ids UUID[]
    NOT NULL DEFAULT '{}';

ALTER TABLE conversation_logs
    ADD COLUMN IF NOT EXISTS source_candidate_id UUID NULL;

CREATE OR REPLACE VIEW world_observation_trace AS
SELECT
    d.id AS document_id,
    d.raw_file_path,
    d.sha256_checksum,
    d.generated_by,
    d.observed_at,
    i.id AS item_id,
    i.topic,
    i.title,
    i.summary,
    i.freshness,
    i.source_hint,
    i.confidence,
    p.id AS interpretation_id,
    p.persona_state_version_id,
    p.persona_lexicon_version_id,
    p.relevance_to_user,
    p.tomoko_interest,
    p.emotional_tone,
    p.memory_value,
    p.speakability_hint,
    p.interpretation_text,
    p.reason_json,
    p.created_at AS interpretation_created_at,
    c.id AS utterance_candidate_id,
    e.id AS diary_entry_id,
    l.id AS conversation_log_id
FROM world_observation_documents d
JOIN world_observation_items i
  ON i.document_id = d.id
LEFT JOIN world_observation_interpretations p
  ON p.item_id = i.id
LEFT JOIN utterance_candidates c
  ON c.source = 'world_observation:' || p.id::text
LEFT JOIN diary_entries e
  ON p.id = ANY(e.source_world_observation_interpretation_ids)
LEFT JOIN conversation_logs l
  ON c.id = l.source_candidate_id;
