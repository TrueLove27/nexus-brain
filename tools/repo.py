from __future__ import annotations

import subprocess
import time
from pathlib import Path

import yaml

from core.events import EventBus


def register_repo_tools(registry) -> None:
    ws = registry.workspace
    events = EventBus.get()
    root = Path(__file__).resolve().parent.parent

    with open(root / "config" / "features.yaml", encoding="utf-8") as f:
        features = yaml.safe_load(f)
    editor = features.get("live", {}).get("open_editor", "cursor")
    delay_ms = features.get("live", {}).get("typewriter_delay_ms", 12)

    def _open_editor(path: Path) -> None:
        if editor == "none":
            return
        cmd = editor if editor in ("cursor", "code") else "cursor"
        try:
            subprocess.Popen([cmd, str(path)], shell=True)
        except Exception:
            registry.run_powershell(f'Start-Process notepad "{path}"')

    def clone_repo(url: str, directory: str = "") -> str:
        dest = Path(directory) if directory else ws / Path(url.rstrip("/").split("/")[-1]).stem
        if dest.exists() and any(dest.iterdir()):
            events.emit("repo_cloned", {"path": str(dest), "status": "exists"})
            _open_editor(dest)
            return f"Repo already exists at {dest} — opened in editor"
        events.emit("repo_clone_start", {"url": url, "dest": str(dest)})
        output = registry.run_powershell(f'git clone "{url}" "{dest}"', timeout=300)
        events.emit("repo_cloned", {"path": str(dest), "url": url})
        _open_editor(dest)
        return f"Cloned to {dest}\n{output}"

    def write_file_live(path: str, content: str) -> str:
        p = Path(path) if Path(path).is_absolute() else ws / path
        p.parent.mkdir(parents=True, exist_ok=True)
        events.emit("code_write_start", {"path": str(p), "length": len(content)})
        _open_editor(p)

        typed = []
        with open(p, "w", encoding="utf-8") as f:
            for i, ch in enumerate(content):
                typed.append(ch)
                f.write(ch)
                f.flush()
                if i % 3 == 0:
                    events.emit("code_typing", {
                        "path": str(p),
                        "progress": round((i + 1) / len(content) * 100),
                        "snippet": "".join(typed[-80:]),
                    })
                time.sleep(delay_ms / 1000)

        events.emit("code_write_done", {"path": str(p)})
        return f"Live-wrote {len(content)} chars to {p} (visible on screen)"

    def run_command_live(command: str, cwd: str = "") -> str:
        work_dir = cwd or str(ws)
        events.emit("terminal_start", {"command": command, "cwd": work_dir})
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", command],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=work_dir,
        )
        lines = []
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            events.emit("terminal_output", {"text": line})
        proc.wait()
        output = "\n".join(lines)
        events.emit("terminal_done", {"command": command, "exit_code": proc.returncode})
        return output[:8000] or f"(exit code {proc.returncode})"

    def open_in_editor(path: str) -> str:
        p = Path(path) if Path(path).is_absolute() else ws / path
        if not p.exists():
            return f"Error: not found: {p}"
        _open_editor(p if p.is_dir() else p.parent)
        events.emit("editor_open", {"path": str(p)})
        return f"Opened {p} in {editor}"

    def git_status(directory: str = ".") -> str:
        p = Path(directory) if Path(directory).is_absolute() else ws / directory
        events.emit("terminal_start", {"command": "git status", "cwd": str(p)})
        output = registry.run_powershell("git status --short", cwd=str(p))
        for line in output.splitlines()[:30]:
            events.emit("terminal_output", {"text": line})
        events.emit("terminal_done", {"command": "git status", "exit_code": 0})
        return output[:4000] or "Not a git repository or no changes."

    registry.register("clone_repo", "Clone a git repository and open it in the editor",
                      {"url": "str", "directory": "str (optional)"}, clone_repo)
    registry.register("write_file_live", "Write code with live typing visible on screen",
                      {"path": "str", "content": "str"}, write_file_live)
    registry.register("run_command_live", "Run command with live terminal output on screen",
                      {"command": "str", "cwd": "str (optional)"}, run_command_live)
    registry.register("open_in_editor", "Open file or folder in Cursor/VS Code",
                      {"path": "str"}, open_in_editor)
    registry.register("git_status", "Show git status summary for a directory",
                      {"directory": "str (optional)"}, git_status)
