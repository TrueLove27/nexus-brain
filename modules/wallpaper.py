"""Dynamic album-art wallpaper with speaker motion animation."""

from __future__ import annotations

import ctypes
import math
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from core.events import EventBus


class WallpaperEngine:
    SPI_SETDESKWALLPAPER = 20

    def __init__(self, output_dir: Path, fps: int = 3):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.fps = fps
        self._running = False
        self._thread: threading.Thread | None = None
        self._current_track = ""
        self._frames: list[Path] = []
        self._frame_idx = 0
        self.events = EventBus.get()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._animate_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def update_from_track(self, title: str, artist: str, thumbnail_path: str) -> None:
        track_key = f"{artist} - {title}"
        if track_key == self._current_track and self._frames:
            return
        if not thumbnail_path or not Path(thumbnail_path).exists():
            return
        self._current_track = track_key
        self._frames = self._build_frames(Path(thumbnail_path), title, artist)
        self._frame_idx = 0
        self.events.emit("wallpaper_updated", {"track": track_key, "frames": len(self._frames)})

    def _build_frames(self, cover: Path, title: str, artist: str, n_frames: int = 8) -> list[Path]:
        base = Image.open(cover).convert("RGB")
        screen_w, screen_h = 1920, 1080
        base = base.resize((screen_w, screen_h), Image.Resampling.LANCZOS)
        base = base.filter(ImageFilter.GaussianBlur(radius=2))

        frames = []
        for i in range(n_frames):
            frame = base.copy()
            overlay = Image.new("RGBA", (screen_w, screen_h), (0, 0, 0, 80))
            frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(frame)

            # Center album art
            art_size = 420
            art = Image.open(cover).convert("RGB").resize((art_size, art_size), Image.Resampling.LANCZOS)
            ax = (screen_w - art_size) // 2
            ay = (screen_h - art_size) // 2 - 40
            frame.paste(art, (ax, ay))

            # Speaker bars animation
            bar_count = 12
            bar_w, gap = 14, 8
            total_w = bar_count * bar_w + (bar_count - 1) * gap
            start_x = (screen_w - total_w) // 2
            bar_y = ay + art_size + 30
            for b in range(bar_count):
                phase = (i / n_frames) * math.pi * 2 + b * 0.5
                height = int(20 + 40 * abs(math.sin(phase)))
                x0 = start_x + b * (bar_w + gap)
                draw.rounded_rectangle(
                    [x0, bar_y + 50 - height, x0 + bar_w, bar_y + 50],
                    radius=4, fill=(0, 200, 255),
                )

            # Track info
            draw.text((screen_w // 2 - len(title) * 5, bar_y + 70), title[:50], fill=(255, 255, 255))
            draw.text((screen_w // 2 - len(artist) * 4, bar_y + 95), artist[:50], fill=(180, 180, 180))

            out = self.output_dir / f"frame_{i}.jpg"
            frame.save(out, quality=92)
            frames.append(out)
        return frames

    def _animate_loop(self) -> None:
        while self._running:
            if self._frames:
                frame = self._frames[self._frame_idx % len(self._frames)]
                self._set_wallpaper(frame)
                self._frame_idx += 1
            time.sleep(1 / self.fps)

    def _set_wallpaper(self, path: Path) -> None:
        ctypes.windll.user32.SystemParametersInfoW(
            self.SPI_SETDESKWALLPAPER, 0, str(path.resolve()), 3
        )
