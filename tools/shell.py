from __future__ import annotations

import subprocess

from core.events import EventBus
from .registry import ToolRegistry


def register_shell_tools(registry: ToolRegistry) -> None:
    events = EventBus.get()

    def run_command(command: str, cwd: str = "") -> str:
        # Policy gate also runs in ToolRegistry.execute; re-check for direct callers.
        policy = getattr(registry, "policy", None)
        if policy is not None and policy.enabled:
            denied = policy.check_shell_command(command)
            if denied:
                return f"Error: {denied}"
            if cwd:
                path_denied = policy.check_path(cwd, registry.workspace)
                if path_denied:
                    return f"Error: {path_denied}"

        work_dir = cwd if cwd else None
        events.emit("terminal_start", {"command": command, "cwd": work_dir or str(registry.workspace)})
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", command],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=work_dir or str(registry.workspace),
        )
        lines = []
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            events.emit("terminal_output", {"text": line})
        proc.wait()
        output = "\n".join(lines)
        events.emit("terminal_done", {"command": command, "exit_code": proc.returncode})
        return (output[:8000] or f"(exit code {proc.returncode})")

    def list_processes(filter_name: str = "") -> str:
        if filter_name:
            cmd = f"Get-Process | Where-Object {{$_.ProcessName -like '*{filter_name}*'}} | Select-Object -First 20 Name,Id,CPU | Format-Table"
        else:
            cmd = "Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name,Id,CPU | Format-Table"
        return registry.run_powershell(cmd)

    def check_env(var_name: str = "") -> str:
        if var_name:
            return registry.run_powershell(f'echo "$env:{var_name}"')
        return registry.run_powershell(
            "Get-ChildItem Env: | Sort-Object Name | Select-Object -First 40 Name,Value | Format-Table"
        )

    registry.register("run_command", "Run a PowerShell command on Windows",
                      {"command": "str", "cwd": "str (optional)"}, run_command)
    registry.register("list_processes", "List running processes",
                      {"filter_name": "str (optional)"}, list_processes)
    registry.register("check_env", "Check environment variables",
                      {"var_name": "str (optional)"}, check_env)
