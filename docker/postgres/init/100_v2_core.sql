CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS v2_process_heartbeats (
    process_name text PRIMARY KEY,
    process_kind text NOT NULL,
    status text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_conversation_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    close_reason text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_stt_observations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_kind text NOT NULL CHECK (event_kind IN ('partial', 'final')),
    text text NOT NULL,
    is_final boolean NOT NULL DEFAULT false,
    stability double precision NOT NULL DEFAULT 0,
    audio_started_at timestamptz,
    audio_ended_at timestamptz,
    p_yielding double precision,
    recommended_silence_ms integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_floor_observations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    floor_state text NOT NULL,
    silence_ms integer NOT NULL DEFAULT 0,
    user_speaking boolean NOT NULL DEFAULT false,
    tomoko_speaking boolean NOT NULL DEFAULT false,
    playback_active boolean NOT NULL DEFAULT false,
    p_yielding double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_speech_decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    floor_observation_id uuid REFERENCES v2_floor_observations(id) ON DELETE SET NULL,
    decision text NOT NULL,
    should_execute boolean NOT NULL DEFAULT false,
    log_only boolean NOT NULL DEFAULT true,
    reason text NOT NULL,
    score_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_user_status_observations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    present boolean NOT NULL,
    activity_label text NOT NULL,
    summary text NOT NULL,
    confidence double precision NOT NULL DEFAULT 0,
    visible_text text NOT NULL DEFAULT '',
    app_name text,
    window_title text,
    url text,
    artifact_path text,
    source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_candidates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seed_id uuid,
    source text NOT NULL,
    source_key text NOT NULL,
    text text NOT NULL,
    priority double precision NOT NULL DEFAULT 0,
    urgency double precision NOT NULL DEFAULT 0,
    intrusion double precision NOT NULL DEFAULT 0,
    maturity double precision NOT NULL DEFAULT 0,
    candidate_score double precision NOT NULL DEFAULT 0,
    lifecycle text NOT NULL DEFAULT 'active',
    context_tags text[] NOT NULL DEFAULT '{}',
    expires_at timestamptz,
    spoken_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid(),
    UNIQUE (source, source_key)
);

CREATE TABLE IF NOT EXISTS v2_session_summaries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES v2_conversation_sessions(id) ON DELETE CASCADE,
    keyword text NOT NULL,
    conclusion text NOT NULL,
    summary_text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_summary_embeddings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    summary_id uuid NOT NULL REFERENCES v2_session_summaries(id) ON DELETE CASCADE,
    embedding double precision[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_context_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid REFERENCES v2_conversation_sessions(id) ON DELETE SET NULL,
    recent_utterances jsonb NOT NULL DEFAULT '[]'::jsonb,
    summaries jsonb NOT NULL DEFAULT '[]'::jsonb,
    calendar_items jsonb NOT NULL DEFAULT '{}'::jsonb,
    user_status_id uuid REFERENCES v2_user_status_observations(id) ON DELETE SET NULL,
    candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
    elapsed_ms double precision NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_utterances (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES v2_conversation_sessions(id) ON DELETE CASCADE,
    stt_observation_id uuid REFERENCES v2_stt_observations(id) ON DELETE SET NULL,
    speaker text NOT NULL,
    text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_prompt_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    context_snapshot_id uuid REFERENCES v2_context_snapshots(id) ON DELETE SET NULL,
    decision_id uuid REFERENCES v2_speech_decisions(id) ON DELETE SET NULL,
    utterance_id uuid REFERENCES v2_utterances(id) ON DELETE SET NULL,
    candidate_id uuid REFERENCES v2_candidates(id) ON DELETE SET NULL,
    scope text NOT NULL,
    priority integer NOT NULL DEFAULT 0,
    cancel_policy text NOT NULL,
    prompt_text text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_model_output_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL REFERENCES v2_prompt_requests(id) ON DELETE CASCADE,
    event_kind text NOT NULL,
    text_delta text NOT NULL DEFAULT '',
    text text NOT NULL DEFAULT '',
    discarded boolean NOT NULL DEFAULT false,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_audio_output_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL REFERENCES v2_prompt_requests(id) ON DELETE CASCADE,
    event_kind text NOT NULL,
    content_type text NOT NULL DEFAULT 'audio/wav',
    byte_length integer NOT NULL DEFAULT 0,
    is_final boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_world_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source text NOT NULL,
    source_key text NOT NULL,
    raw_text text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid(),
    UNIQUE (source, source_key)
);

CREATE TABLE IF NOT EXISTS v2_world_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES v2_world_documents(id) ON DELETE CASCADE,
    title text NOT NULL,
    body text NOT NULL,
    confidence double precision NOT NULL DEFAULT 0,
    flags jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_world_interpretations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id uuid NOT NULL REFERENCES v2_world_items(id) ON DELETE CASCADE,
    summary text NOT NULL,
    confidence double precision NOT NULL DEFAULT 0,
    flags jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_eval_turns (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid REFERENCES v2_conversation_sessions(id) ON DELETE SET NULL,
    speech_end_to_first_text_ms double precision NOT NULL,
    speech_end_to_first_audio_ms double precision NOT NULL,
    turn_total_latency_ms double precision NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE IF NOT EXISTS v2_eval_scores (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_turn_id uuid NOT NULL REFERENCES v2_eval_turns(id) ON DELETE CASCADE,
    responsiveness double precision NOT NULL,
    attended_feeling double precision NOT NULL,
    turn_taking_naturalness double precision NOT NULL,
    interruption_robustness double precision NOT NULL,
    memory_naturalness double precision NOT NULL,
    persona_consistency double precision NOT NULL,
    recovery_quality double precision NOT NULL,
    notes text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid,
    trace_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE OR REPLACE FUNCTION v2_notify_id(channel_name text, event_id uuid)
RETURNS void AS $$
BEGIN
    IF channel_name NOT IN (
        'v2_stt_observation',
        'v2_prompt_request',
        'v2_model_output',
        'v2_candidate',
        'v2_user_status',
        'v2_info_ready',
        'v2_summary_ready'
    ) THEN
        RAISE EXCEPTION 'unknown v2 notify channel: %', channel_name;
    END IF;
    PERFORM pg_notify(channel_name, event_id::text);
END;
$$ LANGUAGE plpgsql;
