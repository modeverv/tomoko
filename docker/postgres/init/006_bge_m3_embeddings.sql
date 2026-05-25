DROP INDEX IF EXISTS conversation_embeddings_embedding_idx;
DROP INDEX IF EXISTS conversation_sessions_summary_embedding_idx;

DELETE FROM conversation_embeddings;

ALTER TABLE conversation_embeddings
    ALTER COLUMN embedding TYPE vector(1024)
    USING NULL::vector(1024);

UPDATE conversation_sessions
SET summary_embedding = NULL,
    summary_embedding_model = NULL,
    summary_embedded_at = NULL,
    summary_status = CASE
        WHEN summary_status = 'completed' THEN 'pending'
        ELSE summary_status
    END
WHERE summary_embedding IS NOT NULL
   OR summary_embedding_model IS NOT NULL;

ALTER TABLE conversation_sessions
    ALTER COLUMN summary_embedding TYPE vector(1024)
    USING NULL::vector(1024);

CREATE INDEX IF NOT EXISTS conversation_embeddings_embedding_idx
    ON conversation_embeddings
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS conversation_sessions_summary_embedding_idx
    ON conversation_sessions
    USING hnsw (summary_embedding vector_cosine_ops)
    WHERE summary_embedding IS NOT NULL;
