"""Apply versioned SQL migrations to PostgreSQL."""

from __future__ import annotations

import re
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _split_statements(sql: str) -> list[str]:
    """Split on ';' outside dollar-quotes and line comments (DO $$ ... $$ blocks)."""
    stmts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        if sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                buf.append(sql[i])
                i += 1
            continue
        if sql[i] == "$":
            m = re.match(r"\$([A-Za-z0-9_]*)\$", sql[i:])
            if m:
                tag = m.group(0)
                buf.append(tag)
                i += len(tag)
                end = sql.find(tag, i)
                if end < 0:
                    buf.append(sql[i:])
                    break
                buf.append(sql[i : end + len(tag)])
                i = end + len(tag)
                continue
        if sql[i] == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(sql[i])
        i += 1
    stmt = "".join(buf).strip()
    if stmt:
        stmts.append(stmt)
    return stmts


def run_migrations(dsn: str) -> list[int]:
    """Apply pending migrations. Returns list of versions applied."""
    applied: list[int] = []
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    with psycopg.connect(dsn) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.commit()

        for path in migration_files:
            version = int(path.stem.split("_")[0])
            row = conn.execute(
                "SELECT version FROM schema_migrations WHERE version = %s", (version,)
            ).fetchone()
            if row:
                continue

            sql = path.read_text(encoding="utf-8")
            for stmt in _split_statements(sql):
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
                (version,),
            )
            conn.commit()
            applied.append(version)

    return applied
