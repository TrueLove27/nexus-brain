from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from core.subprocess_env import SubprocessEnvPolicy, get_env_policy, scrubbed_environ
from core.tool_policy import ToolPolicy
from core.tool_subprocess import is_job_cancelled, run_tracked


class ToolRegistry:
    def __init__(
        self,
        workspace: str,
        policy: ToolPolicy | None = None,
        env_policy: SubprocessEnvPolicy | None = None,
    ):
        self.workspace = Path(workspace)
        self.policy = policy
        self.env_policy = env_policy
        self._tools: dict[str, dict[str, Any]] = {}
        self._register_builtins()

    def child_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Environment for tool subprocesses — host secrets scrubbed out."""
        return scrubbed_environ(extra=extra, policy=self.env_policy or get_env_policy())

    def _register_builtins(self) -> None:
        from .filesystem import register_filesystem_tools
        from .shell import register_shell_tools
        from .code import register_code_tools
        from .windows import register_windows_tools
        from .media import register_media_tools
        from .installer import register_installer_tools
        from .repo import register_repo_tools
        from .database import register_database_tools
        from .github import register_github_tools

        register_filesystem_tools(self)
        register_shell_tools(self)
        register_code_tools(self)
        register_windows_tools(self)
        register_media_tools(self)
        register_installer_tools(self)
        register_repo_tools(self)
        register_database_tools(self)
        register_github_tools(self)

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
            if is_job_cancelled():
                return "Error: lease reclaimed — tool execution cancelled"
            if self.policy is not None:
                denied = self.policy.check_tool(name, args or {}, self.workspace)
                if denied:
                    from core.events import EventBus
                    EventBus.get().emit("tool_denied", {"tool": name, "reason": denied[:400]})
                    return f"Error: {denied}"

            from core.effect_ledger import execute_with_ledger
            from core.events import EventBus

            def _run() -> str:
                EventBus.get().emit("tool_start", {"tool": name, "args": args})
                out = self._tools[name]["handler"](**args)
                if self.policy is not None:
                    self.policy.record_tool()
                EventBus.get().emit("tool_done", {"tool": name, "result": out[:500]})
                return out

            result, skipped = execute_with_ledger(name, args or {}, _run)
            if skipped:
                EventBus.get().emit("tool_effect_skipped", {
                    "tool": name,
                    "args": args,
                    "result": (result or "")[:500],
                })
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
        if is_job_cancelled():
            return "Error: lease reclaimed — refusing to spawn tool subprocess"
        if self.policy is not None and self.policy.enabled:
            denied = self.policy.check_shell_command(command)
            if denied:
                return f"Error: {denied}"
            if cwd:
                path_denied = self.policy.check_path(cwd, self.workspace)
                if path_denied:
                    return f"Error: {path_denied}"
            else:
                # Confine default shell cwd to workspace root.
                work_check = self.policy.check_path(self.workspace, self.workspace)
                if work_check:
                    return f"Error: {work_check}"

        work_dir = cwd or str(self.workspace)
        try:
            result = run_tracked(
                ["powershell", "-NoProfile", "-Command", command],
                command=command,
                cwd=work_dir,
                env=self.child_env(),
                timeout=timeout,
            )
        except RuntimeError as e:
            return f"Error: {e}"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        output = (result.stdout + result.stderr).strip()
        return output or f"(exit code {result.returncode})"
