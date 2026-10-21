CREATE TABLE IF NOT EXISTS research_results (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    summary_embedding vector(1024) NOT NULL,
    summary_embedding_model TEXT NOT NULL DEFAULT '',
    short_answer TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    citation_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_artifact_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE research_results
    ADD COLUMN IF NOT EXISTS query TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS summary_text TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS summary_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS summary_embedding_model TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS short_answer TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS citation_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS raw_artifact_path TEXT,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS research_results_summary_embedding_hnsw_idx
    ON research_results USING hnsw (summary_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS research_results_fetched_at_idx
    ON research_results (fetched_at DESC);

CREATE INDEX IF NOT EXISTS research_results_provider_fetched_at_idx
    ON research_results (provider, fetched_at DESC);
