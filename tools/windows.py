from __future__ import annotations

from pathlib import Path

from .registry import ToolRegistry


def register_windows_tools(registry: ToolRegistry) -> None:
    ws = registry.workspace

    def open_file(path: str) -> str:
        p = Path(path) if Path(path).is_absolute() else ws / path
        if not p.exists():
            return f"Error: not found: {p}"
        return registry.run_powershell(f'Start-Process "{p}"')

    def open_url(url: str) -> str:
        return registry.run_powershell(f'Start-Process "{url}"')

    def create_workflow(name: str, steps: str, schedule: str = "") -> str:
        workflows_dir = Path(__file__).resolve().parent.parent / "data" / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        script_path = workflows_dir / f"{name}.ps1"
        header = f"# Nexus workflow: {name}\n# Schedule: {schedule or 'manual'}\n\n"
        script_path.write_text(header + steps, encoding="utf-8")
        if schedule:
            task_name = f"Nexus_{name}"
            register_cmd = (
                f'$action = New-ScheduledTaskAction -Execute "powershell.exe" '
                f'-Argument "-File \'{script_path}\'" ; '
                f'Register-ScheduledTask -TaskName "{task_name}" -Action $action '
                f'-Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) '
                f'-RepetitionInterval (New-TimeSpan -Minutes {schedule})) -Force'
            )
            registry.run_powershell(register_cmd)
            return f"Workflow saved to {script_path} and scheduled as {task_name}"
        return f"Workflow saved to {script_path} (run manually)"

    def queue_task(description: str) -> str:
        inbox = Path(__file__).resolve().parent.parent / "data" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        import time
        fname = f"task_{int(time.time())}.txt"
        (inbox / fname).write_text(description, encoding="utf-8")
        return f"Task queued in inbox: {fname}"

    registry.register("open_file", "Open a file with its default Windows application",
                      {"path": "str"}, open_file)
    registry.register("open_url", "Open a URL in the default browser",
                      {"url": "str"}, open_url)
    registry.register("create_workflow", "Create and optionally schedule a PowerShell workflow",
                      {"name": "str", "steps": "str", "schedule": "str minutes (optional)"},
                      create_workflow)
    registry.register("queue_task", "Queue a task for the proactive agent to pick up",
                      {"description": "str"}, queue_task)
