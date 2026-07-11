"""Apply versioned SQL migrations to PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


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
            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
                (version,),
            )
            conn.commit()
            applied.append(version)

    return applied
