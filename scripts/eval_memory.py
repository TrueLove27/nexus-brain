#!/usr/bin/env python3
"""Memory quality eval — recall hit-rate and learning usefulness.

Seeds a known fixture of facts / episodes / learnings (tagged with eval_run_id),
queries with related natural-language questions, and reports whether expected
content appears in recall / get_history_context / get_learnings_for_goal.

Works offline: when Ollama embeddings are unavailable, uses a deterministic
hash embedding so ranking still exercises the vector path (keyword fallback
also remains active in BrainMemory).

Usage:
  py scripts/eval_memory.py --help
  py scripts/eval_memory.py
  py scripts/eval_memory.py --no-markdown
  py scripts/eval_memory.py --cleanup --eval-run-id <id>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVAL_CATEGORY_PREFIX = "eval_"


# ---------------------------------------------------------------------------
# Fixture: known ground-truth memories + NL queries
# ---------------------------------------------------------------------------

SEED_FACTS: list[dict[str, Any]] = [
    {
        "id": "fact_workspace",
        "content": "User workspace for Nexus Brain is C:\\Users\\cheki\\nexus-brain",
        "needles": ["nexus-brain", "workspace"],
    },
    {
        "id": "fact_shell",
        "content": "User prefers PowerShell for shell commands, never bash on Windows",
        "needles": ["powershell", "bash"],
    },
    {
        "id": "fact_embed",
        "content": "Nexus uses nomic-embed-text via Ollama for 768-dimensional embeddings",
        "needles": ["nomic-embed-text", "768"],
    },
]

SEED_EPISODES: list[dict[str, Any]] = [
    {
        "id": "ep_pgvector",
        "content": (
            "Completed hybrid recall upgrade: PostgresMemory.recall uses HNSW "
            "vector similarity plus keyword ts_rank and recency/access ranking"
        ),
        "source": "task",
        "needles": ["hybrid recall", "hnsw", "pgvector"],
    },
    {
        "id": "ep_learning",
        "content": (
            "Validated learning loop: embed lessons, dedupe above 0.88 similarity, "
            "contradiction supersession, task_id linkage"
        ),
        "source": "task",
        "needles": ["dedupe", "0.88", "contradiction"],
    },
]

SEED_PROCEDURES: list[dict[str, Any]] = [
    {
        "id": "proc_health",
        "trigger_text": "when checking Nexus health before a session",
        "content": "Run py main.py health; verify Ollama models, Postgres, inbox depth",
        "needles": ["main.py health", "ollama", "inbox"],
    },
]

SEED_LEARNINGS: list[dict[str, Any]] = [
    {
        "id": "learn_leases",
        "task_goal": "Fix durable job lease reclaim double-complete race",
        "outcome": "success",
        "lesson": (
            "Always renew mid-run lease heartbeats and check fencing tokens before "
            "marking a durable job completed after reclaim"
        ),
        "needles": ["fencing", "heartbeat", "lease"],
    },
    {
        "id": "learn_redact",
        "task_goal": "Stop API keys leaking into tool_calls rows",
        "outcome": "success",
        "lesson": (
            "Apply secret redaction before any Postgres write of tool_calls, "
            "checkpoints, or job_traces"
        ),
        "needles": ["redaction", "tool_calls", "job_traces"],
    },
]

# Natural-language probes with expected fixture ids and retrieval channels.
PROBE_QUERIES: list[dict[str, Any]] = [
    {
        "id": "q_workspace",
        "query": "Where is the Nexus Brain project workspace on this machine?",
        "expect_ids": ["fact_workspace"],
        "channels": ["recall", "history", "facts"],
        "needles": ["nexus-brain"],
    },
    {
        "id": "q_shell_pref",
        "query": "What shell should I use for commands on Windows?",
        "expect_ids": ["fact_shell"],
        "channels": ["recall", "history", "facts"],
        "needles": ["powershell"],
    },
    {
        "id": "q_embeddings",
        "query": "Which embedding model does Nexus use and what dimension?",
        "expect_ids": ["fact_embed"],
        "channels": ["recall", "history", "facts"],
        "needles": ["nomic-embed-text"],
    },
    {
        "id": "q_hybrid",
        "query": "How does hybrid recall work with Postgres and vectors?",
        "expect_ids": ["ep_pgvector"],
        "channels": ["recall", "history", "episodes"],
        "needles": ["hybrid", "hnsw"],
    },
    {
        "id": "q_learning_loop",
        "query": "Remind me how the validated learning dedupe and contradiction flow works",
        "expect_ids": ["ep_learning"],
        "channels": ["recall", "history", "episodes"],
        "needles": ["dedupe", "contradiction"],
    },
    {
        "id": "q_health_proc",
        "query": "What should I run to check Nexus health before starting work?",
        "expect_ids": ["proc_health"],
        "channels": ["history", "procedures"],
        "needles": ["health"],
    },
    {
        "id": "q_lease_lesson",
        "query": "How do I safely complete a durable job after lease reclaim?",
        "expect_ids": ["learn_leases"],
        "channels": ["learnings", "history"],
        "needles": ["fencing", "heartbeat"],
    },
    {
        "id": "q_secrets",
        "query": "How do we prevent API keys from being stored in tool call traces?",
        "expect_ids": ["learn_redact"],
        "channels": ["learnings", "history"],
        "needles": ["redaction", "secret"],
    },
]

# Extra learning used only for the usefulness check (inject → retrieve).
USEFULNESS_LEARNING = {
    "id": "learn_useful_eval",
    "task_goal": "Stabilize SQLite fallback when Postgres is down",
    "outcome": "success",
    "lesson": (
        "When PostgreSQL is unavailable create_memory must fall back to BrainMemory "
        "SQLite and still serve recall and get_history_context without raising"
    ),
    "query": "What happens to memory if Postgres is down during Nexus startup?",
    "needles": ["sqlite", "fallback", "postgres"],
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Evaluate Nexus memory quality: seed known facts/episodes/learnings, "
            "query with NL probes, measure recall hit-rate and learning usefulness."
        ),
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "data" / "evals",
        help="Directory for JSON/Markdown reports (default: data/evals)",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "brain.yaml",
        help="brain.yaml for embed model / ollama URL (SQLite always used for seed isolation)",
    )
    p.add_argument(
        "--eval-run-id",
        default="",
        help="Tag for seeded rows (default: auto uuid). Pass with --cleanup to remove.",
    )
    p.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete rows tagged with --eval-run-id from --db (or temp db path) and exit",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Optional SQLite path (default: isolated temp DB under data/evals/_tmp)",
    )
    p.add_argument(
        "--keep-db",
        action="store_true",
        help="Keep the eval SQLite file after the run (still under data/evals)",
    )
    p.add_argument(
        "--no-markdown",
        action="store_true",
        help="Skip writing the Markdown report (JSON always written)",
    )
    p.add_argument(
        "--force-hash-embeddings",
        action="store_true",
        help="Skip Ollama; always use deterministic hash embeddings",
    )
    return p.parse_args()


def _load_embed_cfg(config_path: Path) -> tuple[str, str]:
    embed_model = "nomic-embed-text"
    ollama_url = "http://localhost:11434"
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        llm = cfg.get("llm") or {}
        embed_model = llm.get("embed_model", embed_model)
        ollama_url = llm.get("base_url", ollama_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[eval_memory] config load failed ({exc}) — using defaults", flush=True)
    return embed_model, ollama_url


def _hash_embedding(text: str, dim: int = 768) -> list[float]:
    """Deterministic bag-of-token hash embedding for offline evals."""
    vec = [0.0] * dim
    tokens = [t.lower() for t in (text or "").split() if len(t) > 2]
    if not tokens:
        tokens = ["empty"]
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        # Spread each token across a few dims
        for i in range(0, 16, 4):
            idx = int.from_bytes(digest[i : i + 2], "big") % dim
            sign = 1.0 if digest[i + 2] % 2 == 0 else -1.0
            mag = (digest[i + 3] + 1) / 256.0
            vec[idx] += sign * mag
    # L2 normalize
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _probe_ollama(ollama_url: str, embed_model: str) -> bool:
    try:
        import requests

        r = requests.post(
            f"{ollama_url.rstrip('/')}/api/embeddings",
            json={"model": embed_model, "prompt": "eval ping"},
            timeout=5,
        )
        return r.ok and bool(r.json().get("embedding"))
    except Exception:
        return False


def _hash_embed_fn(text: str, model: str | None = None, url: str | None = None) -> list[float]:
    from brain.embeddings import EMBED_DIM

    return _hash_embedding(text, EMBED_DIM)


def _install_embed_fallback(force_hash: bool, ollama_url: str, embed_model: str) -> str:
    """Return mode: 'ollama' | 'hash'. Monkeypatch embed_text when needed.

    Importing ``brain.embeddings`` loads ``brain/__init__.py``, which binds the
    original ``embed_text`` into ``brain.memory``. Patch both modules (and the
    instance ``_embed`` method after construct) so offline evals skip Ollama.
    """
    import brain.embeddings as emb_mod
    import brain.memory as mem_mod

    if force_hash or not _probe_ollama(ollama_url, embed_model):
        emb_mod.embed_text = _hash_embed_fn  # type: ignore[assignment]
        mem_mod.embed_text = _hash_embed_fn  # type: ignore[assignment]
        return "hash"
    return "ollama"


def _bind_memory_embed(memory: Any, mode: str) -> None:
    if mode != "hash":
        return
    memory._embed = lambda text: _hash_embed_fn(text)  # type: ignore[method-assign]


def _needle_hit(haystack: str, needles: list[str]) -> bool:
    text = (haystack or "").lower()
    return any(n.lower() in text for n in needles if n)


def _join_texts(parts: list[str]) -> str:
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Seed / cleanup
# ---------------------------------------------------------------------------

def _meta(run_id: str, fixture_id: str, extra: dict | None = None) -> dict[str, Any]:
    m = {"eval_run_id": run_id, "eval_fixture_id": fixture_id, "source": "eval"}
    if extra:
        m.update(extra)
    return m


def _eval_category(run_id: str) -> str:
    return f"{EVAL_CATEGORY_PREFIX}{run_id}"


def seed_memory(memory: Any, run_id: str) -> dict[str, list[int]]:
    """Insert fixture rows; return map of kind -> row ids."""
    ids: dict[str, list[int]] = {
        "memories": [],
        "episodes": [],
        "facts": [],
        "procedures": [],
        "learnings": [],
    }
    cat = _eval_category(run_id)

    for fact in SEED_FACTS:
        mid = memory.remember(
            fact["content"],
            category=cat,
            metadata=_meta(run_id, fact["id"]),
        )
        ids["memories"].append(int(mid))
        if hasattr(memory, "insert_fact"):
            fid = memory.insert_fact(
                fact["content"],
                confidence=0.9,
                category=cat,
            )
            ids["facts"].append(int(fid))

    for ep in SEED_EPISODES:
        mid = memory.remember(
            ep["content"],
            category=cat,
            metadata=_meta(run_id, ep["id"], {"source": ep.get("source", "task")}),
        )
        ids["memories"].append(int(mid))
        # remember() already dual-writes an episode; also record with explicit tag
        if hasattr(memory, "record_episode"):
            eid = memory.record_episode(
                ep["content"],
                ep.get("source", "task"),
                metadata=_meta(run_id, ep["id"]),
            )
            ids["episodes"].append(int(eid))

    for proc in SEED_PROCEDURES:
        if hasattr(memory, "insert_procedure"):
            pid = memory.insert_procedure(
                proc["trigger_text"],
                proc["content"],
                confidence=0.85,
                category=cat,
            )
            ids["procedures"].append(int(pid))
        mid = memory.remember(
            f"PROCEDURE [{proc['trigger_text']}]: {proc['content']}",
            category=cat,
            metadata=_meta(run_id, proc["id"]),
        )
        ids["memories"].append(int(mid))

    for learn in SEED_LEARNINGS:
        goal = f"[eval:{run_id}] {learn['task_goal']}"
        lid = memory.record_learning(
            task_goal=goal,
            outcome=learn["outcome"],
            lesson=learn["lesson"],
            confidence=0.8,
        )
        ids["learnings"].append(int(lid))

    return ids


def cleanup_eval_data(memory: Any, run_id: str) -> dict[str, int]:
    """Delete rows tagged with eval_run_id / eval category / [eval:run_id] goal."""
    deleted = {
        "memories": 0,
        "episodes": 0,
        "facts": 0,
        "procedures": 0,
        "learnings": 0,
    }
    cat = _eval_category(run_id)
    marker = f"[eval:{run_id}]"
    run_needle = f'"eval_run_id": "{run_id}"'
    run_needle_alt = f'"eval_run_id":"{run_id}"'

    # Prefer direct SQLite cleanup when available
    if getattr(memory, "storage_type", "") == "sqlite" and hasattr(memory, "_conn"):
        with memory._conn() as conn:
            deleted["memories"] = conn.execute(
                "DELETE FROM memories WHERE category = ? OR metadata LIKE ? OR metadata LIKE ?",
                (cat, f"%{run_needle}%", f"%{run_needle_alt}%"),
            ).rowcount
            deleted["episodes"] = conn.execute(
                "DELETE FROM memory_episodes WHERE metadata LIKE ? OR metadata LIKE ?",
                (f"%{run_needle}%", f"%{run_needle_alt}%"),
            ).rowcount
            deleted["facts"] = conn.execute(
                "DELETE FROM memory_facts WHERE category = ?",
                (cat,),
            ).rowcount
            deleted["procedures"] = conn.execute(
                "DELETE FROM memory_procedures WHERE category = ?",
                (cat,),
            ).rowcount
            deleted["learnings"] = conn.execute(
                "DELETE FROM learnings WHERE task_goal LIKE ?",
                (f"{marker}%",),
            ).rowcount
        return deleted

    # Best-effort generic path (Postgres or unknown)
    try:
        if hasattr(memory, "_conn"):
            with memory._conn() as conn:
                # Postgres uses %s — try both styles
                try:
                    cur = conn.cursor() if hasattr(conn, "cursor") else conn
                    for sql, args, key in [
                        (
                            "DELETE FROM memories WHERE category = %s OR metadata::text LIKE %s",
                            (cat, f"%{run_id}%"),
                            "memories",
                        ),
                        (
                            "DELETE FROM memory_episodes WHERE metadata::text LIKE %s",
                            (f"%{run_id}%",),
                            "episodes",
                        ),
                        ("DELETE FROM memory_facts WHERE category = %s", (cat,), "facts"),
                        (
                            "DELETE FROM memory_procedures WHERE category = %s",
                            (cat,),
                            "procedures",
                        ),
                        (
                            "DELETE FROM learnings WHERE task_goal LIKE %s",
                            (f"{marker}%",),
                            "learnings",
                        ),
                    ]:
                        try:
                            cur.execute(sql, args)
                            deleted[key] = cur.rowcount or 0
                        except Exception:
                            pass
                    if hasattr(conn, "commit"):
                        conn.commit()
                except Exception:
                    pass
    except Exception as exc:  # noqa: BLE001
        print(f"[eval_memory] cleanup warning: {exc}", flush=True)
    return deleted


# ---------------------------------------------------------------------------
# Probe evaluation
# ---------------------------------------------------------------------------

def _collect_channels(memory: Any, query: str) -> dict[str, str]:
    channels: dict[str, str] = {}

    try:
        recalled = memory.recall(query, limit=10) or []
        channels["recall"] = _join_texts([str(x) for x in recalled])
    except Exception as exc:  # noqa: BLE001
        channels["recall"] = f"__ERROR__:{exc}"

    try:
        channels["history"] = memory.get_history_context(query) or ""
    except Exception as exc:  # noqa: BLE001
        channels["history"] = f"__ERROR__:{exc}"

    try:
        learns = memory.get_learnings_for_goal(query, limit=8) or []
        channels["learnings"] = _join_texts(
            [f"{l.get('task_goal', '')} | {l.get('lesson', '')}" for l in learns]
        )
    except Exception as exc:  # noqa: BLE001
        channels["learnings"] = f"__ERROR__:{exc}"

    try:
        if hasattr(memory, "search_facts"):
            facts = memory.search_facts(query, limit=8) or []
            channels["facts"] = _join_texts([str(f.get("content") or "") for f in facts])
        else:
            channels["facts"] = ""
    except Exception as exc:  # noqa: BLE001
        channels["facts"] = f"__ERROR__:{exc}"

    try:
        if hasattr(memory, "search_episodes"):
            eps = memory.search_episodes(query, limit=8) or []
            channels["episodes"] = _join_texts([str(e.get("content") or "") for e in eps])
        else:
            channels["episodes"] = ""
    except Exception as exc:  # noqa: BLE001
        channels["episodes"] = f"__ERROR__:{exc}"

    try:
        if hasattr(memory, "search_procedures"):
            procs = memory.search_procedures(query, limit=8) or []
            channels["procedures"] = _join_texts(
                [
                    f"{p.get('trigger_text', '')} | {p.get('content', '')}"
                    for p in procs
                ]
            )
        else:
            channels["procedures"] = ""
    except Exception as exc:  # noqa: BLE001
        channels["procedures"] = f"__ERROR__:{exc}"

    return channels


def evaluate_probes(memory: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for probe in PROBE_QUERIES:
        print(f"  probing {probe['id']} ...", flush=True)
        channels = _collect_channels(memory, probe["query"])
        wanted = probe.get("channels") or ["recall", "history"]
        needles = probe.get("needles") or []
        channel_hits: dict[str, bool] = {}
        for ch in wanted:
            hay = channels.get(ch, "")
            channel_hits[ch] = (not hay.startswith("__ERROR__")) and _needle_hit(
                hay, needles
            )
        hit = any(channel_hits.values())
        rows.append(
            {
                "id": probe["id"],
                "query": probe["query"],
                "expect_ids": probe.get("expect_ids") or [],
                "needles": needles,
                "channels_checked": wanted,
                "channel_hits": channel_hits,
                "hit": hit,
                "preview": {
                    ch: (channels.get(ch) or "")[:220] for ch in wanted
                },
            }
        )
        mark = "HIT" if hit else "MISS"
        print(f"    -> {mark}", flush=True)
    return rows


def evaluate_learning_usefulness(memory: Any, run_id: str) -> dict[str, Any]:
    """Inject a learning, then check retrieval for a related goal."""
    learn = USEFULNESS_LEARNING
    goal = f"[eval:{run_id}] {learn['task_goal']}"
    started = time.perf_counter()
    lid = memory.record_learning(
        task_goal=goal,
        outcome=learn["outcome"],
        lesson=learn["lesson"],
        confidence=0.85,
    )
    matches = memory.get_learnings_for_goal(learn["query"], limit=8) or []
    history = ""
    try:
        history = memory.get_history_context(learn["query"]) or ""
    except Exception:
        history = ""

    lesson_texts = _join_texts(
        [f"{m.get('task_goal', '')} | {m.get('lesson', '')}" for m in matches]
    )
    retrieved = _needle_hit(lesson_texts, learn["needles"]) or _needle_hit(
        history, learn["needles"]
    )
    id_match = any(int(m.get("id") or 0) == int(lid) for m in matches)

    # Optional: mark outcome helped and confirm confidence bump path works
    helped_ok = False
    try:
        helped_ok = bool(memory.record_learning_outcome(int(lid), helped=True))
    except Exception:
        helped_ok = False

    return {
        "learning_id": int(lid),
        "fixture_id": learn["id"],
        "query": learn["query"],
        "needles": learn["needles"],
        "retrieved": retrieved,
        "id_in_top": id_match,
        "useful": retrieved or id_match,
        "record_outcome_ok": helped_ok,
        "match_count": len(matches),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "preview": lesson_texts[:300],
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _write_reports(
    out_dir: Path,
    report: dict[str, Any],
    *,
    write_md: bool,
) -> tuple[Path, Path | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["ended_at"]).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"memory_eval_{stamp}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md_path: Path | None = None
    if write_md:
        md_path = out_dir / f"memory_eval_{stamp}.md"
        lines = [
            "# Memory Quality Eval Report",
            "",
            f"- **Started:** {report['started_at']}",
            f"- **Ended:** {report['ended_at']}",
            f"- **Eval run id:** `{report['eval_run_id']}`",
            f"- **Storage:** {report['storage']}",
            f"- **Embedding mode:** {report['embedding_mode']}",
            f"- **Recall hit-rate:** {report['hit_rate_pct']}% "
            f"({report['hits']}/{report['probe_count']})",
            f"- **Learning useful:** {'yes' if report['learning_usefulness'].get('useful') else 'no'}",
            "",
            "## Probes",
            "",
            "| Id | Hit | Channels | Query |",
            "|----|-----|----------|-------|",
        ]
        for p in report["probes"]:
            ch = ", ".join(
                f"{k}={'Y' if v else 'n'}" for k, v in (p.get("channel_hits") or {}).items()
            )
            q = (p.get("query") or "").replace("|", "/")
            lines.append(
                f"| `{p['id']}` | {'yes' if p.get('hit') else 'no'} | {ch} | {q[:60]} |"
            )

        lu = report["learning_usefulness"]
        lines.extend(
            [
                "",
                "## Learning usefulness",
                "",
                f"- Injected learning id: `{lu.get('learning_id')}`",
                f"- Retrieved for related goal: **{'yes' if lu.get('useful') else 'no'}**",
                f"- Id in top matches: {lu.get('id_in_top')}",
                f"- record_learning_outcome: {lu.get('record_outcome_ok')}",
                "",
                "## Notes",
                "",
                f"- Seeded ids: `{json.dumps(report.get('seeded_ids') or {})}`",
                f"- Cleanup deleted: `{json.dumps(report.get('cleanup') or {})}`",
                "",
            ]
        )
        md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    return json_path, md_path


def _make_memory(db_path: Path, embed_model: str, ollama_url: str) -> Any:
    from brain.memory import BrainMemory

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return BrainMemory(
        db_path=db_path,
        embed_model=embed_model,
        ollama_url=ollama_url,
        brain_cfg={"memory_tiers": {"enabled": True}},
    )


def main() -> int:
    args = _parse_args()
    run_id = (args.eval_run_id or "").strip() or uuid.uuid4().hex[:12]
    embed_model, ollama_url = _load_embed_cfg(args.config)

    embed_mode = _install_embed_fallback(
        args.force_hash_embeddings, ollama_url, embed_model
    )

    # Resolve DB path
    tmp_dir: tempfile.TemporaryDirectory | None = None
    owned_tmp = False
    if args.db:
        db_path = args.db
    else:
        # Isolated DB under data/evals so --cleanup can still target it if --keep-db
        keep_root = ROOT / "data" / "evals" / "_runs"
        keep_root.mkdir(parents=True, exist_ok=True)
        if args.keep_db or args.cleanup:
            db_path = keep_root / f"eval_{run_id}.db"
        else:
            tmp_dir = tempfile.TemporaryDirectory(prefix="nexus_mem_eval_")
            owned_tmp = True
            db_path = Path(tmp_dir.name) / "memory.db"

    print("Nexus memory quality eval", flush=True)
    print(f"  eval_run_id:     {run_id}", flush=True)
    print(f"  db:              {db_path}", flush=True)
    print(f"  embedding_mode:  {embed_mode}", flush=True)
    print(f"  cleanup_only:    {args.cleanup}", flush=True)
    print(flush=True)

    memory = _make_memory(db_path, embed_model, ollama_url)
    _bind_memory_embed(memory, embed_mode)

    if args.cleanup:
        deleted = cleanup_eval_data(memory, run_id)
        print("Cleanup:", json.dumps(deleted), flush=True)
        # Remove empty keep-db file if we own it
        if db_path.exists() and db_path.stat().st_size < 4096:
            try:
                db_path.unlink()
            except OSError:
                pass
        if owned_tmp and tmp_dir:
            tmp_dir.cleanup()
        return 0

    started_at = datetime.now(timezone.utc).isoformat()
    seeded = seed_memory(memory, run_id)
    print(
        f"  seeded: memories={len(seeded['memories'])} facts={len(seeded['facts'])} "
        f"episodes={len(seeded['episodes'])} procedures={len(seeded['procedures'])} "
        f"learnings={len(seeded['learnings'])}",
        flush=True,
    )

    probes = evaluate_probes(memory)
    hits = sum(1 for p in probes if p.get("hit"))
    probe_count = len(probes)
    hit_rate = round((hits / probe_count) * 100.0, 1) if probe_count else 0.0

    usefulness = evaluate_learning_usefulness(memory, run_id)
    print(
        f"  learning usefulness: "
        f"{'useful' if usefulness.get('useful') else 'not useful'} "
        f"(id={usefulness.get('learning_id')})",
        flush=True,
    )

    # Always clean tagged rows from this run's DB (temp or kept)
    cleaned = cleanup_eval_data(memory, run_id)
    ended_at = datetime.now(timezone.utc).isoformat()

    report = {
        "started_at": started_at,
        "ended_at": ended_at,
        "eval_run_id": run_id,
        "storage": getattr(memory, "storage_type", "sqlite"),
        "db_path": str(db_path),
        "embedding_mode": embed_mode,
        "embed_model": embed_model,
        "ollama_url": ollama_url,
        "probe_count": probe_count,
        "hits": hits,
        "hit_rate_pct": hit_rate,
        "probes": probes,
        "learning_usefulness": usefulness,
        "seeded_ids": seeded,
        "cleanup": cleaned,
        "fixture_counts": {
            "facts": len(SEED_FACTS),
            "episodes": len(SEED_EPISODES),
            "procedures": len(SEED_PROCEDURES),
            "learnings": len(SEED_LEARNINGS),
            "probes": len(PROBE_QUERIES),
        },
    }

    json_path, md_path = _write_reports(
        args.out_dir, report, write_md=not args.no_markdown
    )

    logs_dir = ROOT / "data" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(ended_at).strftime("%Y%m%d_%H%M%S")
    log_copy = logs_dir / f"memory_eval_{stamp}.json"
    log_copy.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(flush=True)
    print(
        f"Summary: hit-rate {hit_rate}% ({hits}/{probe_count}); "
        f"learning_useful={bool(usefulness.get('useful'))}",
        flush=True,
    )
    print(f"Wrote {json_path}", flush=True)
    if md_path:
        print(f"Wrote {md_path}", flush=True)
    print(f"Wrote {log_copy}", flush=True)

    if owned_tmp and tmp_dir:
        # Windows: SQLite may keep the file handle until GC; ignore cleanup errors
        try:
            del memory
        except Exception:
            pass
        try:
            tmp_dir.cleanup()
        except PermissionError:
            pass
    elif not args.keep_db and db_path.exists() and "_runs" in str(db_path):
        try:
            db_path.unlink()
        except OSError:
            pass

    # Non-zero if catastrophically bad (< 25%) — soft signal for CI
    if hit_rate < 25.0 and not usefulness.get("useful"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
