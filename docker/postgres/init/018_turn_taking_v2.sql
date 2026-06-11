CREATE TABLE IF NOT EXISTS partial_transcript_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_session_id UUID REFERENCES conversation_sessions(id) ON DELETE SET NULL,
    turn_id UUID,
    revision INTEGER NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    vad_state TEXT,
    attention_mode TEXT,
    raw_text TEXT NOT NULL,
    filtered_text TEXT,
    stable_text TEXT,
    unstable_tail TEXT,
    audio_level_db DOUBLE PRECISION,
    source TEXT
);

CREATE TABLE IF NOT EXISTS turn_taking_v2_advisories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_id UUID REFERENCES partial_transcript_observations(id) ON DELETE SET NULL,
    conversation_session_id UUID REFERENCES conversation_sessions(id) ON DELETE SET NULL,
    turn_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    semantic_saturation DOUBLE PRECISION,
    remaining_info_risk DOUBLE PRECISION,
    semantic_split_risk DOUBLE PRECISION,
    speech_decision_score DOUBLE PRECISION,
    safe_response_level INTEGER,
    proposal TEXT,
    confidence DOUBLE PRECISION,
    would_start_inference BOOLEAN,
    reason TEXT
);

ALTER TABLE turn_taking_v2_advisories ADD COLUMN IF NOT EXISTS would_start_inference BOOLEAN;

CREATE INDEX IF NOT EXISTS partial_transcript_obs_session_idx ON partial_transcript_observations (conversation_session_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS turn_taking_v2_advisories_session_idx ON turn_taking_v2_advisories (conversation_session_id, created_at DESC);
