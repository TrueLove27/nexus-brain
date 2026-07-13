-- Nexus Brain schema v004 — per-job execution traces for forensics

-- Link tool/agent telemetry to durable jobs
ALTER TABLE tool_calls
    ADD COLUMN IF NOT EXISTS job_id BIGINT REFERENCES jobs(id) ON DELETE SET NULL;

ALTER TABLE agent_events
    ADD COLUMN IF NOT EXISTS job_id BIGINT REFERENCES jobs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_tool_calls_job
    ON tool_calls (job_id)
    WHERE job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_events_job
    ON agent_events (job_id, created_at)
    WHERE job_id IS NOT NULL;

-- Structured lifecycle events per job attempt (claim, reclaim, complete, fail)
CREATE TABLE IF NOT EXISTS job_traces (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL DEFAULT 1,
    runner_id TEXT,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN (
            'claimed',
            'lease_reclaimed',
            'started',
            'completed',
            'failed',
            'retry_scheduled',
            'cancelled'
        )),
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_traces_job_created
    ON job_traces (job_id, created_at);

CREATE INDEX IF NOT EXISTS idx_job_traces_type_created
    ON job_traces (event_type, created_at DESC);
