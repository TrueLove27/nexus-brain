-- Nexus Brain schema v006 — durable ReAct step checkpoints for lease-resume

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS checkpoint JSONB NOT NULL DEFAULT '{}';

-- Lifecycle traces for checkpoint writes and reclaim resumes
ALTER TABLE job_traces DROP CONSTRAINT IF EXISTS job_traces_event_type_check;
ALTER TABLE job_traces ADD CONSTRAINT job_traces_event_type_check
    CHECK (event_type IN (
        'claimed',
        'lease_reclaimed',
        'started',
        'heartbeat',
        'checkpoint',
        'resumed',
        'completed',
        'failed',
        'retry_scheduled',
        'cancelled',
        'fenced_out'
    ));
