"""Клавиатуры мастера «Запланировать» и списка запланированных."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.utils.button_styles import ikb
from app.utils.time_msk import format_schedule_msk


def get_schedule_day_keyboard(post_id: str, *, reschedule: bool = False) -> InlineKeyboardMarkup:
    prefix = "sched_rs_" if reschedule else "sched_"
    rows = [
        [
            ikb("Сегодня", f"{prefix}day_0_{post_id}"),
            ikb("Завтра", f"{prefix}day_1_{post_id}"),
        ],
        [
            ikb("Послезавтра", f"{prefix}day_2_{post_id}"),
            ikb("Другая дата", f"{prefix}day_custom_{post_id}"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}cancel_{post_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_schedule_time_keyboard(post_id: str, *, reschedule: bool = False) -> InlineKeyboardMarkup:
    prefix = "sched_rs_" if reschedule else "sched_"
    presets = ("10:00", "12:00", "15:00", "18:00", "21:00")
    rows = []
    row: list[InlineKeyboardButton] = []
    for label in presets:
        hhmm = label.replace(":", "")
        row.append(ikb(label, f"{prefix}time_{hhmm}_{post_id}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([ikb("Другое время", f"{prefix}time_custom_{post_id}")])
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ День", callback_data=f"{prefix}back_day_{post_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}cancel_{post_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_schedule_confirm_keyboard(
    post_id: str, *, reschedule: bool = False
) -> InlineKeyboardMarkup:
    prefix = "sched_rs_" if reschedule else "sched_"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ikb("✅ Подтвердить", f"{prefix}confirm_{post_id}", style="success")],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить", callback_data=f"{prefix}back_day_{post_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"{prefix}cancel_{post_id}"
                ),
            ],
        ]
    )


def get_scheduled_list_keyboard(items: list) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for item in items[:15]:
        name = (getattr(item, "post_name", None) or f"Пост {item.post_id[:8]}")[:28]
        when = ""
        if getattr(item, "scheduled_at", None):
            when = format_schedule_msk(item.scheduled_at)
        label = f"📅 {name}" if not when else f"📅 {name} · {when}"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"scheduled_post_{item.post_id}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_scheduled_post_actions_keyboard(post_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ikb("✏️ Перенести", f"sched_rs_start_{post_id}")],
            [ikb("📋 В очередь сейчас", f"scheduled_promote_{post_id}")],
            [ikb("❌ Отменить", f"scheduled_cancel_{post_id}", style="danger")],
            [
                InlineKeyboardButton(
                    text="⬅️ К запланированным", callback_data="scheduled_menu"
                )
            ],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")],
        ]
    )
