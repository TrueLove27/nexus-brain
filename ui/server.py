"""FastAPI backend — receives commands, streams live events."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from core.engine import NexusEngine
from core.events import EventBus


class RunRequest(BaseModel):
    goal: str


def create_app(engine: NexusEngine | None = None) -> FastAPI:
    root = Path(__file__).resolve().parent.parent
    if engine is None:
        engine = NexusEngine()
    bus = EventBus.get(root / "data" / "logs" / "live_events.jsonl")

    with open(root / "config" / "features.yaml", encoding="utf-8") as f:
        features = yaml.safe_load(f)

    app = FastAPI(title="Nexus Live")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.engine = engine
    app.state.bus = bus

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return (root / "ui" / "dashboard.html").read_text(encoding="utf-8")

    @app.get("/health")
    async def health():
        return engine.health_check()

    @app.get("/status")
    async def status():
        """Live session progress + inbox task queue (poll-friendly)."""
        return engine.runtime_status()

    @app.get("/jobs/{job_id}/trace")
    async def job_trace(job_id: int):
        """Forensics package: job row + lifecycle traces + tool_calls + agent_events."""
        package = engine.job_forensics(job_id)
        if package is None:
            return {"error": "not_found", "job_id": job_id}
        return package

    @app.get("/jobs/traces/recent")
    async def recent_job_traces(limit: int = 10):
        """Recent lease-reclaim and failure traces for ops/debugging."""
        if not hasattr(engine.memory, "job_trace_summary"):
            return {"error": "postgres_required"}
        try:
            return engine.memory.job_trace_summary(limit=max(1, min(limit, 50)))
        except Exception as e:
            return {"error": str(e)}

    @app.post("/run")
    async def run_task(req: RunRequest):
        bus.emit("task_start", {"goal": req.goal})

        def _run():
            try:
                result = engine.run(req.goal)
                bus.emit("task_done", {"goal": req.goal, "status": result.get("status"),
                                        "result": result.get("result", "")[:2000],
                                        "job_id": result.get("job_id"),
                                        "task_id": result.get("task_id")})
            except Exception as e:
                bus.emit("task_error", {"goal": req.goal, "error": str(e)})

        threading.Thread(target=_run, daemon=True).start()
        return {"accepted": True, "goal": req.goal}

    @app.get("/events")
    async def event_stream():
        queue: asyncio.Queue[dict] = asyncio.Queue()

        def on_event(event: dict):
            try:
                queue.put_nowait(event)
            except Exception:
                pass

        bus.subscribe(on_event)

        async def generate():
            for ev in bus.history(50):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/now-playing")
    async def now_playing():
        from services.music_daemon import MusicDaemon
        return MusicDaemon(root).get_now_playing()

    return app


def run_server(engine: NexusEngine | None = None, port: int = 9477):
    import uvicorn
    root = Path(__file__).resolve().parent.parent
    with open(root / "config" / "features.yaml", encoding="utf-8") as f:
        port = yaml.safe_load(f).get("live", {}).get("api_port", port)
    app = create_app(engine)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
