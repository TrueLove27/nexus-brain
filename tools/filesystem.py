from __future__ import annotations

from pathlib import Path

from core.events import EventBus
from .registry import ToolRegistry


def register_filesystem_tools(registry: ToolRegistry) -> None:
    ws = registry.workspace
    events = EventBus.get()

    def _resolve(path: str) -> Path | str:
        """Resolve path; return error string if sandbox denies it."""
        policy = getattr(registry, "policy", None)
        if policy is not None and policy.enabled:
            denied = policy.check_path(path, ws)
            if denied:
                return f"Error: {denied}"
            return policy.resolve_path(path, ws)
        return Path(path) if Path(path).is_absolute() else ws / path

    def read_file(path: str, max_lines: int = 500) -> str:
        p = _resolve(path)
        if isinstance(p, str):
            return p
        if not p.exists():
            return f"Error: file not found: {p}"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"
        return "\n".join(lines)

    def write_file(path: str, content: str) -> str:
        p = _resolve(path)
        if isinstance(p, str):
            return p
        p.parent.mkdir(parents=True, exist_ok=True)
        events.emit("code_write_start", {"path": str(p), "length": len(content)})
        snippet = content[-500:] if len(content) > 500 else content
        events.emit("code_typing", {"path": str(p), "progress": 100, "snippet": snippet})
        p.write_text(content, encoding="utf-8")
        events.emit("code_write_done", {"path": str(p)})
        return f"Wrote {len(content)} chars to {p}"

    def edit_file(path: str, old_text: str, new_text: str) -> str:
        p = _resolve(path)
        if isinstance(p, str):
            return p
        if not p.exists():
            return f"Error: file not found: {p}"
        content = p.read_text(encoding="utf-8", errors="replace")
        if old_text not in content:
            return f"Error: old_text not found in {p}"
        events.emit("code_edit", {"path": str(p), "old_text": old_text[:400], "new_text": new_text[:400]})
        p.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        events.emit("code_write_done", {"path": str(p)})
        return f"Edited {p}"

    def list_dir(path: str = ".", max_entries: int = 100) -> str:
        p = _resolve(path)
        if isinstance(p, str):
            return p
        if not p.exists():
            return f"Error: directory not found: {p}"
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        lines = []
        for e in entries[:max_entries]:
            tag = "DIR " if e.is_dir() else "FILE"
            lines.append(f"{tag} {e.name}")
        if len(entries) > max_entries:
            lines.append(f"... and {len(entries) - max_entries} more")
        return "\n".join(lines)

    def search_files(pattern: str, directory: str = ".", max_results: int = 30) -> str:
        p = _resolve(directory)
        if isinstance(p, str):
            return p
        matches = []
        for f in p.rglob(pattern):
            if f.is_file():
                matches.append(str(f.relative_to(ws) if f.is_relative_to(ws) else f))
            if len(matches) >= max_results:
                break
        return "\n".join(matches) if matches else f"No files matching '{pattern}' in {p}"

    registry.register("read_file", "Read contents of a file",
                      {"path": "str", "max_lines": "int (optional)"}, read_file)
    registry.register("write_file", "Create or overwrite a file",
                      {"path": "str", "content": "str"}, write_file)
    registry.register("edit_file", "Replace text in a file",
                      {"path": "str", "old_text": "str", "new_text": "str"}, edit_file)
    registry.register("list_dir", "List directory contents",
                      {"path": "str (optional)"}, list_dir)
    registry.register("search_files", "Search for files by glob pattern",
                      {"pattern": "str", "directory": "str (optional)"}, search_files)
