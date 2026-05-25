CREATE TABLE IF NOT EXISTS initiative_feedback_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    candidate_id UUID NULL,
    source TEXT NOT NULL,
    topic TEXT NULL,
    emotional_need TEXT NULL,
    feedback_kind TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    transcript_text TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_initiative_feedback_source_time
    ON initiative_feedback_signals (source, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_initiative_feedback_topic_time
    ON initiative_feedback_signals (topic, observed_at DESC)
    WHERE topic IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_initiative_feedback_emotional_need_time
    ON initiative_feedback_signals (emotional_need, observed_at DESC)
    WHERE emotional_need IS NOT NULL;
