"""Validated learning loop — embed lessons, dedupe/merge, confidence, contradictions."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from brain.embeddings import cosine_similarity, embed_text

log = logging.getLogger("nexus.learning")

# Near-duplicate: merge / bump confidence instead of inserting
DUPLICATE_SIM = 0.88
# Opposite-outcome lesson that is still thematically close → contradiction
CONTRADICTION_SIM = 0.72

# Confidence bumps on corroboration / useful signal
CONF_DUP_BUMP = 0.08
CONF_NEW_SUCCESS = 0.55
CONF_NEW_FAILURE = 0.45
CONF_MAX = 0.98
CONF_MIN = 0.05


def _clamp_conf(v: float) -> float:
    return max(CONF_MIN, min(CONF_MAX, float(v)))


def _normalize_text(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s


def _token_set(s: str) -> set[str]:
    return {t for t in _normalize_text(s).split() if len(t) > 2}


def text_similarity(a: str, b: str) -> float:
    """Jaccard over significant tokens + substring bonus for near-matches."""
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        na, nb = _normalize_text(a), _normalize_text(b)
        if not na or not nb:
            return 0.0
        if na in nb or nb in na:
            return 0.9
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    jaccard = inter / union if union else 0.0
    na, nb = _normalize_text(a), _normalize_text(b)
    if na in nb or nb in na:
        jaccard = max(jaccard, 0.85)
    return jaccard


def _parse_json_blob(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


class BrainLearning:
    def __init__(
        self,
        memory,
        llm_chat_fn,
        *,
        embed_model: str | None = None,
        ollama_url: str | None = None,
        learning_cfg: dict | None = None,
        brain_cfg: dict | None = None,
    ):
        self.memory = memory
        self.llm_chat = llm_chat_fn
        self.embed_model = embed_model or getattr(memory, "embed_model", "nomic-embed-text")
        self.ollama_url = (ollama_url or getattr(memory, "ollama_url", "http://localhost:11434")).rstrip("/")
        cfg = learning_cfg or (brain_cfg or {}).get("learning") or {}
        self.duplicate_sim = float(cfg.get("duplicate_similarity", DUPLICATE_SIM))
        self.contradiction_sim = float(cfg.get("contradiction_similarity", CONTRADICTION_SIM))

    def _embed(self, text: str) -> list[float] | None:
        if hasattr(self.memory, "_embed"):
            try:
                return self.memory._embed(text)
            except Exception:
                pass
        return embed_text(text, self.embed_model, self.ollama_url)

    def learn_from_task(
        self,
        goal: str,
        result: str,
        steps: list[dict],
        success: bool,
        task_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Extract, embed, dedupe/merge or contradict-retract a lesson; link task_id."""
        outcome = "success" if success else "failure"
        step_summary = "\n".join(
            f"  - {s.get('action', '?')}: {str(s.get('result', ''))[:200]}"
            for s in steps[-10:]
        )

        self._record_episode(goal, result, step_summary, outcome, task_id, len(steps))

        lesson = self._extract_lesson(goal, result, step_summary, success)
        if not lesson or len(lesson) < 10:
            if success:
                lesson = f"Completed: {result[:300]}"
            else:
                lesson = f"Failed attempt: {(result or goal)[:300]}"
            if len(lesson) < 10:
                lesson = None

        action: dict[str, Any] | None = None
        if lesson:
            embedding = self._embed(f"{goal}\n{lesson}")
            action = self._validate_and_store(
                goal=goal,
                outcome=outcome,
                lesson=lesson,
                embedding=embedding,
                task_id=task_id,
                success=success,
            )

        # Knowledge graph: entities + weighted relations from goal/result/steps
        try:
            from brain.knowledge_graph import KnowledgeGraph

            KnowledgeGraph(self.memory, self.llm_chat).extract_from_text(
                goal,
                result,
                steps,
                success=success,
                task_id=task_id,
            )
        except Exception as exc:
            log.debug("knowledge graph extract skipped: %s", exc)

        return action

    def record_learning_outcome(self, learning_id: int, helped: bool) -> bool:
        """Optional feedback loop: bump helpful_count / confidence when a lesson helped."""
        if hasattr(self.memory, "record_learning_outcome"):
            try:
                return bool(self.memory.record_learning_outcome(int(learning_id), bool(helped)))
            except Exception as exc:
                log.warning("record_learning_outcome failed: %s", exc)
                return False
        return False

    # ------------------------------------------------------------------ internals

    def _record_episode(
        self,
        goal: str,
        result: str,
        step_summary: str,
        outcome: str,
        task_id: int | None,
        step_count: int,
    ) -> None:
        if not hasattr(self.memory, "record_episode"):
            return
        try:
            episode = (
                f"Task {outcome}: {goal[:300]}\n"
                f"Result: {result[:400]}\n"
                f"Steps:\n{step_summary[:800]}"
            )
            kwargs: dict[str, Any] = {
                "metadata": {
                    "goal": goal[:200],
                    "outcome": outcome,
                    "step_count": step_count,
                    "source": "learning",
                }
            }
            # Prefer task_id kw when supported
            try:
                self.memory.record_episode(episode, "task", task_id=task_id, **kwargs)
            except TypeError:
                self.memory.record_episode(episode, "task", **kwargs)
        except Exception as exc:
            log.debug("episode record skipped: %s", exc)

    def _extract_lesson(self, goal: str, result: str, step_summary: str, success: bool) -> str | None:
        prompt = (
            f"A task was {'completed successfully' if success else 'attempted but had issues'}.\n\n"
            f"Goal: {goal}\n"
            f"Result: {result}\n"
            f"Steps taken:\n{step_summary}\n\n"
            "Extract ONE concise lesson (max 2 sentences) that will help with similar future tasks. "
            "Focus on: what worked, what didn't, user preferences implied, technical gotchas. "
            "Reply with ONLY the lesson text — no preamble."
        )
        try:
            raw = self.llm_chat([{"role": "user", "content": prompt}])
            if not raw:
                return None
            lesson = raw.strip()
            lesson = re.sub(r"^(lesson|takeaway)\s*:\s*", "", lesson, flags=re.I)
            lesson = lesson.strip().strip('"').strip("'")
            return lesson if len(lesson) > 10 else None
        except Exception as exc:
            log.warning("lesson extraction failed: %s", exc)
            return None

    def _validate_and_store(
        self,
        *,
        goal: str,
        outcome: str,
        lesson: str,
        embedding: list[float] | None,
        task_id: int | None,
        success: bool,
    ) -> dict[str, Any]:
        """Dedupe/merge, contradiction retract, or insert — then episodic remember."""
        similar = self._find_similar(lesson, embedding, limit=12)
        duplicate = None
        contradiction = None

        for hit in similar:
            sim = float(hit.get("similarity") or 0.0)
            hit_outcome = (hit.get("outcome") or "").lower()
            same_outcome = hit_outcome == outcome or (
                hit_outcome in ("success", "done") and outcome == "success"
            ) or (
                hit_outcome in ("failure", "failed", "incomplete") and outcome == "failure"
            )
            opposite = (
                (hit_outcome in ("success", "done") and outcome == "failure")
                or (hit_outcome in ("failure", "failed", "incomplete") and outcome == "success")
            )

            if sim >= self.duplicate_sim and same_outcome and duplicate is None:
                duplicate = hit
            elif sim >= self.contradiction_sim and opposite and contradiction is None:
                contradiction = hit
            elif sim >= self.duplicate_sim and opposite and contradiction is None:
                contradiction = hit

        if contradiction and duplicate is None:
            if not self._confirm_contradiction(lesson, contradiction, outcome):
                if float(contradiction.get("similarity") or 0) >= self.duplicate_sim:
                    duplicate = contradiction
                    contradiction = None
                else:
                    contradiction = None

        if duplicate is not None:
            action = self._merge_duplicate(duplicate, lesson, embedding, task_id, outcome)
        elif contradiction is not None:
            action = self._supersede_contradiction(
                contradiction, goal, outcome, lesson, embedding, task_id, success
            )
        else:
            action = self._insert_new(goal, outcome, lesson, embedding, task_id, success)

        # Dual-write into recall bag only for net-new or superseding lessons
        if action.get("action") in ("inserted", "superseded", "superseded_fallback"):
            try:
                lid = action.get("id")
                self.memory.remember(
                    f"[{outcome}] {lesson}",
                    category="learning",
                    metadata={
                        "goal": goal[:300],
                        "outcome": outcome,
                        "task_id": task_id,
                        "learning_id": lid,
                        "action": action.get("action"),
                        "source": "learning",
                    },
                )
            except Exception as exc:
                log.debug("remember learning skipped: %s", exc)

        return action

    def _find_similar(
        self,
        lesson: str,
        embedding: list[float] | None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        if hasattr(self.memory, "find_similar_learnings"):
            try:
                hits = self.memory.find_similar_learnings(
                    lesson, embedding=embedding, limit=limit, min_score=0.45
                )
                if hits:
                    return hits
            except Exception as exc:
                log.warning("find_similar_learnings failed: %s", exc)

        rows = self._list_active_learnings(limit=80)
        scored: list[dict[str, Any]] = []
        for row in rows:
            sim = 0.0
            if embedding and row.get("embedding"):
                emb = row["embedding"]
                if isinstance(emb, str):
                    try:
                        emb = json.loads(emb)
                    except Exception:
                        emb = None
                if isinstance(emb, list):
                    sim = cosine_similarity(embedding, emb)
            text_sim = text_similarity(lesson, row.get("lesson") or "")
            sim = max(sim, text_sim)
            if sim >= 0.45:
                scored.append({**row, "similarity": sim})
        scored.sort(key=lambda r: r["similarity"], reverse=True)
        return scored[:limit]

    def _list_active_learnings(self, limit: int = 80) -> list[dict[str, Any]]:
        if hasattr(self.memory, "list_active_learnings"):
            try:
                return self.memory.list_active_learnings(limit=limit)
            except Exception:
                pass
        return []

    def _confirm_contradiction(
        self,
        new_lesson: str,
        old: dict[str, Any],
        new_outcome: str,
    ) -> bool:
        """Ask LLM whether the new lesson retracts the old one. Heuristic fallback."""
        old_lesson = old.get("lesson") or ""
        old_outcome = old.get("outcome") or ""
        if text_similarity(new_lesson, old_lesson) >= 0.75:
            return True

        prompt = (
            "Do these two lessons CONTRADICT each other (one retracts/invalidates the other)?\n"
            f"Old [{old_outcome}]: {old_lesson}\n"
            f"New [{new_outcome}]: {new_lesson}\n\n"
            'Reply ONLY JSON: {"contradicts": true|false, "reason": "short"}'
        )
        try:
            raw = self.llm_chat([{"role": "user", "content": prompt}])
            data = _parse_json_blob(raw or "")
            if data and "contradicts" in data:
                return bool(data["contradicts"])
        except Exception as exc:
            log.debug("contradiction LLM check failed: %s", exc)
        return True

    def _merge_duplicate(
        self,
        existing: dict[str, Any],
        lesson: str,
        embedding: list[float] | None,
        task_id: int | None,
        outcome: str,
    ) -> dict[str, Any]:
        lid = int(existing["id"])
        old_conf = float(existing.get("confidence") or 0.5)
        new_conf = _clamp_conf(old_conf + CONF_DUP_BUMP)
        merged_lesson = self._merge_lesson_text(existing.get("lesson") or "", lesson)
        if hasattr(self.memory, "merge_learning"):
            self.memory.merge_learning(
                lid,
                lesson=merged_lesson,
                confidence=new_conf,
                embedding=embedding,
                task_id=task_id,
                outcome=outcome,
            )
        else:
            if hasattr(self.memory, "record_learning"):
                try:
                    self.memory.record_learning(
                        existing.get("task_goal") or "",
                        outcome,
                        merged_lesson,
                        task_id=task_id,
                    )
                except TypeError:
                    self.memory.record_learning(
                        existing.get("task_goal") or "", outcome, merged_lesson
                    )
        return {
            "action": "merged",
            "id": lid,
            "confidence": new_conf,
            "similarity": existing.get("similarity"),
            "lesson": merged_lesson,
        }

    def _merge_lesson_text(self, old: str, new: str) -> str:
        if not old:
            return new
        if not new or text_similarity(old, new) >= 0.92:
            return old
        if len(old) + len(new) < 400 and new.lower() not in old.lower():
            return f"{old.rstrip('. ')}. Also: {new}"
        return new if len(new) > len(old) * 1.15 else old

    def _supersede_contradiction(
        self,
        old: dict[str, Any],
        goal: str,
        outcome: str,
        lesson: str,
        embedding: list[float] | None,
        task_id: int | None,
        success: bool,
    ) -> dict[str, Any]:
        old_id = int(old["id"])
        conf = _clamp_conf(CONF_NEW_SUCCESS if success else CONF_NEW_FAILURE + 0.05)
        if hasattr(self.memory, "insert_learning"):
            new_id = self.memory.insert_learning(
                task_goal=goal,
                outcome=outcome,
                lesson=lesson,
                task_id=task_id,
                embedding=embedding,
                confidence=conf,
                status="active",
            )
            if hasattr(self.memory, "supersede_learning"):
                self.memory.supersede_learning(old_id, new_id)
            return {
                "action": "superseded",
                "id": new_id,
                "superseded_id": old_id,
                "confidence": conf,
                "similarity": old.get("similarity"),
                "lesson": lesson,
            }
        self._insert_via_record(goal, outcome, lesson, task_id, embedding, conf)
        return {"action": "superseded_fallback", "id": None, "superseded_id": old_id, "lesson": lesson}

    def _insert_new(
        self,
        goal: str,
        outcome: str,
        lesson: str,
        embedding: list[float] | None,
        task_id: int | None,
        success: bool,
    ) -> dict[str, Any]:
        conf = _clamp_conf(CONF_NEW_SUCCESS if success else CONF_NEW_FAILURE)
        if hasattr(self.memory, "insert_learning"):
            new_id = self.memory.insert_learning(
                task_goal=goal,
                outcome=outcome,
                lesson=lesson,
                task_id=task_id,
                embedding=embedding,
                confidence=conf,
                status="active",
            )
            return {"action": "inserted", "id": new_id, "confidence": conf, "lesson": lesson}
        self._insert_via_record(goal, outcome, lesson, task_id, embedding, conf)
        return {"action": "inserted", "id": None, "confidence": conf, "lesson": lesson}

    def _insert_via_record(
        self,
        goal: str,
        outcome: str,
        lesson: str,
        task_id: int | None,
        embedding: list[float] | None,
        confidence: float,
    ) -> None:
        if not hasattr(self.memory, "record_learning"):
            return
        try:
            self.memory.record_learning(
                goal,
                outcome,
                lesson,
                task_id=task_id,
                embedding=embedding,
                confidence=confidence,
            )
        except TypeError:
            self.memory.record_learning(goal, outcome, lesson)
