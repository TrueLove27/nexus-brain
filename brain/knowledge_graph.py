"""Project/entity knowledge graph — upsert, link, neighborhood, LLM extraction."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

from brain.repos.entities import (
    ENTITY_TYPES,
    EntitiesRepo,
    normalize_entity_name,
)

log = logging.getLogger("nexus.knowledge_graph")

ChatFn = Callable[[list[dict[str, str]]], str]

EXTRACT_PROMPT = """Extract a knowledge graph fragment from this agent task.

Return ONLY valid JSON (no markdown) in this exact shape:
{{
  "entities": [
    {{"name": "string", "entity_type": "project|file|tool|concept|person|repo|other", "description": "short"}}
  ],
  "relations": [
    {{"from": "entity name", "to": "entity name", "relation_type": "USED_TOOL|FAILED_ON|LEARNED_FROM|PREFERS|TOUCHES|PART_OF|RELATED_TO|MENTIONS", "weight": 1.0, "evidence": "short"}}
  ]
}}

Rules:
- 2-10 entities, 1-12 relations; quality over quantity
- Prefer concrete names (repos, tools, file paths, project names)
- On failure outcome, include FAILED_ON edges to the failing tool/file/concept
- On success, include USED_TOOL / TOUCHES / PART_OF when evidenced
- relation_type must be uppercase snake like USED_TOOL

Goal: {goal}
Outcome: {outcome}
Result: {result}
Steps:
{steps}
"""

TEACH_PROMPT = """The user taught this preference. Extract knowledge-graph entities and PREFERS relations.

Return ONLY valid JSON (no markdown):
{{
  "entities": [
    {{"name": "string", "entity_type": "project|file|tool|concept|person|repo|other", "description": "short"}}
  ],
  "relations": [
    {{"from": "user", "to": "entity name", "relation_type": "PREFERS", "weight": 1.0, "evidence": "short"}}
  ]
}}

Preference: {preference}
"""


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def format_neighborhood(nb: dict[str, Any], max_lines: int = 16) -> list[str]:
    """Human-readable lines for history context."""
    entity = nb.get("entity")
    if not entity:
        return []
    lines: list[str] = []
    et = entity.get("entity_type", "other")
    name = entity.get("name", "?")
    desc = (entity.get("description") or "")[:120]
    head = f"- {et}:{name}"
    if desc:
        head += f" — {desc}"
    lines.append(head)

    neighbors = {int(n["id"]): n for n in (nb.get("neighbors") or []) if n.get("id") is not None}
    for rel in (nb.get("relations") or [])[: max_lines - 1]:
        fid = int(rel.get("from_id") or 0)
        tid = int(rel.get("to_id") or 0)
        center_id = int(entity["id"])
        other_id = tid if fid == center_id else fid
        other = neighbors.get(other_id) or {}
        other_label = other.get("name") or f"#{other_id}"
        other_type = other.get("entity_type") or "?"
        direction = "->" if fid == center_id else "<-"
        w = rel.get("weight")
        w_s = f" w={w:.1f}" if isinstance(w, (int, float)) else ""
        evid = (rel.get("evidence") or "")[:80]
        line = f"  {direction} [{rel.get('relation_type')}] {other_type}:{other_label}{w_s}"
        if evid:
            line += f" ({evid})"
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return lines


class SqliteEntitiesBackend:
    """SQLite parity for kg_entities / kg_relations (BrainMemory)._"""

    def __init__(self, memory, embed_fn=None):
        self.memory = memory
        self._embed = embed_fn

    def _conn(self):
        return self.memory._conn()

    def upsert(
        self,
        name: str,
        entity_type: str = "other",
        *,
        description: str | None = None,
        metadata: dict | None = None,
        embedding: list[float] | None = None,
        bump_mention: bool = True,
    ) -> dict[str, Any]:
        et = (entity_type or "other").strip().lower()
        if et not in ENTITY_TYPES:
            et = "other"
        norm = normalize_entity_name(name)
        display = (name or "").strip()[:300] or norm
        now = datetime.now(timezone.utc).isoformat()
        if embedding is None and self._embed and (description or display):
            embedding = self._embed(f"{et}: {display}. {description or ''}".strip())
        meta = json.dumps(metadata or {})
        emb = json.dumps(embedding) if embedding else None
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id, mention_count FROM kg_entities WHERE entity_type = ? AND normalized_name = ?",
                (et, norm),
            ).fetchone()
            if existing:
                mc = int(existing["mention_count"] or 1) + (1 if bump_mention else 0)
                conn.execute(
                    """UPDATE kg_entities SET
                         name = COALESCE(?, name),
                         description = COALESCE(NULLIF(?, ''), description),
                         embedding = COALESCE(?, embedding),
                         metadata = ?,
                         mention_count = ?,
                         updated_at = ?
                       WHERE id = ?""",
                    (display, description, emb, meta, mc, now, existing["id"]),
                )
                eid = int(existing["id"])
            else:
                cur = conn.execute(
                    """INSERT INTO kg_entities
                       (entity_type, name, normalized_name, description, embedding,
                        metadata, mention_count, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (et, display, norm, description, emb, meta, now, now),
                )
                eid = int(cur.lastrowid or 0)
            row = conn.execute(
                """SELECT id, entity_type, name, normalized_name, description,
                          metadata, mention_count, created_at, updated_at
                   FROM kg_entities WHERE id = ?""",
                (eid,),
            ).fetchone()
        return dict(row) if row else {}

    def get(self, entity_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, entity_type, name, normalized_name, description,
                          metadata, mention_count, created_at, updated_at
                   FROM kg_entities WHERE id = ?""",
                (entity_id,),
            ).fetchone()
        return dict(row) if row else None

    def find(self, name: str, entity_type: str | None = None) -> dict[str, Any] | None:
        norm = normalize_entity_name(name)
        with self._conn() as conn:
            if entity_type:
                row = conn.execute(
                    """SELECT id, entity_type, name, normalized_name, description,
                              metadata, mention_count, created_at, updated_at
                       FROM kg_entities WHERE entity_type = ? AND normalized_name = ?""",
                    (entity_type.strip().lower(), norm),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT id, entity_type, name, normalized_name, description,
                              metadata, mention_count, created_at, updated_at
                       FROM kg_entities WHERE normalized_name = ?
                       ORDER BY mention_count DESC LIMIT 1""",
                    (norm,),
                ).fetchone()
        return dict(row) if row else None

    def search_by_text(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        like = f"%{q[:80]}%"
        norm_like = f"%{normalize_entity_name(query)[:80]}%"
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, entity_type, name, normalized_name, description,
                          metadata, mention_count, created_at, updated_at
                   FROM kg_entities
                   WHERE LOWER(name) LIKE ? OR normalized_name LIKE ?
                      OR LOWER(COALESCE(description, '')) LIKE ?
                   ORDER BY mention_count DESC, updated_at DESC
                   LIMIT ?""",
                (like, norm_like, like, limit),
            ).fetchall()
            hits = [dict(r) for r in rows]
            if hits:
                return hits
            tokens = [t for t in re.split(r"\W+", q) if len(t) > 2][:12]
            scored: dict[int, tuple[int, dict]] = {}
            for tok in tokens:
                tlike = f"%{tok}%"
                trows = conn.execute(
                    """SELECT id, entity_type, name, normalized_name, description,
                              metadata, mention_count, created_at, updated_at
                       FROM kg_entities
                       WHERE normalized_name LIKE ? OR LOWER(name) LIKE ?
                          OR LOWER(COALESCE(description, '')) LIKE ?
                       LIMIT 40""",
                    (tlike, tlike, tlike),
                ).fetchall()
                for r in trows:
                    d = dict(r)
                    eid = int(d["id"])
                    prev = scored.get(eid)
                    score = (prev[0] if prev else 0) + 1
                    scored[eid] = (score, d)
            ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
            return [d for _, d in ranked[:limit]]

    def link(
        self,
        from_id: int,
        to_id: int,
        relation_type: str,
        *,
        weight: float = 1.0,
        evidence: str | None = None,
        task_id: int | None = None,
        accumulate: bool = True,
    ) -> dict[str, Any]:
        if from_id == to_id:
            return {}
        rel = (relation_type or "RELATED_TO").strip().upper()[:64]
        w = max(0.0, float(weight))
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                """SELECT id, weight FROM kg_relations
                   WHERE from_id = ? AND to_id = ? AND relation_type = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (from_id, to_id, rel),
            ).fetchone()
            if existing and accumulate:
                new_w = float(existing["weight"] or 0) + w
                conn.execute(
                    """UPDATE kg_relations SET weight = ?,
                         evidence = COALESCE(NULLIF(?, ''), evidence),
                         task_id = COALESCE(?, task_id)
                       WHERE id = ?""",
                    (new_w, evidence, task_id, existing["id"]),
                )
                eid = int(existing["id"])
            else:
                cur = conn.execute(
                    """INSERT INTO kg_relations
                       (from_id, to_id, relation_type, weight, evidence, task_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (from_id, to_id, rel, w, evidence, task_id, now),
                )
                eid = int(cur.lastrowid or 0)
            row = conn.execute(
                """SELECT id, from_id, to_id, relation_type, weight, evidence, task_id, created_at
                   FROM kg_relations WHERE id = ?""",
                (eid,),
            ).fetchone()
        return dict(row) if row else {}

    def neighborhood(
        self,
        entity_id: int,
        *,
        hops: int = 1,
        limit: int = 40,
    ) -> dict[str, Any]:
        center = self.get(entity_id)
        if not center:
            return {"entity": None, "relations": [], "neighbors": []}
        hop = max(1, min(int(hops), 2))
        frontier = {int(entity_id)}
        seen_nodes = {int(entity_id)}
        all_rels: list[dict] = []
        neighbors: dict[int, dict] = {}

        with self._conn() as conn:
            for _ in range(hop):
                if not frontier:
                    break
                next_frontier: set[int] = set()
                for fid in list(frontier):
                    rows = conn.execute(
                        """SELECT id, from_id, to_id, relation_type, weight,
                                  evidence, task_id, created_at
                           FROM kg_relations
                           WHERE from_id = ? OR to_id = ?
                           ORDER BY weight DESC, created_at DESC
                           LIMIT ?""",
                        (fid, fid, limit),
                    ).fetchall()
                    for r in rows:
                        d = dict(r)
                        all_rels.append(d)
                        for nid in (int(d["from_id"]), int(d["to_id"])):
                            if nid not in seen_nodes:
                                seen_nodes.add(nid)
                                next_frontier.add(nid)
                frontier = next_frontier

            for nid in seen_nodes:
                if nid == entity_id:
                    continue
                row = conn.execute(
                    """SELECT id, entity_type, name, normalized_name, description,
                              metadata, mention_count, created_at, updated_at
                       FROM kg_entities WHERE id = ?""",
                    (nid,),
                ).fetchone()
                if row:
                    neighbors[nid] = dict(row)

        uniq: dict[int, dict] = {}
        for rel in all_rels:
            uniq[int(rel["id"])] = rel
        rels = sorted(uniq.values(), key=lambda r: float(r.get("weight") or 0), reverse=True)[
            :limit
        ]
        return {
            "entity": center,
            "relations": rels,
            "neighbors": list(neighbors.values())[:limit],
        }

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            e = conn.execute("SELECT COUNT(*) AS c FROM kg_entities").fetchone()
            r = conn.execute("SELECT COUNT(*) AS c FROM kg_relations").fetchone()
        return {
            "kg_entities": int(e["c"] if e else 0),
            "kg_relations": int(r["c"] if r else 0),
        }


class KnowledgeGraph:
    """High-level API over Postgres EntitiesRepo or SQLite backend."""

    def __init__(self, memory: Any, llm_chat_fn: ChatFn | None = None):
        self.memory = memory
        self.llm_chat = llm_chat_fn
        embed_fn = getattr(memory, "_embed", None)
        repo = getattr(memory, "entities", None)
        if repo is not None:
            self._backend: EntitiesRepo | SqliteEntitiesBackend = repo
        else:
            self._backend = SqliteEntitiesBackend(memory, embed_fn=embed_fn)

    def upsert_entity(
        self,
        name: str,
        entity_type: str = "other",
        *,
        description: str | None = None,
        metadata: dict | None = None,
        bump_mention: bool = True,
    ) -> dict[str, Any]:
        return self._backend.upsert(
            name,
            entity_type,
            description=description,
            metadata=metadata,
            bump_mention=bump_mention,
        )

    def link(
        self,
        from_id: int,
        to_id: int,
        relation_type: str,
        *,
        weight: float = 1.0,
        evidence: str | None = None,
        task_id: int | None = None,
    ) -> dict[str, Any]:
        return self._backend.link(
            from_id,
            to_id,
            relation_type,
            weight=weight,
            evidence=evidence,
            task_id=task_id,
        )

    def link_names(
        self,
        from_name: str,
        to_name: str,
        relation_type: str,
        *,
        from_type: str = "other",
        to_type: str = "other",
        weight: float = 1.0,
        evidence: str | None = None,
        task_id: int | None = None,
    ) -> dict[str, Any]:
        a = self.upsert_entity(from_name, from_type)
        b = self.upsert_entity(to_name, to_type)
        if not a or not b:
            return {}
        return self.link(
            int(a["id"]),
            int(b["id"]),
            relation_type,
            weight=weight,
            evidence=evidence,
            task_id=task_id,
        )

    def neighborhood(
        self,
        query: str | None = None,
        *,
        entity_id: int | None = None,
        entity: str | None = None,
        hops: int = 1,
        limit: int = 40,
    ) -> dict[str, Any]:
        """Neighborhood by entity id, exact name, or free-text query."""
        if entity_id is not None:
            return self._backend.neighborhood(int(entity_id), hops=hops, limit=limit)
        name = entity or query
        if not name:
            return {"entity": None, "relations": [], "neighbors": [], "matches": []}
        found = self._backend.find(name)
        if found:
            nb = self._backend.neighborhood(int(found["id"]), hops=hops, limit=limit)
            nb["matches"] = [found]
            return nb
        matches = self._backend.search_by_text(name, limit=5)
        if not matches:
            return {"entity": None, "relations": [], "neighbors": [], "matches": []}
        # Merge 1-hop for top matches
        primary = matches[0]
        nb = self._backend.neighborhood(int(primary["id"]), hops=hops, limit=limit)
        nb["matches"] = matches
        if len(matches) > 1:
            extra_neighbors: dict[int, dict] = {
                int(n["id"]): n for n in nb.get("neighbors") or [] if n.get("id") is not None
            }
            extra_rels = {int(r["id"]): r for r in nb.get("relations") or [] if r.get("id") is not None}
            for m in matches[1:3]:
                sub = self._backend.neighborhood(int(m["id"]), hops=1, limit=limit // 2)
                for n in sub.get("neighbors") or []:
                    extra_neighbors[int(n["id"])] = n
                for r in sub.get("relations") or []:
                    extra_rels[int(r["id"])] = r
            nb["neighbors"] = list(extra_neighbors.values())[:limit]
            nb["relations"] = list(extra_rels.values())[:limit]
        return nb

    def extract_from_text(
        self,
        goal: str,
        result: str = "",
        steps: list[dict] | None = None,
        *,
        success: bool = True,
        task_id: int | None = None,
        preference: str | None = None,
    ) -> dict[str, Any]:
        """LLM-extract entities + relations and persist them."""
        empty = {"entities": [], "relations": [], "written_entities": 0, "written_relations": 0}
        if not self.llm_chat:
            return empty

        if preference:
            prompt = TEACH_PROMPT.format(preference=preference[:1500])
        else:
            step_summary = "\n".join(
                f"  - {s.get('action', '?')}: {str(s.get('result', ''))[:200]}"
                for s in (steps or [])[-12:]
            )
            prompt = EXTRACT_PROMPT.format(
                goal=(goal or "")[:800],
                outcome="success" if success else "failure",
                result=(result or "")[:800],
                steps=step_summary[:1200] or "(none)",
            )

        try:
            raw = self.llm_chat([{"role": "user", "content": prompt}])
            data = _extract_json(raw) or {}
        except Exception as exc:
            log.debug("kg extract failed: %s", exc)
            return empty

        name_to_id: dict[str, int] = {}
        written_e = 0
        written_r = 0

        # Always ensure a "user" person entity for preference edges
        if preference:
            user = self.upsert_entity("user", "person", description="The human operator")
            if user:
                name_to_id["user"] = int(user["id"])
                written_e += 1

        for ent in data.get("entities") or []:
            if not isinstance(ent, dict):
                continue
            name = str(ent.get("name") or "").strip()
            if not name:
                continue
            et = str(ent.get("entity_type") or "other").strip().lower()
            if et not in ENTITY_TYPES:
                et = "other"
            desc = str(ent.get("description") or "")[:500] or None
            row = self.upsert_entity(name, et, description=desc)
            if row and row.get("id") is not None:
                name_to_id[normalize_entity_name(name)] = int(row["id"])
                name_to_id[name.lower()] = int(row["id"])
                written_e += 1

        for rel in data.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            frm = str(rel.get("from") or "").strip()
            to = str(rel.get("to") or "").strip()
            if not frm or not to:
                continue
            rtype = str(rel.get("relation_type") or "RELATED_TO").strip().upper()
            try:
                weight = float(rel.get("weight") if rel.get("weight") is not None else 1.0)
            except (TypeError, ValueError):
                weight = 1.0
            evidence = str(rel.get("evidence") or "")[:400] or None

            def _resolve(label: str) -> int | None:
                key = normalize_entity_name(label)
                if key in name_to_id:
                    return name_to_id[key]
                if label.lower() in name_to_id:
                    return name_to_id[label.lower()]
                found = self._backend.find(label)
                if found:
                    return int(found["id"])
                # Upsert as other
                row = self.upsert_entity(label, "other")
                return int(row["id"]) if row and row.get("id") is not None else None

            fid = _resolve(frm)
            tid = _resolve(to)
            if fid is None or tid is None:
                continue
            self.link(
                fid,
                tid,
                rtype,
                weight=weight,
                evidence=evidence,
                task_id=task_id,
            )
            written_r += 1

        return {
            "entities": list(data.get("entities") or []),
            "relations": list(data.get("relations") or []),
            "written_entities": written_e,
            "written_relations": written_r,
        }

    def context_for_goal(self, goal: str, max_entities: int = 4, max_lines: int = 20) -> str:
        """Format a Knowledge graph section for get_history_context."""
        matches = self._backend.search_by_text(goal, limit=max_entities)
        if not matches:
            # try token-level neighborhood merge
            nb = self.neighborhood(query=goal, hops=1, limit=24)
            if not nb.get("entity"):
                return ""
            lines = ["## Knowledge graph"]
            lines.extend(format_neighborhood(nb, max_lines=max_lines))
            return "\n".join(lines)

        lines = ["## Knowledge graph"]
        used = 0
        for ent in matches:
            nb = self._backend.neighborhood(int(ent["id"]), hops=1, limit=16)
            chunk = format_neighborhood(nb, max_lines=max(4, max_lines // max(1, len(matches))))
            lines.extend(chunk)
            used += len(chunk)
            if used >= max_lines:
                break
        return "\n".join(lines) if len(lines) > 1 else ""

    def blend_descriptions(self, query: str, limit: int = 3) -> list[str]:
        """Entity descriptions to optionally blend into recall() results."""
        matches = self._backend.search_by_text(query, limit=limit)
        out: list[str] = []
        for m in matches:
            desc = (m.get("description") or "").strip()
            name = m.get("name") or "?"
            et = m.get("entity_type") or "other"
            if desc:
                out.append(f"[kg:{et}] {name}: {desc[:180]}")
            else:
                nb = self._backend.neighborhood(int(m["id"]), hops=1, limit=6)
                nnames = [n.get("name") for n in (nb.get("neighbors") or [])[:3] if n.get("name")]
                if nnames:
                    out.append(f"[kg:{et}] {name} linked to {', '.join(nnames)}")
                else:
                    out.append(f"[kg:{et}] {name}")
        return out

    def stats(self) -> dict[str, int]:
        try:
            return self._backend.stats()
        except Exception:
            return {"kg_entities": 0, "kg_relations": 0}
