from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from core.events import EventBus


def register_database_tools(registry) -> None:
    root = Path(__file__).resolve().parent.parent
    events = EventBus.get()

    cfg_path = root / "config" / "features.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    dbs = cfg.get("databases", {})

    def list_databases() -> str:
        lines = [f"- {name}: {spec.get('type', '?')}" for name, spec in dbs.items()]
        events.emit("database_list", {"count": len(lines)})
        return "\n".join(lines) if lines else "No databases configured in config/features.yaml"

    def query_database(db_name: str, query: str, limit: int = 50) -> str:
        if db_name not in dbs:
            return f"Unknown database '{db_name}'. Use list_databases. Available: {', '.join(dbs.keys())}"

        spec = dbs[db_name]
        db_type = spec.get("type", "sqlite")
        events.emit("database_query", {"db": db_name, "query": query[:200]})

        if db_type == "sqlite":
            db_path = Path(spec["path"])
            if not db_path.exists():
                return f"SQLite file not found: {db_path}"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.execute(query)
                if query.strip().upper().startswith("SELECT"):
                    rows = cur.fetchmany(limit)
                    result = [dict(r) for r in rows]
                    events.emit("database_result", {"db": db_name, "rows": len(result)})
                    return json.dumps(result, indent=2, default=str)
                conn.commit()
                return f"OK — {cur.rowcount} rows affected"
            except Exception as e:
                return f"Query error: {e}"
            finally:
                conn.close()

        if db_type == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
                conn = psycopg.connect(
                    f"postgresql://{spec['user']}:{spec['password']}@{spec.get('host','localhost')}:{spec.get('port',5432)}/{spec['database']}",
                    row_factory=dict_row,
                )
                with conn:
                    cur = conn.execute(query)
                    if query.strip().upper().startswith("SELECT"):
                        rows = cur.fetchmany(limit)
                        result = [dict(r) for r in rows]
                        events.emit("database_result", {"db": db_name, "rows": len(result)})
                        return json.dumps(result, indent=2, default=str)
                    conn.commit()
                    return f"OK — {cur.rowcount} rows affected"
            except ImportError:
                return "Install psycopg: py -m pip install psycopg[binary]"
            except Exception as e:
                return f"PostgreSQL error: {e}"

        if db_type == "mongodb":
            try:
                from pymongo import MongoClient
                client = MongoClient(spec["uri"], serverSelectionTimeoutMS=5000)
                db = client[spec["database"]]
                if query.strip().startswith("{"):
                    filt = json.loads(query)
                    coll_name = spec.get("collection", "documents")
                    rows = list(db[coll_name].find(filt).limit(limit))
                    for r in rows:
                        r["_id"] = str(r.get("_id", ""))
                    return json.dumps(rows, indent=2, default=str)
                return "MongoDB: pass query as JSON filter object"
            except ImportError:
                return "Install pymongo for MongoDB support: py -m pip install pymongo"
            except Exception as e:
                return f"MongoDB error: {e}"

        return f"Unsupported database type: {db_type}"

    registry.register("list_databases", "List configured databases",
                      {}, list_databases)
    registry.register("query_database", "Run a query against a configured database",
                      {"db_name": "str", "query": "str", "limit": "int (optional)"},
                      query_database)
