"""Inline keyboards for the post editing panel."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.utils.button_styles import ikb


def get_edit_panel_keyboard(
    *,
    has_photos: bool,
    has_videos: bool,
    has_changes: bool,
) -> InlineKeyboardMarkup:
    """Main editing panel."""
    rows = [
        [
            ikb("📝 Изменить текст", "edit_change_text"),
            ikb("📋 Скопировать текст", "edit_copy_text"),
        ],
        [
            ikb("📷 Удалить фото…", "edit_manage_photos"),
            ikb("📹 Удалить видео…", "edit_manage_videos"),
        ],
    ]
    clear_row = []
    if has_photos:
        clear_row.append(ikb("🗑 Очистить все фото", "edit_clear_photos"))
    if has_videos:
        clear_row.append(ikb("🗑 Очистить все видео", "edit_clear_videos"))
    if clear_row:
        rows.append(clear_row)
    save_label = "💾 Сохранить" + (" ●" if has_changes else "")
    rows.append([ikb(save_label, "edit_save"), ikb("❌ Отмена", "edit_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_edit_text_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ikb("⬅️ К панели", "edit_back_panel")]]
    )


def get_edit_photo_manage_keyboard(photos: list) -> InlineKeyboardMarkup:
    rows = []
    for i, _ in enumerate(photos, 1):
        rows.append([ikb(f"🗑 Удалить фото #{i}", f"edit_del_photo_{i - 1}")])
    rows.append([ikb("⬅️ К панели", "edit_back_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_edit_video_manage_keyboard(videos: list) -> InlineKeyboardMarkup:
    rows = []
    for i, _ in enumerate(videos, 1):
        rows.append([ikb(f"🗑 Удалить видео #{i}", f"edit_del_video_{i - 1}")])
    rows.append([ikb("⬅️ К панели", "edit_back_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_edit_copy_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ikb("❌ Удалить сообщение", "edit_delete_copy")]]
    )
