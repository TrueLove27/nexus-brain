"""Subtle thinking indicator — Claude-style."""

from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext

from core.status_messages import IDLE_PHRASES
from ui import theme as T

DOT_INTERVAL_MS = 450
PHRASE_ROTATE_MS = 3200


class ThinkingIndicator:
    def __init__(
        self,
        root: tk.Tk,
        header_label: tk.Label,
        chat_widget: scrolledtext.ScrolledText,
    ):
        self.root = root
        self.header_label = header_label
        self.chat = chat_widget
        self._active = False
        self._mode = "idle"
        self._phrase = "Thinking"
        self._phrase_index = 0
        self._dot_count = 0
        self._dot_job: str | None = None
        self._rotate_job: str | None = None
        self._chat_start: str | None = None
        self._chat_end: str | None = None

    def start(self, mode: str = "idle") -> None:
        self.stop()
        self._active = True
        self._mode = mode
        self._phrase = "Thinking" if mode == "idle" else "Working"
        self._phrase_index = 0
        self._dot_count = 0
        self._ensure_line()
        self._schedule_dot()
        if mode == "idle":
            self._schedule_rotate()

    def set_phrase(self, text: str) -> None:
        if not self._active:
            self.start(mode="work")
        self._mode = "work"
        self._phrase = text.rstrip(".")
        self._cancel_rotate()
        self._render()

    def stop(self) -> None:
        self._active = False
        self._cancel_dot()
        self._cancel_rotate()
        self._remove_line()
        self.header_label.config(text="")

    def _ensure_line(self) -> None:
        self._remove_line()
        self.chat.configure(state="normal")
        self._chat_start = self.chat.index("end-1c")
        self._chat_end = self._chat_start
        self.chat.configure(state="disabled")

    def _remove_line(self) -> None:
        if self._chat_start:
            try:
                self.chat.configure(state="normal")
                self.chat.delete(self._chat_start, "end-1c")
                self.chat.configure(state="disabled")
            except tk.TclError:
                pass
        self._chat_start = None
        self._chat_end = None

    def _render(self) -> None:
        if not self._active:
            return
        dots = "." * (self._dot_count + 1)
        display = f"{self._phrase}{dots}"
        self.header_label.config(text=display, fg=T.MUTED)

        if self._chat_start is not None:
            try:
                self.chat.configure(state="normal")
                if self._chat_end:
                    end = self.chat.index("end-1c")
                    if self.chat.compare(end, ">", self._chat_end):
                        self.chat.delete(self._chat_end, end)
                self.chat.insert(self._chat_end or self._chat_start, f"{display}\n", "status")
                self._chat_end = self.chat.index("end-1c")
                self.chat.configure(state="disabled")
                self.chat.see("end")
            except tk.TclError:
                pass

    def _tick_dot(self) -> None:
        if not self._active:
            return
        self._dot_count = (self._dot_count + 1) % 3
        self._render()
        self._schedule_dot()

    def _rotate_phrase(self) -> None:
        if not self._active or self._mode != "idle":
            return
        self._phrase_index = (self._phrase_index + 1) % len(IDLE_PHRASES)
        self._phrase = IDLE_PHRASES[self._phrase_index].rstrip(".")
        self._render()
        self._schedule_rotate()

    def _schedule_dot(self) -> None:
        self._cancel_dot()
        self._dot_job = self.root.after(DOT_INTERVAL_MS, self._tick_dot)

    def _schedule_rotate(self) -> None:
        self._cancel_rotate()
        self._rotate_job = self.root.after(PHRASE_ROTATE_MS, self._rotate_phrase)

    def _cancel_dot(self) -> None:
        if self._dot_job:
            try:
                self.root.after_cancel(self._dot_job)
            except tk.TclError:
                pass
            self._dot_job = None

    def _cancel_rotate(self) -> None:
        if self._rotate_job:
            try:
                self.root.after_cancel(self._rotate_job)
            except tk.TclError:
                pass
            self._rotate_job = None
