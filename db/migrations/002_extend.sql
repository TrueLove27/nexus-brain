-- Nexus Brain schema v002 — extend existing tables

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL;
ALTER TABLE learnings ADD COLUMN IF NOT EXISTS task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL;
