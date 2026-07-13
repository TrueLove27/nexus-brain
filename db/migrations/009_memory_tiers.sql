-- Nexus Brain schema v009 — multi-tier cognitive memory
-- Episodic episodes → semantic facts → procedural patterns

CREATE TABLE IF NOT EXISTS memory_episodes (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('task', 'chat', 'tool', 'teach')),
    task_id INTEGER,
    conversation_id INTEGER,
    job_id BIGINT,
    content TEXT NOT NULL,
    embedding JSONB,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consolidated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_memory_episodes_created
    ON memory_episodes (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_episodes_source
    ON memory_episodes (source, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_episodes_unconsolidated
    ON memory_episodes (created_at DESC)
    WHERE consolidated_at IS NULL;

CREATE TABLE IF NOT EXISTS memory_facts (
    id BIGSERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    embedding JSONB,
    confidence REAL NOT NULL DEFAULT 0.7
        CHECK (confidence >= 0 AND confidence <= 1),
    source_episode_ids JSONB NOT NULL DEFAULT '[]',
    superseded_by INTEGER REFERENCES memory_facts(id) ON DELETE SET NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'general',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_facts_created
    ON memory_facts (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_facts_category
    ON memory_facts (category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_facts_active
    ON memory_facts (created_at DESC)
    WHERE superseded_by IS NULL;

CREATE TABLE IF NOT EXISTS memory_procedures (
    id BIGSERIAL PRIMARY KEY,
    trigger_text TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding JSONB,
    confidence REAL NOT NULL DEFAULT 0.7
        CHECK (confidence >= 0 AND confidence <= 1),
    use_count INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'general',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_procedures_created
    ON memory_procedures (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_procedures_category
    ON memory_procedures (category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_procedures_trigger
    ON memory_procedures (trigger_text);
