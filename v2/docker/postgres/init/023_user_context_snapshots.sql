CREATE TABLE IF NOT EXISTS user_context_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    computed_at TIMESTAMPTZ NOT NULL,
    device_id TEXT,
    present BOOLEAN,
    presence_observed_at TIMESTAMPTZ,
    activity_label TEXT,
    activity_observed_at TIMESTAMPTZ,
    screen_activity_label TEXT,
    screen_observed_at TIMESTAMPTZ,
    calendar_summary TEXT,
    world_summary TEXT,
    user_activity_summary TEXT NOT NULL,
    context_summary TEXT NOT NULL,
    interaction_readiness TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    source_frame_ids UUID[] NOT NULL DEFAULT '{}',
    source_observation_ids UUID[] NOT NULL DEFAULT '{}',
    model TEXT,
    raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT user_context_snapshots_readiness_check
        CHECK (
            interaction_readiness IN (
                'away',
                'do_not_disturb',
                'low_intrusion_ok',
                'chat_ok',
                'needs_help_maybe'
            )
        ),
    CONSTRAINT user_context_snapshots_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE INDEX IF NOT EXISTS user_context_snapshots_computed_idx
    ON user_context_snapshots (computed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS user_context_snapshots_device_computed_idx
    ON user_context_snapshots (device_id, computed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS user_context_snapshots_readiness_computed_idx
    ON user_context_snapshots (interaction_readiness, computed_at DESC);
