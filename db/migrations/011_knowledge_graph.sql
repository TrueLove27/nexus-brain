-- Nexus Brain schema v011 — project / entity knowledge graph
-- Entities + weighted relations extracted from tasks and preferences

CREATE TABLE IF NOT EXISTS kg_entities (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL
        CHECK (entity_type IN ('project', 'file', 'tool', 'concept', 'person', 'repo', 'other')),
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    description TEXT,
    embedding JSONB,
    metadata JSONB NOT NULL DEFAULT '{}',
    mention_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (entity_type, normalized_name)
);

CREATE INDEX IF NOT EXISTS idx_kg_entities_normalized
    ON kg_entities (normalized_name);
CREATE INDEX IF NOT EXISTS idx_kg_entities_type
    ON kg_entities (entity_type, mention_count DESC);
CREATE INDEX IF NOT EXISTS idx_kg_entities_name_trgm
    ON kg_entities (LOWER(name));

CREATE TABLE IF NOT EXISTS kg_relations (
    id BIGSERIAL PRIMARY KEY,
    from_id BIGINT NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    to_id BIGINT NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0
        CHECK (weight >= 0),
    evidence TEXT,
    task_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (from_id <> to_id)
);

CREATE INDEX IF NOT EXISTS idx_kg_relations_from
    ON kg_relations (from_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_kg_relations_to
    ON kg_relations (to_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_kg_relations_type
    ON kg_relations (relation_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kg_relations_task
    ON kg_relations (task_id)
    WHERE task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kg_relations_pair
    ON kg_relations (from_id, to_id, relation_type);
