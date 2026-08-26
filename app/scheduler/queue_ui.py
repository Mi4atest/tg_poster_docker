"""Хелперы отображения очереди без ORM lazy-load."""
from __future__ import annotations

from typing import Mapping, Optional


PLATFORM_LABELS = {
    "vk": "ВК",
    "telegram": "Telegram",
    "instagram": "Instagram",
    "max": "Max",
    "avito": "Авито",
}

PLATFORM_TITLES = {
    "vk": "ВКонтакте",
    "telegram": "Telegram",
    "instagram": "Instagram",
    "max": "Max",
    "avito": "Авито",
}


def queue_item_display_name(item) -> str:
    name = getattr(item, "post_name", None)
    if name:
        return name
    post = getattr(item, "post", None)
    if post and getattr(post, "name", None):
        return post.name
    post_id = getattr(item, "post_id", "") or ""
    return f"Пост {post_id[:8]}" if post_id else "Пост"


def format_queue_pause_status(
    *,
    global_pause: bool = False,
    platform_pauses: Optional[Mapping[str, bool]] = None,
    platform: Optional[str] = None,
) -> str:
    """Строка статуса паузы — всегда разная для разных состояний (чтобы Telegram принял edit)."""
    if global_pause:
        if platform:
            title = PLATFORM_TITLES.get(platform, platform)
            return f"⏸ Глобальная пауза — {title} тоже стоит. Сначала «Возобновить все»."
        paused = [
            PLATFORM_LABELS.get(name, name)
            for name, on in (platform_pauses or {}).items()
            if on
        ]
        extra = f" (ещё пауза платформ: {', '.join(paused)})" if paused else ""
        return f"⏸ Глобальная пауза — публикации не идут{extra}"
    if platform:
        if (platform_pauses or {}).get(platform):
            title = PLATFORM_TITLES.get(platform, platform)
            return f"⏸ {title} на паузе — очередь не публикуется"
        return "▶️ Публикации идут"
    paused = [
        PLATFORM_LABELS.get(name, name)
        for name, on in (platform_pauses or {}).items()
        if on
    ]
    if paused:
        return f"⏸ На паузе: {', '.join(paused)}"
    return "▶️ Публикации идут"
