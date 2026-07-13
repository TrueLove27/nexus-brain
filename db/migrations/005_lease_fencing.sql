-- Nexus Brain schema v005 — lease fencing tokens + heartbeat traces

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS fence_token BIGINT NOT NULL DEFAULT 0;

-- Refresh trace event kinds for mid-run heartbeat / fence rejection
ALTER TABLE job_traces DROP CONSTRAINT IF EXISTS job_traces_event_type_check;
ALTER TABLE job_traces ADD CONSTRAINT job_traces_event_type_check
    CHECK (event_type IN (
        'claimed',
        'lease_reclaimed',
        'started',
        'heartbeat',
        'completed',
        'failed',
        'retry_scheduled',
        'cancelled',
        'fenced_out'
    ));
