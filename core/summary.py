"""Human-readable summaries when tasks don't complete."""

from __future__ import annotations

ACTION_LABELS = {
    "read_file": "Read",
    "write_file": "Wrote",
    "edit_file": "Edited",
    "list_dir": "Listed",
    "run_command": "Ran command",
    "search_files": "Searched files",
    "search_code": "Searched code",
    "install_app": "Tried to install",
    "clone_repo": "Cloned",
    "check_env": "Checked environment",
    "finish": "Finished",
}


def summarize_incomplete(steps: list[dict], goal: str) -> str:
    if not steps:
        return (
            "I worked on this but couldn't finish in one pass. "
            "Try breaking it into a smaller, specific step."
        )

    bullets: list[str] = []
    last_error = ""
    for s in steps:
        action = s.get("action", "")
        if action == "finish":
            continue
        args = s.get("args") or {}
        result = (s.get("result") or "").strip()
        label = ACTION_LABELS.get(action, action.replace("_", " ").title())
        detail = ""
        if "path" in args:
            detail = str(args["path"])
        elif "command" in args:
            detail = str(args["command"])[:60]
        elif args:
            detail = str(list(args.values())[0])[:60]
        line = f"  - {label}"
        if detail:
            line += f" {detail}"
        if result and result.lower().startswith("error"):
            last_error = result[:200]
            line += f" ({result[:80]})"
        bullets.append(line)

    unique = []
    seen = set()
    for b in bullets[-12:]:
        if b not in seen:
            seen.add(b)
            unique.append(b)

    parts = [
        "I worked on this but couldn't fully finish in one pass. Here's what I did:",
        *unique[:8],
    ]
    if last_error:
        parts.append(f"\nI got stuck because: {last_error}")
    elif steps:
        last = steps[-1]
        act = last.get("action", "unknown")
        parts.append(f"\nI got stuck while: {act.replace('_', ' ')}")

    if any(w in goal.lower() for w in ("ui", "interface", "nexus_app", "desktop app")):
        parts.append("\nRestart the app after UI file changes to see them.")

    parts.append("\nTell me a more specific next step and I'll try again.")
    return "\n".join(parts)
