CREATE TABLE IF NOT EXISTS perception_frames (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,
    device_id TEXT,
    captured_at TIMESTAMPTZ NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    retained BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT perception_frames_source_check
        CHECK (source IN ('camera', 'screenshot')),
    CONSTRAINT perception_frames_width_check
        CHECK (width IS NULL OR width > 0),
    CONSTRAINT perception_frames_height_check
        CHECK (height IS NULL OR height > 0)
);

CREATE INDEX IF NOT EXISTS perception_frames_source_captured_idx
    ON perception_frames (source, captured_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS perception_frames_retained_source_captured_idx
    ON perception_frames (retained, source, captured_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS perception_frames_sha256_idx
    ON perception_frames (sha256);
