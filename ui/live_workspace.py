"""Live Workspace — subtle Claude-style side panel."""

from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext

from core.status_messages import event_to_workspace
from ui import theme as T


class LiveWorkspace:
    def __init__(self, parent: tk.Widget):
        outer = tk.Frame(parent, bg=T.BG)
        outer.pack(fill="both", expand=True)

        # Subtle left divider
        tk.Frame(outer, bg=T.BORDER, width=1).pack(side="left", fill="y")

        content = tk.Frame(outer, bg=T.BG)
        content.pack(fill="both", expand=True, padx=20, pady=16)

        hdr = tk.Frame(content, bg=T.BG)
        hdr.pack(fill="x", pady=(0, 16))
        tk.Label(
            hdr, text="Live Workspace", bg=T.BG, fg=T.TEXT,
            font=(T.FONT_UI[0], 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            hdr, text="What Nexus is doing right now",
            bg=T.BG, fg=T.MUTED, font=(T.FONT_UI[0], 9),
        ).pack(anchor="w", pady=(2, 0))

        self.now_label = tk.Label(
            content,
            text="Idle",
            bg=T.SURFACE,
            fg=T.NOW_COLOR,
            font=(T.FONT_UI[0], 10),
            anchor="w",
            padx=14,
            pady=10,
            wraplength=400,
            justify="left",
        )
        self.now_label.pack(fill="x", pady=(0, 12))

        def _section(title: str) -> tk.Frame:
            tk.Label(
                content, text=title, bg=T.BG, fg=T.MUTED,
                font=(T.FONT_UI[0], 8, "bold"),
            ).pack(anchor="w", pady=(8, 4))
            frame = tk.Frame(content, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.BORDER)
            frame.pack(fill="both", expand=True, pady=(0, 8))
            return frame

        code_frame = _section("CODE")
        self.code_text = scrolledtext.ScrolledText(
            code_frame, bg=T.SURFACE, fg=T.CODE_COLOR,
            font=(T.FONT_MONO[0], 9), relief="flat", wrap="word",
            state="disabled", height=8, padx=10, pady=10,
            insertbackground=T.CODE_COLOR,
        )
        self.code_text.pack(fill="both", expand=True)

        term_frame = _section("TERMINAL")
        self.term_text = scrolledtext.ScrolledText(
            term_frame, bg=T.SURFACE, fg=T.TERM_COLOR,
            font=(T.FONT_MONO[0], 9), relief="flat", wrap="word",
            state="disabled", height=6, padx=10, pady=10,
            insertbackground=T.TERM_COLOR,
        )
        self.term_text.pack(fill="both", expand=True)

        self._code_path = ""
        self._term_lines = 0
        self._max_term_lines = 80

    def reset(self) -> None:
        self.now_label.config(text="Working…", fg=T.ACCENT)
        self._code_path = ""
        self._term_lines = 0
        self._clear(self.code_text)
        self._clear(self.term_text)

    def idle(self) -> None:
        self.now_label.config(text="Idle — waiting for a task", fg=T.NOW_COLOR)
        self._code_path = ""
        self._clear(self.code_text)
        self._clear(self.term_text)
        self._term_lines = 0

    def _clear(self, widget: scrolledtext.ScrolledText) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.configure(state="disabled")

    def handle_event(self, event: dict) -> None:
        routed = event_to_workspace(event)
        if not routed:
            return
        section, content = routed
        if section == "now":
            self.now_label.config(text=content, fg=T.ACCENT)
        elif section == "code":
            self._show_code(event, content)
        elif section == "terminal":
            self._append_terminal(content)

    def _show_code(self, event: dict, content: str) -> None:
        etype = event.get("type", "")
        path = event.get("path", "")
        if path:
            self._code_path = path
        self.code_text.configure(state="normal")
        if etype == "code_write_start":
            self.code_text.delete("1.0", tk.END)
            if path:
                self.code_text.insert("end", f"# {path}\n")
        elif etype == "code_edit":
            self.code_text.delete("1.0", tk.END)
            self.code_text.insert(
                "end",
                f"# {path}\n--- removed ---\n{event.get('old_text', '')[:400]}\n"
                f"--- added ---\n{event.get('new_text', '')[:400]}",
            )
        elif etype == "code_typing":
            snippet = event.get("snippet", content)
            if path and not self.code_text.get("1.0", "end").strip():
                self.code_text.insert("end", f"# {path}\n")
            lines = self.code_text.get("1.0", "end").split("\n")
            if len(lines) > 2:
                self.code_text.delete("end-2l", "end-1c")
            self.code_text.insert("end", snippet + "\n")
        else:
            self.code_text.insert("end", content + "\n")
        self.code_text.see("end")
        self.code_text.configure(state="disabled")

    def _append_terminal(self, line: str) -> None:
        self.term_text.configure(state="normal")
        self.term_text.insert("end", line + "\n")
        self._term_lines += 1
        if self._term_lines > self._max_term_lines:
            self.term_text.delete("1.0", "2.0")
            self._term_lines -= 1
        self.term_text.see("end")
        self.term_text.configure(state="disabled")
