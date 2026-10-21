CREATE TABLE IF NOT EXISTS screen_activity_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    frame_id UUID NOT NULL REFERENCES perception_frames(id)
        ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL,
    screen_activity_label TEXT NOT NULL,
    app_hint TEXT,
    document_hint TEXT,
    url_hint TEXT,
    confidence DOUBLE PRECISION NOT NULL,
    model TEXT NOT NULL,
    raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT screen_activity_observations_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE UNIQUE INDEX IF NOT EXISTS screen_activity_observations_frame_idx
    ON screen_activity_observations (frame_id);

CREATE INDEX IF NOT EXISTS screen_activity_observations_observed_idx
    ON screen_activity_observations (observed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS screen_activity_observations_label_observed_idx
    ON screen_activity_observations (screen_activity_label, observed_at DESC);
