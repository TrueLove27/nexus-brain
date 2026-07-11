"""Quick action chips — Claude-style suggestion pills."""

from __future__ import annotations

import tkinter as tk

from ui import theme as T


class QuickActions(tk.Frame):
    """Centered row of subtle suggestion chips."""

    def __init__(self, parent, actions: list[tuple[str, str]], on_pick, **kwargs):
        super().__init__(parent, bg=T.BG_CHAT, **kwargs)
        self._on_pick = on_pick
        self._chips: list[tk.Label] = []

        row = tk.Frame(self, bg=T.BG_CHAT)
        row.pack(anchor="center")

        for label, goal in actions:
            chip = tk.Label(
                row,
                text=label,
                bg=T.SURFACE,
                fg=T.TEXT_SECONDARY,
                font=(T.FONT_UI[0], 10),
                padx=14,
                pady=7,
                cursor="hand2",
                highlightthickness=1,
                highlightbackground=T.BORDER,
            )
            chip.pack(side="left", padx=4)
            chip.bind("<Enter>", lambda e, w=chip: self._hover(w, True))
            chip.bind("<Leave>", lambda e, w=chip: self._hover(w, False))
            chip.bind("<Button-1>", lambda e, g=goal: self._on_pick(g))
            self._chips.append(chip)

    def _hover(self, widget: tk.Label, on: bool) -> None:
        widget.configure(
            bg=T.SURFACE_HOVER if on else T.SURFACE,
            fg=T.TEXT if on else T.TEXT_SECONDARY,
            highlightbackground=T.ACCENT if on else T.BORDER,
        )

    def set_enabled(self, enabled: bool) -> None:
        for chip in self._chips:
            chip.configure(cursor="hand2" if enabled else "arrow")
