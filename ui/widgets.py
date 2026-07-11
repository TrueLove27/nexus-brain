"""Reusable styled widgets for the Nexus desktop app."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui import theme as T


class GlowButton(tk.Canvas):
    """Primary action button with hover glow."""

    def __init__(self, parent, text="Send", command=None, width=96, height=40, **kwargs):
        super().__init__(
            parent, width=width, height=height,
            bg=T.PANEL, highlightthickness=0, bd=0, **kwargs,
        )
        self._text = text
        self._command = command
        self._hover = False
        self._disabled = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if "state" in kw:
            self._disabled = kw.pop("state") == "disabled"
        if "text" in kw:
            self._text = kw.pop("text")
        if "command" in kw:
            self._command = kw.pop("command")
        super().configure(**kw)
        self._draw()

    config = configure

    def _on_enter(self, _=None):
        if not self._disabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _=None):
        self._hover = False
        self._draw()

    def _on_click(self, _=None):
        if not self._disabled and self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        w, h = int(self.cget("width")), int(self.cget("height"))
        r = 8
        fill = T.MUTED if self._disabled else (T.ACCENT_SOFT if self._hover else T.ACCENT)
        fg = T.BG if not self._disabled else T.PANEL
        if self._hover and not self._disabled:
            self._round_rect(2, 2, w - 2, h - 2, r, fill=T.ACCENT_GLOW, outline="")
        self._round_rect(4, 4, w - 4, h - 4, r, fill=fill, outline="")
        self.create_text(w // 2, h // 2, text=self._text, fill=fg, font=(T.FONT_UI[0], 10, "bold"))

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)


class StatusPill(tk.Frame):
    """Compact status indicator with colored dot."""

    def __init__(self, parent, label="", color=T.GREEN, **kwargs):
        super().__init__(parent, bg=T.HEADER, **kwargs)
        self._dot = tk.Canvas(self, width=10, height=10, bg=T.HEADER, highlightthickness=0, bd=0)
        self._dot.pack(side="left")
        self._dot.create_oval(2, 2, 8, 8, fill=color, outline=color)
        self._label = tk.Label(
            self, text=label, bg=T.HEADER, fg=T.TEXT_SECONDARY, font=(T.FONT_UI[0], 8),
        )
        self._label.pack(side="left", padx=(4, 0))

    def set(self, label: str, color: str = T.GREEN) -> None:
        self._label.config(text=label)
        self._dot.delete("all")
        self._dot.create_oval(2, 2, 8, 8, fill=color, outline=color)


class Card(tk.Frame):
    """Elevated panel with title bar."""

    def __init__(self, parent, title="", accent=T.ACCENT, **kwargs):
        super().__init__(
            parent, bg=T.PANEL, highlightthickness=1,
            highlightbackground=T.BORDER, highlightcolor=T.BORDER_GLOW, **kwargs,
        )
        hdr = tk.Frame(self, bg=T.PANEL)
        hdr.pack(fill="x", padx=T.PAD, pady=(12, 6))
        tk.Frame(hdr, bg=accent, width=3, height=14).pack(side="left", padx=(0, 8))
        tk.Label(
            hdr, text=title.upper(), bg=T.PANEL, fg=accent,
            font=(T.FONT_UI[0], 8, "bold"),
        ).pack(side="left")
        tk.Frame(self, bg=T.BORDER, height=1).pack(fill="x", padx=T.PAD)
        self.body = tk.Frame(self, bg=T.PANEL)
        self.body.pack(fill="both", expand=True, padx=8, pady=8)


class PlaceholderEntry(tk.Frame):
    """Styled input with placeholder hint."""

    def __init__(self, parent, placeholder="Ask Nexus anything...", on_submit=None, **kwargs):
        super().__init__(parent, bg=T.PANEL, **kwargs)
        self._placeholder = placeholder
        self._on_submit = on_submit
        self._has_focus = False

        shell = tk.Frame(
            self, bg=T.BG_ELEVATED, highlightthickness=1,
            highlightbackground=T.BORDER, highlightcolor=T.ACCENT,
        )
        shell.pack(fill="x", expand=True)
        self.entry = tk.Entry(
            shell, font=(T.FONT_UI[0], 12), bg=T.BG_ELEVATED, fg=T.TEXT,
            insertbackground=T.ACCENT, relief="flat", bd=0,
        )
        self.entry.pack(fill="x", expand=True, ipady=12, ipadx=14, padx=2, pady=2)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<Return>", self._submit)
        self._show_placeholder()

    def _focus_in(self, _=None):
        self._has_focus = True
        if self.entry.get() == self._placeholder:
            self.entry.delete(0, tk.END)
            self.entry.config(fg=T.TEXT)

    def _focus_out(self, _=None):
        self._has_focus = False
        if not self.entry.get().strip():
            self._show_placeholder()

    def _show_placeholder(self):
        self.entry.delete(0, tk.END)
        self.entry.insert(0, self._placeholder)
        self.entry.config(fg=T.MUTED)

    def _submit(self, _=None):
        if self._on_submit:
            self._on_submit()

    def get(self) -> str:
        val = self.entry.get().strip()
        return "" if val == self._placeholder else val

    def clear(self) -> None:
        self.entry.delete(0, tk.END)
        if not self._has_focus:
            self._show_placeholder()
        else:
            self.entry.config(fg=T.TEXT)

    def focus_set(self):
        self.entry.focus_set()


def apply_dark_scrollbar_style(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Vertical.TScrollbar",
        background=T.BORDER,
        troughcolor=T.BG_CHAT,
        bordercolor=T.BG_CHAT,
        arrowcolor=T.MUTED,
    )
