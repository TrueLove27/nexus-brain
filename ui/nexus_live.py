"""Nexus Live — starts everything: API, dashboard, overlay, music wallpaper."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def start():
    with open(ROOT / "config" / "features.yaml", encoding="utf-8") as f:
        features = yaml.safe_load(f)
    port = features.get("live", {}).get("api_port", 9477)

    from core.engine import NexusEngine
    from core.events import EventBus
    from services.music_daemon import MusicDaemon
    from ui.server import create_app

    EventBus.get(ROOT / "data" / "logs" / "live_events.jsonl")
    engine = NexusEngine()

    # Music wallpaper daemon
    music = MusicDaemon(ROOT)
    music.start()

    # API server
    import uvicorn
    app = create_app(engine)
    server_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning"),
        daemon=True,
    )
    server_thread.start()
    time.sleep(1)

    # Open dashboard in browser
    webbrowser.open(f"http://127.0.0.1:{port}")

    print(f"""
============================================================
  NEXUS LIVE is running

  Dashboard:  http://127.0.0.1:{port}
  Hotkey:     Win+Shift+N  (floating command bar)
  Music:      Wallpaper syncs to now playing

  Type from the dashboard OR press Win+Shift+N anywhere
============================================================
""")

    # Overlay (blocks — runs tkinter main loop)
    from ui.overlay import OverlayBar
    OverlayBar(port).run()


if __name__ == "__main__":
    start()
