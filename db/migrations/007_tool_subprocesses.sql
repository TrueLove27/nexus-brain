-- Nexus Brain schema v007 — durable tool child PIDs for reclaim-safe reaping

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS active_children JSONB NOT NULL DEFAULT '[]';

-- Lifecycle traces for subprocess reaping on reclaim / fence loss
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
        'fenced_out',
        'subprocesses_reaped'
    ));
