"""Inline keyboards for the evening report panel."""
from __future__ import annotations

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.utils.button_styles import ikb

FIELD_BUTTONS: list[tuple[str, str]] = [
    ("notes", "📝 Заметки"),
    ("morning_cash", "Касса на утро"),
    ("day_cash", "За день"),
    ("bn", "БН"),
    ("new_advance", "Новый аванс"),
    ("old_advance", "Старый аванс"),
    ("surrendered", "Сдано"),
    ("buybacks", "Выкупы"),
    ("wholesale", "Опт"),
    ("credit", "Кредит"),
    ("nf", "НФ"),
]


def get_evening_report_panel_keyboard(
    *,
    report_text: str,
    has_changes: bool,
) -> InlineKeyboardMarkup:
    rows = [[ikb(label, f"er_field_{key}")] for key, label in FIELD_BUTTONS]
    rows.append([ikb("➕ Расход или приход", "er_extra_manage")])
    save_label = "💾 Сохранить" + (" ●" if has_changes else "")
    copy_btn = InlineKeyboardButton(
        text="📋 Скопировать",
        copy_text=CopyTextButton(text=report_text or " "),
    )
    rows.append([ikb(save_label, "evening_report_save"), copy_btn])
    rows.append([ikb("📋 Текст для копирования", "evening_report_copy"), ikb("❌ Отмена", "evening_report_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_evening_report_field_prompt_keyboard(field_key: str) -> InlineKeyboardMarkup:
    rows = []
    if field_key != "notes":
        rows.append([ikb("Пропустить", f"er_skip_{field_key}")])
    else:
        rows.append([ikb("Очистить", f"er_skip_{field_key}")])
    rows.append([ikb("⬅️ К панели", "er_back_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_evening_report_extra_manage_keyboard(extra_items: list) -> InlineKeyboardMarkup:
    rows = []
    for i, item in enumerate(extra_items):
        name = (item.get("name") or "—")[:24]
        rows.append([ikb(f"🗑 {name}", f"er_del_extra_{i}")])
    rows.append([ikb("➕ Добавить", "er_extra_add")])
    rows.append([ikb("⬅️ К панели", "er_back_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_evening_report_extra_kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ikb("Расход", "er_extra_kind_expense"), ikb("Приход", "er_extra_kind_income")],
            [ikb("⬅️ Назад", "er_extra_manage")],
        ]
    )


def get_evening_report_extra_name_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ikb("⬅️ Назад", "er_extra_manage")]]
    )


def get_evening_report_copy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ikb("❌ Удалить сообщение", "evening_report_delete_copy")]]
    )
