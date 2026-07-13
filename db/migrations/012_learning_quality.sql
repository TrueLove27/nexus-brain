-- Nexus Brain schema v012 — validated learning quality loop
-- confidence scoring, embeddings, dedupe/supersede/retract, task_id linkage

-- Quality + lifecycle columns
ALTER TABLE learnings ADD COLUMN IF NOT EXISTS confidence REAL NOT NULL DEFAULT 0.5;
ALTER TABLE learnings ADD COLUMN IF NOT EXISTS use_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE learnings ADD COLUMN IF NOT EXISTS helpful_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE learnings ADD COLUMN IF NOT EXISTS embedding JSONB;
ALTER TABLE learnings ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE learnings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Self-FK for supersession (nullable until successor exists)
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'learnings'
      AND column_name = 'superseded_by'
  ) THEN
    ALTER TABLE learnings ADD COLUMN superseded_by INTEGER REFERENCES learnings(id) ON DELETE SET NULL;
  END IF;
END $$;

-- task_id already exists from 001/002; ensure FK index for lookups
CREATE INDEX IF NOT EXISTS idx_learnings_task_id ON learnings(task_id);
CREATE INDEX IF NOT EXISTS idx_learnings_status ON learnings(status);
CREATE INDEX IF NOT EXISTS idx_learnings_active
  ON learnings(created_at DESC)
  WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_learnings_confidence
  ON learnings(confidence DESC)
  WHERE status = 'active';

-- Clamp confidence for existing rows if any drift
UPDATE learnings
SET confidence = GREATEST(0.0, LEAST(1.0, COALESCE(confidence, 0.5)))
WHERE confidence IS NULL OR confidence < 0 OR confidence > 1;

-- Keyword search on lessons (no pgvector required)
ALTER TABLE learnings ADD COLUMN IF NOT EXISTS content_tsv tsvector;

UPDATE learnings
SET content_tsv = to_tsvector(
  'english',
  coalesce(lesson, '') || ' ' || coalesce(task_goal, '')
)
WHERE content_tsv IS NULL;

CREATE INDEX IF NOT EXISTS idx_learnings_content_tsv ON learnings USING GIN (content_tsv);

CREATE OR REPLACE FUNCTION learnings_content_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.content_tsv := to_tsvector(
    'english',
    coalesce(NEW.lesson, '') || ' ' || coalesce(NEW.task_goal, '')
  );
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_learnings_content_tsv ON learnings;
CREATE TRIGGER trg_learnings_content_tsv
  BEFORE INSERT OR UPDATE OF lesson, task_goal ON learnings
  FOR EACH ROW EXECUTE PROCEDURE learnings_content_tsv_trigger();

-- Optional: pgvector column + HNSW (skip cleanly if extension missing)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
    ALTER TABLE learnings ADD COLUMN IF NOT EXISTS embedding_vec vector(768);
    BEGIN
      CREATE INDEX IF NOT EXISTS idx_learnings_embedding_hnsw
        ON learnings USING hnsw (embedding_vec vector_cosine_ops);
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'HNSW on learnings skipped: %', SQLERRM;
    END;
  ELSE
    RAISE NOTICE 'Skipping learnings.embedding_vec — pgvector not installed';
  END IF;
END $$;

-- Status constraint (soft: allow only known values going forward via check if safe)
DO $$ BEGIN
  ALTER TABLE learnings DROP CONSTRAINT IF EXISTS learnings_status_check;
  ALTER TABLE learnings ADD CONSTRAINT learnings_status_check
    CHECK (status IN ('active', 'superseded', 'retracted'));
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'learnings status check skipped: %', SQLERRM;
END $$;

DO $$ BEGIN
  ALTER TABLE learnings DROP CONSTRAINT IF EXISTS learnings_confidence_check;
  ALTER TABLE learnings ADD CONSTRAINT learnings_confidence_check
    CHECK (confidence >= 0 AND confidence <= 1);
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'learnings confidence check skipped: %', SQLERRM;
END $$;
