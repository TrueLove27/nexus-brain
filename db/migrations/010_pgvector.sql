-- Nexus Brain schema v010 — hybrid recall foundations
-- nomic-embed-text → 768 dims (embedding_vec vector(768); NULL on dim mismatch)
-- Safe without pgvector: tsvector/GIN always; vector columns only if extension loads.

-- Keyword search (no pgvector required)
ALTER TABLE memories ADD COLUMN IF NOT EXISTS content_tsv tsvector;

UPDATE memories
SET content_tsv = to_tsvector('english', coalesce(content, ''))
WHERE content_tsv IS NULL AND content IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memories_content_tsv ON memories USING GIN (content_tsv);

CREATE OR REPLACE FUNCTION memories_content_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.content_tsv := to_tsvector('english', coalesce(NEW.content, ''));
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_memories_content_tsv ON memories;
CREATE TRIGGER trg_memories_content_tsv
  BEFORE INSERT OR UPDATE OF content ON memories
  FOR EACH ROW EXECUTE PROCEDURE memories_content_tsv_trigger();

-- Optional: pgvector extension (must not fail installs without it)
DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pgvector not available: %', SQLERRM;
END $$;

-- Vector column + HNSW only when extension is present
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
    ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding_vec vector(768);
    BEGIN
      CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw
        ON memories USING hnsw (embedding_vec vector_cosine_ops);
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'HNSW index on memories skipped: %', SQLERRM;
    END;

    -- Tier tables (added by multi-tier memory migration when present)
    IF to_regclass('public.memory_facts') IS NOT NULL THEN
      ALTER TABLE memory_facts ADD COLUMN IF NOT EXISTS embedding_vec vector(768);
      ALTER TABLE memory_facts ADD COLUMN IF NOT EXISTS content_tsv tsvector;
      BEGIN
        CREATE INDEX IF NOT EXISTS idx_memory_facts_embedding_hnsw
          ON memory_facts USING hnsw (embedding_vec vector_cosine_ops);
      EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'HNSW on memory_facts skipped: %', SQLERRM;
      END;
      BEGIN
        CREATE INDEX IF NOT EXISTS idx_memory_facts_content_tsv
          ON memory_facts USING GIN (content_tsv);
      EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'GIN on memory_facts skipped: %', SQLERRM;
      END;
    END IF;

    IF to_regclass('public.memory_episodes') IS NOT NULL THEN
      ALTER TABLE memory_episodes ADD COLUMN IF NOT EXISTS embedding_vec vector(768);
      BEGIN
        CREATE INDEX IF NOT EXISTS idx_memory_episodes_embedding_hnsw
          ON memory_episodes USING hnsw (embedding_vec vector_cosine_ops);
      EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'HNSW on memory_episodes skipped: %', SQLERRM;
      END;
    END IF;

    IF to_regclass('public.memory_procedures') IS NOT NULL THEN
      ALTER TABLE memory_procedures ADD COLUMN IF NOT EXISTS embedding_vec vector(768);
      ALTER TABLE memory_procedures ADD COLUMN IF NOT EXISTS content_tsv tsvector;
      BEGIN
        CREATE INDEX IF NOT EXISTS idx_memory_procedures_embedding_hnsw
          ON memory_procedures USING hnsw (embedding_vec vector_cosine_ops);
      EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'HNSW on memory_procedures skipped: %', SQLERRM;
      END;
    END IF;
  ELSE
    RAISE NOTICE 'Skipping embedding_vec columns — pgvector extension not installed';
  END IF;
END $$;
