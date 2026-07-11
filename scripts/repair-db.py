"""Inspect and repair Nexus Postgres schema."""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from db.migrate import run_migrations

DSN = "postgresql://postgres:postgres@localhost:5432/nexus_brain"


def main() -> None:
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        tables = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
        ).fetchall()
        print("tables:", [t["tablename"] for t in tables])
        try:
            vers = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
            print("migrations before:", vers)
        except Exception as exc:
            print("schema_migrations missing:", exc)

    applied = run_migrations(DSN)
    print("applied:", applied)

    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        vers = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        print("migrations after:", vers)
        if conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name='messages'"
        ).fetchone():
            count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
            print("messages count:", count)


if __name__ == "__main__":
    main()
