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

### Train Nexus for YOUR workflow
```powershell
py main.py teach "My projects live in C:\Users\cheki\projects"
py main.py teach "Use PowerShell, not bash"
py main.py teach "When coding, always add error handling"
```

Preferences are saved to `data/user_preferences.md` and injected into every agent's brain.

## Architecture

```
You → Nexus Orchestrator → Specialist Agents → Tools → Your System
         ↓                      ↓
      Brain Memory          Agent Factory (spawns new agents)
         ↓
      Learning Loop (trains from outcomes)
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
