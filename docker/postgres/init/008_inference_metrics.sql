CREATE TABLE IF NOT EXISTS inference_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backend_name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    latency_ms DOUBLE PRECISION,
    error TEXT,
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inference_metrics_backend_measured_at
    ON inference_metrics (backend_name, measured_at DESC);
