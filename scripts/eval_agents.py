#!/usr/bin/env python3
"""Benchmark Nexus agent task completion rate across Ollama models.

Runs a small fixed set of synthetic goals through NexusEngine for each model,
reports completion rate (% status == done), durations, and writes JSON + markdown
under data/evals/.

Usage:
  py scripts/eval_agents.py --help
  py scripts/eval_agents.py
  py scripts/eval_agents.py --models llama3.2,qwen2.5:7b
  py scripts/eval_agents.py --models llama3.2 --max-iterations 10
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Lightweight synthetic goals — no fixtures, safe on a local machine.
DEFAULT_GOALS: list[dict[str, str]] = [
    {"id": "greeting", "goal": "hello nexus"},
    {"id": "capabilities", "goal": "what can you do?"},
    {"id": "chat_summary", "goal": "In one short sentence, what is Nexus Brain?"},
    {
        "id": "list_scripts",
        "goal": (
            "List the .py filenames under the scripts directory in this project. "
            "Call finish when you have the list."
        ),
    },
    {
        "id": "readme_heading",
        "goal": (
            "Read README.md at the project root and finish with only the first "
            "markdown heading line."
        ),
    },
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Benchmark Nexus agent task completion across Ollama models.",
    )
    p.add_argument(
        "--models",
        default="",
        help="Comma-separated Ollama model names (default: model from config/brain.yaml)",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "brain.yaml",
        help="Base brain.yaml path",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "data" / "evals",
        help="Directory for JSON/Markdown reports (default: data/evals)",
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=12,
        help="Cap agent ReAct iterations for faster evals (default: 12)",
    )
    p.add_argument(
        "--skip-unavailable",
        action="store_true",
        default=True,
        help="Skip models not pulled in Ollama (default: true)",
    )
    p.add_argument(
        "--require-all-models",
        action="store_true",
        help="Fail if a listed model is not available instead of skipping",
    )
    return p.parse_args()


def _load_base_config(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _write_eval_config(base: dict[str, Any], model: str, max_iterations: int, path: Path) -> None:
    cfg = json.loads(json.dumps(base))  # deep copy via JSON
    cfg.setdefault("llm", {})["model"] = model
    brain = cfg.setdefault("brain", {})
    brain["max_agent_iterations"] = max_iterations
    brain["wind_down_at_iteration"] = max(int(max_iterations * 0.8), max_iterations - 2)
    brain["learn_from_every_task"] = False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def _resolve_models(raw: str, default_model: str) -> list[str]:
    if not raw.strip():
        return [default_model]
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return models or [default_model]


def _run_goal(engine, goal: str) -> dict[str, Any]:
    started = time.perf_counter()
    error: str | None = None
    result: dict[str, Any] = {}
    try:
        result = engine.run(goal)
    except Exception as exc:  # noqa: BLE001 — eval must continue across goals
        error = f"{type(exc).__name__}: {exc}"
        result = {"status": "error", "result": error, "steps": []}
    duration = time.perf_counter() - started
    status = result.get("status") or "error"
    return {
        "status": status,
        "duration_seconds": round(duration, 3),
        "steps": len(result.get("steps") or []),
        "agent": result.get("agent"),
        "result_preview": (result.get("result") or "")[:240],
        "error": error,
    }


def _eval_model(
    model: str,
    base_cfg: dict[str, Any],
    goals: list[dict[str, str]],
    max_iterations: int,
    config_path: Path,
) -> dict[str, Any]:
    from core.engine import NexusEngine
    from llm.ollama import OllamaProvider

    llm_cfg = base_cfg.get("llm", {})
    probe = OllamaProvider(
        model=model,
        base_url=llm_cfg.get("base_url", "http://localhost:11434"),
        max_tokens=llm_cfg.get("max_tokens", 4096),
    )
    available = probe.is_available()
    if not available:
        return {
            "model": model,
            "available": False,
            "skipped": True,
            "goals": [],
            "done": 0,
            "total": len(goals),
            "completion_rate": 0.0,
            "total_duration_seconds": 0.0,
        }

    _write_eval_config(base_cfg, model, max_iterations, config_path)
    engine = NexusEngine(config_path=config_path)

    goal_rows: list[dict[str, Any]] = []
    for g in goals:
        print(f"  [{model}] {g['id']} ...", flush=True)
        row = _run_goal(engine, g["goal"])
        row["id"] = g["id"]
        row["goal"] = g["goal"]
        goal_rows.append(row)
        print(
            f"    -> {row['status']} in {row['duration_seconds']}s "
            f"({row['steps']} steps)",
            flush=True,
        )

    done = sum(1 for r in goal_rows if r["status"] == "done")
    total = len(goal_rows)
    total_duration = round(sum(r["duration_seconds"] for r in goal_rows), 3)
    return {
        "model": model,
        "available": True,
        "skipped": False,
        "goals": goal_rows,
        "done": done,
        "total": total,
        "completion_rate": round((done / total) * 100.0, 1) if total else 0.0,
        "total_duration_seconds": total_duration,
        "avg_duration_seconds": round(total_duration / total, 3) if total else 0.0,
    }


def _write_report(out_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["ended_at"]).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"agent_eval_{stamp}.json"
    md_path = out_dir / f"agent_eval_{stamp}.md"

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# Agent Eval Report",
        "",
        f"- **Started:** {report['started_at']}",
        f"- **Ended:** {report['ended_at']}",
        f"- **Goals per model:** {report['goal_count']}",
        f"- **Max iterations:** {report['max_iterations']}",
        "",
        "## Models",
        "",
        "| Model | Available | Done | Total | Completion % | Duration (s) |",
        "|-------|-----------|------|-------|--------------|--------------|",
    ]
    for m in report["models"]:
        if m.get("skipped"):
            lines.append(
                f"| `{m['model']}` | no | — | {m['total']} | skipped | — |"
            )
        else:
            lines.append(
                f"| `{m['model']}` | yes | {m['done']} | {m['total']} | "
                f"{m['completion_rate']}% | {m['total_duration_seconds']} |"
            )

    lines.extend(["", "## Per-goal detail", ""])
    for m in report["models"]:
        lines.append(f"### `{m['model']}`")
        lines.append("")
        if m.get("skipped"):
            lines.append("_Skipped — model not available in Ollama._")
            lines.append("")
            continue
        for g in m.get("goals") or []:
            lines.append(
                f"- **{g['id']}**: `{g['status']}` in {g['duration_seconds']}s "
                f"({g['steps']} steps)"
            )
        lines.append("")

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    args = _parse_args()
    base_cfg = _load_base_config(args.config)
    default_model = base_cfg.get("llm", {}).get("model", "llama3.2")
    models = _resolve_models(args.models, default_model)
    skip_unavailable = args.skip_unavailable and not args.require_all_models

    print("Nexus agent eval")
    print(f"  models: {', '.join(models)}")
    print(f"  goals:  {len(DEFAULT_GOALS)}")
    print(f"  max_iterations: {args.max_iterations}")
    print()

    started_at = datetime.now(timezone.utc).isoformat()
    model_results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="nexus_eval_") as tmp:
        tmp_dir = Path(tmp)
        for model in models:
            cfg_path = tmp_dir / f"brain_{model.replace(':', '_').replace('/', '_')}.yaml"
            result = _eval_model(
                model, base_cfg, DEFAULT_GOALS, args.max_iterations, cfg_path,
            )
            if result.get("skipped") and not skip_unavailable:
                print(f"ERROR: model '{model}' is not available in Ollama", file=sys.stderr)
                return 1
            if result.get("skipped"):
                print(f"  [{model}] skipped (not available)")
            model_results.append(result)

    ended_at = datetime.now(timezone.utc).isoformat()
    report = {
        "started_at": started_at,
        "ended_at": ended_at,
        "goal_count": len(DEFAULT_GOALS),
        "goals": DEFAULT_GOALS,
        "max_iterations": args.max_iterations,
        "models": model_results,
    }

    json_path, md_path = _write_report(args.out_dir, report)

    # Also drop a copy under data/logs for operators who watch that folder.
    logs_dir = ROOT / "data" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(ended_at).strftime("%Y%m%d_%H%M%S")
    log_copy = logs_dir / f"agent_eval_{stamp}.json"
    log_copy.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print()
    print("Summary")
    for m in model_results:
        if m.get("skipped"):
            print(f"  {m['model']}: skipped (unavailable)")
        else:
            print(
                f"  {m['model']}: {m['completion_rate']}% "
                f"({m['done']}/{m['total']} done) in {m['total_duration_seconds']}s"
            )
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {log_copy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
