-- Nexus Brain schema v008 — idempotent tool-call effect ledger

-- Durable record of applied tool side effects so lease-reclaim resumes
-- skip re-executing the same action+args within a job.
CREATE TABLE IF NOT EXISTS tool_call_effects (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    effect_key TEXT NOT NULL,
    action TEXT NOT NULL,
    args JSONB NOT NULL DEFAULT '{}',
    result TEXT,
    iteration INTEGER,
    fence_token INTEGER,
    runner_id TEXT,
    status TEXT NOT NULL DEFAULT 'applied'
        CHECK (status IN ('applied', 'pending')),
    skipped_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, effect_key)
);

CREATE INDEX IF NOT EXISTS idx_tool_call_effects_job
    ON tool_call_effects (job_id, created_at);

CREATE INDEX IF NOT EXISTS idx_tool_call_effects_action
    ON tool_call_effects (job_id, action);

-- Lifecycle traces for ledger skip / seed events
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
        'subprocesses_reaped',
        'effect_skipped',
        'effects_seeded'
    ));
