-- Nexus Brain schema v003 — durable job queue with leasing

CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
    dedup_key TEXT,
    source TEXT NOT NULL DEFAULT 'inbox',
    payload JSONB NOT NULL DEFAULT '{}',
    result TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 4,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- Only one active (pending/running) job per dedup key
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup_active
    ON jobs (dedup_key)
    WHERE dedup_key IS NOT NULL AND status IN ('pending', 'running');

CREATE INDEX IF NOT EXISTS idx_jobs_status_created
    ON jobs (status, created_at);

CREATE INDEX IF NOT EXISTS idx_jobs_claimable
    ON jobs (available_at, created_at)
    WHERE status = 'pending'
       OR (status = 'running' AND lease_expires_at IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_jobs_lease_owner
    ON jobs (lease_owner)
    WHERE status = 'running';
