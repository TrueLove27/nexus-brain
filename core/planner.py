"""Fast path for common goals — bypasses LLM when intent is obvious."""

from __future__ import annotations

import re

from tools.registry import ToolRegistry


def try_direct(goal: str, tools: ToolRegistry) -> dict | None:
    """Execute simple goals without LLM. Returns result dict or None."""
    g = goal.lower().strip()

    # Volume
    if any(w in g for w in ["volume up", "louder", "increase volume", "turn it up"]):
        step = 10
        m = re.search(r"(\d+)", g)
        if m:
            step = int(m.group(1))
        result = tools.execute("volume_up", {"step": step})
        return {"status": "done", "result": result, "steps": [{"action": "volume_up"}], "direct": True}

    if any(w in g for w in ["volume down", "quieter", "decrease volume", "turn it down", "lower volume"]):
        step = 10
        m = re.search(r"(\d+)", g)
        if m:
            step = int(m.group(1))
        result = tools.execute("volume_down", {"step": step})
        return {"status": "done", "result": result, "steps": [{"action": "volume_down"}], "direct": True}

    m = re.search(r"(?:set volume|volume)\s+(?:to\s+)?(\d+)", g)
    if m:
        result = tools.execute("set_volume", {"percent": int(m.group(1))})
        return {"status": "done", "result": result, "steps": [{"action": "set_volume"}], "direct": True}

    # Play music
    m = re.search(r"(?:play|put on|start)\s+(?:song\s+)?(?:music\s+)?(.+)", goal, re.I)
    if m and any(w in g for w in ["play", "put on", "start"]):
        query = m.group(1).strip().strip("'\"")
        app = "youtube" if "youtube" in g else "spotify"
        result = tools.execute("play_music", {"query": query, "app": app})
        return {"status": "done", "result": result, "steps": [{"action": "play_music"}], "direct": True}

    # Install app
    m = re.search(r"(?:install|download|get)\s+(.+?)(?:\s+app)?$", goal, re.I)
    if m and any(w in g for w in ["install", "download", "get"]):
        app = m.group(1).strip()
        result = tools.execute("install_app", {"app_name": app})
        return {"status": "done", "result": result, "steps": [{"action": "install_app"}], "direct": True}

    # Clone repo
    m = re.search(r"(?:clone|checkout)\s+(?:repo(?:sitory)?\s+)?(https?://\S+|git@\S+)", goal, re.I)
    if m and any(w in g for w in ["clone", "checkout"]):
        result = tools.execute("clone_repo", {"url": m.group(1)})
        return {"status": "done", "result": result, "steps": [{"action": "clone_repo"}], "direct": True}

    # Now playing
    if "now playing" in g or "what song" in g or "what's playing" in g:
        result = tools.execute("now_playing", {})
        return {"status": "done", "result": result, "steps": [{"action": "now_playing"}], "direct": True}

    # List directory
    m = re.search(r"list(?:ing)?\s+(?:the\s+)?(?:top\s+)?(\d+)?\s*(?:files?|folders?|items?|directories?)?\s*(?:in|of|from|under)\s+(.+)", g)
    if m or "list" in g and ("folder" in g or "directory" in g or "file" in g):
        path = _extract_path(goal) or "."
        result = tools.execute("list_dir", {"path": path})
        return {"status": "done", "result": result, "steps": [{"action": "list_dir", "path": path}], "direct": True}

    # Read file
    m = re.search(r"(?:read|open|show|display|cat)\s+(?:the\s+)?(?:file\s+)?(.+\.\w+)", g, re.I)
    if m:
        path = m.group(1).strip().strip("'\"")
        result = tools.execute("read_file", {"path": path})
        return {"status": "done", "result": result, "steps": [{"action": "read_file", "path": path}], "direct": True}

    # Run command
    m = re.search(r"(?:run|execute)\s+(?:command\s+)?[`'\"](.+)[`'\"]", goal, re.I)
    if m:
        cmd = m.group(1)
        result = tools.execute("run_command", {"command": cmd})
        return {"status": "done", "result": result, "steps": [{"action": "run_command"}], "direct": True}

    # Search files
    m = re.search(r"search\s+(?:for\s+)?(?:files?\s+)?(?:matching\s+)?[`'\"](.+)[`'\"]", g)
    if m:
        result = tools.execute("search_files", {"pattern": m.group(1)})
        return {"status": "done", "result": result, "steps": [{"action": "search_files"}], "direct": True}

    # Search code
    m = re.search(r"search\s+(?:for\s+)?[`'\"](.+)[`'\"]\s+in\s+(?:the\s+)?code", g)
    if m:
        result = tools.execute("search_code", {"query": m.group(1)})
        return {"status": "done", "result": result, "steps": [{"action": "search_code"}], "direct": True}

    # Health check
    if "health" in g and any(w in g for w in ["nexus", "system", "check", "status", "ollama"]):
        result = tools.execute("run_command", {"command": "py main.py health"})
        return {"status": "done", "result": result, "steps": [{"action": "run_command"}], "direct": True}

    # Git status
    if "git status" in g or ("git" in g and "status" in g):
        result = tools.execute("git_status", {})
        return {"status": "done", "result": result, "steps": [{"action": "git_status"}], "direct": True}

    # List python files in project
    if "python" in g and "file" in g and ("list" in g or "show" in g):
        result = tools.execute("search_files", {"pattern": "*.py", "directory": "."})
        return {"status": "done", "result": result, "steps": [{"action": "search_files"}], "direct": True}

    # Teach / remember preference
    m = re.search(r"^(?:teach|remember|prefer)\s+(.+)", goal, re.I)
    if m:
        return {"status": "teach", "preference": m.group(1).strip(), "direct": True}

    return None


def _extract_path(goal: str) -> str | None:
    patterns = [
        r"[Cc]:\\[^\s\"']+",
        r"[Cc]:/[^\s\"']+",
        r"~/[^\s\"']+",
        r"\./[^\s\"']+",
    ]
    for pat in patterns:
        m = re.search(pat, goal)
        if m:
            return m.group(0)
    m = re.search(r"(?:in|of|from|under)\s+([A-Za-z]:\\[^\s,.]+)", goal, re.I)
    if m:
        return m.group(1)
    return None
