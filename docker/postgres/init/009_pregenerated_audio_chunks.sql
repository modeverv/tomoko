CREATE TABLE IF NOT EXISTS pregenerated_audio_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    utterance_candidate_id UUID NOT NULL
        REFERENCES utterance_candidates(id)
        ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    audio_data BYTEA NOT NULL,
    audio_format TEXT NOT NULL DEFAULT 'riff_wave',
    is_last BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pregenerated_audio_chunks_index_check
        CHECK (chunk_index >= 0),
    CONSTRAINT pregenerated_audio_chunks_audio_data_check
        CHECK (length(audio_data) > 0),
    CONSTRAINT pregenerated_audio_chunks_audio_format_check
        CHECK (audio_format <> ''),
    CONSTRAINT pregenerated_audio_chunks_candidate_index_unique
        UNIQUE (utterance_candidate_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS pregenerated_audio_chunks_candidate_idx
    ON pregenerated_audio_chunks (utterance_candidate_id, chunk_index);
