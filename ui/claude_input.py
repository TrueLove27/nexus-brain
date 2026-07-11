"""Claude-style message input — rounded bar with integrated send button."""

from __future__ import annotations

import tkinter as tk

from ui import theme as T


class ClaudeInputBar(tk.Frame):
    """Multi-line input with placeholder and circular send button."""

    def __init__(self, parent, placeholder="Message Nexus…", on_submit=None, **kwargs):
        super().__init__(parent, bg=T.BG_CHAT, **kwargs)
        self._placeholder = placeholder
        self._on_submit = on_submit
        self._has_focus = False
        self._disabled = False

        self._shell = tk.Frame(
            self,
            bg=T.SURFACE_INPUT,
            highlightthickness=1,
            highlightbackground=T.BORDER,
            highlightcolor=T.BORDER_FOCUS,
        )
        self._shell.pack(fill="x", expand=True, padx=T.CHAT_PAD_X, pady=(0, 16))

        inner = tk.Frame(self._shell, bg=T.SURFACE_INPUT)
        inner.pack(fill="both", expand=True, padx=4, pady=4)

        self.text = tk.Text(
            inner,
            height=1,
            font=(T.FONT_UI[0], 12),
            bg=T.SURFACE_INPUT,
            fg=T.MUTED,
            insertbackground=T.ACCENT,
            relief="flat",
            wrap="word",
            padx=12,
            pady=10,
            highlightthickness=0,
            bd=0,
        )
        self.text.pack(side="left", fill="both", expand=True)
        self.text.bind("<FocusIn>", self._focus_in)
        self.text.bind("<FocusOut>", self._focus_out)
        self.text.bind("<Return>", self._on_return)
        self.text.bind("<KeyRelease>", self._auto_height)
        self._show_placeholder()

        self._send = tk.Canvas(
            inner, width=36, height=36, bg=T.SURFACE_INPUT,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        self._send.pack(side="right", padx=(4, 8), pady=6)
        self._send.bind("<Button-1>", lambda e: self._submit())
        self._send.bind("<Enter>", lambda e: self._draw_send(hover=True))
        self._send.bind("<Leave>", lambda e: self._draw_send(hover=False))
        self._draw_send(hover=False)

    def _draw_send(self, hover: bool = False) -> None:
        self._send.delete("all")
        disabled = self._disabled
        fill = T.BORDER if disabled else (T.ACCENT_HOVER if hover else T.ACCENT)
        self._send.create_oval(2, 2, 34, 34, fill=fill, outline="")
        arrow = "▲" if not disabled else "·"
        fg = T.BG if not disabled else T.MUTED
        self._send.create_text(18, 18, text=arrow, fill=fg, font=(T.FONT_UI[0], 11, "bold"))

    def _auto_height(self, _=None) -> None:
        if self._has_focus or self.text.get("1.0", "end-1c").strip() != self._placeholder:
            lines = int(self.text.index("end-1c").split(".")[0])
            self.text.configure(height=min(max(lines, 1), 5))

    def _focus_in(self, _=None) -> None:
        self._has_focus = True
        if self.text.get("1.0", "end-1c").strip() == self._placeholder:
            self.text.delete("1.0", tk.END)
            self.text.configure(fg=T.TEXT)

    def _focus_out(self, _=None) -> None:
        self._has_focus = False
        if not self.text.get("1.0", "end-1c").strip():
            self._show_placeholder()

    def _show_placeholder(self) -> None:
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", self._placeholder)
        self.text.configure(fg=T.MUTED, height=1)

    def _on_return(self, event) -> str | None:
        if event.state & 0x1:  # Shift+Enter = newline
            return None
        self._submit()
        return "break"

    def _submit(self) -> None:
        if self._disabled or not self._on_submit:
            return
        self._on_submit()

    def get(self) -> str:
        val = self.text.get("1.0", "end-1c").strip()
        return "" if val == self._placeholder else val

    def clear(self) -> None:
        self.text.delete("1.0", tk.END)
        self.text.configure(height=1)
        if not self._has_focus:
            self._show_placeholder()
        else:
            self.text.configure(fg=T.TEXT)

    def set_enabled(self, enabled: bool) -> None:
        self._disabled = not enabled
        state = tk.NORMAL if enabled else tk.DISABLED
        self.text.configure(state=state)
        self._draw_send(hover=False)

    def focus_set(self) -> None:
        self.text.focus_set()
