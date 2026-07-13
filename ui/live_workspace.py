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

        # Session progress + task queue (poll-driven)
        self._build_status_block(content)

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
        self._last_status_key = ""

    def _build_status_block(self, parent: tk.Widget) -> None:
        tk.Label(
            parent, text="SESSION", bg=T.BG, fg=T.MUTED,
            font=(T.FONT_UI[0], 8, "bold"),
        ).pack(anchor="w", pady=(0, 4))

        session_box = tk.Frame(
            parent, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.BORDER,
        )
        session_box.pack(fill="x", pady=(0, 10))
        inner = tk.Frame(session_box, bg=T.SURFACE)
        inner.pack(fill="x", padx=12, pady=10)

        top = tk.Frame(inner, bg=T.SURFACE)
        top.pack(fill="x")
        self.session_state = tk.Label(
            top, text="Idle", bg=T.SURFACE, fg=T.MUTED,
            font=(T.FONT_UI[0], 10, "bold"), anchor="w",
        )
        self.session_state.pack(side="left")
        self.session_meta = tk.Label(
            top, text="—", bg=T.SURFACE, fg=T.MUTED,
            font=(T.FONT_UI[0], 9), anchor="e",
        )
        self.session_meta.pack(side="right")

        self.session_task = tk.Label(
            inner, text="No session yet", bg=T.SURFACE, fg=T.TEXT_SECONDARY,
            font=(T.FONT_UI[0], 9), anchor="w", justify="left", wraplength=400,
        )
        self.session_task.pack(fill="x", pady=(6, 0))

        tk.Label(
            parent, text="TASK QUEUE", bg=T.BG, fg=T.MUTED,
            font=(T.FONT_UI[0], 8, "bold"),
        ).pack(anchor="w", pady=(4, 4))

        queue_box = tk.Frame(
            parent, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.BORDER,
        )
        queue_box.pack(fill="x", pady=(0, 12))
        self.queue_text = tk.Label(
            queue_box,
            text="Inbox empty",
            bg=T.SURFACE,
            fg=T.TEXT_SECONDARY,
            font=(T.FONT_UI[0], 9),
            anchor="nw",
            justify="left",
            wraplength=400,
            padx=12,
            pady=10,
        )
        self.queue_text.pack(fill="x")

    def update_runtime_status(self, status: dict) -> None:
        """Refresh session + queue from engine.runtime_status() snapshot."""
        session = status.get("session") or {}
        queue = status.get("queue") or {}

        key = (
            session.get("state"),
            session.get("cycles"),
            session.get("last_status"),
            session.get("last_task"),
            queue.get("pending_count"),
            tuple(i.get("goal") for i in (queue.get("pending") or [])),
            tuple(i.get("goal") for i in (queue.get("recent") or [])),
        )
        if key == self._last_status_key:
            return
        self._last_status_key = key

        state = (session.get("state") or "idle").lower()
        cycles = int(session.get("cycles") or 0)
        completed = int(session.get("completed") or 0)
        failed = int(session.get("failed") or 0)
        last_status = session.get("last_status") or "—"
        last_task = (session.get("last_task") or "").strip()

        if state == "running":
            self.session_state.config(text="Running", fg=T.GREEN)
        else:
            self.session_state.config(text="Idle", fg=T.MUTED)

        meta_bits = [f"cycle {cycles}" if cycles else "no cycles"]
        if completed or failed:
            meta_bits.append(f"{completed} ok / {failed} fail")
        self.session_meta.config(text=" · ".join(meta_bits))

        if last_task:
            self.session_task.config(
                text=f"Last: {last_status} — {last_task[:120]}",
                fg=T.TEXT_SECONDARY,
            )
        elif state == "running":
            self.session_task.config(text="Session active…", fg=T.NOW_COLOR)
        else:
            self.session_task.config(text="No session yet", fg=T.MUTED)

        lines: list[str] = []
        pending = queue.get("pending") or []
        pending_count = int(queue.get("pending_count") or len(pending))
        if pending:
            lines.append(f"Pending ({pending_count})")
            for item in pending[:6]:
                lines.append(f"  · {item.get('goal') or item.get('file')}")
            if pending_count > 6:
                lines.append(f"  · +{pending_count - 6} more")
        else:
            lines.append("Pending (0) — inbox empty")

        recent = queue.get("recent") or []
        if recent:
            lines.append("")
            lines.append("Recent")
            for item in recent[:4]:
                lines.append(f"  · {item.get('goal') or item.get('file')}")

        failed_count = int(queue.get("failed_count") or 0)
        if failed_count:
            lines.append("")
            lines.append(f"Failed archive: {failed_count}")

        self.queue_text.config(text="\n".join(lines), fg=T.TEXT_SECONDARY)

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
        etype = event.get("type", "")
        if etype in (
            "session_start", "session_task_start", "session_task_done",
            "session_summary", "inbox_task", "task_complete",
        ):
            # Parent poll refreshes; mark dirty so next poll redraws.
            self._last_status_key = ""
            return

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
