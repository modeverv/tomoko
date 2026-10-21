CREATE TABLE IF NOT EXISTS conversation_embeddings (
    conversation_log_id UUID PRIMARY KEY
        REFERENCES conversation_logs(id) ON DELETE CASCADE,
    embedding vector(384) NOT NULL,
    model TEXT NOT NULL,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversation_embeddings_embedding_idx
    ON conversation_embeddings
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS conversation_embeddings_embedded_at_idx
    ON conversation_embeddings (embedded_at DESC);
