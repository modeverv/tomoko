CREATE TABLE IF NOT EXISTS stop_intent_observations (
    id UUID PRIMARY KEY,
    conversation_session_id UUID NULL REFERENCES conversation_sessions(id),
    turn_id TEXT NULL,
    transcript_id TEXT NOT NULL,
    transcript_text TEXT NOT NULL,
    rule_kind TEXT NOT NULL,
    adopted_action TEXT NOT NULL,
    playback_state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    reply_state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error TEXT
);

CREATE TABLE IF NOT EXISTS stop_intent_shadow_signals (
    id UUID PRIMARY KEY,
    observation_id UUID NOT NULL REFERENCES stop_intent_observations(id) ON DELETE CASCADE,
    method TEXT NOT NULL,
    model TEXT,
    predicted_kind TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL,
    raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stop_intent_observations_pending
    ON stop_intent_observations (created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_stop_intent_observations_locked
    ON stop_intent_observations (locked_at)
    WHERE status = 'processing';

CREATE INDEX IF NOT EXISTS idx_stop_intent_observations_session
    ON stop_intent_observations (conversation_session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_stop_intent_shadow_method_kind
    ON stop_intent_shadow_signals (method, predicted_kind, confidence, latency_ms);

CREATE OR REPLACE VIEW stop_intent_shadow_analysis AS
SELECT
    o.id AS observation_id,
    o.conversation_session_id,
    o.turn_id,
    o.transcript_id,
    o.rule_kind,
    o.adopted_action,
    o.created_at AS observed_at,
    s.method,
    s.model,
    s.predicted_kind,
    s.confidence,
    s.latency_ms,
    NOT COALESCE((o.reply_state_json ->> 'first_reply_text_emitted')::boolean, false)
        AS classification_arrived_before_first_reply_text,
    NOT COALESCE((o.reply_state_json ->> 'first_audio_chunk_emitted')::boolean, false)
        AS classification_arrived_before_first_audio,
    (
        s.predicted_kind IN ('hard_stop', 'soft_stop', 'withdraw')
        AND o.adopted_action NOT IN ('restart_turn', 'withdraw')
    ) AS would_have_changed_action
FROM stop_intent_observations o
JOIN stop_intent_shadow_signals s ON s.observation_id = o.id;
