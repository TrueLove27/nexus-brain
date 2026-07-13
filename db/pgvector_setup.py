"""Optional pgvector + hybrid-search setup. Never raises — safe when extension missing.

Called from PostgresMemory._init_db after SQL migrations so installs without
pgvector still boot. nomic-embed-text uses 768 dims.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("nexus.pgvector")

EMBED_DIM = 768

_TIER_TABLES = ("memory_facts", "memory_episodes", "memory_procedures", "learnings")


def ensure_pgvector(conn: Any) -> bool:
    """Idempotent vector + tsvector setup. Returns True if pgvector is usable."""
    try:
        _ensure_content_tsv(conn)
    except Exception as exc:
        log.warning("content_tsv setup failed: %s", exc)

    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()
    except Exception as exc:
        log.info("pgvector extension unavailable: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False

    if not _extension_loaded(conn):
        return False

    try:
        _ensure_vector_column(conn, "memories")
        _ensure_hnsw(conn, "memories", "idx_memories_embedding_hnsw")
        for table in _TIER_TABLES:
            if _table_exists(conn, table):
                _ensure_vector_column(conn, table)
                _ensure_hnsw(conn, table, f"idx_{table}_embedding_hnsw")
                if table not in ("memory_episodes",):
                    _ensure_tier_tsv(conn, table)
        conn.commit()
        return True
    except Exception as exc:
        log.warning("pgvector column/index setup failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def pgvector_available(conn: Any) -> bool:
    """True when extension is loaded and memories.embedding_vec exists."""
    try:
        if not _extension_loaded(conn):
            return False
        row = conn.execute(
            """SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'memories'
                 AND column_name = 'embedding_vec'"""
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _extension_loaded(conn: Any) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _table_exists(conn: Any, name: str) -> bool:
    row = conn.execute(
        "SELECT to_regclass(%s) AS reg", (f"public.{name}",)
    ).fetchone()
    if not row:
        return False
    reg = row.get("reg") if isinstance(row, dict) else row[0]
    return reg is not None


def _ensure_content_tsv(conn: Any) -> None:
    conn.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS content_tsv tsvector")
    conn.execute("""
        UPDATE memories
        SET content_tsv = to_tsvector('english', coalesce(content, ''))
        WHERE content_tsv IS NULL AND content IS NOT NULL
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_memories_content_tsv
        ON memories USING GIN (content_tsv)
    """)
    conn.execute("""
        CREATE OR REPLACE FUNCTION memories_content_tsv_trigger() RETURNS trigger AS $$
        BEGIN
          NEW.content_tsv := to_tsvector('english', coalesce(NEW.content, ''));
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    conn.execute("DROP TRIGGER IF EXISTS trg_memories_content_tsv ON memories")
    try:
        conn.execute("""
            CREATE TRIGGER trg_memories_content_tsv
              BEFORE INSERT OR UPDATE OF content ON memories
              FOR EACH ROW EXECUTE FUNCTION memories_content_tsv_trigger()
        """)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.execute("""
            CREATE TRIGGER trg_memories_content_tsv
              BEFORE INSERT OR UPDATE OF content ON memories
              FOR EACH ROW EXECUTE PROCEDURE memories_content_tsv_trigger()
        """)
    conn.commit()


def _ensure_tier_tsv(conn: Any, table: str) -> None:
    cols = conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s""",
        (table,),
    ).fetchall()
    names = {
        (c.get("column_name") if isinstance(c, dict) else c[0]) for c in cols
    }
    text_col = None
    for candidate in ("content", "lesson", "fact", "pattern", "text"):
        if candidate in names:
            text_col = candidate
            break
    if not text_col:
        return
    # learnings: lesson + task_goal for keyword ranking
    if table == "learnings" and "task_goal" in names:
        expr = "coalesce(lesson, '') || ' ' || coalesce(task_goal, '')"
    else:
        expr = f"coalesce({text_col}::text, '')"
    conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS content_tsv tsvector")
    conn.execute(f"""
        UPDATE {table}
        SET content_tsv = to_tsvector('english', {expr})
        WHERE content_tsv IS NULL
    """)
    conn.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{table}_content_tsv
        ON {table} USING GIN (content_tsv)
    """)


def _ensure_vector_column(conn: Any, table: str) -> None:
    conn.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS embedding_vec vector({EMBED_DIM})"
    )


def _ensure_hnsw(conn: Any, table: str, index_name: str) -> None:
    try:
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS {index_name}
            ON {table} USING hnsw (embedding_vec vector_cosine_ops)
        """)
    except Exception as exc:
        log.info("HNSW index %s skipped: %s", index_name, exc)
