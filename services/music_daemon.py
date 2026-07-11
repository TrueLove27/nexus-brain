"""Background service — watches now playing and updates wallpaper."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import yaml

from core.events import EventBus
from modules.wallpaper import WallpaperEngine


class MusicDaemon:
    def __init__(self, root: Path):
        with open(root / "config" / "features.yaml", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        music_cfg = self.config.get("music", {})
        self.enabled = music_cfg.get("wallpaper_enabled", True)
        self.poll_interval = music_cfg.get("poll_interval_seconds", 2)
        self.ps_script = root / "modules" / "now_playing.ps1"
        self.engine = WallpaperEngine(
            root / music_cfg.get("wallpaper_dir", "data/wallpaper"),
            fps=music_cfg.get("animation_fps", 3),
        )
        self.events = EventBus.get()
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_track = ""

    def start(self) -> None:
        if self._running or not self.enabled:
            return
        self._running = True
        self.engine.start()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.events.emit("music_daemon_start", {})

    def stop(self) -> None:
        self._running = False
        self.engine.stop()

    def get_now_playing(self) -> dict:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.ps_script)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout.strip())
        except Exception as e:
            return {"playing": False, "error": str(e)}
        return {"playing": False}

    def _loop(self) -> None:
        while self._running:
            info = self.get_now_playing()
            if info.get("playing"):
                track = f"{info.get('artist', '')} - {info.get('title', '')}"
                if track != self._last_track:
                    self._last_track = track
                    self.events.emit("now_playing", info)
                thumb = info.get("thumbnail", "")
                if thumb:
                    self.engine.update_from_track(
                        info.get("title", ""), info.get("artist", ""), thumb,
                    )
            time.sleep(self.poll_interval)
