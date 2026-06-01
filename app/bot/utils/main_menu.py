"""Сборка главного меню бота с актуальными счётчиками."""

from app.bot.keyboards.main_keyboard import get_main_keyboard


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


async def build_main_keyboard(bot):
    """Главное меню с актуальными счётчиками очереди и черновиков."""
    queue_count = get_queue_count(bot)
    drafts_count = await get_drafts_count()
    return get_main_keyboard(queue_count=queue_count, drafts_count=drafts_count)
