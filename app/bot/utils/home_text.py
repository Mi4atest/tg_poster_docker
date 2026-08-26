"""Текст напоминалок на главном экране."""
from __future__ import annotations

import html
from typing import Any

from app.services.shop_notes_service import CATEGORY_EMOJI


def note_line_html(note: dict[str, Any]) -> str:
    emoji = CATEGORY_EMOJI.get((note.get("category") or "").strip(), "📌")
    body = html.escape((note.get("body") or "").strip() or "Без текста")
    return f"{emoji} {body}"


def format_notes_html(notes: list[dict[str, Any]]) -> str:
    lines = [note_line_html(n) for n in notes]
    return "\n".join(lines)


def truncate_note_button(body: str, limit: int = 28) -> str:
    text = " ".join((body or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def format_home_html(notes: list[dict[str, Any]], sales_html: str) -> str:
    parts: list[str] = []
    notes_block = format_notes_html(notes)
    if notes_block:
        parts.append(notes_block)
    if sales_html:
        parts.append("<b>Сводка месяца</b>\n" + sales_html)
    return "\n\n".join(parts) if parts else "Главное меню"
