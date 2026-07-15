"""Inline keyboards for the evening report panel."""
from __future__ import annotations

from typing import Any

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.utils.button_styles import ikb

COPY_TEXT_MAX_LEN = 256

FIELD_LABELS: dict[str, str] = {
    "notes": "📝 Заметки",
    "morning_cash": "🌅 Касса на утро",
    "day_cash": "📈 За день",
    "bn": "💳 БН",
    "new_advance": "📥 Нов. аванс",
    "old_advance": "📤 Ст. аванс",
    "surrendered": "🚛 Сдано",
    "buybacks": "🔄 Выкупы",
    "wholesale": "📦 Опт",
    "credit": "🏦 Кредит",
    "nf": "📊 НФ",
}

FIELD_PAIRS: list[tuple[str, str]] = [
    ("morning_cash", "day_cash"),
    ("bn", "new_advance"),
    ("old_advance", "surrendered"),
    ("buybacks", "wholesale"),
    ("credit", "nf"),
]


def copy_text_for_button(report_text: str) -> str:
    """Telegram CopyTextButton: max 256 символов."""
    text = (report_text or " ").strip() or " "
    if len(text) > COPY_TEXT_MAX_LEN:
        return text[:COPY_TEXT_MAX_LEN]
    return text


def _is_field_filled(key: str, draft: dict[str, Any]) -> bool:
    if key == "notes":
        return bool((draft.get("notes_text") or "").strip())
    if key == "nf":
        return draft.get("nf_primary") is not None and draft.get("nf_secondary") is not None
    return draft.get(key) is not None


def _field_button_label(key: str, draft: dict[str, Any]) -> str:
    label = FIELD_LABELS[key]
    if _is_field_filled(key, draft):
        label += " ●"
    return label


def evening_report_date_callback(year: int, month: int, day: int) -> str:
    return f"evening_report_date_{year}_{month}_{day}"


def get_evening_report_panel_keyboard(
    *,
    draft: dict[str, Any],
    report_text: str,
    has_changes: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [ikb(_field_button_label("notes", draft), "er_field_notes")],
    ]
    for left_key, right_key in FIELD_PAIRS:
        rows.append([
            ikb(_field_button_label(left_key, draft), f"er_field_{left_key}"),
            ikb(_field_button_label(right_key, draft), f"er_field_{right_key}"),
        ])

    extra_label = "➕ Расход или приход"
    if draft.get("extra_items"):
        extra_label += " ●"
    rows.append([ikb(extra_label, "er_extra_manage")])

    save_label = "💾 Сохранить" + (" ●" if has_changes else "")
    copy_btn = InlineKeyboardButton(
        text="📋 Скопировать",
        copy_text=CopyTextButton(text=copy_text_for_button(report_text)),
    )
    rows.append([ikb(save_label, "evening_report_save"), copy_btn])
    rows.append([
        ikb("📋 Текст для копирования", "evening_report_copy"),
        ikb("❌ Отмена", "evening_report_cancel"),
    ])
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
