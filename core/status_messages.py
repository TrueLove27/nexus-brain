"""Turn internal agent events into human-readable status text."""

from __future__ import annotations

from core.summary import summarize_incomplete

IDLE_PHRASES = ["Thinking", "Pondering", "Considering", "One moment"]

TOOL_STATUS = {
    "list_dir": "Looking through a folder",
    "read_file": "Reading a file",
    "write_file": "Writing a file",
    "edit_file": "Editing a file",
    "run_command": "Running a command",
    "run_command_live": "Running a command",
    "open_file": "Opening a file",
    "search_files": "Searching for files",
    "search_code": "Searching the codebase",
    "check_env": "Checking your system",
    "install_app": "Installing an app",
    "clone_repo": "Cloning a repository",
    "play_music": "Starting music",
    "volume_up": "Turning volume up",
    "volume_down": "Turning volume down",
    "set_volume": "Adjusting volume",
    "query_database": "Querying the database",
    "now_playing": "Checking what's playing",
    "create_workflow": "Setting up a workflow",
    "open_in_editor": "Opening in your editor",
    "write_file_live": "Writing code",
}

SKIP_EVENTS = {
    "heartbeat", "task_start", "task_done", "task_error", "tool_done",
    "music_daemon_start", "wallpaper_updated", "database_result",
    "database_list", "code_write_done", "terminal_done", "install_done",
    "repo_cloned", "editor_open", "volume_change",
}


def event_to_status(event: dict) -> str | None:
    """Map an internal event to a short human status line, or None to hide it."""
    etype = event.get("type", "")
    if etype in SKIP_EVENTS:
        return None

    if etype == "agent_action":
        thought = (event.get("thought") or "").strip()
        if thought and len(thought) > 8:
            return thought[0].upper() + thought[1:] if thought else thought
        action = event.get("action", "")
        return TOOL_STATUS.get(action, f"Working on {action.replace('_', ' ')}")

    if etype == "tool_start":
        tool = event.get("tool", "")
        return TOOL_STATUS.get(tool, f"Using {tool.replace('_', ' ')}")

    if etype == "install_start":
        return f"Installing {event.get('app', 'an app')}..."

    if etype == "repo_clone_start":
        return "Cloning repository..."

    if etype == "code_write_start":
        path = event.get("path", "")
        return f"Writing code{f' in {path}' if path else ''}..."

    if etype == "code_typing":
        path = event.get("path", "")
        return f"Typing code{f' in {path}' if path else ''}..."

    if etype == "code_edit":
        path = event.get("path", "")
        return f"Editing {path}" if path else "Editing a file..."

    if etype == "terminal_start":
        cmd = (event.get("command") or "")[:50]
        return f"Running: {cmd}..." if cmd else "Running a terminal command..."

    if etype == "terminal_output":
        line = (event.get("text") or "").strip()
        if line:
            return line[:120]
        return None

    if etype == "now_playing":
        title = event.get("title")
        if title:
            return f"Now playing: {event.get('artist', '')} — {title}"
        return None

    return None


def event_to_workspace(event: dict) -> tuple[str, str] | None:
    """Route event to Live Workspace section: (now|code|terminal, content)."""
    etype = event.get("type", "")

    if etype in ("agent_action", "tool_start", "install_start", "repo_clone_start"):
        status = event_to_status(event)
        return ("now", status) if status else None

    if etype == "code_write_start":
        path = event.get("path", "")
        return ("code", f"Writing {path}" if path else "Writing file...")
    if etype == "code_typing":
        return ("code", event.get("snippet", ""))
    if etype == "code_edit":
        path = event.get("path", "")
        return ("code", f"Editing {path}")
    if etype == "code_write_done":
        path = event.get("path", "")
        return ("now", f"Finished writing {path}" if path else "Finished writing")

    if etype == "terminal_start":
        cmd = event.get("command", "")
        return ("terminal", f"> {cmd}")
    if etype == "terminal_output":
        line = (event.get("text") or "").strip()
        return ("terminal", line) if line else None

    return None


def format_failure(status: str, result: str, steps: list[dict] | None = None, goal: str = "") -> str:
    """Turn a failed/incomplete task into a normal sentence."""
    result = (result or "").strip()
    if status == "incomplete":
        if result and "max iterations" in result.lower():
            if steps:
                return summarize_incomplete(steps, goal)
            return (
                "I wasn't able to finish that in one pass. "
                "Try a more specific step, or tell me exactly which file to change."
            )
        if result:
            return f"I wasn't able to finish that. {result}"
        if steps:
            return summarize_incomplete(steps, goal)
        return "I wasn't able to finish that — it may need more steps. Tell me how you'd like to approach it."
    if status == "failed":
        if result:
            return f"I ran into a problem: {result}"
        return "I ran into a problem and couldn't get it done."
    if status == "error":
        if result:
            return f"I couldn't do that — {result}"
        return "I couldn't do that — something unexpected went wrong."
    return result or "Something didn't work out."


def explain_exception(exc: Exception) -> str:
    msg = str(exc).strip()
    if "ollama" in msg.lower() or "connection" in msg.lower():
        return "I can't reach Ollama right now. Make sure it's running with `ollama serve`."
    if "postgres" in msg.lower():
        return "I'm having trouble connecting to the database."
    return msg if msg else "something unexpected happened on my end."
