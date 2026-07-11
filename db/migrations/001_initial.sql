-- Nexus Brain schema v001

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    session_key TEXT NOT NULL DEFAULT 'default',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'nexus', 'system')),
    content TEXT NOT NULL,
    task_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memories (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    embedding JSONB,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    access_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    goal TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    result TEXT,
    agent_id TEXT,
    steps JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Legacy installs: extend pre-existing tables before indexes
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS tool_calls (
    id SERIAL PRIMARY KEY,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    iteration INTEGER DEFAULT 0,
    action TEXT NOT NULL,
    args JSONB DEFAULT '{}',
    result TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    capabilities JSONB DEFAULT '[]',
    spawned_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    task_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS learnings (
    id SERIAL PRIMARY KEY,
    task_goal TEXT,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    outcome TEXT,
    lesson TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE learnings ADD COLUMN IF NOT EXISTS task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS agent_events (
    id SERIAL PRIMARY KEY,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    payload JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_preferences (
    id SERIAL PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL,
    source TEXT DEFAULT 'user',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON tasks(conversation_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_task ON tool_calls(task_id);
CREATE INDEX IF NOT EXISTS idx_learnings_goal ON learnings(task_goal);
CREATE INDEX IF NOT EXISTS idx_agent_events_task ON agent_events(task_id);
CREATE INDEX IF NOT EXISTS idx_agent_events_type ON agent_events(event_type);
