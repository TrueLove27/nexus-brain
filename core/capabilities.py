"""Structured capability replies — no vague 'check out the tools'."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

CAPABILITY_PATTERNS = [
    r"what\s+can\s+you\s+do",
    r"what\s+do\s+you\s+do",
    r"what\s+are\s+you\s+capable",
    r"what\s+are\s+your\s+capabilities",
    r"^help$",
    r"^help me$",
    r"show\s+me\s+what\s+you\s+can",
    r"list\s+your\s+capabilities",
    r"what\s+tools\s+do\s+you\s+have",
]


def is_capabilities_question(goal: str) -> bool:
    g = goal.lower().strip().strip("!?., ")
    for pat in CAPABILITY_PATTERNS:
        if re.search(pat, g):
            return True
    return False


def build_capabilities_reply(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parent.parent
    agents_path = root / "config" / "agents.yaml"
    categories = [
        ("Files", "list, read, move, or organize folders on your PC"),
        ("Code", "write, edit, debug, and run scripts"),
        ("Apps", "install software via winget"),
        ("Music", "play songs, control volume"),
        ("Git", "clone repos, search code"),
        ("System", "run PowerShell commands, check your environment"),
        ("Databases", "query SQLite or PostgreSQL"),
    ]

    if agents_path.exists():
        with open(agents_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        agent_lines = []
        for spec in cfg.get("builtin_agents", {}).values():
            name = spec.get("name", "")
            role = spec.get("role", "")
            if name and role and name != "Orchestrator":
                agent_lines.append(f"  - {name}: {role[:80]}")
        extra = "\n".join(agent_lines[:6]) if agent_lines else ""

    lines = [
        "Here's what I can help with:",
        "",
    ]
    for cat, desc in categories:
        lines.append(f"  - {cat} - {desc}")
    lines.extend([
        "",
        "Just tell me what you need, for example:",
        '  "install VS Code"',
        '  "list files on my Desktop"',
        '  "run py main.py health"',
    ])
    return "\n".join(lines)
