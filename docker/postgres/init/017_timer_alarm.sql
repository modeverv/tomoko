CREATE TABLE IF NOT EXISTS timer_alarm_entries (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'scheduled',
    due_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL DEFAULT 'voice',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    claimed_worker_id TEXT,
    claimed_at TIMESTAMPTZ,
    failure_reason TEXT NOT NULL DEFAULT ''
);

ALTER TABLE timer_alarm_entries
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'timer',
    ADD COLUMN IF NOT EXISTS label TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'scheduled',
    ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'voice',
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS claimed_worker_id TEXT,
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS failure_reason TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS timer_alarm_scheduled_due_idx
    ON timer_alarm_entries (due_at)
    WHERE status = 'scheduled';

CREATE INDEX IF NOT EXISTS timer_alarm_status_updated_idx
    ON timer_alarm_entries (status, updated_at DESC);
