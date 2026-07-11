from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable


class ToolRegistry:
    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        self._tools: dict[str, dict[str, Any]] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        from .filesystem import register_filesystem_tools
        from .shell import register_shell_tools
        from .code import register_code_tools
        from .windows import register_windows_tools
        from .media import register_media_tools
        from .installer import register_installer_tools
        from .repo import register_repo_tools
        from .database import register_database_tools

        register_filesystem_tools(self)
        register_shell_tools(self)
        register_code_tools(self)
        register_windows_tools(self)
        register_media_tools(self)
        register_installer_tools(self)
        register_repo_tools(self)
        register_database_tools(self)

    def register(self, name: str, description: str, parameters: dict,
                 handler: Callable[..., str]) -> None:
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def execute(self, name: str, args: dict) -> str:
        if name not in self._tools:
            return f"Error: unknown tool '{name}'. Available: {', '.join(self._tools.keys())}"
        try:
            from core.events import EventBus
            EventBus.get().emit("tool_start", {"tool": name, "args": args})
            result = self._tools[name]["handler"](**args)
            EventBus.get().emit("tool_done", {"tool": name, "result": result[:500]})
            return result
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"
        except Exception as e:
            return f"Error executing {name}: {e}"

    def descriptions(self) -> str:
        lines = []
        for t in self._tools.values():
            params = ", ".join(f"{k}: {v}" for k, v in t["parameters"].items())
            lines.append(f"- {t['name']}({params}): {t['description']}")
        return "\n".join(lines)

    def run_powershell(self, command: str, cwd: str | None = None, timeout: int = 120) -> str:
        work_dir = cwd or str(self.workspace)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, cwd=work_dir, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return output or f"(exit code {result.returncode})"
