from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from core.events import EventBus
from services.music_daemon import MusicDaemon


def register_media_tools(registry) -> None:
    root = Path(__file__).resolve().parent.parent
    events = EventBus.get()
    music = MusicDaemon(root)

    def now_playing() -> str:
        info = music.get_now_playing()
        events.emit("now_playing", info)
        return json.dumps(info, indent=2)

    def play_music(query: str, app: str = "spotify") -> str:
        events.emit("play_music", {"query": query, "app": app})
        if app.lower() == "spotify":
            encoded = query.replace(" ", "%20")
            registry.run_powershell(f'Start-Process "spotify:search:{encoded}"')
            time.sleep(2)
            registry.run_powershell(
                'Add-Type -AssemblyName System.Windows.Forms; '
                '[System.Windows.Forms.SendKeys]::SendWait("{ENTER}")'
            )
            return f"Searching Spotify for: {query} (press Enter in Spotify if needed)"
        registry.run_powershell(f'Start-Process "https://www.youtube.com/results?search_query={query.replace(" ", "+")}"')
        return f"Opened YouTube search for: {query}"

    def volume_up(step: int = 10) -> str:
        events.emit("volume_change", {"direction": "up", "step": step})
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            current = volume.GetMasterVolumeLevelScalar()
            volume.SetMasterVolumeLevelScalar(min(1.0, current + step / 100), None)
            return f"Volume up to {int(min(1.0, current + step / 100) * 100)}%"
        except Exception:
            for _ in range(step // 2):
                registry.run_powershell(
                    '(New-Object -ComObject WScript.Shell).SendKeys([char]175)'
                )
            return f"Volume increased (~{step}%)"

    def volume_down(step: int = 10) -> str:
        events.emit("volume_change", {"direction": "down", "step": step})
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            current = volume.GetMasterVolumeLevelScalar()
            volume.SetMasterVolumeLevelScalar(max(0.0, current - step / 100), None)
            return f"Volume down to {int(max(0.0, current - step / 100) * 100)}%"
        except Exception:
            for _ in range(step // 2):
                registry.run_powershell(
                    '(New-Object -ComObject WScript.Shell).SendKeys([char]174)'
                )
            return f"Volume decreased (~{step}%)"

    def set_volume(percent: int) -> str:
        percent = max(0, min(100, percent))
        events.emit("volume_change", {"percent": percent})
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMasterVolumeLevelScalar(percent / 100, None)
            return f"Volume set to {percent}%"
        except Exception as e:
            return f"Error setting volume: {e}"

    def music_wallpaper(enable: str = "true") -> str:
        if enable.lower() in ("true", "1", "yes", "on"):
            music.start()
            return "Music wallpaper daemon started — wallpaper will sync to now playing"
        music.stop()
        return "Music wallpaper daemon stopped"

    registry.register("now_playing", "Get currently playing song (Spotify, browser, any app)",
                      {}, now_playing)
    registry.register("play_music", "Play/search for music on Spotify or YouTube",
                      {"query": "str", "app": "spotify|youtube (optional)"}, play_music)
    registry.register("volume_up", "Increase system volume",
                      {"step": "int percent (optional, default 10)"}, volume_up)
    registry.register("volume_down", "Decrease system volume",
                      {"step": "int percent (optional, default 10)"}, volume_down)
    registry.register("set_volume", "Set volume to exact percentage (0-100)",
                      {"percent": "int"}, set_volume)
    registry.register("music_wallpaper", "Start/stop dynamic album-art wallpaper",
                      {"enable": "true|false (optional)"}, music_wallpaper)
