"""Floating command bar — summon from anywhere with Win+Shift+N."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import threading
import tkinter as tk
import urllib.request
from pathlib import Path

import yaml

user32 = ctypes.windll.user32
WM_HOTKEY = 0x0312
HOTKEY_ID = 1
MOD_WIN = 0x0008
MOD_SHIFT = 0x0004
VK_N = 0x4E


class OverlayBar:
    def __init__(self, api_port: int = 9477):
        self.api_url = f"http://127.0.0.1:{api_port}/run"
        self.root = tk.Tk()
        self.root.title("Nexus")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", 0.95)

        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"700x56+{(sw-700)//2}+80")

        frame = tk.Frame(self.root, bg="#0d1b2a", highlightthickness=2,
                         highlightbackground="#00d4ff")
        frame.pack(fill="both", expand=True)

        self.label = tk.Label(frame, text="⚡ NEXUS", bg="#0d1b2a", fg="#00d4ff",
                              font=("Segoe UI", 11, "bold"))
        self.label.pack(side="left", padx=(12, 4))

        self.entry = tk.Entry(frame, font=("Segoe UI", 13), bg="#0a0a0f", fg="white",
                              insertbackground="#00d4ff", relief="flat", bd=8)
        self.entry.pack(side="left", fill="both", expand=True, padx=4, pady=8)
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Escape>", lambda e: self.hide())

        self.status = tk.Label(frame, text="", bg="#0d1b2a", fg="#4ade80",
                               font=("Segoe UI", 9))
        self.status.pack(side="right", padx=12)

        self.root.withdraw()
        self._register_hotkey()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._poll_hotkey()

    def _register_hotkey(self):
        user32.RegisterHotKey(None, HOTKEY_ID, MOD_WIN | MOD_SHIFT, VK_N)

    def _poll_hotkey(self):
        try:
            msg = ctypes.wintypes.MSG()
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.toggle()
        except Exception:
            pass
        self.root.after(100, self._poll_hotkey)

    def toggle(self):
        if self.root.state() == "withdrawn":
            self.show()
        else:
            self.hide()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.entry.focus_set()
        self.entry.select_range(0, tk.END)

    def hide(self):
        self.root.withdraw()

    def _submit(self, _event=None):
        goal = self.entry.get().strip()
        if not goal:
            return
        self.status.config(text="Working...", fg="#fbbf24")
        self.entry.delete(0, tk.END)
        threading.Thread(target=self._send, args=(goal,), daemon=True).start()
        self.root.after(1500, self.hide)

    def _send(self, goal: str):
        try:
            data = json.dumps({"goal": goal}).encode()
            req = urllib.request.Request(
                self.api_url, data=data,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            self.root.after(0, lambda: self.status.config(text="Sent ✓", fg="#4ade80"))
        except Exception as e:
            self.root.after(0, lambda: self.status.config(text=f"Error", fg="#f87171"))

    def run(self):
        self.root.mainloop()


def start_overlay():
    root = Path(__file__).resolve().parent.parent
    with open(root / "config" / "features.yaml", encoding="utf-8") as f:
        port = yaml.safe_load(f).get("live", {}).get("api_port", 9477)
    OverlayBar(port).run()
