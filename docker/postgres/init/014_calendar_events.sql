CREATE TABLE IF NOT EXISTS calendar_events (
    source_id TEXT NOT NULL,
    uid TEXT NOT NULL,
    summary TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    all_day BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'confirmed',
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_event_hash TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_id, uid, start_time)
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_context
    ON calendar_events (start_time, end_time);
