#!/usr/bin/env python3
"""Backfill memories.embedding_vec from JSONB embedding (nomic-embed-text = 768 dims).

Usage:
  py scripts/backfill_embeddings.py
  py scripts/backfill_embeddings.py --limit 1000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill pgvector embedding_vec from JSONB")
    parser.add_argument("--limit", type=int, default=500, help="Max rows to update this run")
    args = parser.parse_args()

    from core.engine import NexusEngine

    engine = NexusEngine()
    mem = engine.memory
    if mem.storage_type != "postgres":
        print("[backfill] storage is SQLite — nothing to backfill (vectors are Postgres-only)")
        return 0
    if not hasattr(mem, "backfill_vectors"):
        print("[backfill] memory backend has no backfill_vectors()")
        return 1

    n = mem.backfill_vectors(limit=args.limit)
    stats = mem.memory_stats() if hasattr(mem, "memory_stats") else {}
    print(f"[backfill] updated {n} row(s); pgvector={stats.get('pgvector')} memory_count={stats.get('memory_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
