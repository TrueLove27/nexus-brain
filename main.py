#!/usr/bin/env python3
"""
Nexus Brain — Autonomous multi-agent system for your Windows machine.

Usage:
  py main.py                    # Interactive mode
  py main.py run "your goal"    # One-shot task
  py main.py daemon             # Proactive background mode
  py main.py session [hours]    # Timed portfolio work session (default 3h)
  py main.py portfolio          # Drop next portfolio task into inbox
  py main.py agents             # List all agents
  py main.py health             # Check system status
  py main.py teach "preference" # Train Nexus with a preference
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table

from core.engine import NexusEngine
from core.proactive import ProactiveDaemon

console = Console()


def show_banner():
    console.print(Panel(
        "[bold cyan]NEXUS BRAIN[/bold cyan]\n"
        "Autonomous agent with its own memory, specialists, and proactive mode.\n"
        "Powered by local Ollama — zero cost.",
        border_style="cyan",
    ))


def cmd_health(engine: NexusEngine):
    status = engine.health_check()
    table = Table(title="System Health")
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    for k, v in status.items():
        color = "green" if v is True or (isinstance(v, int) and v > 0) else "yellow"
        table.add_row(k, f"[{color}]{v}[/{color}]")
    console.print(table)
    if not status["ollama"]:
        console.print("[red]Ollama is not running. Start it: ollama serve[/red]")


def cmd_agents(engine: NexusEngine):
    agents = engine.list_agents()
    table = Table(title="Active Agents")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Role")
    for a in agents:
        table.add_row(a["id"], a["name"], a["role"][:60])
    console.print(table)


def cmd_run(engine: NexusEngine, goal: str):
    console.print(f"\n[bold]Goal:[/bold] {goal}\n")
    with console.status("[cyan]Nexus is thinking and acting..."):
        result = engine.run(goal)

    color = "green" if result["status"] == "done" else "yellow"
    console.print(Panel(
        f"[{color}]{result.get('result', 'No result')}[/{color}]",
        title=f"Result ({result['status']})",
        border_style=color,
    ))

    if result.get("steps"):
        console.print(f"\n[dim]Completed in {len(result['steps'])} steps[/dim]")


def cmd_teach(engine: NexusEngine, preference: str):
    engine.teach(preference)
    console.print(f"[green]Learned:[/green] {preference}")


def cmd_daemon(engine: NexusEngine):
    interval = engine.config["brain"].get("proactive_interval_seconds", 30)
    daemon = ProactiveDaemon(engine, interval=interval)

    def on_done(task, result):
        console.print(f"[green]Done:[/green] {task[:60]}... → {result.get('status')}")

    daemon.on_task_complete = on_done
    daemon.start()
    console.print(f"[cyan]Proactive daemon running.[/cyan] Drop tasks in: data/inbox/*.txt")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        daemon.stop()
        console.print("\n[yellow]Daemon stopped.[/yellow]")


def cmd_session(engine: NexusEngine, hours: float = 3.0):
    from core.session_runner import SessionRunner

    console.print(f"\n[bold]Starting {hours}h portfolio session on nexus-brain[/bold]\n")
    runner = SessionRunner(engine, hours=hours)

    def on_task(task: str):
        console.print(f"[cyan]Task:[/cyan] {task[:100]}")

    with console.status("[cyan]Session running..."):
        summary = runner.run(on_task=on_task)

    summary_md = summary.get("summary_md", "")
    summary_json = summary.get("summary_json", "")
    console.print(Panel(
        f"Cycles: {summary['cycles']}\n"
        f"Completed: {summary['completed']}\n"
        f"Failures: {summary.get('failed', 0)}\n"
        f"Log: data/logs/session.jsonl\n"
        f"Report: {summary_md}\n"
        f"JSON: {summary_json}",
        title="Session Summary",
        border_style="green",
    ))


def cmd_portfolio(engine: NexusEngine):
    from core.portfolio_bridge import PortfolioBridge

    bridge = PortfolioBridge.from_engine(engine)
    task = bridge.next_unchecked_task()
    if not task:
        console.print("[yellow]No pending portfolio tasks.[/yellow]")
        return

    path = bridge.drop_next_task(str(engine.root))
    console.print(f"[green]Dropped into inbox:[/green] {path}")
    console.print(f"[cyan]Task:[/cyan] {task}")


def interactive_mode(engine: NexusEngine):
    show_banner()
    cmd_health(engine)
    console.print("\n[dim]Commands: run <goal> | session [hours] | portfolio | agents | teach <note> | recall <query> | spawn <name> <role> | quit[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold cyan]nexus>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if user_input.lower() == "agents":
            cmd_agents(engine)
            continue
        if user_input.lower() == "health":
            cmd_health(engine)
            continue
        if user_input.lower().startswith("teach "):
            cmd_teach(engine, user_input[6:])
            continue
        if user_input.lower().startswith("recall "):
            memories = engine.recall(user_input[7:])
            for m in memories:
                console.print(f"  • {m}")
            continue
        if user_input.lower().startswith("spawn "):
            parts = user_input[6:].split(" ", 1)
            name = parts[0]
            role = parts[1] if len(parts) > 1 else "General specialist"
            agent = engine.create_agent(name, role)
            console.print(f"[green]Spawned agent:[/green] {agent['name']} ({agent['id']})")
            continue
        if user_input.lower().startswith("run "):
            cmd_run(engine, user_input[4:])
            continue
        if user_input.lower().startswith("session"):
            parts = user_input.split()
            hrs = float(parts[1]) if len(parts) > 1 else 3.0
            cmd_session(engine, hrs)
            continue
        if user_input.lower() == "portfolio":
            cmd_portfolio(engine)
            continue

        cmd_run(engine, user_input)


def main():
    engine = NexusEngine()

    if len(sys.argv) < 2:
        interactive_mode(engine)
        return

    cmd = sys.argv[1].lower()

    if cmd == "health":
        show_banner()
        cmd_health(engine)
    elif cmd == "agents":
        cmd_agents(engine)
    elif cmd == "run" and len(sys.argv) > 2:
        show_banner()
        cmd_run(engine, " ".join(sys.argv[2:]))
    elif cmd == "teach" and len(sys.argv) > 2:
        cmd_teach(engine, " ".join(sys.argv[2:]))
    elif cmd == "daemon":
        show_banner()
        cmd_daemon(engine)
    elif cmd == "session":
        show_banner()
        hrs = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
        cmd_session(engine, hrs)
    elif cmd == "portfolio":
        show_banner()
        cmd_portfolio(engine)
    elif cmd == "live":
        from ui.nexus_live import start
        start()
    elif cmd == "app":
        from ui.nexus_app import start
        start()
    elif cmd == "create-agent" and len(sys.argv) > 3:
        agent = engine.create_agent(sys.argv[2], " ".join(sys.argv[3:]))
        console.print(f"Created: {agent}")
    else:
        console.print(__doc__)


if __name__ == "__main__":
    main()
