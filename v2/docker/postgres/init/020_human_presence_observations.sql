CREATE TABLE IF NOT EXISTS human_presence_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    frame_id UUID NOT NULL REFERENCES perception_frames(id)
        ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL,
    present BOOLEAN NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    model TEXT NOT NULL,
    raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT human_presence_observations_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE UNIQUE INDEX IF NOT EXISTS human_presence_observations_frame_idx
    ON human_presence_observations (frame_id);

CREATE INDEX IF NOT EXISTS human_presence_observations_observed_idx
    ON human_presence_observations (observed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS human_presence_observations_present_observed_idx
    ON human_presence_observations (present, observed_at DESC);
