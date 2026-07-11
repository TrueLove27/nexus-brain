"""Claude-style chat message rendering helpers."""

from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext

from ui import theme as T


def configure_chat_tags(chat: scrolledtext.ScrolledText) -> None:
    chat.tag_configure("spacer", spacing3=16)

    # User message — right-aligned bubble feel
    chat.tag_configure(
        "user_label", foreground=T.MUTED,
        font=(T.FONT_UI[0], 9), spacing1=20, spacing3=2,
    )
    chat.tag_configure(
        "user_text",
        foreground=T.TEXT,
        background=T.USER_BUBBLE,
        font=(T.FONT_UI[0], 12),
        lmargin1=60, lmargin2=60, rmargin=8,
        spacing1=4, spacing3=4,
    )

    # Nexus message — clean left-aligned prose
    chat.tag_configure(
        "nexus_label", foreground=T.ACCENT,
        font=(T.FONT_UI[0], 10, "bold"), spacing1=20, spacing3=4,
    )
    chat.tag_configure(
        "nexus_text",
        foreground=T.NEXUS_COLOR,
        font=(T.FONT_UI[0], 12),
        lmargin1=8, lmargin2=8, rmargin=40,
        spacing1=2, spacing3=8,
    )
    chat.tag_configure(
        "nexus_error",
        foreground=T.RED,
        font=(T.FONT_UI[0], 12),
        lmargin1=8, lmargin2=8, rmargin=40,
        spacing1=2, spacing3=8,
    )

    # Thinking / status
    chat.tag_configure(
        "status", foreground=T.MUTED,
        font=(T.FONT_UI[0], 11, "italic"),
        lmargin1=8, lmargin2=8,
    )

    # Welcome hero
    chat.tag_configure(
        "welcome_title", foreground=T.TEXT,
        font=(T.FONT_UI[0], 22), spacing1=40, spacing3=8,
        justify="center",
    )
    chat.tag_configure(
        "welcome_sub", foreground=T.TEXT_SECONDARY,
        font=(T.FONT_UI[0], 12), spacing3=24,
        lmargin1=40, lmargin2=40, justify="center",
    )


def append_user(chat: scrolledtext.ScrolledText, text: str) -> None:
    chat.configure(state="normal")
    chat.insert("end", "You\n", "user_label")
    chat.insert("end", f"  {text}\n", "user_text")
    chat.insert("end", "\n", "spacer")
    chat.configure(state="disabled")
    chat.see("end")


def append_nexus(chat: scrolledtext.ScrolledText, text: str, *, error: bool = False) -> None:
    chat.configure(state="normal")
    chat.insert("end", "Nexus\n", "nexus_label")
    tag = "nexus_error" if error else "nexus_text"
    chat.insert("end", f"{text}\n", tag)
    chat.insert("end", "\n", "spacer")
    chat.configure(state="disabled")
    chat.see("end")


def append_welcome(chat: scrolledtext.ScrolledText, title: str, subtitle: str) -> None:
    chat.configure(state="normal")
    chat.insert("end", f"{title}\n", "welcome_title")
    chat.insert("end", f"{subtitle}\n", "welcome_sub")
    chat.configure(state="disabled")
