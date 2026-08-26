from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.bot.utils.button_styles import ikb
from app.bot.utils.home_text import truncate_note_button
from app.services.shop_notes_service import (
    CATEGORY_ASSORTMENT,
    CATEGORY_SERVICE,
    CATEGORY_STATIONERY,
)


def get_note_add_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ikb("❌ Отмена", "note_add_cancel")]]
    )


def get_note_category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ikb("📎 Канцы", f"note_cat_{CATEGORY_STATIONERY}")],
            [ikb("📦 Ассортимент", f"note_cat_{CATEGORY_ASSORTMENT}")],
            [ikb("🔧 Сервис", f"note_cat_{CATEGORY_SERVICE}")],
            [ikb("без метки", "note_cat_none")],
            [ikb("❌ Отмена", "note_add_cancel")],
        ]
    )


def get_note_done_picker_keyboard(notes: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for note in notes:
        body = truncate_note_button(note.get("body") or "")
        rows.append([
            InlineKeyboardButton(
                text=f"✅ {body}",
                callback_data=f"note_done_{note['id']}",
            )
        ])
    rows.append([ikb("⬅️ Назад", "note_done_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
