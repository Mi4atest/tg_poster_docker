"""Экраны после создания поста: черновик готов и пост в очереди."""
from __future__ import annotations

import html
from typing import Any, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.post_avito_keyboard import AVITO_BODY_LABELS, AVITO_SCREEN_LABELS, _label_for_level
from app.bot.utils.button_styles import ikb
from app.integrations.avito.condition_maps import clamp_avito_level
from app.services.settings_service import SettingsService, get_settings_service

_SOCIAL_PLATFORMS = (
    ("vk", "VK"),
    ("telegram", "TG"),
    ("instagram", "IG"),
    ("max", "Max"),
)

_ALL_PLATFORMS = _SOCIAL_PLATFORMS + (("avito", "Авито"),)


def _media_line(photo_count: int, video_count: int) -> str:
    parts = []
    if photo_count > 0:
        parts.append(f"📷 {photo_count}")
    if video_count > 0:
        parts.append(f"📹 {video_count}")
    return " · ".join(parts) if parts else "без медиа"


def _ordinal_ru(n: int) -> str:
    return f"{n}-й"


def _enabled_social(service: SettingsService) -> list[tuple[str, str]]:
    return [(k, label) for k, label in _SOCIAL_PLATFORMS if service.is_platform_enabled(k)]


def _queue_position(stats: dict, service: SettingsService) -> int:
    enabled = _enabled_social(service)
    if not enabled:
        return max(int(stats.get("total", 0) or 0), 1)
    return max(int(stats.get(key, 0) or 0) for key, _ in enabled)


def format_publication_intervals_line(service: Optional[SettingsService] = None) -> str:
    service = service or get_settings_service()
    parts: list[str] = []
    for key, label in _ALL_PLATFORMS:
        if key == "avito":
            if service.is_platform_enabled("avito") and service.is_avito_queue_allowed():
                parts.append(f"{label} — фид")
            else:
                parts.append(f"{label} выкл")
            continue
        if service.is_platform_enabled(key):
            mins = service.get_platform_interval_minutes(key)
            parts.append(f"{label} {mins} мин")
        else:
            parts.append(f"{label} выкл")
    return "📡 " + " · ".join(parts)


def format_publication_eta_line(stats: dict, service: Optional[SettingsService] = None) -> str:
    service = service or get_settings_service()
    parts: list[str] = []
    for key, label in _SOCIAL_PLATFORMS:
        if not service.is_platform_enabled(key):
            continue
        count = int(stats.get(key, 0) or 0)
        if count <= 0:
            continue
        interval = service.get_platform_interval_minutes(key)
        eta = max(0, (count - 1) * interval)
        if eta == 0:
            parts.append(f"{label} скоро")
        else:
            parts.append(f"{label} ~{eta} мин")
    if not parts:
        return ""
    return "⏱ Примерно: " + " · ".join(parts)


def format_avito_draft_line(avito_draft: Optional[dict], service: Optional[SettingsService] = None) -> str:
    service = service or get_settings_service()
    if not service.is_platform_enabled("avito") or not service.is_avito_queue_allowed():
        return ""
    if not isinstance(avito_draft, dict):
        return ""
    sl = clamp_avito_level(avito_draft.get("screen_level", 1))
    bl = clamp_avito_level(avito_draft.get("body_level", 1))
    screen = html.escape(_label_for_level(sl, AVITO_SCREEN_LABELS))
    body = html.escape(_label_for_level(bl, AVITO_BODY_LABELS))
    return f"🛒 Авито: экран «{screen}», корпус «{body}»"


def build_post_ready_text(post_name: str, photo_count: int, video_count: int) -> str:
    name = html.escape(post_name or "Без названия")
    media = _media_line(photo_count, video_count)
    lines = [
        "✅ <b>Пост готов</b>",
        "",
        f"📝 {name}",
        media,
        "",
        "Статус: 📝 черновик",
        "👉 Следующий шаг — «В очередь»",
    ]
    return "\n".join(lines)


def build_post_ready_keyboard(post_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ikb("📋 В очередь", f"add_to_queue_{post_id}")],
            [InlineKeyboardButton(text="📝 В черновики", callback_data="pending_posts")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")],
        ]
    )


def build_post_queued_text(
    post_name: str,
    photo_count: int,
    video_count: int,
    queue_stats: dict,
    avito_draft: Optional[dict] = None,
    service: Optional[SettingsService] = None,
) -> str:
    service = service or get_settings_service()
    name = html.escape(post_name or "Без названия")
    media = _media_line(photo_count, video_count)
    position = _queue_position(queue_stats, service)

    lines = [
        f"✅ <b>Пост в очереди ({_ordinal_ru(position)})</b>",
        "",
        f"📝 {name}",
        media,
        "",
        "⏳ Публикация начнётся автоматически",
    ]

    eta = format_publication_eta_line(queue_stats, service)
    if eta:
        lines.append(eta)

    lines.append(format_publication_intervals_line(service))

    avito_line = format_avito_draft_line(avito_draft, service)
    if avito_line:
        lines.append(avito_line)

    lines.extend(["", "<b>Что дальше?</b>"])
    return "\n".join(lines)


def build_post_queued_keyboard(post_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ikb("🆕 Создать ещё один", f"create_another_post_{post_id}")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")],
        ]
    )


def post_media_counts(post: dict[str, Any]) -> tuple[int, int]:
    photos = post.get("photos") or []
    videos = post.get("videos") or []
    photo_count = len(photos) if isinstance(photos, list) else 0
    video_count = len(videos) if isinstance(videos, list) else 0
    return photo_count, video_count
