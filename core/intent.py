"""Detect whether the user is chatting or asking for real work on their machine."""

from __future__ import annotations

import re

# If any of these appear, the user wants action — not small talk.
ACTION_SIGNALS = [
    "install", "download", "create", "write", "edit", "read", "open", "run",
    "execute", "list", "clone", "fix", "debug", "delete", "remove", "move",
    "copy", "search", "find", "setup", "set up", "configure", "automate",
    "build", "deploy", "query", "backup", "restore", "rename", "organize",
    "script", "code", "file", "folder", "directory", "command", "terminal",
    "volume", "play", "pause", "music", "repo", "repository", "database",
    "sql", "workflow", "schedule", "spawn", "teach", "remember",
]

CHAT_PATTERNS = [
    r"^(hi|hello|hey|yo|sup|hiya|howdy)\b",
    r"^good\s+(morning|afternoon|evening|night)\b",
    r"^(thanks?|thank\s+you|thx|ty)\b",
    r"^(bye|goodbye|see\s+ya|later|cya)\b",
    r"^how\s+are\s+you",
    r"^what'?s\s+up\b",
    r"^(what\s+can\s+you\s+do|who\s+are\s+you|help|what\s+do\s+you\s+do)\b",
    r"^(ok|okay|cool|nice|got\s+it|sure|alright|k)\.?$",
    r"^i\s+see\.?$",
    r"^lol\.?$",
    r"^haha\.?$",
]


def is_conversational(goal: str) -> bool:
    """True for greetings, thanks, meta questions — things that need no tools."""
    g = goal.lower().strip().strip("!?.,…")

    if not g:
        return True

    if any(signal in g for signal in ACTION_SIGNALS):
        return False

    for pat in CHAT_PATTERNS:
        if re.search(pat, g):
            return True

    return False


def is_simple_greeting(goal: str) -> bool:
    from core.capabilities import is_capabilities_question
    if is_capabilities_question(goal):
        return False
    g = goal.lower().strip().strip("!?., ")
    patterns = [
        r"^(hi|hello|hey|yo|sup|hiya|howdy)\b",
        r"^good\s+(morning|afternoon|evening|night)\b",
        r"^how\s+are\s+you",
        r"^what'?s\s+up\b",
    ]
    return any(re.search(p, g) for p in patterns)


def build_greeting_reply() -> str:
    return (
        "Hey! I'm doing well - I'm Nexus, your local agent on this PC.\n\n"
        "I can help with files, writing code, installing apps, running commands, "
        "and automating tasks. Try something like \"list files on my Desktop\" "
        "or \"what can you do?\""
    )
