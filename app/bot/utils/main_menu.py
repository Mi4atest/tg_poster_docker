"""Сборка главного меню: напоминалки, сводка месяца, клавиатура."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import LinkPreviewOptions

from app.bot.keyboards.main_keyboard import get_main_keyboard
from app.bot.utils.home_text import format_home_html
from app.utils.monthly_sales_formatter import format_monthly_sales_html

logger = logging.getLogger(__name__)


def get_queue_count(bot) -> int:
    """Количество постов в очереди публикации."""
    try:
        if hasattr(bot, "orchestrator"):
            stats = bot.orchestrator.get_queue_stats()
            return stats.get("total", 0)
    except Exception:
        pass
    return 0


async def get_drafts_count() -> int:
    """Количество черновиков (не в очереди, не опубликованы)."""
    from app.bot.handlers.post_management import get_pending_count_api

    return await get_pending_count_api()


async def build_main_keyboard(bot, notes_count: int = 0):
    """Главное меню с актуальными счётчиками очереди, черновиков и заметок."""
    queue_count = get_queue_count(bot)
    drafts_count = await get_drafts_count()
    return get_main_keyboard(
        queue_count=queue_count,
        drafts_count=drafts_count,
        notes_count=notes_count,
    )


async def build_home_screen(bot) -> tuple[str, object]:
    """Текст + клавиатура главного экрана."""
    from app.db.database import run_db
    from app.db.monthly_sales_queries import load_current_month_archived
    from app.services.shop_notes_service import list_active_notes

    notes: list = []
    try:
        notes = await run_db(list_active_notes)
    except Exception:
        logger.exception("home: failed to load shop notes")

    products: list = []
    month_name = "Месяц"
    try:
        products, month_name = await run_db(load_current_month_archived)
    except Exception:
        logger.exception("home: failed to load monthly archive")

    sales_html = format_monthly_sales_html(products, month_name)
    text = format_home_html(notes, sales_html)
    keyboard = await build_main_keyboard(bot, notes_count=len(notes))
    return text, keyboard


async def show_home(target, bot, *, edit: bool = True) -> None:
    """Показать главный экран (edit сообщения или новое)."""
    text, keyboard = await build_home_screen(bot)
    kwargs = {
        "parse_mode": "HTML",
        "reply_markup": keyboard,
        "link_preview_options": LinkPreviewOptions(is_disabled=True),
    }
    if edit:
        try:
            await target.edit_text(text, **kwargs)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            logger.warning("home edit failed, sending new: %s", e)
    await target.answer(text, **kwargs)
