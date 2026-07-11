"""Nexus Desktop App — Claude-inspired conversational UI."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import scrolledtext
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.engine import NexusEngine
from core.events import EventBus
from core.status_messages import event_to_status, explain_exception, format_failure
from ui import theme as T
from ui.assets.logo_gen import ensure_assets
from ui.chat_messages import append_nexus, append_user, append_welcome, configure_chat_tags
from ui.claude_input import ClaudeInputBar
from ui.live_workspace import LiveWorkspace
from ui.quick_actions import QuickActions
from ui.thinking_indicator import ThinkingIndicator
from ui.widgets import apply_dark_scrollbar_style


class NexusApp:
    def __init__(self):
        self.engine = NexusEngine()
        self.bus = EventBus.get(ROOT / "data" / "logs" / "live_events.jsonl")
        self._working = False
        self._assets = ensure_assets()

        self.root = tk.Tk()
        self.root.title("Nexus")
        self.root.geometry("1200x780")
        self.root.minsize(980, 660)
        self.root.configure(bg=T.BG)
        try:
            self.root.iconbitmap(str(self._assets["icon"]))
        except tk.TclError:
            pass

        apply_dark_scrollbar_style(self.root)
        self._build_ui()
        self.thinking = ThinkingIndicator(self.root, self.status_label, self.chat)
        self.live = LiveWorkspace(self.right_panel)
        self._subscribe_events()
        self._update_footer()
        self._welcome()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self._build_header()

        body = tk.Frame(self.root, bg=T.BG)
        body.pack(fill="both", expand=True)

        split = tk.PanedWindow(
            body, orient="horizontal", bg=T.BG, sashwidth=1,
            sashrelief="flat", opaqueresize=True,
        )
        split.pack(fill="both", expand=True)

        chat_col = tk.Frame(split, bg=T.BG_CHAT)
        split.add(chat_col, width=560)

        self.chat = scrolledtext.ScrolledText(
            chat_col, bg=T.BG_CHAT, fg=T.TEXT,
            font=(T.FONT_UI[0], 12), relief="flat", wrap="word",
            state="disabled", padx=T.CHAT_PAD_X, pady=8,
            insertbackground=T.ACCENT, selectbackground=T.NEXUS_GLOW,
            borderwidth=0, highlightthickness=0,
        )
        self.chat.pack(fill="both", expand=True)
        configure_chat_tags(self.chat)

        self.quick_actions = QuickActions(
            chat_col,
            actions=[
                ("Say hello", "hello nexus"),
                ("What can you do?", "what can you do?"),
                ("List Python files", "list python files in this project"),
                ("Check health", "check nexus health"),
            ],
            on_pick=self._quick_send,
        )
        self.quick_actions.pack(fill="x", pady=(0, 4))

        self.input = ClaudeInputBar(
            chat_col, placeholder="Message Nexus…", on_submit=self._send,
        )
        self.input.pack(fill="x", side="bottom")
        self.input.focus_set()

        self.right_panel = tk.Frame(split, bg=T.BG)
        split.add(self.right_panel)

        self._build_footer()

    def _build_header(self):
        header = tk.Frame(self.root, bg=T.BG, height=T.HEADER_H)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Frame(header, bg=T.BORDER, height=1).pack(side="bottom", fill="x")

        inner = tk.Frame(header, bg=T.BG)
        inner.pack(fill="both", expand=True, padx=20)

        logo_img = tk.PhotoImage(file=str(self._assets["logo_sm"]))
        self._logo_ref = logo_img
        tk.Label(inner, image=logo_img, bg=T.BG).pack(side="left", padx=(0, 10))

        brand = tk.Frame(inner, bg=T.BG)
        brand.pack(side="left")
        tk.Label(
            brand, text="Nexus", bg=T.BG, fg=T.TEXT,
            font=(T.FONT_UI[0], 14, "bold"),
        ).pack(anchor="w")
        self.status_label = tk.Label(
            brand, text="", bg=T.BG, fg=T.MUTED,
            font=(T.FONT_UI[0], 9),
        )
        self.status_label.pack(anchor="w")

        right = tk.Frame(inner, bg=T.BG)
        right.pack(side="right")
        self.model_label = tk.Label(
            right, text="", bg=T.BG, fg=T.MUTED, font=(T.FONT_UI[0], 9),
        )
        self.model_label.pack(side="right", padx=(12, 0))
        self.status_dot = tk.Canvas(right, width=8, height=8, bg=T.BG, highlightthickness=0, bd=0)
        self.status_dot.pack(side="right", pady=4)
        self.status_dot.create_oval(1, 1, 7, 7, fill=T.MUTED, outline=T.MUTED, tags="dot")

    def _build_footer(self):
        footer = tk.Frame(self.root, bg=T.BG, height=T.FOOTER_H)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self.footer_label = tk.Label(
            footer, text="", bg=T.BG, fg=T.MUTED, font=(T.FONT_UI[0], 8),
        )
        self.footer_label.pack(side="left", padx=20, pady=6)

    def _welcome(self):
        hint = self.engine.get_session_resume_hint()
        append_welcome(
            self.chat,
            "How can I help you today?",
            hint or "I'm Nexus — your local agent. Chat naturally or ask me to work on your computer.",
        )
        if hint:
            append_nexus(self.chat, hint)

    def _subscribe_events(self):
        self.bus.subscribe(self._on_event)

    def _on_event(self, event: dict):
        self.root.after(0, lambda: self._handle_event(event))

    def _handle_event(self, event: dict):
        status = event_to_status(event)
        if status and self._working:
            self.thinking.set_phrase(status)
        self.live.handle_event(event)

    def _user_say(self, text: str):
        append_user(self.chat, text)

    def _nexus_say(self, text: str, error: bool = False):
        self.thinking.stop()
        append_nexus(self.chat, text, error=error)

    def _set_busy(self, busy: bool) -> None:
        self._working = busy
        self.input.set_enabled(not busy)
        self.quick_actions.set_enabled(not busy)
        if not busy:
            self.input.focus_set()

    def _quick_send(self, goal: str) -> None:
        if self._working:
            return
        self.input.text.configure(state="normal", fg=T.TEXT)
        self.input.text.delete("1.0", tk.END)
        self.input.text.insert("1.0", goal)
        self.input.text.configure(height=1)
        self._send()

    def _send(self):
        goal = self.input.get().strip()
        if not goal or self._working:
            return
        self.input.clear()
        self._set_busy(True)
        self._user_say(goal)
        self.live.reset()
        self.thinking.start(mode="idle")
        threading.Thread(target=self._execute, args=(goal,), daemon=True).start()

    def _execute(self, goal: str):
        try:
            result = self.engine.run(goal)
            status = result.get("status", "unknown")
            text = (result.get("result") or "").strip()
            steps = result.get("steps", [])

            if status in ("failed", "incomplete", "error"):
                reply = format_failure(status, text, steps=steps, goal=goal)
                self.root.after(0, lambda: self._nexus_say(reply, error=True))
            else:
                self.root.after(0, lambda: self._nexus_say(text or "Done."))
        except Exception as exc:
            reply = f"I couldn't do that — {explain_exception(exc)}"
            self.root.after(0, lambda: self._nexus_say(reply, error=True))
        finally:
            self.root.after(0, lambda: self._set_busy(False))
            self.root.after(0, self.live.idle)

    def _update_footer(self):
        health = self.engine.health_check()
        ollama_ok = health.get("ollama")
        model = health.get("model", "")
        storage = health.get("storage", "")

        dot_color = T.GREEN if ollama_ok else T.RED
        self.status_dot.itemconfig("dot", fill=dot_color, outline=dot_color)
        self.model_label.config(text=model)
        self.footer_label.config(
            text=f"Memory: {storage}  ·  Workspace: nexus-brain"
        )

    def _on_close(self):
        self.thinking.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def start():
    NexusApp().run()


if __name__ == "__main__":
    start()
