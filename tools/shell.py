from __future__ import annotations

from core.events import EventBus
from core.tool_subprocess import is_job_cancelled, kill_process_tree, popen_tracked, unregister_pid
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

        if is_job_cancelled():
            return "Error: lease reclaimed — refusing to spawn tool subprocess"

        work_dir = cwd if cwd else None
        events.emit("terminal_start", {"command": command, "cwd": work_dir or str(registry.workspace)})
        try:
            proc = popen_tracked(
                ["powershell", "-NoProfile", "-Command", command],
                command=command,
                cwd=work_dir or str(registry.workspace),
                env=registry.child_env(),
            )
        except RuntimeError as e:
            return f"Error: {e}"

        lines = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if is_job_cancelled():
                    kill_process_tree(int(proc.pid))
                    events.emit("terminal_done", {
                        "command": command,
                        "exit_code": -1,
                        "reaped": True,
                    })
                    return "Error: lease reclaimed — tool subprocess terminated"
                line = line.rstrip()
                lines.append(line)
                events.emit("terminal_output", {"text": line})
            proc.wait()
            output = "\n".join(lines)
            events.emit("terminal_done", {"command": command, "exit_code": proc.returncode})
            return (output[:8000] or f"(exit code {proc.returncode})")
        finally:
            if proc.pid:
                unregister_pid(int(proc.pid))

    def list_processes(filter_name: str = "") -> str:
        if filter_name:
            cmd = f"Get-Process | Where-Object {{$_.ProcessName -like '*{filter_name}*'}} | Select-Object -First 20 Name,Id,CPU | Format-Table"
        else:
            cmd = "Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name,Id,CPU | Format-Table"
        return registry.run_powershell(cmd)

    def check_env(var_name: str = "") -> str:
        # Runs inside a scrubbed child — host API keys/tokens are not visible.
        policy = getattr(registry, "env_policy", None)
        if policy is None:
            from core.subprocess_env import get_env_policy
            policy = get_env_policy()
        if var_name and policy.should_strip(var_name):
            return (
                f"(scrubbed) '{var_name}' is withheld from tool subprocesses "
                f"and is not readable via check_env"
            )
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
