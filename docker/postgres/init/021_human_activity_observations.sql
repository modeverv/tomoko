CREATE TABLE IF NOT EXISTS human_activity_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    frame_id UUID NOT NULL REFERENCES perception_frames(id)
        ON DELETE CASCADE,
    presence_observation_id UUID REFERENCES human_presence_observations(id)
        ON DELETE SET NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    activity_label TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    model TEXT NOT NULL,
    raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT human_activity_observations_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE UNIQUE INDEX IF NOT EXISTS human_activity_observations_frame_idx
    ON human_activity_observations (frame_id);

CREATE INDEX IF NOT EXISTS human_activity_observations_observed_idx
    ON human_activity_observations (observed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS human_activity_observations_label_observed_idx
    ON human_activity_observations (activity_label, observed_at DESC);
