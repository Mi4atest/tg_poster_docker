"""Post editing panel: text formatting, draft state, UI refresh."""

from __future__ import annotations

import html
import re
from typing import Any, Optional, Union

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.keyboards.post_edit_keyboard import (
    get_edit_panel_keyboard,
    get_edit_text_prompt_keyboard,
)
from app.bot.utils.post_media import format_media_summary
from app.utils.product_parser import extract_product_description, extract_product_name

TEXT_PREVIEW_LEN = 120
# Запас под HTML-обёртку и статусы площадок (лимит сообщения TG = 4096).
ARCHIVE_CARD_TEXT_LIMIT = 3500


def _escape_md(text: str) -> str:
    for ch in ("\\", "_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def edit_draft_from_data(data: dict) -> dict[str, Any]:
    return {
        "text": data.get("edit_post_text") or "",
        "photos": list(data.get("edit_post_photos") or []),
        "videos": list(data.get("edit_post_videos") or []),
    }


def has_unsaved_changes(data: dict) -> bool:
    original = data.get("original_post") or {}
    draft = edit_draft_from_data(data)
    return (
        draft["text"] != (original.get("text") or "")
        or draft["photos"] != list(original.get("photos") or [])
        or draft["videos"] != list(original.get("videos") or [])
    )


def _text_preview(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "_(пусто)_"
    one_line = " ".join(text.split())
    if len(one_line) <= TEXT_PREVIEW_LEN:
        return _escape_md(one_line)
    return _escape_md(one_line[: TEXT_PREVIEW_LEN - 1] + "…")


def format_edit_panel_body(data: dict, *, media_hint: str = "") -> str:
    draft = edit_draft_from_data(data)
    original = data.get("original_post") or {}
    post_name = original.get("name") or "Без названия"
    changed = has_unsaved_changes(data)
    summary = format_media_summary(draft["photos"], draft["videos"])

    lines = [
        "✏️ *Редактирование поста*",
        "",
        f"📝 *{_escape_md(post_name)}*",
        "",
        f"Текст: *{len(draft['text'])}* симв.",
        _text_preview(draft["text"]),
        "",
        summary,
        "",
        "Отправьте *фото или видео альбомом* в чат — файлы *добавятся* к текущим, порядок сохранится.",
        "Лимиты: до 10 фото и 5 видео.",
    ]
    if changed:
        lines.extend(["", "● *Есть несохранённые изменения*"])
    if media_hint:
        lines.extend(["", media_hint])
    return "\n".join(lines)


def format_edit_text_prompt_body(data: dict) -> str:
    draft = edit_draft_from_data(data)
    return (
        "📝 *Новый текст поста*\n\n"
        f"Сейчас: *{len(draft['text'])}* симв.\n"
        f"{_text_preview(draft['text'])}\n\n"
        "Отправьте сообщение с новым текстом."
    )


def format_post_card(post: dict, *, success_prefix: str = "") -> str:
    """Single post view card (drafts / after save). Plain text, no HTML."""
    post_name = post.get("name") or "Без названия"
    text = post.get("text") or ""
    photos = post.get("photos") or []
    videos = post.get("videos") or []
    photo_count = len(photos) if isinstance(photos, list) else 0
    video_count = len(videos) if isinstance(videos, list) else 0

    if len(text) > 1000:
        text = text[:997] + "..."

    lines = []
    if success_prefix:
        lines.append(success_prefix)
        lines.append("")
    lines.extend(
        [
            f"📝 {post_name}",
            "",
            text,
            "",
            f"📷 {photo_count} фото",
            f"📹 {video_count} видео",
            "",
        ]
    )
    vk = "✅" if post.get("is_published_vk") else "❌"
    tg = "✅" if post.get("is_published_telegram") else "❌"
    ig = "✅" if post.get("is_published_instagram") else "❌"
    mx = "✅" if post.get("is_published_max") else "❌"
    av = "✅" if post.get("is_published_avito") else "❌"
    st = "✅" if post.get("is_published_vk_story") else "❌"
    lines.append(f"ВК: {vk}, ТГ: {tg}, IG: {ig}, MAX: {mx}, Авито: {av}, Сторис: {st}")
    return "\n".join(lines)


def _extract_price_line(text: str) -> str:
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if "💵" in stripped or re.match(r"(?i)^цена\s*:", stripped) or "цена:" in lower:
            return stripped
    return ""


def format_archive_post_card(post: dict, *, success_prefix: str = "") -> str:
    """Карточка поста из архива: две области <pre> для копирования (название + описание).

    Как в вечернем отчёте: тап/клик по блоку копирует только его содержимое.
    Цена и метаданные — обычный текст между блоками.
    """
    post_name = post.get("name") or "Без названия"
    text = (post.get("text") or "").strip()
    photos = post.get("photos") or []
    videos = post.get("videos") or []
    photo_count = len(photos) if isinstance(photos, list) else 0
    video_count = len(videos) if isinstance(videos, list) else 0

    title = (extract_product_name(text) or post_name).strip()
    description = (extract_product_description(text) or "").strip()
    price_line = _extract_price_line(text)

    # Укладываемся в лимит сообщения: при переполнении режем описание.
    overhead = 280 + len(post_name) + len(title) + len(price_line) + len(success_prefix or "")
    max_desc = max(200, ARCHIVE_CARD_TEXT_LIMIT - overhead)
    if len(description) > max_desc:
        description = description[: max_desc - 1].rstrip() + "…"

    lines: list[str] = []
    if success_prefix:
        lines.append(html.escape(success_prefix))
        lines.append("")
    lines.append(f"📝 {html.escape(post_name)}")
    lines.append("")
    if title:
        lines.append(f"<pre>{html.escape(title)}</pre>")
        lines.append("")
    if price_line:
        lines.append(html.escape(price_line))
        lines.append("")
    if description:
        lines.append(f"<pre>{html.escape(description)}</pre>")
        lines.append("")
    elif text and not title:
        # Нестандартный пост — один копируемый блок с сырым текстом
        body = text if len(text) <= max_desc else text[: max_desc - 1].rstrip() + "…"
        lines.append(f"<pre>{html.escape(body)}</pre>")
        lines.append("")

    lines.append(f"📷 {photo_count} фото")
    lines.append(f"📹 {video_count} видео")
    lines.append("")
    vk = "✅" if post.get("is_published_vk") else "❌"
    tg = "✅" if post.get("is_published_telegram") else "❌"
    ig = "✅" if post.get("is_published_instagram") else "❌"
    mx = "✅" if post.get("is_published_max") else "❌"
    av = "✅" if post.get("is_published_avito") else "❌"
    st = "✅" if post.get("is_published_vk_story") else "❌"
    lines.append(f"ВК: {vk}, ТГ: {tg}, IG: {ig}, MAX: {mx}, Авито: {av}, Сторис: {st}")
    return "\n".join(lines)


def format_post_card_for_user(
    post: dict,
    user_data: dict | None = None,
    *,
    success_prefix: str = "",
) -> tuple[str, Optional[str]]:
    """Карточка поста: HTML с copy-блоками в архиве, иначе plain text.

    Returns:
        (text, parse_mode) — parse_mode is \"HTML\" or None.
    """
    ud = user_data or {}
    archive_state = ud.get("archive_state") or {}
    from_archive = bool(ud.get("in_archive") or archive_state.get("year") is not None)
    if from_archive:
        return format_archive_post_card(post, success_prefix=success_prefix), "HTML"
    return format_post_card(post, success_prefix=success_prefix), None


def format_photo_manage_body(photos: list) -> str:
    if not photos:
        return "📷 *Фото*\n\nВ посте нет фотографий."
    lines = ["📷 *Удаление фото*", "", f"В посте *{len(photos)}* фото. Выберите, что удалить:"]
    for i in range(1, len(photos) + 1):
        lines.append(f"• Фото #{i}")
    return "\n".join(lines)


def format_video_manage_body(videos: list) -> str:
    if not videos:
        return "📹 *Видео*\n\nВ посте нет видео."
    lines = ["📹 *Удаление видео*", "", f"В посте *{len(videos)}* видео. Выберите, что удалить:"]
    for i in range(1, len(videos) + 1):
        lines.append(f"• Видео #{i}")
    return "\n".join(lines)


async def _edit_ui(
    target: Union[CallbackQuery, Message],
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup],
    *,
    parse_mode: str = "Markdown",
) -> Optional[int]:
    """Edit callback message or reply; returns message_id used for panel tracking."""
    if isinstance(target, CallbackQuery):
        msg = target.message
        try:
            await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return msg.message_id
        except TelegramBadRequest:
            sent = await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return sent.message_id
    sent = await target.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    return sent.message_id


async def show_edit_panel(
    target: Union[CallbackQuery, Message],
    state: FSMContext,
    *,
    media_hint: str = "",
) -> None:
    data = await state.get_data()
    body = format_edit_panel_body(data, media_hint=media_hint)
    draft = edit_draft_from_data(data)
    kb = get_edit_panel_keyboard(
        has_photos=bool(draft["photos"]),
        has_videos=bool(draft["videos"]),
        has_changes=has_unsaved_changes(data),
    )
    msg_id = await _edit_ui(target, body, kb)
    if msg_id is not None:
        await state.update_data(edit_panel_message_id=msg_id)


async def show_edit_text_prompt(target: Union[CallbackQuery, Message], state: FSMContext) -> None:
    data = await state.get_data()
    body = format_edit_text_prompt_body(data)
    await _edit_ui(target, body, get_edit_text_prompt_keyboard())


async def refresh_edit_panel_message(
    bot,
    chat_id: int,
    state: FSMContext,
    *,
    media_hint: str = "",
) -> None:
    """Update the stored panel message after media was added via chat."""
    data = await state.get_data()
    panel_id = data.get("edit_panel_message_id")
    if not panel_id:
        return
    body = format_edit_panel_body(data, media_hint=media_hint)
    draft = edit_draft_from_data(data)
    kb = get_edit_panel_keyboard(
        has_photos=bool(draft["photos"]),
        has_videos=bool(draft["videos"]),
        has_changes=has_unsaved_changes(data),
    )
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=panel_id,
            text=body,
            reply_markup=kb,
            parse_mode="Markdown",
        )
    except TelegramBadRequest:
        pass
