# Nexus Brain

Autonomous multi-agent system that runs on your Windows machine using **free local Ollama** (llama3.2). No API keys, no subscriptions.

## What makes this different

This is not a chatbot that waits for commands. Nexus has:

- **Its own brain** — persistent SQLite memory with semantic recall (nomic-embed-text)
- **Self-training** — learns from every task, stores lessons and your preferences
- **Specialist agents** — Coder, FileAgent, ShellAgent, WorkflowAgent
- **Agent spawning** — creates new custom agents when none fit the job
- **Proactive mode** — watches an inbox and executes tasks without you poking it
- **Real tools** — reads/writes files, runs PowerShell, opens apps, creates workflows

## Quick start

```powershell
cd C:\Users\cheki\nexus-brain
.\scripts\install.ps1
.\scripts\start.ps1
```

Or manually:

```powershell
py -m pip install -r requirements.txt
ollama serve          # if not already running
py main.py health     # verify Ollama + llama3.2
py main.py            # interactive mode
```

## Usage

### Interactive mode
```
nexus> List all Python files on my Desktop
nexus> Create a PowerShell script that backs up my Documents folder
nexus> spawn DataAgent Handles CSV analysis and data cleaning
nexus> teach Always use type hints in Python code
```

### One-shot tasks
```powershell
py main.py run "Fix the bug in my-api/main.py where requests timeout"
```

### Proactive daemon (works while you do other things)
```powershell
py main.py daemon
```
Then drop task files into `data/inbox/` — any `.txt` file with a goal description.

### Portfolio growth session (July 13, 9 PM - midnight)
```powershell
py main.py portfolio          # drop next portfolio-growth-engine task into inbox
py main.py session 3          # 3-hour timed session on nexus-brain backlog
py main.py health             # shows portfolio_pending / portfolio_done counts
```

Integrated with `portfolio-growth-engine` — reads tasks from its backlog, drops them into inbox, and marks them done after success.

### Train Nexus for YOUR workflow
```powershell
py main.py teach "My projects live in C:\Users\cheki\projects"
py main.py teach "Use PowerShell, not bash"
py main.py teach "When coding, always add error handling"
```

Preferences are saved to `data/user_preferences.md` and injected into every agent's brain.

### Agent eval (model benchmark)
Run a small fixed set of synthetic goals through `NexusEngine` and compare completion rate (`status == done`), duration, and step counts across Ollama models:

```powershell
py scripts/eval_agents.py --help
py scripts/eval_agents.py                              # default model from config/brain.yaml
py scripts/eval_agents.py --models llama3.2,qwen2.5:7b
```

Reports are written to `data/evals/` (JSON + Markdown) and a JSON copy under `data/logs/`.

## Architecture

```
You → Nexus Orchestrator → Specialist Agents → Tools → Your System
         ↓                      ↓
      Brain Memory          Agent Factory (spawns new agents)
         ↓
      Learning Loop (trains from outcomes)
         ↓
      Multi-tier consolidation (episodes → facts / procedures)
```

## Multi-tier memory

Nexus uses a cognitive memory stack instead of a single flat bag:

| Tier | Table | What it stores |
|------|-------|----------------|
| **Episodic** | `memory_episodes` | Raw task / chat / tool / teach events |
| **Semantic** | `memory_facts` | Durable facts distilled from episodes (confidence, supersession) |
| **Procedural** | `memory_procedures` | How-to / when-to patterns (`trigger_text` + steps) |

**Dual-write:** `remember()` still writes the legacy `memories` table for compatibility and also records an episode.

**Consolidation:** `MemoryConsolidator` reads unconsolidated episodes, asks the LLM for JSON `{facts, procedures}`, writes them, and sets `consolidated_at`. It runs:

- After each task (`NexusEngine.maybe_consolidate`) when unconsolidated count ≥ `brain.memory_tiers.consolidate_every_n_episodes` (default 5)
- On idle proactive daemon ticks
- Manually: `engine.maybe_consolidate(force=True)` from a Python shell, or after enough tasks complete

`get_history_context(goal)` injects semantic facts, procedural patterns, and recent episodes alongside the existing task/learning sections. Health exposes counts under `memory_tiers`.

## Hybrid recall (Postgres + pgvector)

When Postgres has the `vector` extension, `PostgresMemory.recall()` uses **hybrid ranking** instead of scanning the last 200 JSONB rows:

| Signal | Weight | Source |
|--------|--------|--------|
| Vector similarity | 0.50 | HNSW on `embedding_vec vector(768)` (nomic-embed-text) |
| Keyword rank | 0.25 | `content_tsv` + GIN (`ts_rank`) |
| Recency | 0.15 | `created_at` decay |
| Access | 0.10 | `LN(1 + access_count)` |

If pgvector is missing or the query fails, recall falls back to the JSONB cosine path (same as SQLite). New memories write both JSONB + `embedding_vec` when dims match 768; dim mismatch stores NULL in the vector column.

```powershell
py scripts/backfill_embeddings.py --limit 500   # copy JSONB → embedding_vec
py main.py health                               # shows pgvector: true/false + memory_count
```

`brain.max_context_memories` in `config/brain.yaml` caps how many memories enter agent context.

## Validated learning

After every task (when `brain.learn_from_every_task` is true), Nexus runs a **validated learning loop** instead of blindly inserting a new row:

1. **Extract** — LLM writes one concise lesson from the goal, result, and steps
2. **Embed** — lesson is embedded (`nomic-embed-text` → 768-d) and stored as JSONB + optional `embedding_vec`
3. **Dedupe / merge** — active lessons with cosine (or text) similarity ≥ ~0.88 are merged: confidence bumps, text coalesced, `task_id` filled in
4. **Contradiction** — high-similarity lessons with the opposite outcome are LLM/heuristic-checked; the old row becomes `superseded` with `superseded_by` pointing at the new lesson
5. **Episodic dual-write** — also recorded via `record_episode` / `remember(..., category="learning")` for multi-tier consolidation
6. **Retrieval** — `get_learnings_for_goal` ranks active lessons with hybrid vector+keyword+confidence+use (pgvector) or embedding cosine (SQLite), and bumps `use_count` when injected into agent context

Optional feedback: `engine.learning.record_learning_outcome(learning_id, helped=True)` increments `helpful_count` and adjusts confidence.

```powershell
py main.py health   # shows learning.active / avg_confidence / superseded
```

Schema: migration `012_learning_quality.sql` (`confidence`, `use_count`, `helpful_count`, `embedding`, `embedding_vec`, `superseded_by`, `status`, `updated_at`). Thresholds live under `brain.learning` in `config/brain.yaml`.

## Knowledge graph

Nexus builds a **project/entity graph** from completed tasks and `teach()` preferences:

| Table | Role |
|-------|------|
| `kg_entities` | Nodes: `project`, `file`, `tool`, `concept`, `person`, `repo`, `other` (unique on type + normalized name) |
| `kg_relations` | Weighted edges: `USED_TOOL`, `FAILED_ON`, `LEARNED_FROM`, `PREFERS`, `TOUCHES`, `PART_OF`, … |

**Extraction:** After each task, `BrainLearning` asks the LLM for JSON entities + relations and upserts them (`brain/knowledge_graph.py`). `teach()` adds `PREFERS` edges from a `user` person node.

**Context:** `get_history_context(goal)` appends a **Knowledge graph** section — entities mentioned in the goal plus a 1-hop neighborhood (tools used, failures, related files/projects). `recall()` also blends short entity descriptions into results.

**Health:** `py main.py health` reports `kg_entities` and `kg_relations` counts.

Example questions this unlocks:

- “What tools failed last time we touched *nexus-brain*?”
- “Which files are related to the job queue work?”
- “What does the user prefer about committing / pushing?”
- “What projects use `pgvector` / `PostgresMemory`?”

Neighborhood API shape (also returned by `KnowledgeGraph.neighborhood`):

```python
{
  "entity": {"id": 3, "entity_type": "project", "name": "nexus-brain", "description": "...", ...},
  "relations": [
    {"id": 12, "from_id": 3, "to_id": 7, "relation_type": "USED_TOOL", "weight": 2.0, "evidence": "...", "task_id": 41}
  ],
  "neighbors": [
    {"id": 7, "entity_type": "tool", "name": "shell", "description": "...", ...}
  ],
  "matches": [...]  # when lookup was by free-text query
}
```

## Agents

| Agent | Does |
|-------|------|
| Orchestrator | Plans, delegates, spawns agents |
| Coder | Write/edit/debug code |
| FileAgent | File operations, open files |
| ShellAgent | PowerShell, processes, env |
| WorkflowAgent | Automations, scheduled tasks |
| Custom agents | Created on demand by Nexus |

## Config

- `config/brain.yaml` — LLM model, paths, proactive settings
- `config/agents.yaml` — Built-in agent definitions

## Requirements

- Windows 10/11
- [Ollama](https://ollama.com) with `llama3.2` and `nomic-embed-text` (you already have these)
- Python 3.10+ (`py` launcher)

## Limitations (honest)

- Runs on llama3.2 locally — capable but not as powerful as cloud models
- Complex multi-file refactors may need iteration
- For hardest tasks, pair with Cursor IDE (you're already using it)
- "Training" here means persistent memory + preference learning, not model fine-tuning
