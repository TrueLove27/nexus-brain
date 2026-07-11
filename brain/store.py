"""Memory backend factory — PostgreSQL when available, SQLite fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.memory import BrainMemory
from brain.postgres_memory import PostgresMemory

MemoryBackend = BrainMemory | PostgresMemory


def create_memory(brain_cfg: dict, llm_cfg: dict, root: Path) -> MemoryBackend:
    data_dir = root / brain_cfg["data_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)
    storage = brain_cfg.get("storage", "sqlite").lower()

    if storage == "postgres":
        pg = brain_cfg.get("postgres", {})
        dsn = pg.get("dsn") or _build_dsn(pg)
        try:
            mem = PostgresMemory(
                dsn=dsn,
                data_dir=data_dir,
                embed_model=llm_cfg["embed_model"],
                ollama_url=llm_cfg["base_url"],
            )
            with mem._conn() as conn:
                conn.execute("SELECT 1")
            return mem
        except Exception as exc:
            print(f"[nexus] PostgreSQL unavailable ({exc}) — falling back to SQLite")

    return BrainMemory(
        db_path=root / brain_cfg["memory_db"],
        embed_model=llm_cfg["embed_model"],
        ollama_url=llm_cfg["base_url"],
    )


def _build_dsn(pg: dict[str, Any]) -> str:
    host = pg.get("host", "localhost")
    port = pg.get("port", 5432)
    database = pg.get("database", "nexus_brain")
    user = pg.get("user", "postgres")
    password = pg.get("password", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"
